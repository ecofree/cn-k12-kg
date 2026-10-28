"""真实复审反例：数学结构、来源谱系、考试范围与诊断统计。"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from cnkg.evidence import grade, norm_stem, question_policy, valid_corroboration
from tools.app_items.h4 import ITEMS
from tools.check_stage_policy import build_report
from tools.grade_ext import run
from tools.set_exam_scope import migrate_legacy_topic, observe_topic

ROOT = Path(__file__).resolve().parents[1]


def question(qid="q1", **changes):
    item = {
        "id": qid, "topicId": "m1a.1.1", "type": "numeric",
        "prompt": "请计算算式2+3的值。", "answer": "5", "choices": [],
        "explanation": "参考答案是5。", "source": "source-" + qid,
        "provenance": {"origin_id": "origin-" + qid,
                       "source_url": "https://sources.example/" + qid},
    }
    item.update(changes)
    return item


class MathematicalIdentityTests(unittest.TestCase):
    def test_operations_and_mathematical_conditions_are_preserved(self):
        pairs = [
            ("计算2+3的值", "计算2-3的值"),
            ("计算6+4×5的值", "计算(6+4)×5的值"),
            ("解不等式x<2", "解不等式x≤2"),
            ("区间(1,2)", "区间[1,2]"),
            ("求1.2的倒数", "求12的倒数"),
            ("求x²的值", "求x2的值"),
            ("求A的值", "求a的值"),
            ("π的大小", "3.141592653589793的大小"),
            ("数字1 2", "数字12"),
        ]
        for first, second in pairs:
            with self.subTest(first=first):
                self.assertNotEqual(norm_stem(first), norm_stem(second))

    def test_only_proven_formatting_equivalences_are_normalized(self):
        self.assertEqual(norm_stem(r"计算 $2 \times 3$ 的值。"),
                         norm_stem("计算２×３的值"))
        self.assertEqual(norm_stem(r"x\leq 2"), norm_stem("x≤2"))

    def test_arithmetic_operator_collision_does_not_corroborate(self):
        plus = question("plus")
        minus = question("minus", prompt="请计算算式2-3的值。", answer="-1",
                         explanation="参考答案是-1。")
        items, report = run([plus, minus])
        self.assertEqual(report["cross_source_groups"], 0)
        self.assertTrue(all(item["evidence_level"] == "bare" for item in items))

    def test_boolean_claim_alone_is_not_corroboration(self):
        self.assertEqual(grade(question(), cross_source=True)["level"], "bare")


class ProvenanceTests(unittest.TestCase):
    def test_matching_answers_with_independent_sources_are_bound(self):
        items, report = run([question("a"), question("b", answer="5.0")])
        self.assertEqual(report["cross_source_groups"], 1)
        for item in items:
            self.assertEqual(item["evidence_level"], "corroborated")
            self.assertTrue(valid_corroboration(item, item["cross_source_evidence"]))
            self.assertEqual(len(item["cross_source_evidence"]["origin_ids"]), 2)

    def test_dataset_names_do_not_substitute_for_provenance(self):
        items, _ = run([question("a", provenance=None), question("b", provenance=None)])
        self.assertTrue(all(item["evidence_level"] == "bare" for item in items))
        self.assertTrue(all(item["cross_source_evidence"]["status"] == "insufficient_provenance"
                            for item in items))

    def test_same_origin_or_original_url_is_not_independent(self):
        first = question("a")
        second = question("b")
        second["provenance"]["origin_id"] = first["provenance"]["origin_id"]
        self.assertEqual(run([first, second])[1]["cross_source_groups"], 0)
        second = question("b")
        second["provenance"]["source_url"] = "https://sources.example/a?utm_source=copy#answer"
        self.assertEqual(run([first, second])[1]["cross_source_groups"], 0)

    def test_common_ancestor_and_transitive_reprints_are_not_independent(self):
        original = question("a")
        middle = question("b", prompt="这是一道不同的中间来源记录。")
        copy_item = question("c")
        middle["provenance"]["derived_from"] = ["origin-a"]
        copy_item["provenance"]["derived_from"] = ["origin-b"]
        self.assertEqual(run([original, middle, copy_item])[1]["cross_source_groups"], 0)

    def test_choice_identity_uses_content_not_labels_or_order(self):
        choices = [{"id": "first", "text": "3"}, {"id": "second", "text": "5"}]
        first = question("a", type="choice", choices=choices, answer="second")
        second = question("b", type="choice",
                          choices=[{"id": "x", "text": "5"}, {"id": "y", "text": "3"}], answer="x")
        self.assertEqual(run([first, second])[1]["cross_source_groups"], 1)
        second["choices"][1]["text"] = "7"
        self.assertEqual(run([first, second])[1]["cross_source_groups"], 0)

    def test_answer_conflict_is_quarantined_even_when_each_has_a_source(self):
        first = question("a", usable_for_diagnosis=True, admission_level="cross-checked")
        second = question("b", answer="6", explanation="参考答案是6。",
                          usable_for_diagnosis=True, admission_level="cross-checked")
        items, report = run([first, second])
        self.assertEqual(report["cross_source_groups"], 0)
        for item in items:
            self.assertEqual(item["cross_source_evidence"]["status"], "conflict")
            self.assertEqual(item["evidence_level"], "defective")
            self.assertFalse(item["usable_for_diagnosis"])
            self.assertFalse(question_policy(item)["practice_eligible"])

    def test_corroboration_cannot_survive_content_or_answer_changes(self):
        item = run([question("a"), question("b")])[0][0]
        changed = copy.deepcopy(item)
        changed["answer"] = "6"
        self.assertFalse(valid_corroboration(changed, changed["cross_source_evidence"]))
        self.assertNotEqual(question_policy(changed)["evidence_level"], "corroborated")
        changed = copy.deepcopy(item)
        changed["prompt"] = "请计算算式2-3的值。"
        self.assertFalse(valid_corroboration(changed, changed["cross_source_evidence"]))

    def test_old_unbound_label_is_reassessed_from_its_own_evidence(self):
        item = question("a", evidence_level="corroborated", provenance=None)
        policy = question_policy(item)
        self.assertEqual(policy["evidence_level"], "bare")
        self.assertFalse(policy["diagnosis_eligible"])
        self.assertTrue(policy["practice_eligible"])
        self.assertEqual(policy["reason"], "legacy_corroboration_not_bound")


class ExaminationScopeTests(unittest.TestCase):
    def topic(self, **changes):
        topic = {"id": "m1a.1.1", "name": "分与合", "grade": 1,
                 "exam_scope": "textbook_only", "exam_scope_evidence": {
                     "reason": "完整术语在所有真题池 0 命中", "method": "full_term_search",
                     "terms": ["分与合"], "pools": {"旧池": {"size": 100, "hits": 0}},
                     "total_hits": 0, "checked_at": "2026-10-25"}}
        topic.update(changes)
        return topic

    def test_migration_preserves_original_observation_without_exam_claim(self):
        original = self.topic()
        migrated = migrate_legacy_topic(original)
        self.assertEqual(migrated["exam_scope"], "unknown")
        self.assertEqual(migrated["source_observation"], "not_found_in_scanned_sources")
        self.assertEqual(migrated["source_observation_evidence"]["legacy_exam_scope_evidence"],
                         original["exam_scope_evidence"])
        self.assertEqual(migrate_legacy_topic(migrated), migrated)
        self.assertEqual(original["exam_scope"], "textbook_only")

    def test_positive_mapping_also_does_not_prove_official_exam_scope(self):
        migrated = migrate_legacy_topic(self.topic(
            exam_scope="examined", exam_scope_evidence={"sources": ["exam-dataset"]}))
        self.assertEqual(migrated["exam_scope"], "unknown")
        self.assertEqual(migrated["source_observation"], "found")

    def test_missing_or_empty_sources_are_not_search_misses(self):
        for pools in ([], [("empty", [])]):
            result = observe_topic(self.topic(), set(), pools, "2026-10-26")
            self.assertEqual(result["source_observation"], "source_unavailable")
            self.assertEqual(result["exam_scope"], "unknown")

    def test_observation_distinguishes_hit_and_miss(self):
        hit = observe_topic(self.topic(), set(), [("available", ["分与合练习题"])], "2026-10-26")
        miss = observe_topic(self.topic(), set(), [("available", ["认识立体图形"])], "2026-10-26")
        self.assertEqual(hit["source_observation"], "found")
        self.assertEqual(miss["source_observation"], "not_found_in_scanned_sources")
        self.assertEqual(hit["exam_scope"], "unknown")
        self.assertEqual(miss["exam_scope"], "unknown")

    def test_explicit_scope_is_preserved_and_not_overwritten_by_search(self):
        topic = self.topic(exam_scope="included", exam_scope_evidence={
            "method": "explicit_scope", "reason": "测试用明确范围记录",
            "source_url": "https://exam.example/syllabus", "scope_reference": "section-2"})
        result = observe_topic(topic, set(), [], "2026-10-26")
        self.assertEqual(result["exam_scope"], "included")
        self.assertEqual(result["exam_scope_evidence"], topic["exam_scope_evidence"])

    def test_current_topics_retain_legacy_records_without_search_scope_inferences(self):
        topics = json.loads((ROOT / "data/topics.json").read_text())
        self.assertEqual(len(topics), 477)
        for topic in topics:
            self.assertNotIn(topic["exam_scope"], ("examined", "source_gap", "textbook_only"))
            if topic["exam_scope"] != "unknown":
                self.assertEqual(topic["exam_scope_evidence"]["method"], "explicit_scope")
                self.assertTrue(topic["exam_scope_evidence"]["source_url"])
                self.assertTrue(topic["exam_scope_evidence"]["scope_reference"])
            self.assertIn("legacy_exam_scope_evidence", topic["source_observation_evidence"])


class DiagnosticAccountingTests(unittest.TestCase):
    def test_isolated_and_practice_only_items_do_not_inflate_diagnostic_pool(self):
        topic = {"id": "m1a.1.1", "grade": 1, "name": "数的认识",
                 "exam_scope": "unknown", "source_observation": "found"}
        admitted = question("valid", source="self-generated-test", verify="2+3")
        practice = question("practice", source="self-generated-test", verify="2+3",
                            diagnosis_eligible=False)
        memory = question("memory", source="self-generated-memory-drill", verify="2+3")
        external = question("external", verify="2+3", evidence_level="verified",
                            usable_for_diagnosis=True)
        external_practice = question("external-practice", verify="2+3", evidence_level="verified",
                                     usable_for_diagnosis=True, for_diagnosis=False)
        policy = json.loads((ROOT / "data/stage_policy.json").read_text())
        result = build_report([topic], [admitted, practice, memory],
                              [external, external_practice], policy, [])["primary"]
        self.assertEqual(result["questions_pairs"], 5)
        self.assertEqual(result["questions_pairs_usable"], 2)
        self.assertEqual(sum(result["diagnosable_by_source"].values()), 2)
        self.assertEqual(result["topics_with_external_diagnosable"], 1)
        self.assertEqual(result["unknown_exam_scope_topics"], 1)
        self.assertNotIn("textbook_only_topics", result)
        self.assertNotIn("external_gap_real", result)

    def test_missing_external_bundle_is_not_reported_as_a_mapping_gap(self):
        policy = json.loads((ROOT / "data/stage_policy.json").read_text())
        topic = {"id": "m1a.1.1", "grade": 1, "name": "数的认识",
                 "exam_scope": "unknown", "source_observation": "found"}
        report = build_report([topic], [], [], policy, [], external_available=False)
        self.assertIsNone(report["primary"]["external_unmapped_candidate_topics"])


class MarginalCostTests(unittest.TestCase):
    def test_derivative_is_not_finite_production_difference(self):
        item = next(item for item in ITEMS if "连续变量" in item["prompt"])
        self.assertEqual(item["answer"], "7")
        self.assertIn("万元/吨", item["prompt"])
        self.assertIn("瞬时变化率", item["explanation"])
        self.assertIn("8万元", item["explanation"])
        self.assertIn("有限增量不等于导数值", item["explanation"])
        cost = lambda x: x * x + 3 * x
        self.assertEqual(cost(3) - cost(2), 8)
        self.assertNotEqual(cost(3) - cost(2), int(item["answer"]))


if __name__ == "__main__":
    unittest.main()
