"""Regression checks for V4's mathematical and evidence-boundary repairs.

These checks recompute authored examples; they do not establish educational
effectiveness or replace the separately recorded semantic cross-reviews.
"""
from fractions import Fraction
from itertools import product
import json
import math
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from cnkg.answers import grade_answer


ROOT = Path(__file__).resolve().parents[1]


def read_json(relative):
    return json.loads((ROOT / relative).read_text())


def pairs(text):
    return [(Fraction(x), Fraction(y)) for x, y in
            re.findall(r"\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)", text)]


def event_counts(records):
    return tuple(sum(flag(u, v) for u, v in records) for flag in (
        lambda u, v: u <= 3,
        lambda u, v: v <= 3,
        lambda u, v: u <= 3 and v <= 3,
        lambda u, v: u <= 3 or v <= 3,
        lambda u, v: (u <= 3) != (v <= 3),
    ))


class QualityV4ContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = {p.stem: json.loads(p.read_text())
                     for p in (ROOT / "data/learning_units").glob("*.json")}
        cls.projects = {name: read_json(f"data/projects/{name}.json") for name in (
            "senior_model_comparison", "senior_measurement_bounds",
            "senior_probability_simulation")}

    def test_observable_goals_keep_actual_tasks_in_each_learning_phase(self):
        # Nine former gaps were repaired with relevant tasks, not by treating
        # a unit-wide stage label as evidence for every objective in the unit.
        checked = 0
        for unit in self.units.values():
            for objective in unit["objectives"]:
                if objective.get("granularity") != "observable":
                    continue
                checked += 1
                with self.subTest(objective=objective["id"]):
                    phases = {task["phase"] for task in unit["tasks"]
                              if objective["id"] in task["objective_ids"]}
                    self.assertEqual(phases, {"practice", "transfer", "delayed"})
        self.assertGreater(checked, 0)

    def test_simplest_fraction_guidance_does_not_accept_equal_unreduced_answers(self):
        supports = self.units["fraction_equivalence"]["instructional_support"]["supports"]
        task = next(s["guided_task"] for s in supports
                    if s["id"] == "fraction_equivalence:support:simplify")
        self.assertEqual(grade_answer(task, "3/5")["status"], "correct")
        self.assertEqual(grade_answer(task, "6/10")["status"], "wrong")
        self.assertEqual(grade_answer(task, "27/45")["status"], "wrong")

    def test_plotted_slope_uses_axis_units_and_coordinate_pairs_are_not_a_table(self):
        tasks = {t["id"]: t for t in self.units["linear_function_representations"]["tasks"]}
        task = tasks["linear_function_representations:transfer-8"]
        svg = ET.parse(ROOT / "web" / task["diagram"]["src"].lstrip("/"))
        ns = {"s": "http://www.w3.org/2000/svg"}
        labels = svg.findall(".//s:text", ns)
        x_ticks = [(Fraction(t.attrib["x"]), Fraction(t.text)) for t in labels
                   if t.attrib.get("y") == "335"]
        y_ticks = [(Fraction(t.attrib["y"]), Fraction(t.text)) for t in labels
                   if t.attrib.get("x") == "75"]
        x_scale = (x_ticks[-1][1] - x_ticks[0][1]) / (x_ticks[-1][0] - x_ticks[0][0])
        y_scale = (y_ticks[-1][1] - y_ticks[0][1]) / (y_ticks[-1][0] - y_ticks[0][0])
        dots = svg.findall(".//s:circle", ns)
        self.assertEqual(len(dots), 2)
        dx = (Fraction(dots[1].attrib["cx"]) - Fraction(dots[0].attrib["cx"])) * x_scale
        dy = (Fraction(dots[1].attrib["cy"]) - Fraction(dots[0].attrib["cy"])) * y_scale
        self.assertEqual(Fraction(task["answer"]), dy / dx)
        self.assertEqual(dy / dx, Fraction(5, 2))
        self.assertEqual(grade_answer(task, str(dy / dx))["status"], "correct")
        self.assertEqual(tasks["linear_function_representations:delayed-7"]["representation"], "symbolic")

    def test_model_comparison_recomputes_fit_and_separate_check_residuals(self):
        project = self.projects["senior_model_comparison"]
        train_text, check_text = re.search(
            r"建模表为(.*?)；另列检验表(.*?)。", project["task_brief"]).groups()
        training, checking = pairs(train_text), pairs(check_text)
        self.assertEqual((len(training), len(checking)), (5, 2))
        xm = sum(x for x, _ in training) / len(training)
        ym = sum(y for _, y in training) / len(training)
        sxx = sum((x - xm) ** 2 for x, _ in training)
        sxy = sum((x - xm) * (y - ym) for x, y in training)
        slope = sxy / sxx
        intercept = ym - slope * xm
        train_sse = sum((y - (intercept + slope * x)) ** 2 for x, y in training)
        check_sse = sum((y - (intercept + slope * x)) ** 2 for x, y in checking)
        quadratic_sse = sum((y - (x * x + x + 1)) ** 2 for x, y in training + checking)
        self.assertEqual((slope, intercept, train_sse, check_sse, quadratic_sse), (5, -1, 14, 245, 0))
        reference = project["teacher_reference_solution"]["steps"]
        self.assertIn("b=5、a=−1", reference[0])
        self.assertIn("SSE=4+1+4+1+4=14", reference[1])
        self.assertIn("SSE=245", reference[3])
        self.assertIn("不能声称盲测或独立实测", project["task_brief"])

    def test_measurement_ranges_enclose_all_input_corners_after_rounding(self):
        project = self.projects["senior_measurement_bounds"]
        brief = project["task_brief"]
        ranges = {}
        for symbol in ("d", "θ", "e", "h", "s", "S"):
            low, high = re.search(
                re.escape(symbol) + r"∈\[([\d.]+)°?,([\d.]+)°?\]", brief).groups()
            ranges[symbol] = (float(low), float(high))
        self.assertTrue(0 < ranges["θ"][0] < ranges["θ"][1] < 90)
        self.assertGreater(ranges["s"][0], 0)
        first = [e + d * math.tan(math.radians(theta))
                 for e, d, theta in product(ranges["e"], ranges["d"], ranges["θ"])]
        second = [h * shadow / reference_shadow
                  for h, shadow, reference_shadow in product(ranges["h"], ranges["S"], ranges["s"])]
        reference = " ".join(project["teacher_reference_solution"]["steps"])
        stated = [(float(low), float(high)) for low, high in
                  re.findall(r"保守舍入为\[([\d.]+),([\d.]+)\]", reference)]
        self.assertEqual(len(stated), 2)
        for values, (low, high) in zip((first, second), stated):
            with self.subTest(interval=(low, high)):
                self.assertLessEqual(low, min(values))
                self.assertGreaterEqual(high, max(values))
                self.assertLess(min(values) - low, 0.01)
                self.assertLess(high - max(values), 0.01)
        # The denominator is largest at the lower bound: rounding an ordinary
        # central estimate is not a substitute for this interval calculation.
        self.assertAlmostEqual(min(second), 1.49 * 9.1 / 1.22)
        self.assertAlmostEqual(max(second), 1.51 * 9.3 / 1.18)

    def test_probability_records_and_equal_marginals_preserve_different_joint_mechanisms(self):
        project = self.projects["senior_probability_simulation"]
        records_i = pairs(project["materials"][0])
        sequence = project["materials"][1].split("：", 1)[1].split("；", 1)[0]
        records_ii = [(int(x), int(x)) for x in sequence.split("、")]
        self.assertEqual((len(records_i), len(records_ii)), (20, 20))
        counts_i, counts_ii = event_counts(records_i), event_counts(records_ii)
        self.assertEqual(counts_i, (10, 11, 5, 16, 11))
        self.assertEqual(counts_ii, (12, 12, 12, 12, 0))
        independent = list(product(range(1, 7), repeat=2))
        copied = [(x, x) for x in range(1, 7)]
        probabilities_i = tuple(Fraction(n, len(independent)) for n in event_counts(independent))
        probabilities_ii = tuple(Fraction(n, len(copied)) for n in event_counts(copied))
        self.assertEqual(probabilities_i, (Fraction(1, 2), Fraction(1, 2), Fraction(1, 4), Fraction(3, 4), Fraction(1, 2)))
        self.assertEqual(probabilities_ii, (Fraction(1, 2), Fraction(1, 2), Fraction(1, 2), Fraction(1, 2), 0))
        reference = project["teacher_reference_solution"]["steps"]
        self.assertIn("A发生10次、B发生11次、交集5次", reference[2])
        self.assertIn("U≤3共12次", reference[3])


if __name__ == "__main__":
    unittest.main()
