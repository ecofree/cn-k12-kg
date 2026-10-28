"""Concrete continuation and limited-opportunity counterexamples, not mastery tests."""
import copy
import unittest
from unittest.mock import patch

from cnkg import instruction, task_families
from cnkg.learning_needs import goal_pathway, skill_evidence, task_options
from cnkg.learning_store import _session_summary
from cnkg.planner import plan
from tests import test_instruction_v4 as support_fixture


NOW = "2026-01-02T12:00:00+00:00"


class SupportContinuationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = support_fixture.InstructionTest(methodName="runTest")
        self.fixture.setUp()

    def test_unanswered_and_correct_probe_supports_remain_resumable_without_invented_error_needs(self):
        record = self.fixture.create(needs=[])
        for answered in (False, True):
            with self.subTest(answered_probe=answered):
                if answered:
                    self.fixture.answer(record, "a", 5)
                compact = _session_summary(record)
                result = plan(self.fixture.catalog, [compact], now=NOW, stage="primary")
                self.assertEqual(result["learning_needs"], [])
                continuation = next(row for row in result["next_steps"] if row.get("session_id") == record["id"])
                self.assertEqual(continuation["continuation"], "resume_support")
                self.assertEqual(continuation["unit_id"], "u")
                self.assertFalse(any(row.get("session_id") == record["id"] for row in
                    plan(self.fixture.catalog, [compact], now=NOW, stage="senior")["next_steps"]))
        blocked = {**compact, "resume_blocked": True}
        self.assertFalse(any(row.get("session_id") == record["id"] for row in
            plan(self.fixture.catalog, [blocked], now=NOW)["next_steps"]))

    def test_existing_failure_continuation_is_not_duplicated(self):
        record = self.fixture.create(needs=[])
        self.fixture.answer(record, "b", 5)
        result = plan(self.fixture.catalog, [_session_summary(record)], now=NOW)
        own = [row for row in result["next_steps"] if row.get("session_id") == record["id"]]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["continuation"], "resume_support")
        self.assertEqual(len(result["learning_needs"]), 1)


def pathway_catalog():
    task = {"type": "numeric", "source_kind": "bank", "material_id": "topic:t", "purpose": ["practice"],
            "skill_ids": ["fine"], "family_status": "declared", "representation": "symbolic", "availability": "active"}
    tasks = {f"a{i}": {**task, "id": f"a{i}", "family_id": "same-method"} for i in range(8)}
    tasks["a8"] = {**task, "id": "a8", "family_id": "old-name"}
    tasks["z"] = {**task, "id": "z", "family_id": "different-method"}
    return {"skills": {
                "wide": {"statement": "综合宽目标", "granularity": "composite", "stage": "primary"},
                "fine": {"statement": "具体任务边界", "granularity": "observable", "stage": "primary", "refines_skill_ids": ["wide"]}},
            "units": {"broad-unit": {"id": "broad-unit", "skill_ids": ["wide"], "objectives": [{"id": "broad-goal", "skill_id": "wide"}]}},
            "placements": {"t": {"criterion_skill_ids": ["fine"], "stage": "primary"}}, "tasks": tasks, "materials": {}}


class GoalOpportunityTests(unittest.TestCase):
    def test_number_changes_and_aliases_cannot_crowd_out_an_actual_second_method(self):
        catalog = pathway_catalog()
        with patch.dict(task_families.ALIASES, {"old-name": "same-method"}):
            options = task_options(catalog, ["fine"], [], limit=2)
            self.assertEqual([row["task_id"] for row in options], ["a0", "z"])
            goal = goal_pathway(catalog, "fine")
            self.assertEqual(goal["observation_opportunities"]["known_objective_family_count"], 2)
            self.assertEqual(goal["observation_opportunities"]["available_followup_family_count"], 2)
            exposed = goal_pathway(catalog, "fine", exposures={"families": {
                "old-name": {"reference_count": 1}, "different-method": {"presentation_count": 1}}})
            self.assertEqual(exposed["observation_opportunities"]["available_followup_family_count"], 0)
            self.assertTrue(all(not row["eligible_for_followup"] for row in exposed["task_options"]))
            self.assertFalse(exposed["independent_mastery_evidence"])

    def test_one_method_and_unknown_metadata_are_reported_as_a_gap(self):
        catalog = pathway_catalog()
        catalog["tasks"]["z"].update(family_status="unclassified", representation="unknown")
        with patch.dict(task_families.ALIASES, {"old-name": "same-method"}):
            opportunities = goal_pathway(catalog, "fine")["observation_opportunities"]
        self.assertEqual(opportunities["known_objective_family_count"], 1)
        self.assertFalse(opportunities["multiple_known_families"])
        self.assertIn("不足两种", opportunities["notice"])
        self.assertFalse(opportunities["independent_mastery_evidence"])

    def test_fine_goal_observation_does_not_supply_a_broad_goal_attempt_or_task(self):
        catalog = pathway_catalog()
        original = copy.deepcopy(catalog)
        record = {"id": "one", "kind": "topic", "resource_version": "old", "answers": [
            {"question_id": "a0", "skill_ids": ["fine"], "status": "correct", "family_id": "same-method",
             "family_status": "declared", "representation": "symbolic", "help_used": False}]}
        broad = goal_pathway(catalog, "wide", [record], preferred_unit="broad-unit")
        fine = goal_pathway(catalog, "fine", [record])
        self.assertEqual([row["skill_id"] for row in broad["refined_by"]], ["fine"])
        self.assertEqual([row["skill_id"] for row in fine["broader_goals"]], ["wide"])
        self.assertEqual(broad["task_options"], [])
        self.assertEqual(broad["observation_opportunities"]["task_count"], 0)
        self.assertEqual([row["skill_id"] for row in skill_evidence(catalog, [record], [])], ["fine"])
        self.assertEqual(catalog, original)


if __name__ == "__main__":
    unittest.main()
