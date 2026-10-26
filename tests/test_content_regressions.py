"""已确认的内容事故回归：检验数学含义和答案边界，不以算式通过替代学科审核。"""
from __future__ import annotations

import importlib
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import app_items  # noqa: E402
import write_application_items as writer  # noqa: E402
from cnkg.verify import compare, compare_short, evaluate  # noqa: E402


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


class ContentRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = json.loads((ROOT / "data/questions.json").read_text())
        cls.bank = {q["id"]: q for q in cls.questions}

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
        source = importlib.import_module("tools.teach_brief_2")
        generated = next(c for c in source.CARDS
                         if c["cluster"] == "p-stat" and c["volume"] == "四下")
        self.assertEqual(card, generated)

    def test_repaired_questions_rebuild_exactly_from_active_source(self):
        generated, errors = writer.build(
            app_items.all_items(),
            [q for q in self.questions if q.get("source") != writer.SOURCE],
        )
        self.assertEqual(errors, [])
        rebuilt = {q["id"]: q for q in generated}
        for qid in REPAIRED_IDS:
            with self.subTest(question=qid):
                self.assertEqual(self.bank[qid], rebuilt[qid])

    def test_repaired_cards_rebuild_exactly_without_touching_other_cards(self):
        sources = [importlib.import_module("tools.teach_primary_3"),
                   importlib.import_module("tools.teach_primary_4")]
        source_cards = {c["topicId"]: c for source in sources for c in source.CARDS}
        for tid in REPAIRED_CARDS:
            with self.subTest(card=tid):
                stored = json.loads((ROOT / "data/teach" / f"{tid}.json").read_text())
                self.assertEqual(stored, source_cards[tid])


if __name__ == "__main__":
    unittest.main()
