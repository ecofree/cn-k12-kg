"""已确认的内容事故回归：检验数学含义和答案边界，不以算式通过替代学科审核。"""
from __future__ import annotations

import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from cnkg.verify import compare, compare_short, evaluate  # noqa: E402
from cnkg.evidence import question_policy  # noqa: E402
from tools.rebuild_data import build_data  # noqa: E402
from tools.teach_cards import check_card  # noqa: E402


REPAIRED_IDS = {
    "m6a.1.2-q102", "m6a.6.2-q101", "m6a.6.3-q101", "mh5.7.5-q102",
    "mh5.8.3.2-q102", "mh5.6.1-q102", "m6a.6.3-q102", "m4a.3.4-q102",
    "m5a.7.1-q101", "m5a.7.1-q102", "mh5.7.5-q101", "mh5.8.3.2-q101",
    "m3a.2.4-q101", "m3a.2.4-q102", "m3a.2.6-q101", "m3a.2.6-q102",
    "m7a.4.1-q101", "m7a.4.1-q102", "m7b.12.1.1-q101", "m7b.12.1.1-q102",
    "m5a.2.3-q102", "m6a.5.3-q101", "m5a.3.1-q102", "m7a.2.2.2-q101",
    "m6a.6.2-q102", "m6a.4.3-q103", "m7a.2.2.1-q101",
}
REPAIRED_CARDS = {"m5a.7.1", "m6a.6.2", "m6a.6.3", "m5a.2.1"}

# V5 removed these specific false criterion mappings while retaining useful
# support practice. This is not a blanket exception for unmapped completion
# items: each entry states the actual observation and the rejected target.
ELECTIVE_SUPPORT_ONLY = {
    "mh5.8.1.1-q202": {
        "family_id": "statistics.scatter-nonlinearity",
        "prompt_terms": ("图丙", "五个点", "初步描述"),
        "correct_choice": "可能有非线性关系",
        "reason_terms": ("先降后升", "单一线性", "无关系"),
    },
    "mh5.8.1.1-q203": {
        "family_id": "statistics.observational-causality",
        "prompt_terms": ("冰淇淋", "溺水", "单靠这些数据"),
        "correct_choice": "样本有相关趋势，可能有气温等共同因素",
        "reason_terms": ("因果", "函数", "相关关系"),
    },
    "mh5.8.1.2-q203": {
        "family_id": "statistics.correlation-sign-and-domain",
        "prompt_terms": ("x值都相同", "相关系数公式"),
        "correct_choice": "分母为0，r未定义",
        "reason_terms": ("分母", "未定义"),
    },
}

# Each additional exception was checked against the actual retained objective,
# not inferred from the suffix or relaxed for the whole completion batch.
ELECTIVE_COMPONENT_ONLY = {
    'mh3.1.4.1-q203': ('space.normal-vectors-plane-perpendicular', '一定垂直', ('法向量', '点积0', '证明过程')),
    'mh3.2.2.2-q201': ('line.two-point-evaluation', '13', ('纵坐标', '两点式')),
    'mh3.2.2.2-q204': ('line.two-point-intercept', '5', ('截距5', '两点式')),
    'mh3.2.2.3-q203': ('line.general-equation-nondegenerate', '0x+3y-6=0', ('斜率', '截距')),
    'mh3.2.3.4-q204': ('line.coincident-distance-boundary', '0', ('重合', '退化边界')),
    'mh4.4.2.1-q202': ('sequence.arithmetic-difference-from-indices', '3', ('公差3', '指定项')),
    'mh4.4.4-q203': ('induction.next-term-identification', '9', ('新增项', '归纳证明')),
    'mh4.5.2.2-q204': ('derivative.product-rule-recall', 'u′v+uv′', ('选择', '未实际使用')),
    'mh4.5.3.1-q203': ('derivative.stationary-candidates', '2', ('正根2', '单调区间')),
    'mh5.6.2.1-q201': ('counting.permutation-count', '6', ('未实际列', '排列')),
    'mh5.6.2.1-q204': ('counting.permutation-count', '6', ('未实际列', '排列')),
    'mh5.6.3.2-q204': ('binomial.central-maximum', 'C₈⁴', ('最大值', '对称性', '系数和')),
    'mh5.7.2-q204': ('probability.given-distribution-tail-sum', '0.8', ('读表', '写表', '合法性')),
    'mh5.7.4.1-q204': ('probability.binomial-point-mass', '0.375', ('点概率', '均值方差')),
}


class ContentRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = json.loads((ROOT / "data/questions.json").read_text())
        cls.bank = {q["id"]: q for q in cls.questions}
        cls.catalog_tasks = json.loads((ROOT / "data/catalog.json").read_text())["tasks"]

    def test_component_calculation_does_not_certify_proof_inquiry_or_simulation(self):
        for topic in ("mh4.4.4", "mh2.9.3", "mh2.10.3.2"):
            for number in (101, 102):
                question = self.bank[f"{topic}-q{number}"]
                policy = question_policy(question)
                self.assertFalse(policy["diagnosis_eligible"])
                self.assertTrue(policy["practice_eligible"])
                self.assertEqual(question["target_alignment"], "component_practice_only")

    def test_elective_completion_items_remain_practice_until_objective_aware_diagnosis(self):
        rows = [q for q in self.questions if q.get("completion_batch") == "senior-elective-2026-10"]
        self.assertEqual(len(rows), 189)
        support_seen = set()
        for question in rows:
            with self.subTest(question=question['id']):
                self.assertFalse(question['diagnosis_eligible'])
                self.assertFalse(question['for_diagnosis'])
                self.assertEqual(question['target_alignment'], 'component_practice_only')
                policy = question_policy(question)
                self.assertFalse(policy['diagnosis_eligible'])
                self.assertTrue(policy['practice_eligible'])
                self.assertFalse(policy['may_confirm_mastery'])
                self.assertFalse(policy['may_judge_weak'])
                support = ELECTIVE_SUPPORT_ONLY.get(question['id'])
                component = ELECTIVE_COMPONENT_ONLY.get(question['id'])
                if component is not None:
                    family, answer, reason_terms = component
                    support = {'family_id': family, 'prompt_terms': (),
                               'reason_terms': reason_terms, 'canonical_answer': answer}
                if support is None:
                    # All other items still require a real explicit mapping.
                    self.assertTrue(question['tested_criterion_indices'])
                    continue
                support_seen.add(question['id'])
                self.assertNotIn('tested_criterion_indices', question)
                self.assertEqual(question['family_id'], support['family_id'])
                for term in support['prompt_terms']:
                    self.assertIn(term, question['prompt'])
                correct = (next(choice['text'] for choice in question['choices']
                                if choice['id'] == question['answer'])
                           if question['type'] == 'choice' else question['answer'])
                self.assertEqual(correct, support.get('correct_choice', support.get('canonical_answer')))
                for term in support['reason_terms']:
                    self.assertIn(term, question['alignment_note'])
                task = self.catalog_tasks[question['id']]
                self.assertEqual(task['alignment'], 'topic_only')
                self.assertEqual(task['skill_ids'], [])
                self.assertEqual(task['topic_ids'], [question['topicId']])
                self.assertEqual(task['purpose'], ['practice'])
                self.assertEqual(task['availability'], 'active')
                self.assertTrue(task['policy']['practice_eligible'])
                for field in ('diagnosis_eligible', 'may_confirm_mastery',
                              'may_judge_weak', 'independent_evidence'):
                    self.assertFalse(task['policy'][field])
        self.assertEqual(support_seen, set(ELECTIVE_SUPPORT_ONLY) | set(ELECTIVE_COMPONENT_ONLY))

    def test_advanced_clock_problem_requires_relative_motion_and_exact_fraction(self):
        question = self.bank["m7a.6.3.1-q102"]
        minutes = 90 / (6 - 0.5)
        self.assertTrue(compare(question["answer"], minutes))
        self.assertAlmostEqual(6 * minutes, 90 + 0.5 * minutes)
        self.assertGreater(minutes, 0)
        self.assertLess(minutes, 60)

    def test_wrong_bearing_is_rejected_and_equivalent_bearing_is_accepted(self):
        q = self.bank["m6a.1.2-q102"]
        # 北偏东40度的反向方位角为220度；从西向南的偏角是270-220=50。
        reverse_bearing = (40 + 180) % 360
        self.assertEqual(270 - reverse_bearing, 50)
        for answer in ("南偏西40°", "西偏南50°", "西偏南50度"):
            with self.subTest(answer=answer):
                self.assertTrue(compare_short(answer, q["answer"], q["accept"]))
        for answer in ("西偏南40°", "南偏西50°", "南偏西"):
            with self.subTest(answer=answer):
                self.assertFalse(compare_short(answer, q["answer"], q["accept"]))

    def test_rounded_population_answer_matches_the_requested_integer(self):
        q = self.bank["mh5.7.5-q102"]
        self.assertEqual(q["type"], "numeric")
        self.assertIn("四舍五入取整数", q["prompt"])
        expected = math.floor(1000 * 0.6827 + 0.5)
        self.assertEqual(expected, 683)
        self.assertTrue(compare(q["answer"], expected))
        self.assertEqual(evaluate(q["verify"]), expected)
        self.assertFalse(compare("682.7", q["answer"]))

    def test_contingency_table_is_real_and_its_statistic_uses_that_table(self):
        q = self.bank["mh5.8.3.2-q102"]
        rows = re.findall(r"\((?:a,b|c,d)\)=\((\d+),(\d+)\)", q["prompt"])
        self.assertEqual(len(rows), 2, "题干应给出完整的两行整数频数")
        (a, b), (c, d) = [[int(v) for v in row] for row in rows]
        self.assertTrue(all(v >= 0 for v in (a, b, c, d)))
        n = a + b + c + d
        self.assertEqual(n, 100)
        self.assertEqual([a + b, c + d, a + c, b + d], [50] * 4)
        # 独立重算Pearson的四格统计量，而非复制题库verify字符串。
        expected_cells = [(a + b) * (a + c) / n, (a + b) * (b + d) / n,
                          (c + d) * (a + c) / n, (c + d) * (b + d) / n]
        chi_square = sum((observed - expected) ** 2 / expected
                         for observed, expected in zip((a, b, c, d), expected_cells))
        self.assertEqual(chi_square, 4)
        self.assertTrue(compare(q["answer"], chi_square))

    def test_group_comparison_contains_both_groups_in_its_own_prompt(self):
        q = self.bank["m6a.6.3-q102"]
        groups = ([82, 90, 85, 88, 95], [76, 92, 84, 96])
        for value in sum((list(g) for g in groups), []):
            self.assertIn(str(value), q["prompt"])
        self.assertNotIn("另四名", q["prompt"])
        means = [sum(g) / len(g) for g in groups]
        self.assertTrue(compare(q["answer"], abs(means[0] - means[1])))

    def test_return_route_constraints_agree_with_counting(self):
        q = self.bank["mh5.6.1-q102"]
        self.assertNotIn("原路返回", q["prompt"])
        self.assertIn("每一段", q["prompt"])
        # 枚举两段去程与回程，各段回程必须不同于去程。
        count = sum(1 for outward_ab in range(3) for outward_bc in range(4)
                    for return_cb in range(4) for return_ba in range(3)
                    if outward_ab != return_ba and outward_bc != return_cb)
        self.assertTrue(compare(q["answer"], count))

    def test_false_rules_are_not_taught_in_repaired_explanations(self):
        forbidden = {
            "m6a.6.2-q101": "折扣数越大价格越低",
            "m6a.6.3-q101": "不会被某一个最高分或最低分带偏",
            "m4a.3.4-q102": "被乘数有几位，结果就是几位",
        }
        for qid, statement in forbidden.items():
            with self.subTest(question=qid):
                self.assertNotIn(statement, self.bank[qid]["explanation"])
        self.assertIn("位数比被乘数多1", self.bank["m4a.3.4-q102"]["explanation"])

    def test_same_level_operation_items_do_not_require_multiplication(self):
        for qid in ("m3a.2.4-q101", "m3a.2.4-q102"):
            q = self.bank[qid]
            with self.subTest(question=qid):
                self.assertNotRegex(q["verify"], r"[*/]")
                self.assertTrue(compare(q["answer"], evaluate(q["verify"])))

    def test_polynomial_degree_counts_all_exponents_in_a_term(self):
        q = self.bank["m7a.4.1-q102"]
        self.assertIn("3x²y³-4xy+7", q["prompt"])
        monomials = [(2, 3), (1, 1), (0, 0)]
        degree = max(sum(exponents) for exponents in monomials)
        self.assertTrue(compare(q["answer"], degree))
        self.assertFalse(compare(q["answer"], 3))

    def test_cards_explain_probability_and_rate_conditions(self):
        chance = json.loads((ROOT / "data/teach/m5a.7.1.json").read_text())
        self.assertIn("不放回会改变", chance["why"])
        self.assertIn("每次放回", chance["why"])
        self.assertIn("每个球被摸到的机会相同", chance["worked_example"]["prompt"])
        percent = json.loads((ROOT / "data/teach/m6a.6.2.json").read_text())
        text = json.dumps(percent, ensure_ascii=False)
        self.assertNotIn("比率不会", text)
        self.assertIn("可以超过100%", text)

    def test_threshold_card_does_not_turn_a_statistic_into_a_universal_rule(self):
        card = json.loads((ROOT / "data/teach/m6a.6.3.json").read_text())
        self.assertIn("不能自动决定达标线", card["definition"])
        self.assertIn("受极端值影响", card["why"])
        self.assertIn("人人达标也有意义", card["why"])
        self.assertIn("事先约定", card["worked_example"]["prompt"])

    def test_mean_may_or_may_not_equal_an_observed_value(self):
        card = json.loads((ROOT / "data/teach/clusters/p-stat@四下.json").read_text())
        self.assertNotIn("它不代表其中任何一个数", card["check"])
        self.assertIn("可能等于某个原数据", "".join(card["key_points"]))
        equal_example, unequal_example = [2, 4, 6], [2, 4]
        self.assertIn(sum(equal_example) / len(equal_example), equal_example)
        self.assertNotIn(sum(unequal_example) / len(unequal_example), unequal_example)
        self.assertTrue(card["reference_only"])
        self.assertIn("辅助速查", card["coverage_scope"])

    def test_repaired_questions_rebuild_exactly_from_active_source(self):
        built, _ = build_data()
        rebuilt = {q["id"]: q for q in built["questions.json"]}
        for qid in REPAIRED_IDS:
            with self.subTest(question=qid):
                self.assertEqual(self.bank[qid], rebuilt[qid])

    def test_repaired_cards_are_valid_authoritative_json_without_writes(self):
        topics = {t["id"]: t for t in json.loads((ROOT / "data/topics.json").read_text())}
        for tid in REPAIRED_CARDS:
            with self.subTest(card=tid):
                path = ROOT / "data/teach" / f"{tid}.json"
                before = path.read_bytes()
                stored = json.loads(before)
                self.assertEqual(check_card(stored, topics, self.bank), [])
                self.assertEqual(path.read_bytes(), before)

    def test_completed_primary_cards_keep_current_objective_bindings(self):
        # These 33 authored resources replaced the historical completion writer.
        # Their canonical JSON is checked directly, not regenerated from a
        # second Python copy of the same teaching text.
        identifiers = "m1a.2.5 m1a.2.6 m1a.2.8 m1a.3.2 m1a.3.3 m1a.4.4 m1a.5.4 m1a.5.5 m1b.1.2 m1b.1.3 m1b.2.4 m1b.4.3 m1b.6.1 m1b.6.2 m1b.6.3 m2a.2.6 m2a.2.7 m2a.3.7 m2a.4.4 m2a.5.6 m2b.1.3 m2b.2.2 m2b.4.3 m3a.1.2 m3a.1.3 m3a.6.4 m3b.6.2 m4a.5.2 m4b.3.1 m5a.1.1 m5a.4.4 m5b.4.1 m6a.5.4".split()
        topics = {t["id"]: t for t in json.loads((ROOT / "data/topics.json").read_text())}
        for tid in identifiers:
            with self.subTest(card=tid):
                stored = json.loads((ROOT / "data/teach" / f"{tid}.json").read_text())
                self.assertEqual(check_card(stored, topics, self.bank), [])
                goals = topics[tid]["mastery_criteria"]
                self.assertEqual([row["title"] for row in stored["objective_coverage"]], goals)


if __name__ == "__main__":
    unittest.main()
