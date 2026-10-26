"""Bounded help: no answer leakage, no early resolution, immutable provenance."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from cnkg import instruction
from cnkg.learning_needs import derive_needs
from cnkg.learning_store import LearningStore
from cnkg.admissions import fingerprint, policies
from cnkg.evidence import question_policy, _legacy_question_policy
from cnkg.manipulatives import validate_state


class InstructionTest(unittest.TestCase):
    def setUp(self):
        probe = {"type": "choice", "prompt": "一半再等分两份，涂色两份占几份？", "answer": "a",
                 "choices": [{"id": "a", "text": "2/4", "feedback": "原整体与涂色都细分。"},
                             {"id": "b", "text": "1/4", "feedback": "涂色部分也要细分。"}],
                 "answer_spec": {"kind": "choice"}, "explanation": "原涂色部分变成两小份。"}
        guide = {"type": "numeric", "prompt": "1/2的分子分母同乘3，分子是多少？", "answer": "3", "verify": "1*3",
                 "answer_spec": {"kind": "number", "comparison": "exact"}, "explanation": "分子也同乘3。"}
        self.support = {"id": "u:help", "objective_id": "u:goal", "title": "等值分数", "observation_note": "只确认同倍扩大，不确认整体选择。",
                        "hypotheses": ["可能只乘了分母。"], "diagnostic": probe, "guided_task": guide,
                        "hints": [{"level": i, "text": text} for i, text in enumerate(["保持整体不变。", "每份同样细分。", "1/2=2/4。"], 1)],
                        "followup_task_ids": ["q1", "q2"]}
        self.tasks = [{"id": "q1", "type": "numeric", "prompt": "2/3分子分母同乘2，分子是多少？", "answer": "4", "verify": "2*2",
                       "answer_spec": {"kind": "number", "comparison": "exact"}, "explanation": "分子同乘2。", "phase": "practice",
                       "objective_ids": ["u:goal"], "skill_ids": ["s1"], "family_id": "f1", "family_status": "declared", "representation": "symbolic"},
                      {"id": "q2", "type": "numeric", "prompt": "把三个等份各细分成两份，共多少份？", "answer": "6", "verify": "3*2",
                       "answer_spec": {"kind": "number", "comparison": "exact"}, "explanation": "3×2。", "phase": "transfer",
                       "objective_ids": ["u:goal"], "skill_ids": ["s1"], "family_id": "f2", "family_status": "declared", "representation": "verbal"}]
        self.unit = {"id": "u", "title": "分数", "stage": "primary", "topic_ids": ["t"], "objectives": [{"id": "u:goal", "skill_id": "s1"}],
                     "tasks": self.tasks, "instructional_support": {"schema": 1, "supports": [self.support]}}
        tasks = {t["id"]: t for t in self.tasks}
        self.catalog = SimpleNamespace(units={"u": {**self.unit, "resource_version": "new-version"}}, tasks=tasks,
            task=lambda tid: copy.deepcopy(tasks[tid]), placements={"t": {"stage": "primary"}}, skills={"s1": {}})
        self.origin = {"id": "old", "owner_id": "a" * 64, "kind": "unit", "unit_id": "u", "mode": "practice", "title": "分数",
            "resource_version": "old-version", "content_sha256": "old-version", "created_at": "2026-01-01T00:00:00+00:00", "completed_at": "2026-01-01T00:00:10+00:00",
            "answers": [{"question_id": "old-q", "status": "wrong", "skill_ids": ["s1"], "objective_ids": ["u:goal"],
                         "answered_at": "2026-01-01T00:00:05+00:00"}]}
        self.need = derive_needs(self.catalog, [self.origin])[0]

    def create(self, needs=None, exposures=None):
        return instruction.create(self.unit, "u:help", "a" * 64, self.catalog, exposures or {},
                                  [self.need] if needs is None else needs, "2026-01-02T00:00:00+00:00")[0]

    def answer(self, record, answer, seconds):
        qid = record["task_ids"][record["index"]]
        return instruction.answer(record, qid, answer, f"2026-01-02T00:00:{seconds:02d}+00:00")

    def test_probe_has_no_feedback_answer_or_unreleased_hints(self):
        record = self.create()
        shown = instruction.public_session(record)
        self.assertNotIn("answer", shown["item"])
        self.assertNotIn("feedback", shown["item"]["choices"][0])
        self.assertEqual([], shown["support"]["hints_opened"])
        with self.assertRaises(ValueError):
            instruction.hint(record, 3, "2026-01-02T00:00:01+00:00")

    def test_only_final_confirmation_resolves_with_help_provenance(self):
        record = self.create()
        self.answer(record, "b", 5)
        instruction.hint(record, 2, "2026-01-02T00:00:10+00:00")
        self.answer(record, "3", 15)
        self.assertEqual("open", derive_needs(self.catalog, [self.origin, record])[0]["status"])
        self.answer(record, record["question_snapshots"][record["task_ids"][-1]]["answer"], 25)
        need = derive_needs(self.catalog, [self.origin, record])[0]
        self.assertEqual("resolved", need["status"])
        self.assertEqual("supported_confirmation", need["resolution"]["kind"])
        self.assertFalse(need["resolution"]["independent_evidence"])
        self.assertTrue(need["resolution"]["help_used"])
        self.assertEqual("wrong", self.origin["answers"][0]["status"])
        self.assertEqual("old-version", record["source_origin"]["resource_version"])
        self.assertEqual("new-version", record["resource_version"])

    def test_unknown_owner_or_unrelated_goal_cannot_be_linked(self):
        bad = {**self.need, "skill_ids": ["unrelated"]}
        with self.assertRaises(ValueError):
            self.create([bad])
        record = self.create()
        with tempfile.TemporaryDirectory() as temp:
            store = LearningStore(Path(temp) / "learn.sqlite")
            store.save(record)
            self.assertIsNone(store.load(record["id"], "b" * 64))

    def test_exposed_question_and_family_change_followup_selection(self):
        record = self.create(exposures={"questions": {"q1": {"presentation_count": 2}}, "families": {"f1": {"reference_count": 1}}})
        self.assertEqual("q2", record["task_ids"][-1])
        self.assertFalse(record["context"]["independent_evidence"])

    def test_supported_confirmation_can_use_known_family_without_claiming_independence(self):
        # A different-family process task must not displace an unseen bounded
        # confirmation just to create the appearance of novel evidence.
        self.tasks[1].update(type="rubric", answer_spec={"kind": "rubric"},
            rubric=[{"criterion": "说明完整等分过程", "points": 1}])
        record = self.create(exposures={"families": {"f1": {"reference_count": 1}}})
        self.assertEqual("q1", record["task_ids"][-1])
        self.assertTrue(record["selection_basis"]["same_family_exposed"])
        self.assertFalse(record["context"]["independent_evidence"])
        self.answer(record, "a", 5); self.answer(record, "3", 6); self.answer(record, "4", 7)
        self.assertTrue(record["completed_at"])
        self.assertTrue(record["answers"][-1]["help_used"])

    def test_invalid_answer_and_replay_do_not_overwrite(self):
        record = self.create()
        feedback, events = self.answer(record, "unknown-option", 5)
        self.assertEqual("invalid", feedback["status"])
        self.assertEqual(0, record["index"])
        self.assertEqual([], events)
        first = record["task_ids"][0]
        self.answer(record, "a", 6)
        with self.assertRaises(ValueError):
            instruction.answer(record, first, "b", "2026-01-02T00:00:07+00:00")
        self.assertEqual("a", record["answers"][0]["raw_answer"])

    def test_tampering_frozen_prompt_is_detected(self):
        record = json.loads(json.dumps(self.create()))
        record["question_snapshots"][record["task_ids"][0]]["answer"] = "b"
        self.assertIsNotNone(instruction.version_problem(record))

    def test_manipulations_enforce_math_ranges_and_integer_types(self):
        validate_state("fraction_equivalence", {"numerator": 2, "denominator": 3, "factor": 2})
        for state in ({"numerator": 4, "denominator": 3, "factor": 2}, {"numerator": True, "denominator": 3, "factor": 2}):
            with self.assertRaises(ValueError):
                validate_state("fraction_equivalence", state)
        with self.assertRaises(ValueError):
            validate_state("data_reading_and_mean", {"values": [6, 8, 10, 12], "baseline": 3})


class ExplicitAdmissionTest(unittest.TestCase):
    def test_longer_explanation_cannot_promote_published_task(self):
        q = {"id": "short", "topicId": "t", "type": "short", "prompt": "说明这道题", "answer": "1", "explanation": "答案1"}
        policy = _legacy_question_policy(q)
        q["_admission"] = {"content_sha256": fingerprint(q), "policy": policy}
        self.assertEqual(policy, question_policy(q))
        q["explanation"] = "因为这里有很多连接词和文字，所以解析足够长，故由这些文字得到答案1。"
        self.assertFalse(question_policy(q)["diagnosis_eligible"])
        self.assertEqual("admission_version_mismatch", question_policy(q)["reason"])

    def test_changed_condition_requires_an_explicit_new_admission_record(self):
        q = {"id": "condition", "topicId": "t", "type": "numeric", "prompt": "2+3=?", "answer": "5", "verify": "2+3"}
        policy = _legacy_question_policy(q)
        row = {"id": q["id"], "source_sha256": fingerprint(q), "resolved_sha256": fingerprint(q), "source_policy": policy,
               "policy": policy, "basis": {"method": "versioned_test", "independent_review": False, "limitation": "不是独立审核"}}
        policies(q, q, row)
        with self.assertRaises(ValueError):
            policies({**q, "prompt": "2+3约为多少？"}, q, row)


if __name__ == "__main__":
    unittest.main()
