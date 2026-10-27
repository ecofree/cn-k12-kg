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


class SecondaryAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, _ = build_data(DATA)
        cls.bank = {q["id"]: q for q in rows["questions.json"]}
        cls.topics = {topic["id"]: topic for topic in rows["topics.json"]}
        cls.cards = {tid: json.loads((DATA / f"teach/{tid}.json").read_text()) for tid in TOPICS}
        cls.bindings = json.loads((DATA / "skills/catalog-bindings.json").read_text())["tasks"]

    def test_given_independence_cannot_demonstrate_independence_judgement(self):
        for suffix in ("q2", "q101", "q102", "q201", "q202"):
            q = self.bank["mh2.10.2-" + suffix]
            self.assertNotIn("tested_criterion_indices", q)
            self.assertFalse(q["diagnosis_eligible"])
            self.assertFalse(q["for_diagnosis"])
        self.assertEqual(self.bank["mh2.10.2-q204"]["tested_criterion_indices"], [0])
        # A={1,2,3}, B={3,4,5}: same marginal sizes do not imply independence.
        self.assertNotEqual(Fraction(1, 6), Fraction(3, 6) ** 2)
        self.assertEqual(Fraction(0), Fraction(0) * Fraction(2, 5))
        self.assertIn("正概率", self.cards["mh2.10.2"]["objective_coverage"][1]["observation_note"])

    def test_given_extremum_or_fixed_discount_is_not_evidence_for_another_goal(self):
        for qid in ("mh4.5.3.1-q102", "m8b.22.2-q102", "m8b.23.2-q102", "m8b.23.4-q1"):
            self.assertNotIn("tested_criterion_indices", self.bank[qid])
            self.assertFalse(self.bank[qid]["for_diagnosis"])
        self.assertNotIn("下表", self.bank["m8b.22.2-q102"]["prompt"])
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
        self.assertEqual(sum(not q.get("tested_criterion_indices") for q in tasks), 17)
        for q in tasks:
            self.assertEqual(validate_resource("question", q), [], q["id"])
            resolved = {**q, "answer_spec": q.get("answer_spec") or self.bindings["bank:" + q["id"]]["answer_spec"]}
            self.assertEqual(grade_answer(resolved, q["answer"])["status"], "correct", q["id"])


if __name__ == "__main__":
    unittest.main()
