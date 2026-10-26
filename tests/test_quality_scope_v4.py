"""The registered denominator cannot shrink when teaching or tasks go missing."""
from __future__ import annotations

from collections import Counter
import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg import quality_scope
from cnkg.evidence import question_policy
from tools.teach_cards import check_card

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SECONDARY = ("m7a.1.1", "m7a.1.2.1", "m7a.1.2.4", "m7a.1.2.5", "m7a.2.3.2", "m7a.2.3.3",
             "m7b.12.1.2", "m9b.33.2.1", "mh2.9.1.1", "mh2.9.1.2", "mh5.8.3.1")


class QualityScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = quality_scope.build_scope_report()

    def test_full_registered_denominator_and_distinct_completion_roles(self):
        summary = self.report["summary"]
        self.assertEqual((summary["topic_count"], summary["volume_count"], summary["topic_goals"]), (477, 23, 954))
        self.assertEqual((summary["core_topics"], summary["foundation_topics"]), (342, 135))
        self.assertEqual((summary["unit_goals"], summary["project_goals"], summary["total_goals"]), (104, 66, 1124))
        topics = [row for row in self.report["goals"] if row["resource_kind"] == "topic"]
        self.assertEqual(Counter(row["role"] for row in topics), {"core": 684, "foundation_support": 270})
        self.assertTrue(self.report["manifest_valid"])
        self.assertTrue(all(row.get("optional_participation") for row in self.report["goals"] if row["resource_kind"] == "project"))

    def test_every_resource_reference_points_to_existing_source_content(self):
        loaded = {}
        for row in self.report["goals"]:
            for field in ("material_refs", "process_refs", "task_refs", "revision_refs", "conditions_refs", "representation_refs"):
                for reference in row[field]:
                    path = ROOT / reference["path"]
                    self.assertTrue(path.is_file(), reference)
                    key = str(path)
                    if key not in loaded:
                        loaded[key] = json.loads(path.read_text())
                    value = loaded[key]
                    for part in reference["json_pointer"].split("/")[1:]:
                        part = part.replace("~1", "/").replace("~0", "~")
                        value = value[int(part)] if isinstance(value, list) else value[part]
                    self.assertIsNotNone(value, reference)
                    self.assertEqual(len(reference["content_sha256"]), 64)
            self.assertFalse(row["independent_mastery_evidence"])
            self.assertEqual(row["teacher_review"], "pending")

    def test_omitted_target_material_becomes_gap_without_removing_target(self):
        original_read = quality_scope._read
        missing_path = DATA / "teach/m7a.1.1.json"
        def read(path):
            value = original_read(path)
            if Path(path) == missing_path:
                value.pop("objective_coverage", None)
            return value
        with patch("cnkg.quality_scope._read", side_effect=read):
            report = quality_scope.build_scope_report()
        goals = [row for row in report["goals"] if row["resource_id"] == "m7a.1.1"]
        self.assertEqual(len(goals), 2)
        self.assertTrue(all(row["status"] == "incomplete" for row in goals))
        self.assertTrue(all(any(gap["code"] == "explicit_goal_material_and_task" for gap in row["gaps"]) for row in goals))
        self.assertTrue(all(row["auxiliary_reference_refs"] for row in goals))
        self.assertEqual(report["summary"]["total_goals"], 1124)

    def test_role_downgrade_or_goal_removal_in_manifest_is_rejected(self):
        manifest = copy.deepcopy(quality_scope.scope_manifest())
        foundation = next(row for row in manifest["topics"] if row["role"] == "foundation_support")
        foundation["role"] = "extension"
        foundation["goals"].pop()
        report = quality_scope.build_scope_report(manifest=manifest)
        self.assertFalse(report["manifest_valid"])
        actual = [row for row in report["goals"] if row["resource_id"] == foundation["topic_id"]]
        self.assertEqual(len(actual), 2)
        self.assertTrue(all(row["role"] == "foundation_support" for row in actual))


class ScopeReadIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"
        tid = "m2b.1.2"
        topics = json.loads((DATA / "topics.json").read_text())
        topic = next(t for t in topics if t["id"] == tid)
        tiers = json.loads((DATA / "topic_tiers.json").read_text())
        registry = json.loads((DATA / "skills/registry.json").read_text())
        self.card_path = self.data / "teach" / f"{tid}.json"
        self.card = json.loads((DATA / "teach" / f"{tid}.json").read_text())
        self.practice_ids = {task["id"] for task in self.card["practice"]}
        self.questions = [q for q in json.loads((DATA / "questions.json").read_text())
                          if q["id"] in self.practice_ids]
        for name, value in (
                ("topics.json", [topic]),
                ("topic_tiers.json", {"topics": {tid: tiers["topics"][tid]}}),
                ("skills/registry.json", {"placements": {tid: registry["placements"][tid]},
                                          "objective_skill_ids": {}}),
                ("questions.json", self.questions),
                (f"teach/{tid}.json", self.card)):
            self.write(self.data / name, value)
        self.write(self.data / "curriculum_scope.json", quality_scope.scope_manifest(self.data))

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_file_metadata_is_reused_within_report_and_refreshed_after_source_edit(self):
        reads, relatives = Counter(), Counter()
        read_bytes, relative_to = Path.read_bytes, Path.relative_to

        def counted_read(path):
            reads[path] += 1
            return read_bytes(path)

        def counted_relative(path, *args, **kwargs):
            relatives[path] += 1
            return relative_to(path, *args, **kwargs)

        before_hash = hashlib.sha256(self.card_path.read_bytes()).hexdigest()
        with patch.object(Path, "read_bytes", counted_read), patch.object(Path, "relative_to", counted_relative):
            before = quality_scope.build_scope_report(self.data)
            self.assertEqual(reads[self.card_path], 1)
            self.assertEqual(relatives[self.card_path], 1)
            self.card["objective_coverage"][0]["explanation"] += " 再核对余数小于除数。"
            self.write(self.card_path, self.card)
            after = quality_scope.build_scope_report(self.data)
            self.assertEqual(reads[self.card_path], 2)
            self.assertEqual(relatives[self.card_path], 2)
        after_hash = hashlib.sha256(self.card_path.read_bytes()).hexdigest()
        self.assertNotEqual(before_hash, after_hash)
        self.assertNotEqual(before["source_version"], after["source_version"])
        for report, expected in ((before, before_hash), (after, after_hash)):
            refs = [ref for goal in report["goals"] for key in
                    ("material_refs", "process_refs", "task_refs", "revision_refs", "conditions_refs", "representation_refs")
                    for ref in goal[key]]
            self.assertEqual({ref["content_sha256"] for ref in refs}, {expected})
            self.assertTrue({"/objective_coverage/0/explanation", "/objective_coverage/1/explanation"}
                            .issubset({ref["json_pointer"] for ref in refs}))
            for ref in refs:
                value = self.card
                for part in ref["json_pointer"].split("/")[1:]:
                    part = part.replace("~1", "/").replace("~0", "~")
                    value = value[int(part)] if isinstance(value, list) else value[part]
                self.assertIsNotNone(value)

    def test_policy_is_evaluated_once_per_report_and_source_changes_take_effect(self):
        question = self.questions[0]
        qid = question["id"]
        self.assertTrue(question_policy(question)["practice_eligible"])
        with patch("cnkg.evidence.question_policy", wraps=question_policy) as policies:
            before = quality_scope.build_scope_report(self.data)
            self.assertEqual(Counter(call.args[0]["id"] for call in policies.call_args_list),
                             Counter({qid: 1 for qid in self.practice_ids}))
            question["practice_eligible"] = False
            self.write(self.data / "questions.json", self.questions)
            after = quality_scope.build_scope_report(self.data)
            self.assertEqual(Counter(call.args[0]["id"] for call in policies.call_args_list),
                             Counter({qid: 2 for qid in self.practice_ids}))
        self.assertEqual(before["summary"]["total_goals"], after["summary"]["total_goals"])
        self.assertNotEqual(before["source_version"], after["source_version"])
        for row in before["goals"]:
            self.assertIn(qid, {ref.get("task_id") for ref in row["task_refs"]})
        for row in after["goals"]:
            self.assertNotIn(qid, {ref.get("task_id") for ref in row["task_refs"]})

    def test_question_wording_changes_scope_version_without_reallocating_goals(self):
        before = quality_scope.build_scope_report(self.data)
        self.questions[0]["explanation"] += " 再用除数乘商加余数核对被除数。"
        self.write(self.data / "questions.json", self.questions)
        after = quality_scope.build_scope_report(self.data)
        self.assertNotEqual(before["source_version"], after["source_version"])
        self.assertEqual(before["goals"], after["goals"])

    def enable_admissions(self):
        manifest = json.loads((DATA / "task_admissions.json").read_text())
        manifest["tasks"] = {qid: row for qid, row in manifest["tasks"].items() if qid in self.practice_ids}
        bindings = json.loads((DATA / "skills/catalog-bindings.json").read_text())
        bindings["tasks"] = {key: row for key, row in bindings["tasks"].items()
                             if key in {"bank:" + qid for qid in self.practice_ids}}
        self.write(self.data / "task_admissions.json", manifest)
        self.write(self.data / "skills/catalog-bindings.json", bindings)
        return manifest, bindings

    def test_explicit_resolved_policy_can_remove_practice_without_changing_question(self):
        manifest, _ = self.enable_admissions()
        qid = self.questions[0]["id"]
        question_bytes = (self.data / "questions.json").read_bytes()
        before = quality_scope.build_scope_report(self.data)
        row = manifest["tasks"][qid]
        self.assertTrue(row["source_policy"]["practice_eligible"])
        row["policy"].update(diagnosis_eligible=False, practice_eligible=False,
                             may_confirm_mastery=False, may_judge_weak=False)
        self.write(self.data / "task_admissions.json", manifest)
        after = quality_scope.build_scope_report(self.data)
        self.assertEqual((self.data / "questions.json").read_bytes(), question_bytes)
        self.assertNotEqual(before["source_version"], after["source_version"])
        self.assertEqual([goal["goal_id"] for goal in before["goals"]],
                         [goal["goal_id"] for goal in after["goals"]])
        for goal in before["goals"]:
            self.assertIn(qid, {ref.get("task_id") for ref in goal["task_refs"]})
        for goal in after["goals"]:
            self.assertNotIn(qid, {ref.get("task_id") for ref in goal["task_refs"]})

    def test_changed_question_or_answer_binding_requires_new_explicit_admission(self):
        _, bindings = self.enable_admissions()
        quality_scope.build_scope_report(self.data)
        changed = copy.deepcopy(self.questions)
        changed[0]["explanation"] += " 这是一条尚未重新登记的说明。"
        self.write(self.data / "questions.json", changed)
        with self.assertRaisesRegex(ValueError, "更新显式准入记录"):
            quality_scope.build_scope_report(self.data)
        self.write(self.data / "questions.json", self.questions)
        qid = next(q["id"] for q in self.questions if not q.get("answer_spec"))
        spec = bindings["tasks"]["bank:" + qid]["answer_spec"]
        spec["representation"] = "decimal" if spec.get("representation") != "decimal" else "any"
        self.write(self.data / "skills/catalog-bindings.json", bindings)
        with self.assertRaisesRegex(ValueError, "更新显式准入记录"):
            quality_scope.build_scope_report(self.data)

    def test_published_catalog_cannot_silently_fall_back_when_admissions_are_missing(self):
        self.write(self.data / "catalog.json", {"schema_version": 1})
        with self.assertRaisesRegex(ValueError, "不能退回旧准入规则"):
            quality_scope.build_scope_report(self.data)

    def test_incomplete_or_incompatible_admission_manifest_is_rejected(self):
        manifest, _ = self.enable_admissions()
        invalid = copy.deepcopy(manifest)
        invalid["schema"] = 2
        self.write(self.data / "task_admissions.json", invalid)
        with self.assertRaisesRegex(ValueError, "准入登记版本或结构不正确"):
            quality_scope.build_scope_report(self.data)
        manifest["tasks"].pop(self.questions[0]["id"])
        self.write(self.data / "task_admissions.json", manifest)
        with self.assertRaisesRegex(ValueError, "身份集合不一致"):
            quality_scope.build_scope_report(self.data)


