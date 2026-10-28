"""Planning is version-aware recorded activity, never inferred mastery."""
from __future__ import annotations

import copy
import unittest

from cnkg.planner import plan
from cnkg.learning_store import _work_summary


NOW = "2026-10-26T00:00:00+00:00"


def catalog():
    return {"units": {"u1": {"id": "u1", "title": "分数", "stage": "primary", "resource_version": "v1",
        "objectives": [{"id": "u1:o1", "statement": "解释等值"}], "tasks": [
            {"id": "p1", "phase": "practice"}, {"id": "t1", "phase": "transfer"}, {"id": "d1", "phase": "delayed"}]}}}


def record(mode="practice", status="correct", completed="2026-10-24T00:00:00+00:00"):
    return {"id": "s1", "kind": "unit", "unit_id": "u1", "mode": mode,
            "content_sha256": "v1", "created_at": "2026-10-23T23:00:00+00:00", "completed_at": completed,
            "answers": [{"question_id": "p1", "status": status}]}


def saved_work(identity, *, status="draft", assessment=None, criteria=True, response="已保存的过程",
               updated=NOW, created=NOW):
    """Use the store's actual summary, including its implicit not_checked rows."""
    return _work_summary({"resource_id": identity, "resource_version": "original-work-version", "revision": 1,
        "created_at": created, "updated_at": updated, "evidence_type": "learner_reported_work",
        "payload": {"kind": "project", "source_ref": "project:fixture", "status": status,
            "resource_snapshot": {"title": "保存的作品", "stage": "primary", "criteria": ["说明依据"] if criteria else []},
            "draft": {"response": response}, "self_assessment": [] if assessment is None else
                [{"criterion_index": 0, "status": assessment}]}})


