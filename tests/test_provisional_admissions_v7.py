"""A maintenance decision cannot promote finite observation into mastery."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg.admissions import fingerprint, policies, resolved_question, runtime_policy
from cnkg.evidence import question_policy
from tools import task_admissions


class ProvisionalAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.question = {
            "id": "m1a.1.1-q901", "topicId": "m1a.1.1", "type": "numeric",
            "prompt": "把1个圆片和1个圆片合在一起，现有几个？", "answer": "2", "verify": "1+1",
            "explanation": "把两个部分合起来，1+1=2。该结果只记录这次合并任务。",
            "source": "self-generated-v7-test", "review_status": "draft",
            "answer_spec": {"version": 1, "kind": "number", "comparison": "exact", "representation": "any"},
        }
        self.policy = {"diagnosis_eligible": True, "practice_eligible": True,
                       "may_confirm_mastery": False, "may_judge_weak": False,
                       "evidence_level": "verified", "reviewer_pending": True,
                       "confidence": "screening", "reason": "explicit_provisional_use_decision"}

    def frozen(self, policy):
        question = copy.deepcopy(self.question)
        question["_admission"] = {"fingerprint_version": 2,
                                  "content_sha256": fingerprint(question), "policy": policy}
        return question

    def test_record_cli_keeps_verified_screening_component_only(self):
        # Exercise the actual authoring entry point, including grade/verify,
        # persisted fingerprints, final registry check and runtime policy.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            (data / "skills").mkdir(parents=True)
            (data / "questions.json").write_text(json.dumps([self.question]))
            binding = {"id": self.question["id"], "answer_spec_source": "author"}
            (data / "skills/catalog-bindings.json").write_text(json.dumps({"tasks": {"bank:" + self.question["id"]: binding}}))
            path = data / "task_admissions.json"
            path.write_text(json.dumps({"schema": 1, "tasks": {}}))
            argv = ["task_admissions", "--record-id", self.question["id"], "--purpose", "screening",
                    "--evidence-level", "verified", "--reviewer", "ai-author", "--reason",
                    "重新列式1+1=2；只记录两部分合并，不确认整个计数主题。"]
            with patch.object(task_admissions, "ROOT", root), patch("sys.argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(task_admissions.main(), 0)
            row = json.loads(path.read_text())["tasks"][self.question["id"]]
            self.assertTrue(row["policy"]["diagnosis_eligible"])
            for name in ("source_policy", "policy"):
                self.assertFalse(row[name]["may_confirm_mastery"])
                self.assertFalse(row[name]["may_judge_weak"])
            self.assertFalse(row["basis"]["independent_review"])

    def test_malformed_archive_never_falls_back_to_legacy_eligibility(self):
        self.assertTrue(question_policy(self.question)["diagnosis_eligible"])
        for snapshot in (None, [], "legacy-invalid"):
            with self.subTest(snapshot=snapshot):
                result = question_policy({**self.question, "_admission": snapshot})
                self.assertFalse(result["practice_eligible"])
                self.assertFalse(result["diagnosis_eligible"])
                self.assertEqual(result["reason"], "admission_contract_incomplete")

    def test_boolean_strings_null_policy_and_contradictions_fail_closed(self):
        variants = [None, [], {**self.policy, "diagnosis_eligible": "false"},
                    {**self.policy, "practice_eligible": False},
                    {**self.policy, "may_confirm_mastery": True},
                    {**self.policy, "may_judge_weak": True},
                    {**self.policy, "reason": None}]
        for value in variants:
            with self.subTest(policy=value):
                before = copy.deepcopy(value)
                result = question_policy(self.frozen(value))
                self.assertFalse(result["practice_eligible"])
                self.assertFalse(result["diagnosis_eligible"])
                self.assertEqual(value, before)

    def test_valid_legacy_contract_is_preserved_without_reinterpreting_history(self):
        old = {**self.policy, "reason": "v3_archived_policy", "may_confirm_mastery": True}
        question = self.frozen(old)
        before = copy.deepcopy(question)
        self.assertEqual(runtime_policy(question), old)
        self.assertEqual(question, before)
        self.assertIsNone(runtime_policy(self.question))

    def test_malformed_review_basis_is_rejected_with_a_question_level_error(self):
        binding = {"id": self.question["id"], "answer_spec_source": "author"}
        resolved = resolved_question(self.question, binding)
        row = {"id": self.question["id"], "source_sha256": fingerprint(self.question),
               "resolved_sha256": fingerprint(resolved), "source_policy": self.policy,
               "policy": self.policy,
               "basis": {"independent_review": False, "method": "explicit_semantic_use_review",
                         "limitation": "Only this bounded observation was checked."}}
        variants = [None, [], 42,
                    {**row["basis"], "method": ["not actual method text"]},
                    {**row["basis"], "limitation": {"not": "actual boundary text"}},
                    {**row["basis"], "method": "  "},
                    {**row["basis"], "limitation": "\n"},
                    {**row["basis"], "independent_review": 0}]
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "skills").mkdir()
            (data / "questions.json").write_text(json.dumps([self.question]))
            (data / "skills/catalog-bindings.json").write_text(json.dumps({
                "tasks": {"bank:" + self.question["id"]: binding}}))
            target = data / "task_admissions.json"
            for basis in variants:
                with self.subTest(basis=basis):
                    changed = {**row, "basis": basis}
                    with self.assertRaisesRegex(ValueError, "准入依据"):
                        policies(self.question, resolved, changed)
                    target.write_text(json.dumps({"schema": 1, "tasks": {self.question["id"]: changed}}))
                    before = target.read_bytes()
                    errors = task_admissions.check(data)
                    self.assertEqual(1, len(errors))
                    self.assertIn(self.question["id"] + ": 准入依据", errors[0])
                    self.assertEqual(before, target.read_bytes())

    def test_every_malformed_permission_or_fingerprint_closes_all_uses(self):
        fields = ("diagnosis_eligible", "practice_eligible", "may_confirm_mastery",
                  "may_judge_weak", "reviewer_pending")
        questions = []
        for field in fields:
            for value in ("false", 0, 1, None):
                questions.append((field + "=" + repr(value), self.frozen({**self.policy, field: value})))
            missing = {key: value for key, value in self.policy.items() if key != field}
            questions.append(("missing " + field, self.frozen(missing)))
        for version in (True, False, "2", 0, 3):
            question = self.frozen(self.policy)
            question["_admission"]["fingerprint_version"] = version
            questions.append(("version=" + repr(version), question))
        tampered = self.frozen(self.policy)
        tampered["prompt"] = "把1个圆片和2个圆片合在一起，现有几个？"
        questions.append(("changed prompt", tampered))
        for label, question in questions:
            with self.subTest(case=label):
                original = copy.deepcopy(question)
                result = question_policy(question)
                for field in fields[:-1]:
                    self.assertIs(False, result[field])
                self.assertEqual(original, question)


if __name__ == "__main__":
    unittest.main()
