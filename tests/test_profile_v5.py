"""Real HTTP counterexamples for switching away from a browser-only identity."""
from __future__ import annotations

import base64
import copy
from http.cookiejar import CookieJar
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from cnkg.learning_store import LearningStore
from cnkg.profile_recovery import ProfileRecovery
from web import server


class ProfileSwitchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.servers = []
        self.addCleanup(self.tmp.cleanup)
        for index in range(2):
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            httpd.learning_store = LearningStore(Path(self.tmp.name) / f"{index}.sqlite")
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.servers.append((httpd, thread))
        self.addCleanup(self.close_servers)

    def close_servers(self):
        for httpd, thread in self.servers:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    @staticmethod
    def client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    @staticmethod
    def cookie(client):
        jar = next(handler.cookiejar for handler in client.handlers
                   if isinstance(handler, urllib.request.HTTPCookieProcessor))
        return next((cookie.value for cookie in jar if cookie.name == server._OWNER_COOKIE), None)

    def request(self, client, path, body=None, installation=0):
        url = f"http://127.0.0.1:{self.servers[installation][0].server_port}{path}"
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with client.open(req, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def enable(self, client, label, installation=0):
        code, value = self.request(client, "/api/profile/enable", {"label": label}, installation)
        self.assertEqual(code, 200, value)
        return value["recovery_code"]

    def work(self, client, text, installation=0):
        selector = {"kind": "unit", "unit_id": "fraction_equivalence"}
        code, result = self.request(client, "/api/work?" + urllib.parse.urlencode(selector), installation=installation)
        self.assertEqual(code, 200, result)
        descriptor = result["descriptor"]
        field = descriptor["resource_snapshot"]["fields"][0]["id"]
        payload = {**selector, "resource_id": descriptor["resource_id"], "expected_revision": 0,
                   "request_id": "save-draft", "draft": {field: text}, "reflection": "需继续检查共同单位。",
                   "self_assessment": [{"criterion_index": 0, "status": "partial"}], "status": "draft"}
        code, result = self.request(client, "/api/work", payload, installation)
        self.assertEqual(code, 200, result)
        return descriptor["resource_id"], result["work"]

    def saved_work(self, client, resource_id, installation=0):
        return self.request(client, "/api/work?" + urllib.parse.urlencode({"resource_id": resource_id}),
                            installation=installation)

    def backup(self, client, code, installation=0):
        status, result = self.request(client, "/api/profile/backup", {"recovery_code": code}, installation)
        self.assertEqual(status, 200, result)
        return result

    def test_anonymous_draft_cannot_be_stranded_and_saved_code_restores_exact_work(self):
        a, b = self.client(), self.client()
        resource, original = self.work(a, "A 的原稿：先说明整体相同。")
        self.work(b, "B 的独立原稿。")
        b_code = self.enable(b, "B")
        a_cookie, b_cookie = self.cookie(a), self.cookie(b)
        # Client claims cannot exempt the authenticated current browser owner.
        status, error = self.request(a, "/api/profile/recover", {"recovery_code": b_code,
            "current_owner": "f" * 64, "has_records": False, "saved_current_code": True})
        self.assertEqual(status, 409, error)
        self.assertIn("创建并保存", error["error"])
        self.assertEqual(self.cookie(a), a_cookie)
        self.assertEqual(self.cookie(b), b_cookie)
        self.assertEqual(self.saved_work(a, resource)[1]["work"], original)
        self.assertNotIn(b_code, json.dumps(error))
        self.assertEqual(self.request(b, "/api/profile")[1]["label"], "B")

        a_code = self.enable(a, "A")
        enabled_cookie = self.cookie(a)
        for supplied in (None, b_code, "unusable"):
            status, error = self.request(a, "/api/profile/recover", {
                "recovery_code": b_code, "current_recovery_code": supplied})
            self.assertEqual(status, 409, error)
            self.assertEqual(self.cookie(a), enabled_cookie)
            self.assertNotIn(b_code, json.dumps(error))
        status, recovered = self.request(a, "/api/profile/recover", {
            "recovery_code": b_code, "current_recovery_code": a_code})
        self.assertEqual(status, 200, recovered)
        self.assertEqual(recovered["profile"]["label"], "B")
        self.assertNotIn(a_code, json.dumps(recovered))
        self.assertNotIn(b_code, json.dumps(recovered))
        self.assertNotIn("cookie_token", recovered)
        returned = self.client()
        self.assertEqual(self.request(returned, "/api/profile/recover", {"recovery_code": a_code})[0], 200)
        self.assertEqual(self.saved_work(returned, resource)[1]["work"], original)

    def test_import_is_guarded_and_corrupt_import_preserves_browser_and_both_profiles(self):
        a, source = self.client(), self.client()
        a_resource, original = self.work(a, "A 的导入前作品。")
        b_resource, b_work = self.work(source, "B 的跨安装作品。", installation=1)
        b_code = self.enable(source, "迁移 B", installation=1)
        envelope = self.backup(source, b_code, installation=1)
        original_cookie = self.cookie(a)
        bad = copy.deepcopy(envelope)
        raw = bytearray(base64.b64decode(bad["ciphertext"]))
        raw[0] ^= 1
        bad["ciphertext"] = base64.b64encode(raw).decode()
        self.assertEqual(self.request(a, "/api/profile/restore", {"envelope": bad, "recovery_code": b_code})[0], 400)
        self.assertEqual(self.cookie(a), original_cookie)
        status, error = self.request(a, "/api/profile/restore", {"envelope": envelope, "recovery_code": b_code,
            "switch_confirmed": True})
        self.assertEqual(status, 409, error)
        self.assertEqual(self.cookie(a), original_cookie)
        self.assertEqual(self.saved_work(a, a_resource)[1]["work"], original)
        with self.servers[0][0].learning_store._connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM learning_profiles").fetchone()[0], 0)
        a_code = self.enable(a, "保留 A")
        status, imported = self.request(a, "/api/profile/restore", {"envelope": envelope,
            "recovery_code": b_code, "current_recovery_code": a_code})
        self.assertEqual(status, 200, imported)
        self.assertEqual(self.saved_work(a, b_resource)[1]["work"], b_work)
        returned = self.client()
        self.assertEqual(self.request(returned, "/api/profile/recover", {"recovery_code": a_code})[0], 200)
        self.assertEqual(self.saved_work(returned, a_resource)[1]["work"], original)
        # The original installation remains independent from import and switch.
        self.assertEqual(self.saved_work(source, b_resource, installation=1)[1]["work"], b_work)

    def test_empty_browser_import_and_same_profile_recovery_need_no_second_code(self):
        source = self.client()
        resource, original = self.work(source, "原始作品。", installation=1)
        code = self.enable(source, "空浏览器恢复", installation=1)
        envelope = self.backup(source, code, installation=1)
        target = self.client()
        status, imported = self.request(target, "/api/profile/restore", {"envelope": envelope, "recovery_code": code})
        self.assertEqual(status, 200, imported)
        self.assertEqual(self.saved_work(target, resource)[1]["work"], original)
        previous = self.cookie(target)
        status, recovered = self.request(target, "/api/profile/recover", {"recovery_code": code})
        self.assertEqual(status, 200, recovered)
        self.assertNotEqual(self.cookie(target), previous)
        self.assertEqual(self.saved_work(target, resource)[1]["work"], original)

    def test_records_added_after_status_read_are_protected_by_transaction_check(self):
        a, b = self.client(), self.client()
        b_code = self.enable(b, "B")
        _, status = self.request(a, "/api/profile")
        self.assertEqual(status["switch_protection"], {"has_records": False, "requirement": "none"})
        resource, original = self.work(a, "读取状态以后新存的草稿。")
        cookie = self.cookie(a)
        code, error = self.request(a, "/api/profile/recover", {"recovery_code": b_code})
        self.assertEqual(code, 409, error)
        self.assertEqual(self.cookie(a), cookie)
        self.assertEqual(self.saved_work(a, resource)[1]["work"], original)
        _, status = self.request(a, "/api/profile")
        self.assertEqual(status["switch_protection"], {"has_records": True, "requirement": "enable_recovery"})

    def test_events_without_sessions_or_works_are_not_discarded_by_switch(self):
        a, b = self.client(), self.client()
        b_code = self.enable(b, "B")
        self.request(a, "/api/profile")
        owner = self.cookie(a)
        store = self.servers[0][0].learning_store
        store.add_event(owner, {"event_type": "reference_opened", "occurred_at": "2026-10-27T01:00:00+00:00",
            "resource_id": "old-material", "payload": {"reference_key": "worked-example", "task_ids": ["q1"]}})
        self.assertEqual(store.count(owner), 0)
        self.assertEqual(store.count_works(owner), 0)
        self.assertEqual(self.request(a, "/api/profile/recover", {"recovery_code": b_code})[0], 409)
        self.assertEqual(self.cookie(a), owner)
        self.assertEqual(len(store.events(owner)), 1)

    def test_rotated_current_code_is_required_and_failed_check_does_not_revoke_target(self):
        a, b = self.client(), self.client()
        self.work(a, "必须能返回。")
        old_code = self.enable(a, "A")
        _, rotated = self.request(a, "/api/profile/rotate", {})
        b_code = self.enable(b, "B")
        b_token = self.cookie(b)
        code, error = self.request(a, "/api/profile/recover", {"recovery_code": b_code,
            "current_recovery_code": old_code})
        self.assertEqual(code, 409, error)
        profiles = ProfileRecovery(self.servers[0][0].learning_store)
        self.assertIsNotNone(profiles.resolve_owner(b_token))
        code, value = self.request(a, "/api/profile/recover", {"recovery_code": b_code,
            "current_recovery_code": rotated["recovery_code"]})
        self.assertEqual(code, 200, value)
        self.assertIsNone(profiles.resolve_owner(b_token))


if __name__ == "__main__":
    unittest.main()