class PlannerTests(unittest.TestCase):
    def test_wrong_or_skipped_answers_recommend_relearning_before_transfer(self):
        for status in ("wrong", "not_attempted"):
            actions = plan(catalog(), [record(status=status)], now=NOW)["next_steps"]
            self.assertEqual(actions[0]["action"], "practice")
            self.assertTrue(actions[0]["review_material"])
            self.assertNotIn("transfer", [action["action"] for action in actions])

    def test_process_answers_have_a_revision_path_and_self_report_never_changes_status(self):
        original = record(status="needs_review")
        before = copy.deepcopy(original)
        first = plan(catalog(), [original], now=NOW)
        revise = next(action for action in first["next_steps"] if action["action"] == "continue")
        self.assertEqual(revise["continuation"], "reflect_and_revise")
        self.assertEqual(revise["question_id"], "p1")
        self.assertEqual(revise["work_resource_id"], "session_task:s1:p1")
        self.assertIn("transfer", [action["action"] for action in first["next_steps"]])
        work = {"resource_id": revise["work_resource_id"], "kind": "session_task", "self_assessment_reported": True,
                "self_assessment_completed": True,
                "has_draft": True, "source_ref": {"session_id": "s1", "question_id": "p1"}}
        second = plan(catalog(), [original], [work], now=NOW)
        self.assertFalse(any(action.get("continuation") == "reflect_and_revise" for action in second["next_steps"]))
        self.assertEqual(original, before)
        self.assertEqual(original["answers"][0]["status"], "needs_review")
        self.assertEqual(second["policy"]["basis"], "product_default_not_validated_adaptive_model")

    def test_review_due_time_uses_completed_scope_not_recently_edited_work(self):
        transfer = record(mode="transfer")
        work = {"resource_id": "work", "has_draft": True, "updated_at": NOW, "self_assessment_reported": True}
        suggestion = plan(catalog(), [transfer], [work], now=NOW)["next_steps"][0]
        self.assertEqual(suggestion["action"], "due_review")
        self.assertEqual(suggestion["interval_days"], 1)
        self.assertEqual(suggestion["due_at"], "2026-10-25T00:00:00.000+00:00")
        self.assertTrue(suggestion["ready"])
        self.assertEqual(suggestion["previous_session_id"], "s1")
        only_work = plan(catalog(), [], [work], now=NOW)
        self.assertNotIn("due_review", [action["action"] for action in only_work["next_steps"]])

    def test_aggregated_review_counts_preserve_one_three_seven_day_rule(self):
        delayed = record(mode="delayed")
        delayed["qualified_review_count"] = 1
        suggestion = plan(catalog(), [delayed], now=NOW)["next_steps"][0]
        self.assertEqual(suggestion["interval_days"], 3)
        self.assertFalse(suggestion["ready"])
        delayed["qualified_review_count"] = 8
        suggestion = plan(catalog(), [delayed], now=NOW)["next_steps"][0]
        self.assertEqual(suggestion["interval_days"], 7)

    def test_active_session_continues_only_on_matching_content_version(self):
        active = record(completed=None)
        self.assertEqual(plan(catalog(), [active], now=NOW)["next_steps"][0]["action"], "continue")
        changed = catalog()
        changed["units"]["u1"]["resource_version"] = "v2"
        updated = plan(changed, [active], now=NOW)["next_steps"][0]
        self.assertEqual(updated["action"], "practice")
        self.assertIn("内容已更新", updated["reason"])
        self.assertEqual(active["content_sha256"], "v1")
        active["resume_blocked"] = True
        self.assertEqual(plan(catalog(), [active], now=NOW)["next_steps"][0]["action"], "practice")
        active["resume_blocked"] = False
        resumed = plan(changed, [active], now=NOW)["next_steps"][0]
        self.assertEqual(resumed["action"], "continue")
        self.assertEqual(resumed["resource_version"], "v1")

    def test_prerequisites_use_recorded_activity_and_ignore_self_assessed_completion(self):
        data = catalog()
        data["units"]["u1"]["objectives"][0]["skill_id"] = "sk_fraction_equivalence"
        second = copy.deepcopy(data["units"]["u1"])
        second.update(id="u2", prerequisite_skill_ids=["sk_fraction_equivalence"])
        second["objectives"] = [{"id": "u2:o1"}]
        data["units"]["u2"] = second
        work = {"resource_id": "u1-work", "source_ref": "u1", "self_assessment_reported": True, "has_draft": True}
        steps = plan(data, [], [work], now=NOW)["next_steps"]
        self.assertEqual(next(step for step in steps if step.get("unit_id") == "u2")["prerequisite_unit_ids"], ["u1"])
        steps = plan(data, [record()], [work], now=NOW)["next_steps"]
        unobserved = next(step for step in steps if step.get("unit_id") == "u2")
        self.assertEqual(unobserved["prerequisite_unit_ids"], ["u1"])
        self.assertEqual(unobserved["prerequisite_skill_ids"], ["sk_fraction_equivalence"])
        attempted = record()
        attempted["answers"][0]["skill_ids"] = ["sk_fraction_equivalence"]
        steps = plan(data, [attempted], [work], now=NOW)["next_steps"]
        observed = next(step for step in steps if step.get("unit_id") == "u2")
        self.assertEqual(observed["prerequisite_unit_ids"], [])
        self.assertEqual(observed["observed_prerequisite_skill_ids"], ["sk_fraction_equivalence"])

    def test_unzoned_timestamp_does_not_establish_review_gap(self):
        invalid = record(mode="transfer", completed="2026-10-24T00:00:00")
        self.assertNotIn("due_review", [step["action"] for step in plan(catalog(), [invalid], now=NOW)["next_steps"]])
        with self.assertRaises(ValueError):
            plan(catalog(), [], now="2026-10-26T00:00:00")

    def test_compiled_catalog_can_expose_units_as_dictionary_property(self):
        class Catalog:
            units = catalog()["units"]
        steps = plan(Catalog(), [], now=NOW)["next_steps"]
        self.assertEqual(steps[0]["unit_id"], "u1")
        self.assertEqual(steps[0]["action"], "practice")

    def test_zero_answer_completion_cannot_advance_phase_or_establish_review(self):
        empty = record(mode="transfer")
        empty["answers"] = []
        steps = plan(catalog(), [empty], now=NOW)["next_steps"]
        self.assertEqual(steps[0]["action"], "practice")
        self.assertNotIn("due_review", [step["action"] for step in steps])

    def test_topic_resume_does_not_depend_on_browser_session_storage(self):
        data = catalog()
        data["placements"] = {"topic-1": {"stage": "primary"}}
        active = {"id": "topic-session", "kind": "topic", "scope_id": "scope", "targets": ["topic-1"],
                  "created_at": "2026-10-25T00:00:00+00:00", "mode": "diagnose", "resume_blocked": False,
                  "answers": [{"question_id": "q1", "status": "wrong"}]}
        step = plan(data, [active], now=NOW, stage="primary")["next_steps"][0]
        self.assertEqual(step["session_id"], "topic-session")
        self.assertEqual(step["action"], "continue")
        self.assertNotIn("答错", step["reason"])
        completed = {**active, "id": "newer", "completed_at": NOW}
        steps = plan(data, [active, completed], now=NOW)["next_steps"]
        self.assertFalse(any(step.get("session_id") == "topic-session" for step in steps))
        active["resume_blocked"] = True
        steps = plan(data, [active], now=NOW)["next_steps"]
        self.assertFalse(any(step.get("session_id") == "topic-session" for step in steps))

    def test_stage_selection_filters_works_and_topic_sessions(self):
        data = catalog()
        data["placements"] = {"advanced": {"stage": "senior"}}
        active = {"id": "advanced-session", "kind": "topic", "targets": ["advanced"],
                  "created_at": NOW, "mode": "practice"}
        works = [{"resource_id": "advanced-work", "kind": "project", "stage": "senior", "has_draft": True},
                 {"resource_id": "primary-work", "kind": "unit", "stage": "primary", "has_draft": True}]
        steps = plan(data, [active], works, stage="primary", now=NOW)["next_steps"]
        self.assertIn("primary-work", [step.get("work_resource_id") for step in steps])
        self.assertNotIn("advanced-work", [step.get("work_resource_id") for step in steps])
        self.assertNotIn("advanced-session", [step.get("session_id") for step in steps])

    def test_process_questions_share_one_session_revision_entry(self):
        session = record(status="needs_review")
        session["answers"] += [{"question_id": "p2", "status": "needs_review"},
                               {"question_id": "p3", "status": "needs_review"}]
        works = [{"resource_id": "session_task:s1:p1", "kind": "session_task", "has_draft": True,
                  "self_assessment_reported": True, "self_assessment_completed": True},
                 {"resource_id": "session_task:earlier:p1", "kind": "session_task", "has_draft": True}]
        steps = plan(catalog(), [session], works, now=NOW)["next_steps"]
        revisions = [step for step in steps if step.get("continuation") == "reflect_and_revise"]
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["session_id"], "s1")
        self.assertEqual(revisions[0]["question_ids"], ["p2", "p3"])
        self.assertIn("2 道过程题", revisions[0]["reason"])
        self.assertFalse(any(step.get("continuation") == "edit_work" for step in steps))
        self.assertTrue(all(answer["status"] == "needs_review" for answer in session["answers"]))

    def test_submitted_nonprocess_work_is_available_in_history_without_continue_prompt(self):
        works = [{"resource_id": "submitted-project", "kind": "project", "status": "submitted", "has_draft": True},
                 {"resource_id": "draft-project", "kind": "project", "status": "draft", "has_draft": True},
                 {"resource_id": "submitted-self-check", "kind": "self_check", "status": "submitted", "has_draft": True}]
        steps = plan(catalog(), [], works, now=NOW)["next_steps"]
        self.assertEqual([step["work_resource_id"] for step in steps if step.get("work_resource_id")], ["draft-project"])
        self.assertTrue(any(step["action"] == "practice" for step in steps))

    def test_unfinished_work_survives_many_unassessed_submissions_with_normal_limit(self):
        submitted = [saved_work(f"aa-submitted-{index:04}", status="submitted") for index in range(1000)]
        for criteria in (True, False):
            with self.subTest(criteria=criteria):
                draft = saved_work("zz-older-draft", criteria=criteria,
                                   created="2026-10-20T00:00:00+00:00", updated="2026-10-20T00:00:00+00:00")
                works = submitted + [draft]
                before = copy.deepcopy(works)
                result = plan({"units": {}}, [], works, now=NOW)
                steps = result["next_steps"]
                self.assertEqual(len(steps), 12)
                self.assertEqual(steps[0]["work_resource_id"], "zz-older-draft")
                self.assertEqual(steps[0]["continuation_basis"], "unfinished_draft")
                self.assertTrue(all(step["continuation_basis"] == "unassessed_criteria" for step in steps[1:]))
                self.assertEqual(result["total_candidates"], 1001)
                self.assertEqual(result["need_counts"]["open"], 1000 + int(criteria))
                self.assertTrue(all(need["revision_basis"] == "unassessed_criteria"
                                    for need in result["learning_needs"]))
                self.assertEqual(works, before)

    def test_reported_partial_or_revisit_stays_actionable_ahead_of_unassessed_submissions(self):
        unassessed = [saved_work(f"aa-unassessed-{index:03}", status="submitted") for index in range(20)]
        for assessment in ("partial", "revisit"):
            with self.subTest(assessment=assessment):
                reported = saved_work("zz-reported-difficulty", status="submitted", assessment=assessment)
                result = plan({"units": {}}, [], unassessed + [reported], now=NOW)
                first = result["next_steps"][0]
                self.assertEqual(first["work_resource_id"], "zz-reported-difficulty")
                self.assertEqual(first["continuation_basis"], "learner_reported_difficulty")
                self.assertEqual(first["criterion_outcomes"][0]["status"], assessment)
                self.assertTrue(first["need_ids"])
                need = next(need for need in result["learning_needs"] if need["need_id"] == first["need_ids"][0])
                self.assertEqual(need["revision_basis"], "learner_reported_difficulty")
                self.assertEqual(result["need_counts"]["open"], 21)

    def test_work_order_uses_zoned_saved_times_not_resource_ids_or_input_order(self):
        older = saved_work("zz-older", status="submitted", assessment="partial",
                           updated="2026-10-20T20:00:00+08:00")
        newer = saved_work("aa-newer", status="submitted", assessment="revisit",
                           updated="2026-10-20T06:00:00-07:00")
        unknown = saved_work("00-unknown", status="submitted", assessment="partial",
                             updated="2026-10-19T00:00:00", created=None)
        for works in ([unknown, newer, older], [older, unknown, newer]):
            result = plan({"units": {}}, [], works, now=NOW, limit=2)
            self.assertEqual([step["work_resource_id"] for step in result["next_steps"]], ["zz-older", "aa-newer"])
            self.assertEqual(result["need_counts"]["open"], 3)

    def test_empty_draft_shell_does_not_outrank_explicit_reported_difficulty(self):
        empty = saved_work("00-empty", response="", updated="2026-10-20T00:00:00+00:00")
        reported = saved_work("zz-reported", status="submitted", assessment="partial")
        result = plan({"units": {}}, [], [empty, reported], now=NOW)
        self.assertEqual([step["work_resource_id"] for step in result["next_steps"]], ["zz-reported", "00-empty"])
        self.assertEqual(result["next_steps"][1]["continuation_basis"], "unassessed_criteria")

    def test_active_session_keeps_precedence_over_saved_draft(self):
        active = record(completed=None)
        draft = saved_work("draft", updated="2026-10-20T00:00:00+00:00")
        result = plan(catalog(), [active], [draft], now=NOW)
        self.assertEqual(result["next_steps"][0]["session_id"], active["id"])
        self.assertEqual(result["next_steps"][1]["work_resource_id"], "draft")

    def test_deferred_work_stays_in_ledger_without_draft_fallback_until_reopened(self):
        for assessment in (None, "partial"):
            with self.subTest(assessment=assessment):
                works = [saved_work("saved-draft", assessment=assessment)]
                before = copy.deepcopy(works)
                initial = plan({"units": {}}, [], works, now=NOW)
                need_id = initial["learning_needs"][0]["need_id"]
                state = {"need_id": need_id, "status": "deferred", "updated_at": NOW, "reason": "稍后继续"}
                deferred = plan({"units": {}}, [], works, now=NOW, need_states=[state])
                self.assertEqual(deferred["next_steps"], [])
                self.assertEqual(deferred["need_counts"], {"open": 0, "deferred": 1, "resolved": 0})
                self.assertEqual(deferred["learning_needs"][0]["need_id"], need_id)
                self.assertEqual(deferred["learning_needs"][0]["criterion_outcomes"], works[0]["criterion_outcomes"])
                reopened = plan({"units": {}}, [], works, now=NOW, need_states=[{**state, "status": "open"}])
                self.assertEqual(reopened["next_steps"][0]["work_resource_id"], "saved-draft")
                self.assertEqual(reopened["next_steps"][0]["need_ids"], [need_id])
                self.assertEqual(reopened["learning_needs"][0]["status"], "open")
                self.assertEqual(works, before)


if __name__ == "__main__":
    unittest.main()
