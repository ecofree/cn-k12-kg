"""Specific counterexamples from the V4 audit, not completion-field checks."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from http.cookiejar import CookieJar
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from cnkg.learning_needs import derive_needs, task_options
from cnkg.learning_store import LearningStore, _session_summary
from cnkg.planner import plan
from cnkg.review_timing import TIMING_POLICY, interval_context, review_schedule, unit_activity_scope
from cnkg import engine
from cnkg.graph import Graph

NOW = "2026-10-26T00:00:00+00:00"
OLD = "2026-10-20T00:00:00+00:00"
HELP = "2026-10-21T00:00:00+00:00"


def catalog():
    tasks = {identity: {"id": identity, "phase": phase, "type": "numeric", "availability": "active",
        "skill_ids": ["compare"], "family_id": family, "family_status": "declared",
        "representation": representation, "resource_version": "task-v1"}
        for identity, phase, family, representation in [("p", "practice", "old-family", "bar"),
            ("t", "transfer", "transfer-family", "table"), ("d", "delayed", "new-family", "table")]}
    return {"units": {"u": {"id": "u", "title": "比较", "stage": "primary", "resource_version": "v1",
        "skill_ids": ["compare"], "objectives": [{"id": "goal", "skill_id": "compare"}],
        "task_ids": list(tasks)}}, "tasks": tasks, "placements": {},
        "materials": {"unit:u": {"family_ids": ["example-family"]}},
        "skills": {"compare": {"statement": "比较两个数的大小"}}}


def record(identity="old", status="wrong", *, completed=OLD, phase="practice"):
    return {"id": identity, "kind": "unit", "unit_id": "u", "title": "比较", "stage": "primary",
        "mode": phase, "created_at": completed, "completed_at": completed,
        "resource_version": "v1", "content_sha256": "v1",
        "answers": [{"question_id": "p", "skill_ids": ["compare"], "objective_ids": ["goal"],
            "status": status, "answered_at": completed, "family_id": "old-family",
            "family_status": "declared", "representation": "bar"}]}


def helped_history():
    original = record()
    need = derive_needs(catalog(), [original])[0]
    support = record("help", "correct", completed=HELP)
    support.update(kind="support", mode="support", resource_version="support-version",
        source_origin={"unit_id": "u", "need_ids": [need["need_id"]], "resource_version": "v1"})
    support["answers"][0].update(question_id="confirmation", family_id="support-family", help_used=True,
        resolution_for_need_ids=[need["need_id"]], support_kind="confirmation")
    return [original, support]


def followup(history):
    return next(need for need in derive_needs(catalog(), history) if need["kind"] == "independent_followup")


def later_observation(history):
    need = followup(history)
    later = record("later", "correct", completed=NOW, phase="delayed")
    later["source_origin"] = {"unit_id": "u", "need_ids": [need["need_id"]], "resource_version": "v1"}
    later["context"] = {"timing_policy": TIMING_POLICY, "same_learning_scope": True,
        "same_content_version": True, "delay_window_met": True, "actual_quiet_seconds": 86400,
        "review_interval_qualified": True}
    later["answers"][0].update(question_id="d", family_id="new-family", representation="table", help_used=False,
        followup_basis={"eligible": True, "family_status": "declared", "family_id": "new-family",
                        "representation": "table"})
    return later


class DecisionCounterexampleTests(unittest.TestCase):
    @staticmethod
    def diagnostic_graph():
        return Graph([{"id": "parent", "name": "基础", "mastery_criteria": ["能数物体"]},
                      {"id": "child", "name": "应用", "mastery_criteria": ["能加减计算"]}],
            [{"topicId": "child", "prerequisiteId": "parent", "strength": "hard"}],
            [{"id": f"child-q{index}", "topicId": "child", "type": "numeric", "difficulty": "basic",
              "prompt": "2+3=?" if index == 1 else "5-2=?", "answer": "5" if index == 1 else "3",
              "verify": "2+3" if index == 1 else "5-2", "tested_criterion_indices": [0],
              "family_id": "addition.combine" if index == 1 else "subtraction.remove", "representation": "symbolic",
              "explanation": "把两组数量合并后计数。" if index == 1 else "从已有数量中取走一部分，求剩余。"}
             for index in (1, 2)])

    def test_helped_correct_answers_cannot_create_mastered_or_assumed_ancestors(self):
        graph = self.diagnostic_graph()
        policies = copy.deepcopy(graph.question_policies)
        session = engine.create_session(graph, ["child"])
        for _ in range(2):
            engine.next_item(graph, session)
            engine.apply_answer(graph, session, "correct", help_used=True)
        self.assertEqual(session["state"]["child"], "insufficient_evidence")
        self.assertNotIn("parent", session["state"])
        self.assertTrue(all(entry["verdict"] == "correct" for entry in session["log"]))
        self.assertIn("recorded_help_used", session["evidence_gaps"]["child"])
        self.assertEqual(graph.question_policies, policies)

    def test_unhelped_api_retains_admission_rules_and_rejects_forged_current_evidence(self):
        graph = self.diagnostic_graph()
        session = engine.create_session(graph, ["child"])
        for _ in range(2):
            engine.next_item(graph, session)
            engine.apply_answer(graph, session, "correct")
        self.assertEqual(session["state"]["child"], "mastered")
        for policy in graph.question_policies.values():
            policy["may_confirm_mastery"] = False
        session = engine.create_session(graph, ["child"])
        for _ in range(2):
            engine.next_item(graph, session)
            session["current"]["evidence"] = {"may_confirm_mastery": True}
            engine.apply_answer(graph, session, "correct", help_used=False)
        self.assertEqual(session["state"]["child"], "insufficient_evidence")
        self.assertTrue(all(not entry["evidence"]["may_confirm_mastery"] for entry in session["log"]))

    def test_unknown_representation_is_never_new_even_on_first_attempt(self):
        data = catalog()
        for value in ("unknown", "unclassified", " Unknown ", [], ["unknown", "unclassified"]):
            with self.subTest(value=value):
                data["tasks"]["d"]["representation"] = value
                option = task_options(data, ["compare"], [], allowed_task_ids={"d"})[0]
                self.assertFalse(option["new_representation"])
                self.assertFalse(option["representation_known"])
                self.assertFalse(option["eligible_for_followup"])

    def test_mixed_unknown_metadata_does_not_invent_a_second_representation(self):
        data = catalog()
        data["tasks"]["d"]["representation"] = ["bar", "unknown"]
        option = task_options(data, ["compare"], [record()], allowed_task_ids={"d"})[0]
        self.assertFalse(option["new_representation"])
        self.assertEqual(option["known_representation_ids"], ["bar"])
        self.assertTrue(option["representation_known"])

    def test_recent_material_blocks_plan_and_start_with_the_same_reason(self):
        previous = record(status="correct", phase="transfer")
        activity = {"occurred_at": "2026-10-25T23:00:00+00:00", "event_type": "reference_opened"}
        step = plan(catalog(), [previous], now=NOW, scope_activities={"u": activity})["next_steps"][0]
        started = interval_context({"mode": "delayed", "created_at": NOW, "resource_version": "v1"},
                                   [previous], latest_activity=activity)
        self.assertFalse(step["ready"])
        self.assertTrue(step["practice_available"])
        self.assertEqual(step["due_at"], "2026-10-26T23:00:00.000+00:00")
        self.assertEqual(step["timing_reason"], started["interval_reason"])
        self.assertEqual(step["interval_context"], started)
        self.assertEqual(started["actual_quiet_seconds"], 3600)

    def test_newer_different_version_completion_cannot_be_hidden_by_old_matching_one(self):
        previous = record(status="correct", phase="transfer")
        newer = record("new-version", "correct", completed=HELP, phase="transfer")
        newer.update(resource_version="v2", content_sha256="v2")
        step = plan(catalog(), [previous, newer], now=NOW)["next_steps"][0]
        self.assertFalse(step["ready"])
        self.assertEqual(step["timing_reason"], "content_version_changed")
        self.assertEqual(step["previous_session_id"], "new-version")

    def test_ladder_and_quiet_window_are_separate_and_invalid_time_cannot_pass(self):
        previous = record(status="correct", completed="2026-10-24T00:00:00+00:00", phase="transfer")
        schedule = review_schedule({"resource_version": "v1"}, [previous], now=NOW, interval_days=3)
        self.assertTrue(schedule["interval_qualified"])
        self.assertFalse(schedule["ready"])
        self.assertEqual(schedule["timing_reason"], "review_ladder_not_due")
        for when in ("2026-10-25T00:00:00", "2026-10-27T00:00:00+00:00"):
            step = plan(catalog(), [previous], now=NOW, scope_activities={"u": {
                "occurred_at": when, "event_type": "material_provided"}})["next_steps"][0]
            self.assertFalse(step["ready"])
            self.assertEqual(step["timing_reason"], "invalid_or_future_timestamp")

    def test_unit_boundary_includes_all_phase_tasks_and_example_families(self):
        scope = unit_activity_scope(catalog()["units"]["u"], catalog())
        self.assertEqual(scope["question_ids"], ["d", "p", "t"])
        self.assertIn("example-family", scope["family_ids"])
        previous = record(status="correct", phase="transfer")
        exposure = {"questions": {}, "families": {"example-family": {"last_seen_at": "2026-10-25T23:00:00+00:00",
            "last_reference_at": "2026-10-25T23:00:00+00:00", "last_event_seq": 12}}}
        step = plan(catalog(), [previous], now=NOW, exposures=exposure)["next_steps"][0]
        self.assertFalse(step["ready"])

    def test_helped_success_keeps_bounded_resolution_and_separate_durable_goal_obligation(self):
        history = helped_history()
        before = copy.deepcopy(history)
        result = plan(catalog(), history, now=NOW)
        original = next(need for need in result["learning_needs"] if need["kind"] == "answer_support")
        child = next(need for need in result["learning_needs"] if need["kind"] == "independent_followup")
        self.assertEqual(original["status"], "resolved")
        self.assertEqual(child["status"], "open")
        self.assertEqual(child["parent_need_id"], original["need_id"])
        self.assertEqual(child["observed_at"], HELP)
        self.assertEqual(child["skill_ids"], ["compare"])
        self.assertTrue(child["followup"]["ready"])
        self.assertEqual(child["followup"]["task_ids"], ["d"])
        self.assertEqual(result["skill_evidence"][0]["next_action"], "independent_followup")
        self.assertFalse(result["skill_evidence"][0]["independent_mastery_evidence"])
        self.assertEqual(history, before)

    def test_content_update_or_deferral_never_erases_the_old_target_obligation(self):
        history = helped_history()
        old = followup(history)
        data = catalog()
        data["units"]["u"]["resource_version"] = "v2"
        result = plan(data, history, now=NOW, need_states=[{
            "need_id": old["need_id"], "status": "deferred", "reason": "先做另一目标"}])
        child = next(need for need in result["learning_needs"] if need["kind"] == "independent_followup")
        self.assertEqual(child["need_id"], old["need_id"])
        self.assertEqual(child["resource_version"], "v1")
        self.assertEqual(child["status"], "deferred")
        self.assertFalse(child["followup"]["ready"])
        self.assertFalse(any(step.get("action") == "independent_followup" for step in result["next_steps"]))

    def test_missing_known_family_keeps_a_usable_practice_route_without_false_ready(self):
        data = catalog()
        data["tasks"]["d"].update(family_status="unclassified", representation="unknown")
        result = plan(data, helped_history(), now=NOW)
        child = next(need for need in result["learning_needs"] if need["kind"] == "independent_followup")
        self.assertFalse(child["followup"]["ready"])
        self.assertFalse(child["followup"]["followup_available"])
        self.assertTrue(child["followup"]["practice_available"])
        self.assertEqual(child["status"], "open")

    def test_only_explicit_qualified_goal_followup_closes_observation_not_mastery(self):
        history = helped_history()
        later = later_observation(history)
        for change in ("unlinked", "help", "short_interval", "old_family", "unknown_representation", "wrong_version", "unrelated_goal"):
            row = copy.deepcopy(later)
            if change == "unlinked": row.pop("source_origin")
            if change == "help": row["answers"][0]["help_used"] = True
            if change == "short_interval": row["context"]["actual_quiet_seconds"] = 3600
            if change == "old_family":
                row["answers"][0]["family_id"] = row["answers"][0]["followup_basis"]["family_id"] = "old-family"
            if change == "unknown_representation": row["answers"][0]["followup_basis"]["representation"] = "unknown"
            if change == "wrong_version": row["source_origin"]["resource_version"] = "unrelated-v2"
            if change == "unrelated_goal": row["answers"][0]["skill_ids"] = ["other"]
            with self.subTest(change=change):
                self.assertEqual(followup([*history, row])["status"], "open")
        resolved = followup([*history, later])
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolution"]["kind"], "qualifying_followup_observation")
        self.assertFalse(resolved["resolution"]["independent_evidence"])
        self.assertEqual(history[0]["answers"][0]["status"], "wrong")

    def test_store_summary_preserves_the_evidence_needed_after_reopening_database(self):
        history = helped_history()
        later = later_observation(history)
        compact = [_session_summary(item) for item in [*history, later]]
        self.assertEqual(followup(compact)["status"], "resolved")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "learning.sqlite"
            store = LearningStore(path)
            for item in [*history, later]:
                store.save({**item, "owner_id": "alice"})
            restored = LearningStore(path)
            self.assertEqual(followup(restored.planning_records("alice"))["status"], "resolved")


class DecisionHTTPTests(unittest.TestCase):
    """One real resource-only exposure catches an integration that mocks miss."""
    def setUp(self):
        from web import server
        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.learning_store = LearningStore(Path(self.temp.name) / "learning.sqlite")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.cookies = CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}",
            data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with self.client.open(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_plan_and_start_share_resource_only_material_activity(self):
        uid = "fraction_equivalence"
        self.request("/api/plan")
        owner = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
        version = self.server.get_catalog().units[uid]["resource_version"]
        previous = record(status="correct", phase="transfer")
        previous.update(owner_id=owner, unit_id=uid, resource_version=version, content_sha256=version)
        self.httpd.learning_store.save(previous)
        self.httpd.learning_store.add_event(owner, {"event_type": "material_requested",
            "occurred_at": "2026-10-25T23:00:00+00:00", "resource_id": "unit:" + uid,
            "content_sha256": version, "payload": {}})
        from datetime import datetime, timezone
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 26, tzinfo=timezone.utc)
        with patch("cnkg.planner.datetime", Clock):
            code, result = self.request("/api/plan?stage=primary")
        self.assertEqual(code, 200)
        step = next(item for item in result["next_steps"] if item.get("unit_id") == uid)
        self.assertFalse(step["ready"])
        with patch.object(self.server, "_now", return_value=NOW):
            code, started = self.request("/api/session", {"unit_id": uid, "mode": "delayed"})
        self.assertEqual(code, 200, started)
        self.assertEqual(step["interval_context"]["interval_reason"], started["session"]["context"]["interval_reason"])
        self.assertFalse(started["session"]["context"]["review_interval_qualified"])

    def test_explicit_help_is_saved_for_ordinary_practice_and_task_ids_are_scoped(self):
        uid = "data_reading_and_mean"
        task = next(task for task in self.server.learning_units()[uid]["tasks"] if task["phase"] == "practice")
        code, started = self.request("/api/session", {"unit_id": uid, "mode": "practice", "task_ids": [task["id"]]})
        self.assertEqual(code, 200, started)
        sid = started["session"]["id"]
        code, done = self.request("/api/answer", {"session_id": sid, "question_id": task["id"],
            "answer": task.get("answer", "说明我的思路"), "used_help": True})
        self.assertEqual(code, 200, done)
        owner = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
        saved = self.httpd.learning_store.load(sid, owner)
        self.assertTrue(saved["answers"][0]["help_used"])
        self.assertIn("learner_report", saved["answers"][0]["help_evidence"])
        self.assertEqual(self.request("/api/session", {"unit_id": uid, "mode": "practice", "task_ids": ["another-unit-task"]})[0], 400)

    @contextmanager
    def synthetic_mean_followup(self):
        """A genuinely different mathematical task, added only to an isolated test copy.

        The production mean pool intentionally has no fresh delayed family after
        this help route. Do not relabel its same-form task to make time tests pass.
        """
        uid = "data_reading_and_mean"
        units = copy.deepcopy(self.server.learning_units())
        unit = units[uid]
        catalog = copy.deepcopy(self.server.get_catalog())
        goal = next(goal for goal in catalog.units[uid]["objectives"] if goal["id"].endswith(":mean"))
        task = {"id": uid + ":synthetic-combined-group-mean", "phase": "delayed", "type": "numeric",
                "objective_ids": [goal["id"]],
                "prompt": "合成测试任务：第一组2人，平均每人读5本；第二组3人，平均每人读10本。合在一起5人平均每人读几本？",
                "answer": "8", "verify": "(2*5+3*10)/(2+3)",
                "answer_spec": {"kind": "number", "comparison": "exact", "representation": "any"},
                "explanation": "先以人数乘组平均求两组总数10本和30本，再用40本除以总人数5人；不能直接平均5和10。",
                "feedback": {"correct": "已按人数合并总量。", "incorrect": "两个组人数不同，先恢复各组总量。"},
                "family_id": "statistics.combine-group-means", "representation": "verbal",
                "cognitive_demand": "apply", "source": "synthetic-test-fixture"}
        self.assertIn(task["family_id"], catalog.families, "合成题仍须使用实际数学结构的既有题族")
        unit["tasks"].append(task)
        metadata = {**copy.deepcopy(task), "availability": "active", "source_kind": "unit",
                    "material_id": "unit:" + uid, "skill_ids": [goal["skill_id"]], "family_status": "declared",
                    "resource_version": self.server._hash(task)}
        catalog.tasks[task["id"]] = metadata
        catalog.active_tasks[task["id"]] = metadata
        catalog.units[uid]["task_ids"].append(task["id"])
        catalog.units[uid]["resource_version"] = self.server.session_content.unit_hash(unit, self.server.unit_assets(unit))
        with patch.object(self.server, "_UNIT_CACHE", units), patch.object(self.server, "_CATALOG_CACHE", catalog):
            yield task["id"]

    def complete_mean_help(self):
        uid = "data_reading_and_mean"
        unit = self.server.learning_units()[uid]
        goal = next(goal for goal in self.server.get_catalog().units[uid]["objectives"] if goal["id"].endswith(":mean"))
        with patch.object(self.server, "_now", return_value=OLD):
            code, activity = self.request("/api/session", {"unit_id": uid, "mode": "practice", "focus_skill_ids": [goal["skill_id"]]})
            self.assertEqual(code, 200, activity)
            code, _ = self.request("/api/answer", {"session_id": activity["session"]["id"],
                "question_id": activity["item"]["question"]["id"], "answer": "999999"})
            self.assertEqual(code, 200)
        _, before = self.request("/api/plan")
        original = next(need for need in before["learning_needs"] if need["kind"] == "answer_support")
        support = next(item for item in unit["instructional_support"]["supports"] if item["objective_id"] == goal["id"])
        with patch.object(self.server, "_now", return_value=HELP):
            code, guided = self.request("/api/support/start", {"unit_id": uid, "support_id": support["id"], "need_ids": [original["need_id"]]})
            self.assertEqual(code, 200, guided)
            owner = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
            while not guided["done"]:
                saved = self.httpd.learning_store.load(guided["session"]["id"], owner)
                qid = guided["item"]["id"]
                answer = saved["question_snapshots"][qid].get("answer", "写出推理")
                code, guided = self.request("/api/support/answer", {"session_id": guided["session"]["id"],
                    "question_id": qid, "answer": answer})
                self.assertEqual(code, 200, guided)
        _, after = self.request("/api/plan")
        need = next(need for need in after["learning_needs"] if need["kind"] == "independent_followup")
        self.assertEqual(need["status"], "open")
        return uid, goal, need

    def test_real_mean_same_family_pool_exhaustion_preserves_followup_need(self):
        uid, goal, need = self.complete_mean_help()
        request = {"unit_id": uid, "mode": "delayed", "focus_skill_ids": [goal["skill_id"]],
                   "followup_need_ids": [need["need_id"]]}
        owner = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
        store = self.httpd.learning_store
        before_count = store.count(owner)
        delayed = {task["id"] for task in self.server.learning_units()[uid]["tasks"]
                   if task["phase"] == "delayed" and goal["id"] in task["objective_ids"]}
        options = task_options(self.server.get_catalog(), [goal["skill_id"]], store.planning_records(owner),
                               store.exposures(owner), allowed_task_ids=delayed, limit=None)
        self.assertTrue(options)
        self.assertTrue(all(option["repeated_family"] and not option["eligible_for_followup"] for option in options))
        for when in ("2026-10-21T01:00:00+00:00", NOW):
            with self.subTest(when=when), patch.object(self.server, "_now", return_value=when):
                code, blocked = self.request("/api/session", request)
            self.assertEqual(code, 409, blocked)
            self.assertEqual(blocked["code"], "followup_tasks_unavailable")
            self.assertEqual(blocked["next_action"], {**request, "mode": "practice"})
        self.assertEqual(store.count(owner), before_count, "被阻断的检查不能保存成一场新会话")
        _, final = self.request("/api/plan")
        preserved = next(row for row in final["learning_needs"] if row["need_id"] == need["need_id"])
        self.assertEqual(preserved["status"], "open")
        self.assertIsNone(preserved["resolution"])

    def test_helped_goal_has_a_real_later_observation_and_cannot_skip_the_interval(self):
        # Positive interval contract gets a test-only combined-group problem,
        # while the separate production counterexample keeps its real gap.
        with self.synthetic_mean_followup() as synthetic_id:
            uid, goal, need = self.complete_mean_help()
            request = {"unit_id": uid, "mode": "delayed", "focus_skill_ids": [goal["skill_id"]],
                       "followup_need_ids": [need["need_id"]]}
            owner = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
            store = self.httpd.learning_store
            self.assertNotIn("statistics.combine-group-means", store.exposures(owner)["families"],
                             "本正向夹具未打开例题或练习第三目标，不能通过另起族名避开真实帮助曝光")
            option = task_options(self.server.get_catalog(), [goal["skill_id"]], store.planning_records(owner),
                                  store.exposures(owner), allowed_task_ids={synthetic_id}, limit=None)[0]
            self.assertTrue(option["eligible_for_followup"])
            self.assertFalse(option["repeated_question"])
            self.assertFalse(option["repeated_family"])
            self.assertFalse(option["help_exposed"])
            with patch.object(self.server, "_now", return_value="2026-10-21T01:00:00+00:00"):
                code, blocked = self.request("/api/session", request)
                self.assertEqual(code, 409, blocked)
                self.assertEqual(blocked["code"], "followup_interval_not_ready")
                self.assertFalse(blocked["context"]["review_interval_qualified"])
            with patch.object(self.server, "_now", return_value="2026-10-22T00:00:00+00:00"):
                code, activity = self.request("/api/session", request)
                self.assertEqual(code, 200, activity)
                self.assertEqual(activity["item"]["question"]["id"], synthetic_id)
                sid = activity["session"]["id"]
                saved = store.load(sid, owner)
                self.assertTrue(saved["context"]["review_interval_qualified"])
                self.assertEqual(saved["context"]["actual_quiet_seconds"], 86400)
                self.assertEqual(saved["source_origin"]["need_ids"], [need["need_id"]])
                code, activity = self.request("/api/answer", {"session_id": sid, "question_id": synthetic_id,
                    "answer": "8", "used_help": False})
                self.assertEqual(code, 200, activity)
                self.assertTrue(activity["done"])
            _, final = self.request("/api/plan")
            completed = next(row for row in final["learning_needs"] if row["need_id"] == need["need_id"])
            self.assertEqual(completed["status"], "resolved")
            self.assertEqual(completed["resolution"]["kind"], "qualifying_followup_observation")
            self.assertFalse(completed["resolution"]["independent_evidence"])


if __name__ == "__main__":
    unittest.main()
