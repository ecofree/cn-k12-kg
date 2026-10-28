"""Semantic boundaries for equation, function, derivative and probability chains."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import unittest
from xml.etree import ElementTree

from cnkg.answers import grade_answer
from cnkg.contracts import validate_resource
from tools.rebuild_data import build_data
from tools.teach_cards import check_card

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TOPICS = ("m7a.5.1.2", "m7a.5.2", "m7a.5.3", "m8b.22.2", "m8b.23.1", "m8b.23.2", "m8b.23.4",
          "mh4.5.3.1", "mh4.5.3.2", "mh2.10.2")

# The same 20 formerly unmapped tasks now have explicit limited observations.
# Keep their exact reviewed targets separate from each topic's two old criteria.
COMPONENT_GOALS = {
    "m7a.5.1.2-q2": (2, "equation.solve-linear", "能求一元一次等式中的未知数"),
    "m7a.5.1.2-q4": (2, "equation.solve-linear", "能求一元一次等式中的未知数"),
    "m7a.5.1.2-q101": (3, "equation.balance-model", "能由等重天平的数量条件求每个相同物体的质量"),
    "m7a.5.1.2-q102": (2, "equation.solve-linear", "能求一元一次等式中的未知数"),
    "m7a.5.2-q102": (2, "equation.solution-substitution-parameter", "能把已知方程的解代入以求缺失参数"),
    "m8b.22.2-q1": (2, "function.representation-name-recall", "能识别函数常用的三种表示方法名称"),
    "m8b.22.2-q101": (3, "arithmetic.price-discount", "能按给定单价、数量及折扣计算总价"),
    "m8b.22.2-q102": (4, "money.tiered-charge-then-fixed-discount", "能按分段收费及固定减免规则计算实际费用"),
    "m8b.23.2-q102": (2, "linear.table-to-parameters", "能由两个已知点求一次函数在另一输入处的值"),
    "m8b.23.4-q1": (2, "linear.formula-evaluation", "能代入给定一次函数模型求数值"),
    "m8b.23.4-q2": (3, "model.domain-recall", "能识别实际模型取值范围应依据情境与适用条件"),
    "mh2.10.2-q1": (2, "probability.independence-definition", "能识别两个事件相互独立的概率等式定义"),
    "mh2.10.2-q2": (3, "probability.independent-intersection", "能在独立性已给的条件下计算两个事件同时发生的概率"),
    "mh2.10.2-q101": (3, "probability.independent-intersection", "能在独立性已给的条件下计算两个事件同时发生的概率"),
    "mh2.10.2-q102": (4, "probability.at-least-one-independent", "能在已给独立条件下计算两个事件至少一个发生的概率"),
    "mh2.10.2-q201": (3, "probability.independent-intersection", "能在独立性已给的条件下计算两个事件同时发生的概率"),
    "mh2.10.2-q202": (5, "probability.enumerated-independence", "能在已给均匀骰样本空间中计算两事件交集概率"),
    "mh4.5.3.1-q2": (2, "derivative.linear-sign", "能求已说明会变号的一次导函数的零点"),
    "mh4.5.3.1-q102": (3, "function.value-at-given-point", "能在已给极值点处代入求函数值"),
    "mh4.5.3.1-q203": (4, "derivative.stationary-candidates", "能求给定多项式导函数的指定范围零点"),
}


class SecondaryAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, _ = build_data(DATA)
        cls.bank = {q["id"]: q for q in rows["questions.json"]}
        cls.topics = {topic["id"]: topic for topic in rows["topics.json"]}
        cls.cards = {tid: json.loads((DATA / f"teach/{tid}.json").read_text()) for tid in TOPICS}
        cls.bindings = json.loads((DATA / "skills/catalog-bindings.json").read_text())["tasks"]
        cls.catalog = json.loads((DATA / "catalog.json").read_text())
        review = json.loads((ROOT / "docs/reviews/v7-observable-goal-review.json").read_text())
        cls.component_reviews = {qid: row for row in review["goals"] for qid in row["bank_task_ids"]}

    def assert_only_reviewed_component(self, qid):
        index, proposal, statement = COMPONENT_GOALS[qid]
        q = self.bank[qid]
        review = self.component_reviews[qid]
        self.assertEqual((review["criterion_index"], review["proposal_key"], review["statement"]),
                         (index, proposal, statement), qid)
        self.assertEqual(review["topic_id"], q["topicId"], qid)
        self.assertEqual(q["tested_criterion_indices"], [index], qid)
        self.assertEqual(self.topics[q["topicId"]]["mastery_criteria"][index], statement, qid)
        registered = self.catalog["tasks"][qid]
        self.assertEqual(registered["skill_ids"], [review["skill"]["id"]], qid)
        broad_ids = self.catalog["placements"][q["topicId"]]["criterion_skill_ids"][:2]
        self.assertTrue(set(broad_ids).isdisjoint(registered["skill_ids"]), qid)
        self.assertEqual(review["skill"]["refines_skill_ids"], [], qid)
        self.assertFalse(registered["policy"]["may_confirm_mastery"], qid)
        self.assertFalse(registered["policy"]["may_judge_weak"], qid)

    def test_probability_component_results_do_not_demonstrate_independence_judgement(self):
        for suffix in ("q2", "q101", "q102", "q201", "q202"):
            q = self.bank["mh2.10.2-" + suffix]
            self.assert_only_reviewed_component(q["id"])
            self.assertFalse(q["diagnosis_eligible"])
            self.assertFalse(q["for_diagnosis"])
        self.assertEqual(self.bank["mh2.10.2-q204"]["tested_criterion_indices"], [0])
        # A={1,2,3}, B={3,4,5}: same marginal sizes do not imply independence.
        self.assertNotEqual(Fraction(1, 6), Fraction(3, 6) ** 2)
        self.assertEqual(Fraction(0), Fraction(0) * Fraction(2, 5))
        self.assertIn("正概率", self.cards["mh2.10.2"]["objective_coverage"][1]["observation_note"])

    def test_component_calculations_do_not_observe_broader_goals(self):
        for qid in ("mh4.5.3.1-q102", "m8b.22.2-q102", "m8b.23.2-q102", "m8b.23.4-q1",
                    "m8b.23.4-q2", "m8b.22.2-q1", "m7a.5.1.2-q2", "m7a.5.1.2-q4",
                    "mh4.5.3.1-q2", "mh4.5.3.1-q203"):
            self.assert_only_reviewed_component(qid)
            self.assertFalse(self.bank[qid]["for_diagnosis"])
        self.assertNotIn("下表", self.bank["m8b.22.2-q102"]["prompt"])
        # V7 asks for the calculated payment, not the already supplied discount.
        charge = self.bank["m8b.22.2-q102"]
        resolved = {**charge, "answer_spec": self.bindings["bank:" + charge["id"]]["answer_spec"]}
        self.assertEqual(10 + Fraction(12, 5) * (8 - 3) - 3, 19)
        self.assertEqual(grade_answer(resolved, "19")["status"], "correct")
        self.assertEqual(grade_answer(resolved, "3")["status"], "wrong")
        self.assertNotIn("diagram", charge)
        self.assertNotIn("永远", self.bank["m8b.23.2-q102"]["explanation"])

    def test_the_piecewise_svg_and_task_require_the_same_graph_reading(self):
        q = self.bank["m8b.22.2-q202"]
        root = ElementTree.parse(ROOT / "web" / q["diagram"]["src"].lstrip("/"))
        poly = root.find(".//{http://www.w3.org/2000/svg}polyline")
        points = [tuple(map(float, pair.split(","))) for pair in poly.attrib["points"].split()]
        # Independently decode the authored axes: x=0..5, y=0..7.
        decoded = [((x - 76) * 5 / (598 - 76), (322 - y) * 7 / (322 - 68)) for x, y in points]
        for actual, expected in zip(decoded, [(0, 0), (2, 6), (5, 6)]):
            self.assertAlmostEqual(actual[0], expected[0])
            self.assertAlmostEqual(actual[1], expected[1])
        self.assertEqual(q["answer"], "6")
        self.assertEqual(self.cards["m8b.22.2"]["objective_coverage"][1]["self_check"]["diagram"]["src"], q["diagram"]["src"])
        self.assertEqual(q["family_id"], self.cards["m8b.22.2"]["objective_coverage"][1]["self_check"]["family_id"])

    def test_closed_interval_extrema_keep_all_attaining_points_and_domain(self):
        q = self.bank["mh4.5.3.2-q102"]
        values = {x: x ** 3 - 3 * x for x in (-2, -1, 1, 2)}
        self.assertEqual({x for x, y in values.items() if y == max(values.values())}, {-1, 2})
        self.assertIn("两处", q["explanation"])
        self.assertIn("0≤x≤4", self.bank["mh4.5.3.2-q101"]["prompt"])
        self.assertIn("内点", self.bank["mh4.5.3.2-q1"]["prompt"])
        self.assertIn("连续", self.cards["mh4.5.3.2"]["definition"])

    def test_complete_goal_self_check_asks_for_all_declared_components(self):
        check = self.cards["m8b.23.2"]["objective_coverage"][1]["self_check"]
        for concept in ("增减性", "x轴", "y轴"):
            self.assertIn(concept, check["prompt"])
        self.assertEqual(len(check["criteria"]), 3)
        self.assertIn("linear.slope-monotonicity", check["reference_family_ids"])
        # The same denominator example exposes solving and missed-term repair.
        checks = [row["self_check"] for row in self.cards["m7a.5.2"]["objective_coverage"]]
        self.assertIn(checks[1]["family_id"], checks[0]["reference_family_ids"])
        self.assertIn(checks[0]["family_id"], checks[1]["reference_family_ids"])

    def test_rewording_and_new_numbers_do_not_create_new_families(self):
        for a, b in (("m7a.5.1.2-q2", "m7a.5.1.2-q4"), ("m7a.5.3-q3", "m7a.5.3-q101"),
                     ("m8b.23.2-q2", "m8b.23.2-q101"), ("mh4.5.3.2-q201", "mh4.5.3.2-q202"),
                     ("mh2.10.2-q2", "mh2.10.2-q101")):
            self.assertEqual(self.bank[a]["family_id"], self.bank[b]["family_id"])
        unit = json.loads((DATA / "learning_units/equation_equivalence.json").read_text())
        model = next(task for task in unit["tasks"] if task["id"] == "equation_equivalence:practice-3")
        self.assertEqual(model["family_id"], self.bank["m8b.23.4-q101"]["family_id"])

    def test_reviewed_sources_match_content_contracts_and_accept_their_own_answers(self):
        for card in self.cards.values():
            self.assertEqual(validate_resource("teaching-card", card), [])
            self.assertEqual(check_card(card, self.topics, self.bank), [])
            for entry in card["objective_coverage"]:
                self.assertEqual(len(entry["difficulty_support"]), 3)
                self.assertTrue(entry["observation_note"])
                self.assertNotEqual(entry["self_check"]["representation"], "unknown")
        tasks = [q for q in self.bank.values() if q.get("topicId") in TOPICS and not q["id"].endswith("-q9")]
        self.assertEqual(len(tasks), 66)
        self.assertEqual(len(COMPONENT_GOALS), 20)
        self.assertEqual({q["id"] for q in tasks if q["id"] in self.component_reviews}, set(COMPONENT_GOALS))
        for qid in COMPONENT_GOALS:
            with self.subTest(component=qid):
                self.assert_only_reviewed_component(qid)
        for q in tasks:
            self.assertEqual(validate_resource("question", q), [], q["id"])
            resolved = {**q, "answer_spec": q.get("answer_spec") or self.bindings["bank:" + q["id"]]["answer_spec"]}
            self.assertEqual(grade_answer(resolved, q["answer"])["status"], "correct", q["id"])
            # A bounded component result never certifies whole-topic mastery/weakness.
            policy = self.catalog["tasks"][q["id"]]["policy"]
            self.assertFalse(policy["may_confirm_mastery"], q["id"])
            self.assertFalse(policy["may_judge_weak"], q["id"])


if __name__ == "__main__":
    unittest.main()
