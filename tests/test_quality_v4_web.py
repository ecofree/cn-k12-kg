"""Real HTTP boundaries for profile recovery and goal-specific learning help."""
import json
from fractions import Fraction
from http.cookiejar import CookieJar
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from cnkg.learning_store import LearningStore
from web import server


class QualityV4HTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.servers = []
        for index in range(2):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            httpd.learning_store = LearningStore(Path(self.temp.name) / f"{index}.sqlite")
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.servers.append((httpd, thread))
        self.addCleanup(self.close_servers)
        self.client = self.make_client()

    def close_servers(self):
        for httpd, thread in self.servers:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)

    @staticmethod
    def make_client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, path, body=None, *, client=None, installation=0):
        url = f"http://127.0.0.1:{self.servers[installation][0].server_port}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with (client or self.client).open(req, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_http_backup_restores_support_snapshot_and_need_events_to_new_installation(self):
        uid = "data_reading_and_mean"
        code, teaching = self.request("/api/learning/units/" + uid)
        self.assertEqual(200, code)
        support = teaching["instructional_support"][0]
        self.assertNotIn("diagnostic", support)
        code, activity = self.request("/api/support/start", {"unit_id": uid, "support_id": support["id"]})
        self.assertEqual(200, code, activity)
        self.assertNotIn("answer", activity["item"])
        self.assertTrue(all(set(choice) == {"id", "text"} for choice in activity["item"]["choices"]))
        qid, sid = activity["item"]["id"], activity["session"]["id"]
        authored = server.learning_units()[uid]["instructional_support"]["supports"][0]["diagnostic"]
        bad = next(choice["id"] for choice in authored["choices"] if choice["id"] != authored["answer"])
        code, next_step = self.request("/api/support/answer", {"session_id": sid, "question_id": qid, "answer": bad})
        self.assertEqual(200, code, next_step)
        _, plan = self.request("/api/plan")
        need = next(n for n in plan["learning_needs"] if n["source_ref"].get("session_id") == sid)
        code, _ = self.request("/api/learning-needs/state", {"need_id": need["need_id"], "action": "defer", "reason": "合成验证稍后继续"})
        self.assertEqual(200, code)

        code, fraction = self.request("/api/learning/units/fraction_equivalence")
        self.assertEqual(200, code, fraction)
        operation = {"unit_id": fraction["id"], "resource_version": fraction["resource_version"],
                     "representation_version": fraction["manipulative"]["representation_version"],
                     "state": {"numerator": 1, "denominator": 3, "factor": 2},
                     "reflection": "合成迁移核查：同一整体再等分，涂色实际长度不变。"}
        code, saved_response = self.request("/api/manipulative", operation)
        self.assertEqual(200, code, saved_response)
        code, saved_operation = self.request("/api/manipulative?unit_id=fraction_equivalence")
        self.assertEqual(200, code, saved_operation)
        self.assertTrue(saved_operation["same_version"])
        saved_event = saved_operation["saved"]
        self.assertEqual(operation["resource_version"], saved_event["content_sha256"])
        self.assertEqual(operation["state"], saved_event["payload"]["state"])
        self.assertEqual(operation["reflection"], saved_event["payload"]["reflection"])
        self.assertFalse(saved_event["payload"]["independent_evidence"])
        self.assertEqual(operation["representation_version"], saved_event["payload"]["representation_version"])
        representation = saved_event["payload"]["representation_snapshot"]
        self.assertEqual(operation["representation_version"], representation["representation_version"])
        self.assertEqual({key: value for key, value in fraction["manipulative"].items()
                          if key != "representation_version"}, representation["configuration"])
        self.assertEqual("fraction_strip", representation["configuration"]["kind"])
        self.assertEqual(64, len(representation["renderer_sha256"]))
        self.assertEqual(64, len(representation["validation_sha256"]))

        # A private delayed-feedback grade is part of the recoverable learning
        # asset, but must stay absent from every readable projection after import.
        code, pending = self.request("/api/session", {"unit_id": "independent_events", "mode": "transfer"})
        self.assertEqual(200, code, pending)
        pending_id, pending_qid = pending["session"]["id"], pending["item"]["question"]["id"]
        tasks = {task["id"]: task for task in server.learning_units()["independent_events"]["tasks"]}
        wrong = str(Fraction(tasks[pending_qid]["answer"]) + 1)
        code, pending = self.request("/api/answer", {"session_id": pending_id,
            "question_id": pending_qid, "answer": wrong})
        self.assertEqual(200, code, pending)
        self.assertFalse(pending["done"])
        cookies = next(handler.cookiejar for handler in self.client.handlers
                       if isinstance(handler, urllib.request.HTTPCookieProcessor))
        owner = next(cookie.value for cookie in cookies if cookie.name == server._OWNER_COOKIE)
        original_pending = self.servers[0][0].learning_store.load(pending_id, owner)
        self.assertEqual("wrong", original_pending["answers"][0]["status"])
        _, enabled = self.request("/api/profile/enable", {"label": "合成恢复档案"})
        self.assertNotIn("cookie_token", enabled)
        self.assertNotIn("owner_id", enabled["profile"])
        _, backup = self.request("/api/profile/backup", {"recovery_code": enabled["recovery_code"]})
        new_client = self.make_client()
        self.assertEqual(404, self.request("/api/session/" + sid, client=new_client, installation=1)[0])
        code, empty_operation = self.request("/api/manipulative?unit_id=fraction_equivalence", client=new_client, installation=1)
        self.assertEqual(200, code, empty_operation)
        self.assertIsNone(empty_operation["saved"])
        code, imported = self.request("/api/profile/restore", {"envelope": backup, "recovery_code": enabled["recovery_code"]}, client=new_client, installation=1)
        self.assertEqual(200, code, imported)
        _, restored = self.request("/api/session/" + sid, client=new_client, installation=1)
        self.assertEqual(next_step["item"], restored["item"])
        self.assertEqual(next_step["answers"], restored["answers"])
        code, restored_operation = self.request("/api/manipulative?unit_id=fraction_equivalence", client=new_client, installation=1)
        self.assertEqual(200, code, restored_operation)
        self.assertEqual(saved_event, restored_operation["saved"],
                         "The original operation, explanation and representation snapshot survive import")
        self.assertEqual(saved_operation, restored_operation,
                         "Restoring does not relabel the saved operation with another content or representation version")
        _, restored_plan = self.request("/api/plan", client=new_client, installation=1)
        self.assertEqual("deferred", next(n for n in restored_plan["learning_needs"] if n["need_id"] == need["need_id"])["status"])
        self.assertEqual(original_pending, self.servers[1][0].learning_store.load(pending_id, owner))
        self.assertFalse(any(n["source_ref"].get("session_id") == pending_id for n in restored_plan["learning_needs"]))
        self.assertFalse(any(observation["source_ref"]["session_id"] == pending_id
                             for skill in restored_plan["skill_evidence"] for observation in skill["observations"]))
        _, readable = self.request("/api/export", client=new_client, installation=1)
        exported_pending = next(record for record in readable["sessions"] if record["id"] == pending_id)
        self.assertEqual(wrong, exported_pending["answers"][0]["raw_answer"])
        self.assertNotIn("status", exported_pending["answers"][0])
        self.assertNotIn("summary", exported_pending)
        _, pending = self.request("/api/session/" + pending_id, client=new_client, installation=1)
        while not pending["done"]:
            task = tasks[pending["item"]["question"]["id"]]
            code, pending = self.request("/api/answer", {"session_id": pending_id,
                "question_id": task["id"], "answer": str(task.get("answer", "按条件解释我的过程。"))},
                client=new_client, installation=1)
            self.assertEqual(200, code, pending)
        _, released_plan = self.request("/api/plan", client=new_client, installation=1)
        released_need = next(n for n in released_plan["learning_needs"]
                             if n["source_ref"] == {"session_id": pending_id, "question_id": pending_qid})
        self.assertEqual("wrong", released_need["observed_status"])
        self.assertEqual(409, self.request("/api/profile/restore", {"envelope": backup, "recovery_code": enabled["recovery_code"]}, client=new_client, installation=1)[0])

    def test_foreign_support_is_private_and_recovery_revokes_old_browser_alias(self):
        _, activity = self.request("/api/support/start", {"unit_id": "data_reading_and_mean", "support_id": "data_reading_and_mean:support:read_scale"})
        sid = activity["session"]["id"]
        _, enabled = self.request("/api/profile/enable", {})
        stranger = self.make_client()
        self.assertEqual(404, self.request("/api/session/" + sid, client=stranger)[0])
        self.assertEqual(404, self.request("/api/support/hint", {"session_id": sid, "level": 1}, client=stranger)[0])
        self.assertEqual(400, self.request("/api/profile/recover", {"recovery_code": "incorrect"}, client=stranger)[0])
        self.assertEqual(200, self.request("/api/profile/recover", {"recovery_code": enabled["recovery_code"]}, client=stranger)[0])
        self.assertEqual(200, self.request("/api/session/" + sid, client=stranger)[0])
        self.assertEqual(404, self.request("/api/session/" + sid)[0], "Recovering revokes the former browser alias")

    def test_focused_selection_observes_only_explicit_goals_and_records_its_basis(self):
        uid = "data_reading_and_mean"
        skill = next(o["skill_id"] for o in server.get_catalog().units[uid]["objectives"] if o["id"].endswith(":read_scale"))
        code, activity = self.request("/api/session", {"unit_id": uid, "mode": "practice", "focus_skill_ids": [skill]})
        self.assertEqual(200, code, activity)
        self.assertEqual(["data_reading_and_mean:read_scale"], activity["item"]["question"]["objective_ids"])
        _, answer = self.request("/api/answer", {"session_id": activity["session"]["id"], "question_id": activity["item"]["question"]["id"], "answer": "999"})
        self.assertTrue(answer["done"])
        _, plan = self.request("/api/plan?stage=senior")
        self.assertTrue(any(n["source_ref"].get("session_id") == activity["session"]["id"] for n in plan["learning_needs"]))
        _, history = self.request("/api/history")
        self.assertEqual([skill], history["sessions"][0]["selection_basis"]["focus_skill_ids"])
        self.assertEqual(400, self.request("/api/session", {"unit_id": uid, "mode": "practice", "focus_skill_ids": ["not-in-unit"]})[0])

    def test_manipulation_roundtrip_is_version_bound_and_not_a_score(self):
        _, unit = self.request("/api/learning/units/fraction_equivalence")
        payload = {"unit_id": unit["id"], "resource_version": unit["resource_version"],
                   "representation_version": unit["manipulative"]["representation_version"],
                   "state": {"numerator": 1, "denominator": 3, "factor": 2}, "reflection": "整体不变，每份再分两份。"}
        self.assertEqual(200, self.request("/api/manipulative", payload)[0])
        _, saved = self.request("/api/manipulative?unit_id=fraction_equivalence")
        self.assertEqual(payload["state"], saved["saved"]["payload"]["state"])
        self.assertFalse(saved["saved"]["payload"]["independent_evidence"])
        self.assertTrue(saved["same_version"])
        self.assertEqual("fraction_strip", saved["saved"]["payload"]["representation_snapshot"]["configuration"]["kind"])
        with patch.object(server.manipulatives, "representation_version", return_value="new-renderer-version"):
            _, changed = self.request("/api/manipulative?unit_id=fraction_equivalence")
            self.assertFalse(changed["same_version"])
            self.assertEqual(saved["saved"], changed["saved"], "Old actions survive a renderer change")
            self.assertEqual(409, self.request("/api/manipulative", payload)[0])
        self.assertEqual(409, self.request("/api/manipulative", {**payload, "resource_version": "old"})[0])
        self.assertEqual(400, self.request("/api/manipulative", {**payload, "state": {"numerator": 4, "denominator": 3, "factor": 2}})[0])
        _, other = self.request("/api/manipulative?unit_id=fraction_equivalence", client=self.make_client())
        self.assertIsNone(other["saved"])


if __name__ == "__main__":
    unittest.main()
