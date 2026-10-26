"""Real HTTP portfolio boundaries: owner, revision, snapshot and feedback secrecy."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from fractions import Fraction
from http.cookiejar import CookieJar
import json
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


class PortfolioWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_caches = (server._CATALOG_CACHE, server._UNIT_CACHE, server._PROJECT_CACHE)
        cls.catalog = server.get_catalog()
        cls.units = server.learning_units()
        cls.projects = server.learning_projects()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        server._CATALOG_CACHE, server._UNIT_CACHE, server._PROJECT_CACHE = cls.original_caches

    def setUp(self):
        self.old_store = server._STORE
        self.tmp = tempfile.TemporaryDirectory()
        server._STORE = LearningStore(Path(self.tmp.name) / "learning.sqlite")
        server._UNIT_CACHE = copy.deepcopy(self.units)
        server._PROJECT_CACHE = copy.deepcopy(self.projects)
        server._CATALOG_CACHE = copy.copy(self.catalog)
        server._CATALOG_CACHE.units = copy.deepcopy(self.catalog.units)
        self.cookies = CookieJar()
        self.client = self.new_client(self.cookies)

    def tearDown(self):
        server._STORE = self.old_store
        server._UNIT_CACHE, server._PROJECT_CACHE = self.units, self.projects
        server._CATALOG_CACHE = self.catalog
        self.tmp.cleanup()

    @staticmethod
    def new_client(cookies=None):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies if cookies is not None else CookieJar()))

    def request(self, path, values=None, *, client=None, headers=None):
        request = urllib.request.Request(self.base + path,
            data=None if values is None else json.dumps(values, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with (client or self.client).open(request, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def descriptor(self, **values):
        code, response = self.request("/api/work?" + urllib.parse.urlencode(values))
        self.assertEqual(code, 200, response)
        return response["descriptor"]

    @staticmethod
    def save_values(resource, revision=0, request_id="save-1", text="我的过程"):
        return {"resource_id": resource["resource_id"], "kind": resource["kind"],
            "expected_revision": revision, "request_id": request_id,
            "draft": {resource["resource_snapshot"]["fields"][0]["id"]: text},
            "reflection": "我还要检查条件与单位。", "self_assessment": [
                {"criterion_index": 0, "status": "partial", "comment": "需要补充理由"}], "status": "draft"}

    def create_unit_work(self):
        resource = self.descriptor(kind="unit", unit_id="fraction_equivalence")
        values = self.save_values(resource)
        values["unit_id"] = "fraction_equivalence"
        code, result = self.request("/api/work", values)
        self.assertEqual(code, 200, result)
        return resource, values, result["work"]

    def start_transfer(self, unit_id="independent_events"):
        code, result = self.request("/api/session", {"unit_id": unit_id, "mode": "transfer"})
        self.assertEqual(code, 200, result)
        return result

    def answer(self, response, raw):
        return self.request("/api/answer", {"session_id": response["session"]["id"],
            "question_id": response["item"]["question"]["id"], "answer": raw})

    def test_real_self_check_project_and_unit_descriptors_bind_server_snapshots(self):
        selectors = [
            {"kind": "self_check", "topic_id": "m7a.4.2", "criterion_index": 0},
            {"kind": "project", "project_id": "junior_track_design"},
            {"kind": "unit", "unit_id": "fraction_equivalence"},
        ]
        for selector in selectors:
            with self.subTest(kind=selector["kind"]):
                resource = self.descriptor(**selector)
                values = {**self.save_values(resource), **selector, "resource_version": "f" * 64,
                          "resource_snapshot": {"title": "伪造题目", "fields": [], "criteria": []},
                          "source_ref": "forged", "grade": "correct"}
                code, result = self.request("/api/work", values)
                self.assertEqual(code, 200, result)
                saved = result["work"]["payload"]
                self.assertEqual(saved["resource_snapshot"], resource["resource_snapshot"])
                self.assertEqual(saved["resource_version"], resource["resource_version"])
                self.assertEqual(saved["source_ref"], resource["source_ref"])
                self.assertNotIn("grade", saved)
                self.assertEqual(saved["assessment_basis"], "learner_reported_not_automatic_grade")

    def test_other_cookie_cannot_read_update_history_or_export_saved_work(self):
        resource, _, _ = self.create_unit_work()
        other = self.new_client()
        query = urllib.parse.urlencode({"resource_id": resource["resource_id"]})
        self.assertEqual(self.request("/api/work?" + query, client=other)[0], 404)
        self.assertEqual(self.request("/api/work/revisions?" + query, client=other)[0], 404)
        values = {"resource_id": resource["resource_id"], "expected_revision": 1,
                  "request_id": "foreign", "draft": {"notes": "替换内容"}}
        self.assertEqual(self.request("/api/work", values, client=other)[0], 404)
        self.assertEqual(self.request("/api/works", client=other)[1]["works"], [])
        self.assertEqual(self.request("/api/export", client=other)[1]["works"], [])
        self.assertNotIn(resource["resource_id"], json.dumps(self.request("/api/plan", client=other)[1]))
        code, own = self.request("/api/work?" + query)
        self.assertEqual(code, 200)
        self.assertEqual(own["work"]["revision"], 1)

    def test_idempotent_retries_and_stale_writes_preserve_history(self):
        resource, first, _ = self.create_unit_work()
        second = self.save_values(resource, 1, "save-2", "补充后的过程")
        code, result = self.request("/api/work", second)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["work"]["revision"], 2)
        code, repeated = self.request("/api/work", first)
        self.assertEqual(code, 200, repeated)
        self.assertTrue(repeated["work"]["duplicate"])
        self.assertEqual(repeated["work"]["revision"], 1)
        changed_key = copy.deepcopy(first)
        changed_key["draft"]["notes"] = "试图改写第一次保存"
        self.assertEqual(self.request("/api/work", changed_key)[0], 409)
        self.assertEqual(self.request("/api/work", self.save_values(resource, 1, "stale"))[0], 409)
        code, history = self.request("/api/work/revisions?" + urllib.parse.urlencode({"resource_id": resource["resource_id"]}))
        self.assertEqual(code, 200)
        self.assertEqual([row["revision"] for row in history["revisions"]], [2, 1])
        exported = self.request("/api/export")[1]
        self.assertEqual(exported["schema"], 2)
        self.assertEqual(len(exported["works"][0]["revisions"]), 2)
        self.assertEqual(len([event for event in exported["events"] if event["event_type"] == "work_saved"]), 2)

    def test_simultaneous_http_writes_return_one_success_and_one_conflict(self):
        resource, _, _ = self.create_unit_work()
        # Each thread gets its own opener; the already-established cookie is
        # copied as a header, avoiding cookie-jar mutation during the race.
        jar = next(handler.cookiejar for handler in self.client.handlers if isinstance(handler, urllib.request.HTTPCookieProcessor))
        cookie = "; ".join(f"{value.name}={value.value}" for value in jar)
        barrier = threading.Barrier(2)
        def writer(number):
            barrier.wait()
            return self.request("/api/work", self.save_values(resource, 1, f"writer-{number}", f"并发草稿 {number}"),
                                client=urllib.request.build_opener(), headers={"Cookie": cookie})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(writer, range(2)))
        self.assertCountEqual(codes, [200, 409])

    def test_old_content_work_remains_editable_after_current_unit_changes(self):
        resource, _, _ = self.create_unit_work()
        old_snapshot = copy.deepcopy(resource["resource_snapshot"])
        server._UNIT_CACHE["fraction_equivalence"]["objectives"][0]["statement"] = "新版学习目标"
        server._CATALOG_CACHE.units["fraction_equivalence"]["resource_version"] = "b" * 64
        current = self.descriptor(kind="unit", unit_id="fraction_equivalence")
        self.assertNotEqual(current["resource_id"], resource["resource_id"])
        restored = self.descriptor(resource_id=resource["resource_id"])
        self.assertEqual(restored["resource_snapshot"], old_snapshot)
        code, saved = self.request("/api/work", self.save_values(restored, 1, "old-revision-2", "继续修改原版"))
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["work"]["resource_version"], resource["resource_version"])
        self.assertEqual(saved["work"]["payload"]["resource_snapshot"], old_snapshot)
        old = self.request("/api/work/revisions?" + urllib.parse.urlencode({"resource_id": restored["resource_id"]}))[1]
        self.assertEqual(old["revisions"][1]["payload"]["draft"]["notes"], "我的过程")

    def test_deferred_work_plan_and_export_do_not_reveal_answer_or_correctness(self):
        started = self.start_transfer()
        sid = started["session"]["id"]
        qid = started["item"]["question"]["id"]
        authored = next(task for task in server._UNIT_CACHE["independent_events"]["tasks"] if task["id"] == qid)
        wrong = str(Fraction(authored["answer"]) + 1)
        code, pending = self.answer(started, wrong)
        self.assertEqual(code, 200, pending)
        self.assertFalse(pending["done"])
        self.assertEqual(pending["feedback"], {"deferred": True})
        resource = self.descriptor(kind="session_task", session_id=sid, question_id=qid)
        values = {**self.save_values(resource), "session_id": sid, "question_id": qid}
        self.assertEqual(self.request("/api/work", values)[0], 200)
        planned = self.request("/api/plan?stage=senior")[1]
        own_actions = [step for step in planned["next_steps"] if step.get("unit_id") == "independent_events"]
        self.assertEqual([step["action"] for step in own_actions], ["continue"])
        self.assertNotIn("答错", json.dumps(own_actions, ensure_ascii=False))
        exported = self.request("/api/export")[1]
        attempt = next(row for row in exported["sessions"] if row["id"] == sid)["answers"][0]
        self.assertEqual(attempt["raw_answer"], wrong)
        for private in ("status", "answer", "explanation", "rubric"):
            self.assertNotIn(private, attempt)
        for value in (resource, exported["works"], planned):
            serialized = json.dumps(value, ensure_ascii=False)
            self.assertNotIn(authored["explanation"], serialized)
            self.assertNotIn('"answer":', serialized)
            self.assertNotIn('"status": "wrong"', serialized)
        self.assertNotIn("summary", next(row for row in exported["sessions"] if row["id"] == sid))

    def test_self_assessment_never_rewrites_saved_grades(self):
        current = self.start_transfer()
        sid = current["session"]["id"]
        rubric_id = None
        while not current["done"]:
            qid = current["item"]["question"]["id"]
            task = next(row for row in server._UNIT_CACHE["independent_events"]["tasks"] if row["id"] == qid)
            if task["type"] == "rubric":
                rubric_id = qid
                answer = "我根据样本空间说明独立条件，并准备补充推理。"
            else:
                answer = str(task["answer"])
            code, current = self.answer(current, answer)
            self.assertEqual(code, 200, current)
        self.assertIsNotNone(rubric_id)
        before = copy.deepcopy(current["summary"])
        resource = self.descriptor(kind="session_task", session_id=sid, question_id=rubric_id)
        values = {**self.save_values(resource), "session_id": sid, "question_id": rubric_id,
                  "status": "submitted", "verdict": "correct", "grade": "correct"}
        values["self_assessment"] = [{"criterion_index": index, "status": "met", "comment": "我认为已达到"}
                                     for index in range(len(resource["resource_snapshot"]["criteria"]))]
        code, saved = self.request("/api/work", values)
        self.assertEqual(code, 200, saved)
        self.assertNotIn("grade", saved["work"]["payload"])
        listed = self.request("/api/works")[1]["works"]
        self.assertEqual(next(work for work in listed if work["resource_id"] == resource["resource_id"])["status"], "submitted")
        self.assertEqual(self.request("/api/session/" + sid)[1]["summary"], before)
        self.assertEqual(next(answer for answer in before["answers"] if answer["question_id"] == rubric_id)["status"], "needs_review")

    def test_unpresented_and_foreign_session_tasks_cannot_be_opened(self):
        started = self.start_transfer()
        sid = started["session"]["id"]
        tasks = [task for task in server._UNIT_CACHE["independent_events"]["tasks"] if task["phase"] == "transfer"]
        query = urllib.parse.urlencode({"kind": "session_task", "session_id": sid, "question_id": tasks[-1]["id"]})
        self.assertEqual(self.request("/api/work?" + query)[0], 404)
        foreign = urllib.parse.urlencode({"kind": "session_task", "session_id": sid,
                                          "question_id": started["item"]["question"]["id"]})
        self.assertEqual(self.request("/api/work?" + foreign, client=self.new_client())[0], 404)

    def test_malformed_work_fields_return_validation_response_without_partial_save(self):
        resource = self.descriptor(kind="unit", unit_id="fraction_equivalence")
        base = {**self.save_values(resource), "unit_id": "fraction_equivalence"}
        alterations = [
            {"resource_id": []}, {"resource_id": {"id": resource["resource_id"]}},
            {"status": []}, {"status": {}},
            {"self_assessment": [{"criterion_index": 0, "status": []}]},
            {"self_assessment": [{"criterion_index": True, "status": "met"}]},
            {"draft": {"not-a-field": "attempt"}},
        ]
        for change in alterations:
            with self.subTest(change=change):
                code, result = self.request("/api/work", {**base, **change})
                self.assertEqual(code, 400, result)
        self.assertEqual(self.request("/api/works")[1]["total"], 0)

    def test_work_history_and_list_pagination_are_owner_scoped(self):
        resource, _, _ = self.create_unit_work()
        self.request("/api/work", self.save_values(resource, 1, "next-revision"))
        project = self.descriptor(kind="project", project_id="junior_track_design")
        code, result = self.request("/api/work", {**self.save_values(project), "project_id": "junior_track_design"})
        self.assertEqual(code, 200, result)
        first = self.request("/api/works?limit=1&offset=0")[1]
        second = self.request("/api/works?limit=1&offset=1")[1]
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertEqual(first["works"][0]["status"], "draft")
        self.assertNotEqual(first["works"][0]["resource_id"], second["works"][0]["resource_id"])
        query = urllib.parse.urlencode({"resource_id": resource["resource_id"], "limit": 1, "offset": 1})
        history = self.request("/api/work/revisions?" + query)[1]
        self.assertEqual([row["revision"] for row in history["revisions"]], [1])
        self.assertEqual(self.request("/api/works?limit=0")[0], 400)

    def test_opening_self_check_editor_records_exact_task_and_declared_family_exposure(self):
        resource = self.descriptor(kind="self_check", topic_id="m5b.4.1", criterion_index=1)
        task = server.get_catalog().self_check("m5b.4.1", 1)
        owner = next(cookie.value for cookie in self.cookies if cookie.name == server._OWNER_COOKIE)
        indexed = server.get_store().exposures(owner)
        self.assertIn(task["id"], indexed["questions"])
        self.assertIn(task["family_id"], indexed["families"])
        self.assertEqual(set(indexed["questions"]), {task["id"]})
        self.assertEqual(set(indexed["families"]), {task["family_id"]})
        excluded, _, _ = server.exposure_exclusions(owner)
        bank_siblings = {qid for qid, value in server.get_catalog().tasks.items()
                         if value.get("source_kind") == "bank" and value.get("family_id") == task["family_id"]}
        self.assertTrue(bank_siblings)
        self.assertTrue(bank_siblings <= excluded)
        values = {**self.save_values(resource), "topic_id": "m5b.4.1", "criterion_index": 1}
        self.assertEqual(self.request("/api/work", values)[0], 200)
        previous_count = server.get_store().exposures(owner)["families"][task["family_id"]]["presentation_count"]
        changed = {**task, "family_id": "a-new-family", "resource_version": "f" * 64}
        with patch.dict(server.get_catalog().tasks, {task["id"]: changed}):
            restored = self.descriptor(resource_id=resource["resource_id"])
        self.assertEqual(restored["resource_snapshot"], resource["resource_snapshot"])
        indexed = server.get_store().exposures(owner)
        self.assertGreater(indexed["families"][task["family_id"]]["presentation_count"], previous_count)
        self.assertNotIn("a-new-family", indexed["families"])

    def test_topic_continue_survives_store_reopen_and_stage_filters_saved_works(self):
        code, started = self.request("/api/session", {"targets": ["m7a.5.2"], "mode": "practice", "limit": 2})
        self.assertEqual(code, 200, started)
        server._STORE = LearningStore(server._STORE.path)
        resumed = self.request("/api/plan?stage=junior")[1]["next_steps"]
        suggestion = next(step for step in resumed if step.get("session_id") == started["session"]["id"])
        self.assertEqual(suggestion["action"], "continue")
        primary, _, _ = self.create_unit_work()
        advanced = self.descriptor(kind="unit", unit_id="independent_events")
        values = {**self.save_values(advanced), "unit_id": "independent_events"}
        self.assertEqual(self.request("/api/work", values)[0], 200)
        primary_plan = self.request("/api/plan?stage=primary")[1]["next_steps"]
        self.assertIn(primary["resource_id"], [step.get("work_resource_id") for step in primary_plan])
        self.assertNotIn(advanced["resource_id"], [step.get("work_resource_id") for step in primary_plan])
        self.assertNotIn(started["session"]["id"], [step.get("session_id") for step in primary_plan])


if __name__ == "__main__":
    unittest.main()
