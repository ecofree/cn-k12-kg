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
from cnkg import learning_needs
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

    def test_blank_checkboxes_survive_http_reload_until_actual_response_is_submitted(self):
        resource = self.descriptor(kind="self_check", topic_id="m1a.1.1", criterion_index=0)
        values = self.save_values(resource)
        values.update(topic_id="m1a.1.1", criterion_index=0, draft={}, status="submitted",
            self_assessment=[{"criterion_index": index, "status": "met"}
                             for index in range(len(resource["resource_snapshot"]["criteria"]))])
        code, first = self.request("/api/work", values)
        self.assertEqual(code, 200, first)
        self.assertEqual(first["submission_evidence"]["missing_field_ids"], ["response"])
        needs = self.request("/api/plan")[1]["learning_needs"]
        pending = next(row for row in needs if row.get("work_resource_id") == resource["resource_id"])
        self.assertEqual(pending["status"], "open")
        self.assertEqual(pending["revision_basis"], "missing_submission")
        query = "/api/work?resource_id=" + urllib.parse.quote(resource["resource_id"], safe="")
        restored = self.request(query)[1]
        self.assertEqual(restored["work"], first["work"])
        self.assertEqual(restored["submission_evidence"], first["submission_evidence"])
        values.update(expected_revision=1, request_id="actual-response", draft={"response": "逐个对应，点数顺序为一、二、三。"})
        code, second = self.request("/api/work", values)
        self.assertEqual(code, 200, second)
        self.assertTrue(second["submission_evidence"]["complete"])
        closed = next(row for row in self.request("/api/plan")[1]["learning_needs"] if row["need_id"] == pending["need_id"])
        self.assertEqual(closed["status"], "resolved")
        self.assertFalse(closed["resolution"]["independent_evidence"])
        revisions = self.request("/api/work/revisions?resource_id=" + urllib.parse.quote(resource["resource_id"], safe=""))[1]["revisions"]
        self.assertEqual(revisions[-1], first["work"])
        # A forged client-side completeness flag must never override the frozen descriptor.
        values.update(expected_revision=2, request_id="remove-response", draft={}, submission_evidence={"complete": True})
        self.assertEqual(self.request("/api/work", values)[0], 200)
        reopened = next(row for row in self.request("/api/plan")[1]["learning_needs"] if row["need_id"] == pending["need_id"])
        self.assertEqual(reopened["status"], "open")

    def test_old_session_reports_updated_or_removed_tasks_without_leaking_new_answers_or_rewriting_history(self):
        response = self.start_transfer()
        sid, qid = response["session"]["id"], response["item"]["question"]["id"]
        self.assertEqual(response["session"]["current_content"]["status"], "current")
        original = copy.deepcopy(response["item"]["question"])
        changed_tasks = copy.deepcopy(server.get_catalog().tasks)
        changed_tasks[qid]["resource_version"] = "f" * 64
        changed_tasks[qid]["answer"] = "CURRENT_ANSWER_MUST_STAY_HIDDEN"
        with patch.object(server.get_catalog(), "tasks", changed_tasks):
            code, restored = self.request("/api/session/" + sid)
            self.assertEqual(code, 200)
            self.assertEqual(restored["item"]["question"], original)
            self.assertEqual(restored["session"]["current_content"], {"status": "updated", "changes": [{
                "task_id": qid, "status": "updated", "url": "/?unit=independent_events"}]})
            self.assertNotIn("CURRENT_ANSWER_MUST_STAY_HIDDEN", json.dumps(restored))
            query = urllib.parse.urlencode({"kind": "session_task", "session_id": sid, "question_id": qid})
            work = self.request("/api/work?" + query)[1]
            self.assertEqual(work["current_content"]["status"], "updated")
            self.assertNotIn("CURRENT_ANSWER_MUST_STAY_HIDDEN", json.dumps(work))
            del changed_tasks[qid]
            missing = self.request("/api/session/" + sid)[1]
            self.assertEqual(missing["session"]["current_content"]["changes"], [{"task_id": qid, "status": "unavailable"}])
            self.assertEqual(missing["item"]["question"], original)

    def answer(self, response, raw):
        return self.request("/api/answer", {"session_id": response["session"]["id"],
            "question_id": response["item"]["question"]["id"], "answer": raw})

    def finish(self, response, unit_id="independent_events"):
        tasks = {task["id"]: task for task in server.learning_units()[unit_id]["tasks"]}
        while not response["done"]:
            task = tasks[response["item"]["question"]["id"]]
            code, response = self.answer(response, str(task.get("answer", "我按题目条件说明过程。")))
            self.assertEqual(code, 200, response)
        return response

    def owner(self):
        token = next(cookie.value for cookie in self.cookies if cookie.name == server._OWNER_COOKIE)
        return server.get_profile().resolve_owner(token)

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
        self.assertEqual(exported["schema"], 3)
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
        currency = self.request("/api/work?" + urllib.parse.urlencode({"resource_id": resource["resource_id"]}))[1]["current_content"]
        self.assertEqual(currency, {"status": "updated", "resource_version": "b" * 64, "url": "/?unit=fraction_equivalence"})
        code, saved = self.request("/api/work", self.save_values(restored, 1, "old-revision-2", "继续修改原版"))
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["work"]["resource_version"], resource["resource_version"])
        self.assertEqual(saved["work"]["payload"]["resource_snapshot"], old_snapshot)
        old = self.request("/api/work/revisions?" + urllib.parse.urlencode({"resource_id": restored["resource_id"]}))[1]
        self.assertEqual(old["revisions"][1]["payload"]["draft"]["notes"], "我的过程")

    def test_goal_support_preserves_initial_discrimination_and_revision_separately(self):
        selector = {"kind": "self_check", "topic_id": "m1a.1.1", "criterion_index": 0}
        resource = self.descriptor(**selector)
        snapshot = resource["resource_snapshot"]
        self.assertEqual([field["id"] for field in snapshot["fields"]], ["response", "discrimination", "revision"])
        self.assertTrue(snapshot["difficulty_support"]["hint"])
        self.assertTrue(snapshot["skill_ids"])
        first = {**self.save_values(resource), **selector, "draft": {"response": "第一次逐个数，可能漏了一个。"}}
        self.assertEqual(self.request("/api/work", first)[0], 200)
        for reference in ("difficulty", "hint", "answer"):
            code, opened = self.request("/api/work/reference", {"resource_id": resource["resource_id"], "reference_key": reference})
            self.assertEqual(code, 200, opened)
            self.assertEqual(opened["resource_version"], resource["resource_version"])
        next_values = {**first, "expected_revision": 1, "request_id": "guided-revision",
                       "draft": {"response": first["draft"]["response"], "discrimination": "给每个物体做一次标记，发现漏数。",
                                 "revision": "改变排列后重新逐个对应，总数相同。"}}
        code, saved = self.request("/api/work", next_values)
        self.assertEqual(code, 200, saved)
        records = self.request("/api/work/revisions?" + urllib.parse.urlencode({"resource_id": resource["resource_id"]}))[1]["revisions"]
        self.assertEqual(records[1]["payload"]["draft"], first["draft"])
        self.assertEqual(records[0]["payload"]["draft"], next_values["draft"])
        needs = self.request("/api/plan")[1]["learning_needs"]
        need = next(row for row in needs if row.get("work_resource_id") == resource["resource_id"])
        self.assertEqual(need["skill_ids"], snapshot["skill_ids"])
        self.assertEqual(need["status"], "open")
        references = [event for event in server.get_store().events(self.owner()) if event["event_type"] == "reference_opened"]
        self.assertEqual([row["payload"]["reference_key"] for row in references], ["difficulty", "hint", "answer"])
        self.assertTrue(all(row["content_sha256"] == resource["resource_version"] for row in references))

    def test_restored_goal_support_uses_frozen_answers_families_and_ownership(self):
        selector = {"kind": "self_check", "topic_id": "m1a.1.1", "criterion_index": 0}
        resource = self.descriptor(**selector)
        self.assertEqual(self.request("/api/work", {**self.save_values(resource), **selector})[0], 200)
        changed = server.build_teach("m1a.1.1")
        changed["objective_coverage"][0]["difficulty_support"]["hint"] = "新版提示，不得改写旧作品。"
        changed["objective_coverage"][0]["self_check"]["reference_answer"] = "新版参考。"
        with patch.object(server, "build_teach", return_value=changed):
            current = self.descriptor(**selector)
            self.assertNotEqual(current["resource_id"], resource["resource_id"])
            restored = self.descriptor(resource_id=resource["resource_id"])
            self.assertEqual(restored["resource_snapshot"], resource["resource_snapshot"])
            info = self.request("/api/work?" + urllib.parse.urlencode({"resource_id": resource["resource_id"]}))[1]
            self.assertEqual(info["current_content"]["status"], "updated")
            self.assertEqual(info["current_content"]["url"], "/?teach=m1a.1.1")
            self.assertEqual(info["current_content"]["resource_version"], current["resource_version"])
            values = {"resource_id": restored["resource_id"], "reference_key": "hint"}
            self.assertEqual(self.request("/api/work/reference", values, client=self.new_client())[0], 404)
            code, result = self.request("/api/work/reference", values)
            self.assertEqual(code, 200, result)
            last = [event for event in server.get_store().events(self.owner()) if event["event_type"] == "reference_opened"][-1]
            self.assertEqual(last["payload"]["family_ids"], resource["resource_snapshot"]["family_ids"])
            self.assertEqual(last["content_sha256"], resource["resource_version"])
        self.assertNotIn("新版", json.dumps(restored, ensure_ascii=False))

    def test_missing_current_material_does_not_hide_or_rewrite_saved_work(self):
        resource, _, _ = self.create_unit_work()
        del server._UNIT_CACHE["fraction_equivalence"]
        code, loaded = self.request("/api/work?" + urllib.parse.urlencode({"resource_id": resource["resource_id"]}))
        self.assertEqual(code, 200, loaded)
        self.assertEqual(loaded["current_content"], {"status": "unavailable"})
        self.assertEqual(loaded["descriptor"], resource)
        self.assertEqual(loaded["work"]["payload"]["draft"]["notes"], "我的过程")

    def test_old_one_field_work_is_not_silently_upgraded_to_current_support(self):
        selector = {"kind": "self_check", "topic_id": "m1a.1.1", "criterion_index": 0}
        resource = self.descriptor(**selector)
        legacy = copy.deepcopy(resource)
        legacy["resource_id"] = "self_check:legacy-fixture@" + "a" * 16
        legacy["resource_version"] = "a" * 64
        snapshot = legacy["resource_snapshot"]
        snapshot["fields"] = snapshot["fields"][:1]
        for key in ("work_schema", "reference_answer", "difficulty_support", "skill_ids", "presentation_family_ids"):
            snapshot.pop(key, None)
        from cnkg import portfolio
        server.get_store().save_work(self.owner(), legacy["resource_id"], portfolio.learner_payload(legacy,
            {"draft": {"response": "旧版思路"}, "self_assessment": [], "status": "draft"}), 0, "legacy-save")
        restored = self.descriptor(resource_id=legacy["resource_id"])
        self.assertEqual(restored["resource_snapshot"], snapshot)
        loaded = self.request("/api/work?" + urllib.parse.urlencode({"resource_id": legacy["resource_id"]}))[1]
        self.assertEqual(loaded["current_content"]["status"], "legacy_unverified")
        code, _ = self.request("/api/work/reference", {"resource_id": legacy["resource_id"], "reference_key": "hint"})
        self.assertEqual(code, 400)
        self.assertEqual(self.request("/api/work", self.save_values(restored, 1, "legacy-edit", "继续原题"))[0], 200)

    def test_partial_saved_reference_is_not_claimed_as_opened_or_replaced_by_current_content(self):
        from cnkg import portfolio
        resource = self.descriptor(kind="self_check", topic_id="m1a.1.1", criterion_index=0)
        partial = copy.deepcopy(resource)
        partial["resource_id"] = "self_check:partial-reference@" + "d" * 16
        partial["resource_version"] = "d" * 64
        partial["resource_snapshot"].pop("reference_answer")
        partial["resource_snapshot"]["difficulty_support"].pop("hint")
        owner = self.owner()
        server.get_store().save_work(owner, partial["resource_id"], portfolio.learner_payload(partial,
            {"draft": {"response": "原想法"}}), 0, "partial-reference-save")
        before = server.get_store().events(owner)
        for reference in ("hint", "answer"):
            code, response = self.request("/api/work/reference", {"resource_id": partial["resource_id"], "reference_key": reference})
            self.assertEqual(code, 400, response)
        self.assertEqual(server.get_store().events(owner), before)
        self.assertEqual(self.descriptor(resource_id=partial["resource_id"]), partial)

    def test_support_draft_is_owner_scoped_revisable_and_keeps_its_pre_submission_snapshot(self):
        unit = self.units["fraction_equivalence"]
        support = unit["instructional_support"]["supports"][0]
        code, started = self.request("/api/support/start", {"unit_id": unit["id"], "support_id": support["id"]})
        self.assertEqual(code, 200, started)
        sid, qid = started["session"]["id"], started["item"]["id"]
        selector = {"kind": "session_task", "session_id": sid, "question_id": qid}
        resource = self.descriptor(**selector)
        self.assertTrue(resource["resource_snapshot"]["draft_only"])
        self.assertEqual(resource["resource_snapshot"]["criteria"], [])
        values = {**selector, "resource_id": resource["resource_id"], "expected_revision": 0,
                  "request_id": "support-before-help", "draft": {"response": "我的原来想法"}}
        self.assertEqual(self.request("/api/work", values)[0], 200)
        stored_before = copy.deepcopy(server.get_store().load(sid, self.owner()))
        plan = self.request("/api/plan")[1]
        self.assertFalse(any(need.get("work_resource_id") == resource["resource_id"] for need in plan["learning_needs"]))
        self.assertTrue(any(step.get("session_id") == sid and step.get("continuation") == "resume_support" for step in plan["next_steps"]))
        self.assertEqual(stored_before["answers"], [])
        self.assertEqual(self.request("/api/work?" + urllib.parse.urlencode(selector), client=self.new_client())[0], 404)
        self.assertEqual(self.request("/api/work?resource_id=" + resource["resource_id"], client=self.new_client())[0], 404)
        code, answered = self.request("/api/support/answer", {"session_id": sid, "question_id": qid,
            "answer": support["diagnostic"]["answer"]})
        self.assertEqual(code, 200, answered)
        self.assertEqual(self.descriptor(**selector), resource)
        raw_answer = answered["answers"][0]["raw_answer"]
        values.update(expected_revision=1, request_id="support-after-help", draft={"response": "帮助后的修订"})
        self.assertEqual(self.request("/api/work", values)[0], 200)
        frozen = self.request("/api/session/" + sid)[1]
        self.assertEqual(frozen["answers"][0]["raw_answer"], raw_answer)
        revisions = self.request("/api/work/revisions?" + urllib.parse.urlencode({"resource_id": resource["resource_id"]}))[1]
        self.assertEqual({row["payload"]["draft"]["response"] for row in revisions["revisions"]}, {"我的原来想法", "帮助后的修订"})

    def test_support_answer_retry_recovers_saved_feedback_without_regrading_or_duplicate_answers(self):
        unit = self.units["fraction_equivalence"]
        support = unit["instructional_support"]["supports"][0]
        started = self.request("/api/support/start", {"unit_id": unit["id"], "support_id": support["id"]})[1]
        sid, qid = started["session"]["id"], started["item"]["id"]
        values = {"session_id": sid, "question_id": qid, "answer": support["diagnostic"]["answer"]}
        first = self.request("/api/support/answer", values)[1]
        original = copy.deepcopy(server.get_store().load(sid, self.owner()))
        with patch.object(server.instruction, "answer", side_effect=AssertionError("retry must not regrade")):
            code, retry = self.request("/api/support/answer", values)
            self.assertEqual(code, 200, retry)
            self.assertTrue(retry["duplicate"])
            self.assertEqual(retry["feedback"], first["feedback"])
            self.assertEqual(retry["item"], first["item"])
            self.assertEqual(self.request("/api/support/answer", {**values, "answer": "different"})[0], 409)
        self.assertEqual(server.get_store().load(sid, self.owner()), original)
        events = server.get_store().events(self.owner())
        self.assertEqual(sum(event["event_type"] == "answer_submitted" and event.get("question_id") == qid for event in events), 1)

    def test_completed_legacy_topic_without_graph_or_summary_is_readable_without_rejudgment(self):
        self.request("/api/profile")
        owner = self.owner()
        record = {"id": "f" * 32, "owner_id": owner, "kind": "topic", "mode": "practice", "title": "原来的主题",
            "created_at": "2026-01-01T00:00:00+00:00", "completed_at": "2026-01-01T00:00:05+00:00",
            "content_sha256": "e" * 64, "context": {}, "feedback_policy": "immediate", "feedback_exposed": ["retired-q"],
            "presentations": {"retired-q": {"presented_at": "2026-01-01T00:00:01+00:00"}},
            "answers": [{"question_id": "retired-q", "prompt": "当时保存的题面", "raw_answer": "原回答", "status": "wrong"}],
            "engine": {"mode": "practice", "targets": ["retired-original-topic"], "state": {"retired-original-topic": "weak"},
                       "current": None, "queue": [], "log": [], "asked": {}, "limit": 5}}
        server.get_store().save(record)
        saved = copy.deepcopy(server.get_store().load(record["id"], owner))
        with patch.object(server.engine, "summarize", side_effect=AssertionError("current rules used for historical record")):
            code, loaded = self.request("/api/session/" + record["id"])
            self.assertEqual(code, 200, loaded)
            self.assertEqual(loaded["summary"]["history_status"], "summary_not_saved")
            self.assertEqual(loaded["summary"]["answers"], record["answers"])
            self.assertEqual(loaded["summary"]["saved_topic_states"], record["engine"]["state"])
            exported = server.export_record(saved)
            self.assertEqual(exported["presented_tasks"][0]["snapshot_status"], "partial_legacy_record")
            self.assertEqual(exported["summary"], loaded["summary"])
        self.assertEqual(server.get_store().load(record["id"], owner), saved)

    def test_unfinished_legacy_record_with_missing_current_task_is_blocked_without_a_fabricated_result(self):
        self.request("/api/profile")
        owner = self.owner()
        record = {"id": "9" * 32, "owner_id": owner, "kind": "topic", "mode": "diagnose", "title": "旧会话",
            "created_at": "2026-01-01T00:00:00+00:00", "content_sha256": "b" * 64,
            "context": {}, "feedback_policy": "deferred", "feedback_exposed": [], "presentations": {},
            "answers": [{"question_id": "old-private-q", "prompt": "原来的题面", "raw_answer": "原回答",
                         "status": "wrong", "answer": "尚未放出的旧参考", "explanation": "尚未放出的旧解释"}],
            "engine": {"mode": "diagnose", "targets": ["retired-original-topic"], "state": {},
                       "current": {"questionId": "missing-old-q"}, "queue": [], "log": [], "asked": {}, "limit": 5}}
        server.get_store().save(record)
        saved = copy.deepcopy(server.get_store().load(record["id"], owner))
        with patch.object(server.engine, "summarize", side_effect=AssertionError("missing current task is not completion")):
            code, response = self.request("/api/session/" + record["id"])
        self.assertEqual(code, 200, response)
        self.assertTrue(response["session"]["resume_blocked"])
        self.assertFalse(response["done"])
        self.assertIsNone(response["item"])
        self.assertNotIn("summary", response)
        self.assertEqual(response["saved_answers"][0]["raw_answer"], "原回答")
        for key in ("status", "answer", "explanation"):
            self.assertNotIn(key, response["saved_answers"][0])
        self.assertEqual(server.get_store().load(record["id"], owner), saved)

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
        self.assertEqual(planned["need_counts"], {"open": 0, "deferred": 0, "resolved": 0})
        self.assertEqual(planned["skill_evidence"], [])
        owner = self.owner()
        stored = server.get_store().load(sid, owner)
        internal_records = server.get_store().planning_records(owner)
        self.assertEqual(internal_records[0]["answers"][0]["status"], "wrong")
        internal_need = next(need for need in learning_needs.derive_needs(server.get_catalog(), internal_records)
                             if need["source_ref"] == {"session_id": sid, "question_id": qid})
        need_id = internal_need["need_id"]
        support = next(support for support in server.learning_units()["independent_events"]["instructional_support"]["supports"]
                       if support["objective_id"] in authored["objective_ids"])
        for guessed in (need_id, "need_" + "0" * 32):
            self.assertEqual(self.request("/api/learning-needs/state", {
                "need_id": guessed, "action": "defer", "reason": "现在暂缓"})[0], 404)
            self.assertEqual(self.request("/api/support/start", {"unit_id": "independent_events",
                "support_id": support["id"], "need_ids": [guessed]})[0], 404)
        self.assertFalse(any(event["event_type"].startswith("learning_need_")
                             for event in server.get_store().events(owner)))
        projected, _, _ = server.public_learning_inputs(owner)
        self.assertNotIn("status", projected[0]["answers"][0])
        option = learning_needs.task_options(server.get_catalog(), internal_need["skill_ids"], projected,
                                            allowed_task_ids={qid})[0]
        self.assertTrue(option["repeated_question"], "Withheld grading must not make a seen question look new")
        self.assertFalse(option["new_representation"])
        # A legacy private state remains stored, but must not leak through the
        # readable event export before this source releases its feedback.
        server.get_store().set_need_state(owner, need_id, "defer", "旧版本保留的暂缓记录")
        server.get_store().add_event(owner, {"event_type": "answer_submitted", "question_id": qid,
            "occurred_at": stored["answers"][0]["answered_at"],
            "payload": {"raw_answer": wrong, "status": "wrong", "answer": authored["answer"],
                        "explanation": authored["explanation"]}}, sid)
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
        self.assertFalse(any(event.get("payload", {}).get("need_id") == need_id for event in exported["events"]))
        for event in exported["events"]:
            if event["session_id"] == sid and event["question_id"] == qid and event["event_type"] == "answer_submitted":
                self.assertEqual(event["payload"]["raw_answer"], wrong)
                for private in ("status", "answer", "explanation"):
                    self.assertNotIn(private, event["payload"])
        self.assertEqual(server.get_store().load(sid, owner), stored)
        self.assertTrue(any(event.get("payload", {}).get("need_id") == need_id
                            for event in server.get_store().events(owner)))
        self.finish(pending)
        released = self.request("/api/plan")[1]
        visible_need = next(need for need in released["learning_needs"] if need["need_id"] == need_id)
        self.assertEqual(visible_need["observed_status"], "wrong")
        self.assertEqual(visible_need["status"], "deferred")
        released_export = self.request("/api/export")[1]
        self.assertTrue(any(event.get("payload", {}).get("need_id") == need_id for event in released_export["events"]))

    def test_hidden_correct_answer_does_not_resolve_or_block_public_state_of_old_need(self):
        first = self.start_transfer()
        old_id, qid = first["session"]["id"], first["item"]["question"]["id"]
        authored = next(task for task in server.learning_units()["independent_events"]["tasks"] if task["id"] == qid)
        code, first = self.answer(first, str(Fraction(authored["answer"]) + 1))
        self.assertEqual(code, 200)
        self.finish(first)
        old_need = next(need for need in self.request("/api/plan")[1]["learning_needs"]
                        if need["source_ref"] == {"session_id": old_id, "question_id": qid})
        newer = self.start_transfer()
        newer_id = newer["session"]["id"]
        self.assertEqual(newer["item"]["question"]["id"], qid)
        code, newer = self.answer(newer, str(authored["answer"]))
        self.assertEqual(code, 200)
        self.assertFalse(newer["done"])
        owner = self.owner()
        private = learning_needs.derive_needs(server.get_catalog(), server.get_store().planning_records(owner))
        self.assertEqual(next(need for need in private if need["need_id"] == old_need["need_id"])["status"], "resolved")
        planned = self.request("/api/plan")[1]
        visible = next(need for need in planned["learning_needs"] if need["need_id"] == old_need["need_id"])
        self.assertEqual(visible["status"], "open")
        self.assertIsNone(visible["resolution"])
        self.assertFalse(any(observation["source_ref"]["session_id"] == newer_id
                             for skill in planned["skill_evidence"] for observation in skill["observations"]))
        for action in ("defer", "reopen"):
            code, result = self.request("/api/learning-needs/state", {
                "need_id": old_need["need_id"], "action": action, "reason": "继续原先可见的事项"})
            self.assertEqual(code, 200, result)
        support = next(support for support in server.learning_units()["independent_events"]["instructional_support"]["supports"]
                       if support["objective_id"] in authored["objective_ids"])
        code, result = self.request("/api/support/start", {"unit_id": "independent_events",
            "support_id": support["id"], "need_ids": [old_need["need_id"]]})
        self.assertEqual(code, 200, result)
        self.finish(newer)
        visible = next(need for need in self.request("/api/plan")[1]["learning_needs"]
                       if need["need_id"] == old_need["need_id"])
        self.assertEqual(visible["status"], "resolved")
        self.assertEqual(visible["resolution"]["source_ref"]["session_id"], newer_id)
        self.assertEqual(server.get_store().load(old_id, owner)["answers"][0]["status"], "wrong")

    def test_delayed_session_keeps_wrong_feedback_private_until_completion(self):
        code, started = self.request("/api/session", {"unit_id": "independent_events", "mode": "delayed"})
        self.assertEqual(code, 200, started)
        sid, qid = started["session"]["id"], started["item"]["question"]["id"]
        task = next(task for task in server.learning_units()["independent_events"]["tasks"] if task["id"] == qid)
        wrong = (next(choice["id"] for choice in task["choices"] if choice["id"] != task["answer"])
                 if task["type"] == "choice" else str(Fraction(task["answer"]) + 1))
        code, pending = self.answer(started, wrong)
        self.assertEqual(code, 200, pending)
        self.assertFalse(pending["done"])
        planned = self.request("/api/plan")[1]
        self.assertEqual(planned["learning_needs"], [])
        self.assertEqual(planned["skill_evidence"], [])
        self.assertTrue(any(step.get("session_id") == sid and step["action"] == "continue"
                            for step in planned["next_steps"]))
        self.finish(pending)
        needs = self.request("/api/plan")[1]["learning_needs"]
        self.assertEqual(next(need for need in needs if need["source_ref"] == {
            "session_id": sid, "question_id": qid})["observed_status"], "wrong")

    def test_immediate_practice_feedback_stays_visible_before_session_finishes(self):
        code, started = self.request("/api/session", {"unit_id": "independent_events", "mode": "practice"})
        self.assertEqual(code, 200, started)
        sid, qid = started["session"]["id"], started["item"]["question"]["id"]
        task = next(task for task in server.learning_units()["independent_events"]["tasks"] if task["id"] == qid)
        wrong = (next(choice["id"] for choice in task["choices"] if choice["id"] != task["answer"])
                 if task["type"] == "choice" else str(Fraction(task["answer"]) + 1))
        code, pending = self.answer(started, wrong)
        self.assertEqual(code, 200, pending)
        self.assertFalse(pending["done"])
        self.assertEqual(pending["feedback"]["status"], "wrong")
        planned = self.request("/api/plan")[1]
        need = next(need for need in planned["learning_needs"] if need["source_ref"] == {
            "session_id": sid, "question_id": qid})
        self.assertEqual(need["observed_status"], "wrong")
        self.assertTrue(any(observation["source_ref"] == need["source_ref"] and observation["status"] == "wrong"
                            for skill in planned["skill_evidence"] for observation in skill["observations"]))
        self.assertEqual(self.request("/api/learning-needs/state", {"need_id": need["need_id"],
            "action": "defer", "reason": "已收到即时反馈，先暂缓"})[0], 200)

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
        owner = self.owner()
        snapshot = resource["resource_snapshot"]
        self.assertEqual(snapshot["presentation_family_ids"], [task["family_id"]])
        reference_only = set(snapshot["family_ids"]) - {task["family_id"]}
        self.assertTrue(reference_only, "The real compound self-check includes supporting reference families")
        indexed = server.get_store().exposures(owner)
        self.assertIn(task["id"], indexed["questions"])
        self.assertIn(task["family_id"], indexed["families"])
        self.assertEqual(set(indexed["questions"]), {task["id"]})
        self.assertGreater(indexed["questions"][task["id"]]["presentation_count"], 0)
        self.assertGreater(indexed["families"][task["family_id"]]["presentation_count"], 0)
        for family in snapshot["family_ids"]:
            self.assertGreater(indexed["families"][family]["material_count"], 0)
            self.assertEqual(indexed["families"][family]["reference_count"], 0)
        for family in reference_only:
            self.assertEqual(indexed["families"][family]["presentation_count"], 0)
        excluded, _, _ = server.exposure_exclusions(owner)
        # The compound area-versus-volume task has no matching bank family.
        # Its actual surface/volume references still make those bank tasks seen.
        bank_siblings = {qid for qid, value in server.get_catalog().tasks.items()
                         if value.get("source_kind") == "bank" and value.get("family_id") in reference_only}
        self.assertTrue({"m5b.4.1-q1", "m5b.4.1-q2"} <= bank_siblings)
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
        for family in reference_only:
            self.assertEqual(indexed["families"][family]["presentation_count"], 0)
            self.assertEqual(indexed["families"][family]["reference_count"], 0)
        self.assertNotIn("a-new-family", indexed["families"])

    def test_undeclared_self_check_family_does_not_fall_back_to_reference_presentations(self):
        task = server.get_catalog().self_check("m5b.4.1", 1)
        with patch.dict(server.get_catalog().tasks, {task["id"]: {**task, "family_status": "unclassified"}}):
            resource = self.descriptor(kind="self_check", topic_id="m5b.4.1", criterion_index=1)
        snapshot = resource["resource_snapshot"]
        self.assertEqual(snapshot["presentation_family_ids"], [])
        indexed = server.get_store().exposures(self.owner())
        self.assertGreater(indexed["questions"][task["id"]]["presentation_count"], 0)
        for family in snapshot["family_ids"]:
            self.assertEqual(indexed["families"][family]["presentation_count"], 0)
            self.assertGreater(indexed["families"][family]["material_count"], 0)
            self.assertEqual(indexed["families"][family]["reference_count"], 0)

    def test_legacy_work_exposure_uses_its_frozen_family_fallback_and_distinct_identity(self):
        resource = self.descriptor(kind="self_check", topic_id="m5b.4.1", criterion_index=1)
        task = server.get_catalog().self_check("m5b.4.1", 1)
        row = next(row for row in server.build_teach("m5b.4.1")["objective_coverage"]
                   if row["criterion_index"] == 1)
        legacy = copy.deepcopy(resource)
        legacy["resource_snapshot"].pop("presentation_family_ids")
        legacy["resource_version"] = server._hash({"task": task["resource_version"], "source": row, "work_schema": 2})
        legacy["resource_id"] = f"self_check:{task['id']}@{legacy['resource_version'][:16]}"
        self.assertNotEqual(resource["resource_id"], legacy["resource_id"],
                            "The new exposure contract must not reuse an earlier work identity")
        from cnkg import portfolio
        owner = self.owner()
        payload = portfolio.learner_payload(legacy, {"draft": {"response": "旧契约保留的原过程"}})
        server.get_store().save_work(owner, legacy["resource_id"], payload, 0, "legacy-exposure-contract")
        before = server.get_store().exposures(owner)
        changed = {**task, "family_id": "new-current-family", "resource_version": "e" * 64}
        with patch.dict(server.get_catalog().tasks, {task["id"]: changed}):
            restored = self.descriptor(resource_id=legacy["resource_id"])
        self.assertEqual(restored, legacy)
        self.assertNotIn("presentation_family_ids", restored["resource_snapshot"])
        self.assertEqual(server.get_store().load_work(owner, legacy["resource_id"])["payload"], payload)
        after = server.get_store().exposures(owner)
        for family in legacy["resource_snapshot"]["family_ids"]:
            self.assertEqual(after["families"][family]["presentation_count"],
                             before["families"][family]["presentation_count"] + 1)
            self.assertEqual(after["families"][family]["material_count"],
                             before["families"][family]["material_count"] + 1)
            self.assertEqual(after["families"][family]["reference_count"], 0)
        self.assertNotIn("new-current-family", after["families"])

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
