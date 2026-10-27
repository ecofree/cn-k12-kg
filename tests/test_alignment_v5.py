"""A fuller metadata inventory must not become a claim of learning quality."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from cnkg.admissions import fingerprint, policies, runtime_policy, resolved_question
from cnkg.alignment import build_alignment_report, goal_dimensions, task_dimensions


class AlignmentTests(unittest.TestCase):
    def test_unknown_representation_and_changed_numbers_do_not_create_observation_variety(self):
        task = {"id": "one", "type": "numeric", "skill_ids": ["s"], "family_id": "fraction.add-equal",
                "family_status": "declared", "representation": "unknown"}
        goal = {"resource_kind": "topic", "criterion_index": 0}
        authored = {"objective_coverage": [{"criterion_index": 0, "observation_note": "只看到同分母相加",
            "difficulty_support": {"discriminating_prompt": "分母表示什么？", "hint": "先说每份的大小", "revision_prompt": "用同一整体画图核对"}}]}
        self.assertFalse(task_dimensions(task)["known_representation"])
        dims = goal_dimensions(goal, {}, [task], authored)
        self.assertIn("known_task_structure_and_representation", dims["gaps"])
        first = {**task, "representation": "fraction_strip"}
        second = {**first, "id": "different-numbers"}
        dims = goal_dimensions(goal, {}, [first, second], authored)
        self.assertEqual(len(dims["objective_task_family_ids"]), 1)
        self.assertFalse(dims["multiple_objective_task_families"])
        self.assertTrue(dims["goal_specific_help"])
        self.assertFalse(dims["independent_mastery_evidence"])

    def test_open_process_remains_visible_without_an_automatic_independence_claim(self):
        task = {"id": "construction", "type": "open", "skill_ids": ["s"],
                "family_id": "triangle.altitude-obtuse", "family_status": "declared", "representation": "geometry_diagram"}
        dims = goal_dimensions({"resource_kind": "topic", "criterion_index": 0}, {}, [task], {})
        self.assertEqual(dims["classified_task_ids"], ["construction"])
        self.assertFalse(dims["objective_task_family_ids"])
        self.assertFalse(dims["independent_mastery_evidence"])

    def test_a_focus_never_removes_outside_goals_or_hides_a_malformed_manifest(self):
        with tempfile.TemporaryDirectory() as name:
            data = Path(name)
            (data / "alignment_targets.json").write_text(json.dumps({"schema": 1, "topic_ids": ["t1"], "unit_ids": ["u"]}))
            goals = [{"goal_id": sid, "resource_kind": "topic", "resource_id": rid, "criterion_index": 0,
                      "title": "goal", "statement": "解释", "stage": "primary", "role": "core", "status": "materials_ready"}
                     for sid, rid in (("s1", "t1"), ("s2", "t2"))]
            catalog = {"tasks": {}, "skills": {"s1": {}, "s2": {}}}
            result = build_alignment_report(data, catalog=catalog, scope_report={"goals": goals})
            self.assertEqual(result["summary"]["goals"], 2)
            self.assertEqual(result["summary"]["focus_goals"], 1)
            self.assertEqual(result["summary"]["gap_counts"]["goal_specific_help"], 2)
            self.assertFalse(result["focus_requirements_met"])
            for bad in (None, "t1", [None], [{"id": "t1"}]):
                (data / "alignment_targets.json").write_text(json.dumps({"schema": 1, "topic_ids": bad, "unit_ids": ["u"]}))
                result = build_alignment_report(data, catalog=catalog, scope_report={"goals": goals})
                self.assertTrue(any("topic_ids" in message for message in result["focus_errors"]))


class BoundAdmissionTests(unittest.TestCase):
    def test_binding_only_edits_change_the_effective_fingerprint(self):
        q = {"id": "q", "type": "numeric", "prompt": "2+3", "answer": "5"}
        binding = {"type": "numeric", "family_id": "migration-family", "family_status": "unclassified",
                   "variant_id": "variant", "representation": "unknown", "cognitive_demand": "unknown",
                   "answer_spec": {"kind": "rational"}}
        before = fingerprint(resolved_question(q, binding))
        for change in ({"family_status": "declared"}, {"representation": "symbolic"},
                       {"family_id": "count.combine"}, {"cognitive_demand": "execute"}):
            self.assertNotEqual(before, fingerprint(resolved_question(q, {**binding, **change})))
        authored = {**q, "family_id": "authored-combine", "representation": "manipulative"}
        result = resolved_question(authored, binding)
        self.assertEqual(result["family_status"], "declared")
        self.assertEqual(result["family_id"], authored["family_id"])
        self.assertEqual(result["representation"], "manipulative")

    def test_target_family_and_representation_edits_invalidate_current_admission(self):
        q = {"id": "q", "topicId": "t", "type": "numeric", "prompt": "2+3", "answer": "5",
             "explanation": "把两组物体合起来得到5", "family_id": "count.combine",
             "representation": "objects", "tested_criterion_indices": [0]}
        policy = {"diagnosis_eligible": False, "practice_eligible": True, "may_confirm_mastery": False,
                  "may_judge_weak": False, "evidence_level": "explained", "reviewer_pending": True,
                  "confidence": "screening", "reason": "reviewed_practice"}
        row = {"id": "q", "source_sha256": fingerprint(q), "resolved_sha256": fingerprint(q),
               "source_policy": policy, "policy": policy,
               "basis": {"method": "fixture_review", "independent_review": False, "limitation": "not a teacher review"}}
        self.assertEqual(policies(q, q, row)[1], policy)
        for key, value in (("tested_criterion_indices", [1]), ("family_id", "count.rearrange"),
                           ("representation", "unknown"), ("alignment_note", "different observation")):
            changed = {**q, key: value}
            with self.assertRaisesRegex(ValueError, "改变"):
                policies(changed, changed, row)
        frozen = {**q, "_admission": {"fingerprint_version": 2, "content_sha256": fingerprint(q), "policy": policy}}
        self.assertEqual(runtime_policy(frozen), policy)
        frozen["family_id"] = "count.rearrange"
        self.assertFalse(runtime_policy(frozen)["practice_eligible"])
        # Existing version-1 frozen sessions keep their original decisions.
        old = {**q, "_admission": {"content_sha256": fingerprint(q, 1), "policy": policy}}
        self.assertEqual(runtime_policy(old), policy)


if __name__ == "__main__":
    unittest.main()
