"""Prerequisite routes need actual observations, not successful descendants."""
from __future__ import annotations

import copy
import unittest

from cnkg import engine
from cnkg.graph import Graph
from cnkg.planner import plan
from cnkg.session_content import digest, graph_snapshot


NOW = "2026-10-28T00:00:00+00:00"


def diagnostic_graph(edges):
    return Graph(
        [{"id": tid, "name": tid, "chapter": "先修检查", "mastery_criteria": ["观察一步合并与求剩余"]}
         for tid in ("A", "B", "C")],
        [{"topicId": child, "prerequisiteId": parent, "strength": "hard"}
         for child, parent in edges],
        [{"id": f"{tid}-q{index}", "topicId": tid, "type": "numeric",
          "difficulty": "basic", "prompt": "原有2个，再添1个，现在几个？" if index == 1 else "原有5个，去掉2个，还剩几个？",
          "answer": "3", "verify": "2+1" if index == 1 else "5-2", "tested_criterion_indices": [0],
          "family_id": "quantity.combine" if index == 1 else "quantity.remove", "representation": "verbal",
          "evidence_level": "verified", "source": "external-fixture"}
         for tid in ("A", "B", "C") for index in (1, 2)],
    )


def answer(graph, session, verdict):
    item = engine.next_item(graph, session)
    if item is None:
        raise AssertionError("预期有下一道实际作答题")
    engine.apply_answer(graph, session, verdict)
    return item["topicId"]


def catalog(*, relation_only=False):
    tasks = {
        "read-task": {"id": "read-task", "phase": "practice", "skill_ids": ["read"]},
        "compare-task": {"id": "compare-task", "phase": "practice", "skill_ids": ["compare"]},
        "target-task": {"id": "target-task", "phase": "practice", "skill_ids": ["target"]},
    }
    return {
        "units": {
            "source": {"id": "source", "title": "读图与比较", "resource_version": "v1", "stage": "primary",
                       "skill_ids": ["read", "compare"], "objectives": [{"id": "r", "skill_id": "read"},
                                                                       {"id": "c", "skill_id": "compare"}],
                       "task_ids": ["read-task", "compare-task"]},
            "target": {"id": "target", "title": "分析数据", "resource_version": "v1", "stage": "primary",
                       "skill_ids": ["target"], "objectives": [{"id": "t", "skill_id": "target"}],
                       "prerequisite_skill_ids": [] if relation_only else ["read"],
                       "task_ids": ["target-task"]},
        },
        "skills": {"read": {"statement": "读出图上的数值"}, "compare": {"statement": "比较两个数值"},
                   "target": {"statement": "分析数据", "prerequisite_skill_ids": ["read"] if relation_only else []}},
        "tasks": tasks, "placements": {},
    }


def record(*, skill="compare", status="correct", **answer_fields):
    return {"id": "saved", "kind": "unit", "unit_id": "source", "mode": "practice", "title": "读图与比较",
            "stage": "primary", "resource_version": "v1", "content_sha256": "v1",
            "created_at": NOW, "completed_at": NOW,
            "answers": [{"question_id": f"{skill}-task", "skill_ids": [skill], "status": status,
                         "answered_at": NOW, **answer_fields}]}


def next_target(data, history):
    return next(item for item in plan(data, history, now=NOW)["next_steps"] if item.get("unit_id") == "target")


class DirectDiagnosticObservationTests(unittest.TestCase):
    def test_correct_target_keeps_unasked_ancestors_untested_without_mandatory_relearning(self):
        graph = diagnostic_graph([("A", "B"), ("B", "C")])
        session = engine.create_session(graph, ["A"])
        answer(graph, session, "correct")
        answer(graph, session, "correct")
        self.assertIsNone(engine.next_item(graph, session))
        self.assertEqual(session["state"], {"A": "mastered"})
        summary = engine.summarize(graph, session)
        self.assertEqual(summary["mastered"], ["A"])
        self.assertEqual(summary["assumed"], [])
        self.assertEqual(set(summary["untested"]), {"B", "C"})
        self.assertEqual(summary["support_tasks"], [])

    def test_successful_sibling_does_not_prevent_shared_prerequisite_check(self):
        graph = diagnostic_graph([("A", "B"), ("C", "B")])
        session = engine.create_session(graph, ["A", "C"])
        self.assertEqual([answer(graph, session, "correct") for _ in range(2)], ["A", "A"])
        self.assertNotIn("B", session["state"])
        self.assertEqual([answer(graph, session, "wrong") for _ in range(2)], ["C", "C"])
        self.assertEqual(engine.next_item(graph, session)["topicId"], "B")
        summary = engine.summarize(graph, session)
        self.assertEqual(summary["roots"], [])
        self.assertEqual(summary["root_evidence"]["C"]["missing_prerequisites"], ["B"])
        self.assertEqual([answer(graph, session, "correct") for _ in range(2)], ["B", "B"])
        self.assertEqual(engine.summarize(graph, session)["roots"], ["C"])

    def test_legacy_assumed_is_retained_as_history_without_becoming_tested(self):
        graph = diagnostic_graph([("A", "B")])
        session = engine.create_session(graph, ["A"])
        for _ in range(2):
            answer(graph, session, "correct")
        session["state"]["B"] = "assumed"  # Stored by the historical engine.
        before = copy.deepcopy(session)
        summary = engine.summarize(graph, session)
        self.assertEqual(summary["assumed"], ["B"])
        self.assertEqual(summary["mastered"], ["A"])
        self.assertEqual(summary["by_chapter"]["先修检查"]["mastered"], 1)
        self.assertEqual(session, before)

    def test_old_runtime_blocks_resume_and_frozen_summary_is_not_recomputed(self):
        from web import server
        graph = diagnostic_graph([("A", "B")])
        snapshot = graph_snapshot(graph, ["A"])
        historical = {"kind": "topic", "graph_snapshot": snapshot, "content_sha256": digest(snapshot),
                      "grader_sha256": "historical-diagnostic-runtime", "completed_at": None}
        before = copy.deepcopy(historical)
        self.assertIn("判分规则已经更新", server._version_problem(historical))
        self.assertEqual(historical, before)
        historical.update(completed_at=NOW, summary_snapshot={"assumed": ["B"], "mastered": ["A"],
                                                             "legacy": True})
        frozen = copy.deepcopy(historical)
        self.assertIsNone(server._version_problem(historical))
        shown = server.session_summary(historical)
        self.assertEqual(shown, historical["summary_snapshot"])
        shown["assumed"].clear()
        self.assertEqual(historical, frozen)