class SecondaryFoundationContentTests(unittest.TestCase):
    def test_all_eleven_cards_teach_their_actual_targets_and_disclose_observation_limits(self):
        topics = {topic["id"]: topic for topic in json.loads((DATA / "topics.json").read_text())}
        bank = {question["id"]: question for question in json.loads((DATA / "questions.json").read_text())}
        for tid in SECONDARY:
            card = json.loads((DATA / "teach" / f"{tid}.json").read_text())
            with self.subTest(topic=tid):
                self.assertEqual(card["tier"], "tool")
                self.assertEqual(check_card(card, topics, bank), [])
                self.assertEqual(len(card["objective_coverage"]), 2)
                self.assertTrue(card["conditions"])
                for index, goal in enumerate(card["objective_coverage"]):
                    self.assertEqual(goal["title"], topics[tid]["mastery_criteria"][index])
                    self.assertTrue(goal["observation_note"])
                    self.assertEqual(set(goal["difficulty_support"]), {"discriminating_prompt", "hint", "revision_prompt"})

    def test_equal_individual_probabilities_do_not_imply_all_samples_equal(self):
        sample_space = [{1, 2}, {3, 4}]
        marginal = {person: Fraction(sum(person in sample for sample in sample_space), 2) for person in range(1, 5)}
        self.assertEqual(set(marginal.values()), {Fraction(1, 2)})
        self.assertNotIn({1, 3}, sample_space)
        card = json.loads((DATA / "teach/mh2.9.1.1.json").read_text())
        answer = card["objective_coverage"][0]["self_check"]["reference_answer"]
        self.assertIn("不是简单随机抽样", answer)
        self.assertIn("四组概率为0", answer)

    def test_contingency_table_conditions_and_denominators_are_distinct(self):
        a_yes, a_no, b_yes, b_no = 16, 24, 18, 42
        self.assertEqual(a_yes + a_no + b_yes + b_no, 100)
        self.assertEqual(Fraction(a_yes, a_yes + a_no), Fraction(2, 5))
        self.assertEqual(Fraction(a_yes, a_yes + b_yes), Fraction(8, 17))
        self.assertEqual(Fraction(a_yes, 100), Fraction(4, 25))
        card = json.loads((DATA / "teach/mh5.8.3.1.json").read_text())
        answer = card["objective_coverage"][1]["self_check"]["reference_answer"]
        for value in ("2/5", "8/17", "4/25", "不能建立因果"):
            self.assertIn(value, answer)


if __name__ == "__main__":
    unittest.main()