class PrerequisitePlanningObservationTests(unittest.TestCase):
    def test_finished_container_does_not_hide_an_unanswered_explicit_or_related_skill(self):
        for relation_only in (False, True):
            with self.subTest(relation_only=relation_only):
                data, history = catalog(relation_only=relation_only), [record()]
                before = copy.deepcopy(history)
                item = next_target(data, history)
                self.assertEqual(item["prerequisite_skill_ids"], ["read"])
                self.assertEqual(item["prerequisite_unit_ids"], ["source"])
                self.assertEqual(item["observed_prerequisite_skill_ids"], [])
                self.assertIn("没有具体作答记录", item["reason"])
                self.assertEqual(history, before)

    def test_skip_unknown_and_private_feedback_do_not_count_as_observed_answers(self):
        scenarios = [{"status": "not_attempted"}, {"status": "unknown"},
                     {"status": "wrong", "skipped": True},
                     {"status": "correct", "_feedback_pending": True}]
        for fields in scenarios:
            with self.subTest(fields=fields):
                item = next_target(catalog(relation_only=True), [record(skill="read", **fields)])
                self.assertEqual(item["prerequisite_skill_ids"], ["read"])
                self.assertEqual(item["prerequisite_unit_ids"], ["source"])
                self.assertEqual(item["observed_prerequisite_skill_ids"], [])

    def test_direct_helped_correct_wrong_and_process_answers_are_observations_not_mastery(self):
        for status in ("correct", "wrong", "needs_review"):
            with self.subTest(status=status):
                history = [record(skill="read", status=status, help_used=True)]
                before = copy.deepcopy(history)
                result = plan(catalog(), history, now=NOW)
                item = next(step for step in result["next_steps"] if step.get("unit_id") == "target")
                self.assertEqual(item["prerequisite_skill_ids"], [])
                self.assertEqual(item["observed_prerequisite_skill_ids"], ["read"])
                self.assertEqual(item["prerequisite_unit_ids"], [])
                self.assertEqual(item["prerequisite_evidence"], "direct_skill_attempts_and_unit_activity_not_mastery")
                evidence = next(row for row in result["skill_evidence"] if row["skill_id"] == "read")
                self.assertFalse(evidence["independent_mastery_evidence"])
                self.assertTrue(evidence["observations"][0]["help_used"])
                self.assertEqual(history, before)

    def test_old_version_answer_stays_visible_without_removing_current_version_check(self):
        data = catalog()
        history = [record(skill="read")]
        data["units"]["source"]["resource_version"] = "v2"
        item = next_target(data, history)
        self.assertEqual(item["prerequisite_skill_ids"], ["read"])
        self.assertEqual(item["prerequisite_unit_ids"], ["source"])
        observations = plan(data, history, now=NOW)["skill_evidence"][0]["observations"]
        self.assertEqual(observations[0]["resource_version"], "v1")
        self.assertEqual(history[0]["resource_version"], "v1")

    def test_current_task_remapping_cannot_invent_an_old_skill_observation(self):
        data = catalog()
        historical = record()
        historical["answers"][0].pop("skill_ids")
        historical["question_snapshots"] = {"compare-task": {"skill_ids": ["compare"]}}
        data["tasks"]["compare-task"]["skill_ids"] = ["read"]
        item = next_target(data, [historical])
        self.assertEqual(item["prerequisite_skill_ids"], ["read"])
        self.assertEqual(item["observed_prerequisite_skill_ids"], [])

    def test_legacy_assumed_without_an_answer_never_satisfies_current_prerequisite(self):
        historical = {"id": "old-topic", "kind": "topic", "mode": "diagnose", "created_at": NOW,
                      "completed_at": NOW, "answers": [], "engine": {"targets": ["topic"],
                      "state": {"read": "assumed"}}, "summary_snapshot": {"assumed": ["read"]}}
        before = copy.deepcopy(historical)
        item = next_target(catalog(), [historical])
        self.assertEqual(item["prerequisite_skill_ids"], ["read"])
        self.assertEqual(item["observed_prerequisite_skill_ids"], [])
        self.assertEqual(historical, before)


if __name__ == "__main__":
    unittest.main()
