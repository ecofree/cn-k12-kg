"""Confirmed mathematical counterexamples and explicit boundaries for the V3 statistics chain."""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import unittest

from cnkg.answers import grade_answer
from cnkg.evidence import grade, question_policy
from cnkg.learning import check_unit

ROOT = Path(__file__).resolve().parents[1]
UNIT_IDS = ("data_reading_and_mean", "sampling_and_variation", "correlation_and_regression")


def mean(values):
    return sum(map(Fraction, values), Fraction()) / len(values)


def variance(values):
    centre = mean(values)
    return sum((Fraction(x) - centre) ** 2 for x in values) / len(values)


class QualityV3ContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = {uid: json.loads((ROOT / "data/learning_units" / f"{uid}.json").read_text())
                     for uid in UNIT_IDS}
        cls.tasks = {t["id"]: t for u in cls.units.values() for t in u["tasks"]}
        cls.app = {q["id"]: q for q in json.loads(
            (ROOT / "data/question_sources/application.json").read_text())["questions"]}

    def test_new_objectives_have_explicit_evidence_in_every_phase_without_approval_claims(self):
        topics = json.loads((ROOT / "data/topics.json").read_text())
        standards = json.loads((ROOT / "data/standards.json").read_text())
        for unit in self.units.values():
            with self.subTest(unit=unit["id"]):
                self.assertEqual(check_unit(unit, topics, standards), [])
                self.assertEqual(unit["teacher_review"], {"status": "pending"})
                for objective in unit["objectives"]:
                    self.assertEqual(objective["granularity"], "observable")
                    phases = {task["phase"] for task in unit["tasks"]
                              if objective["id"] in task["objective_ids"]}
                    self.assertEqual(phases, {"practice", "transfer", "delayed"})
                for task in unit["tasks"]:
                    self.assertTrue(task["family_id"])
                    self.assertTrue(task["answer_spec"])
                    if task["type"] != "rubric":
                        self.assertEqual(grade_answer(task, task["answer"])["status"], "correct")

    def test_fraction_variance_and_signed_residual_do_not_accept_convenient_wrong_values(self):
        cases = {
            "sampling_and_variation:practice-4": ("4/6", "0.667"),
            "correlation_and_regression:practice-3": ("2", "-2"),
            "correlation_and_regression:transfer-3": ("-1", "1"),
            "correlation_and_regression:delayed-3": ("-3", "9"),
            "correlation_and_regression:practice-4": ("9/25", "0.6"),
        }
        for task_id, (right, wrong) in cases.items():
            with self.subTest(task=task_id):
                self.assertEqual(grade_answer(self.tasks[task_id], right)["status"], "correct")
                self.assertEqual(grade_answer(self.tasks[task_id], wrong)["status"], "wrong")

    def test_unequal_group_means_counterexample_uses_people_not_group_count(self):
        values = [8] * 2 + [0] * 6
        actual = mean(values)
        wrong = mean([8, 0])
        self.assertEqual(actual, 2)
        self.assertEqual(wrong, 4)
        task = self.tasks["data_reading_and_mean:delayed-3"]
        self.assertIn("2人", task["prompt"])
        self.assertIn("6人", task["prompt"])
        self.assertIn("平均2本", task["explanation"])
        # This requires an explanation, not an automatic numeric certification.
        self.assertEqual(task["type"], "rubric")
        self.assertEqual(grade_answer(task, "平均2本，但我尚未说明为什么")["status"], "needs_review")

    def test_missing_record_and_recorded_zero_are_different_statistical_inputs(self):
        task = self.tasks["data_reading_and_mean:delayed-5"]
        known = [2, 4, 6, 8]
        self.assertEqual(mean(known), 5)
        self.assertEqual(mean(known + [0]), 4)
        self.assertEqual(mean(known + [10]), 6)
        self.assertIn("缺失值不是已确认的0", task["explanation"])
        recorded_zero = self.tasks["data_reading_and_mean:delayed-2"]
        self.assertEqual(Fraction(recorded_zero["answer"]), mean([0, 6, 12]))

    def test_equal_range_and_mean_do_not_determine_variance(self):
        a, b = [0, 5, 5, 10], [0, 0, 10, 10]
        self.assertEqual(mean(a), mean(b))
        self.assertEqual(max(a) - min(a), max(b) - min(b))
        self.assertEqual(variance(a), Fraction(25, 2))
        self.assertEqual(variance(b), 25)
        text = self.tasks["sampling_and_variation:transfer-4"]["explanation"]
        self.assertIn("12.5", text)
        self.assertIn("乙25", text)

    def test_ols_fit_and_residuals_recomputed_from_paired_data(self):
        xs, ys = list(map(Fraction, (0, 1, 2))), list(map(Fraction, (1, 2, 6)))
        xm, ym = mean(xs), mean(ys)
        sxx = sum((x - xm) ** 2 for x in xs)
        sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
        slope = sxy / sxx
        intercept = ym - slope * xm
        residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
        self.assertEqual(slope, Fraction(5, 2))
        self.assertEqual(intercept, Fraction(1, 2))
        self.assertEqual(sum(residuals), 0)
        self.assertEqual(residuals[1], -1)
        for tid, result in (("correlation_and_regression:transfer-3", residuals[1]),):
            self.assertEqual(Fraction(self.tasks[tid]["answer"]), result)

    def test_transfer_fit_uses_different_data_from_the_worked_example(self):
        xs, ys = list(map(Fraction, (1, 2, 3))), list(map(Fraction, (2, 4, 8)))
        xm, ym = mean(xs), mean(ys)
        slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / sum((x - xm) ** 2 for x in xs)
        self.assertEqual(slope, 3)
        intercept = ym - slope * xm
        self.assertEqual(intercept, Fraction(-4, 3))
        self.assertEqual(Fraction(self.tasks["correlation_and_regression:transfer-2"]["answer"]), intercept)
        example = self.units["correlation_and_regression"]["worked_examples"][1]
        self.assertIn("(0,2)、(1,7)、(2,6)", example["prompt"])
        self.assertIn("ŷ=3+2x", example["answer"])

    def test_equal_group_means_allow_unequal_group_sizes(self):
        self.assertEqual(mean([4] * 2 + [4] * 7), mean([4, 4]))
        conditions = "".join(self.units["data_reading_and_mean"]["explanation"]["conditions"])
        self.assertNotIn("只有两组份数相等", conditions)
        self.assertIn("平均数本来相同", conditions)

    def test_zero_correlation_curve_has_nonzero_variance_and_actual_function_relation(self):
        xs = list(map(Fraction, (-2, -1, 0, 1, 2)))
        ys = [x ** 2 for x in xs]
        xm, ym = mean(xs), mean(ys)
        cross = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
        self.assertEqual(cross, 0)
        self.assertGreater(variance(xs), 0)
        self.assertGreater(variance(ys), 0)
        task = self.tasks["correlation_and_regression:transfer-1"]
        self.assertIn("y=x²", task["explanation"])
        undefined = self.tasks["correlation_and_regression:delayed-1"]
        answer = next(c["text"] for c in undefined["choices"] if c["id"] == undefined["answer"])
        self.assertIn("未定义", answer)

    def test_no_intercept_fit_breaks_the_unqualified_r_squared_identity(self):
        xs, ys = list(map(Fraction, (1, 2))), list(map(Fraction, (3, 4)))
        xm, ym = mean(xs), mean(ys)
        sxx = sum((x - xm) ** 2 for x in xs)
        syy = sum((y - ym) ** 2 for y in ys)
        sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
        r_squared = sxy * sxy / (sxx * syy)
        slope_through_origin = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
        sse = sum((y - slope_through_origin * x) ** 2 for x, y in zip(xs, ys))
        self.assertEqual(r_squared, 1)
        self.assertEqual(slope_through_origin, Fraction(11, 5))
        self.assertEqual(1 - sse / syy, Fraction(-3, 5))
        task = self.tasks["correlation_and_regression:transfer-4"]
        self.assertIn("−0.6", task["explanation"])
        self.assertIn("中心化定义", task["explanation"])

    def test_tail_count_does_not_identify_a_percentile_under_the_declared_convention(self):
        # 20% strictly above 80, yet the repository's explicit 80th-percentile
        # convention gives 75. This reproduces the former false equivalence.
        data = [60] * 400 + [90] * 100
        self.assertEqual(Fraction(sum(x > 80 for x in data), len(data)), Fraction(1, 5))
        k = len(data) * 80 // 100
        percentile = Fraction(data[k - 1] + data[k], 2)
        self.assertEqual(percentile, 75)
        q = self.app["mh2.9.2.2-q102"]
        self.assertNotIn("等价于说", q["explanation"])
        self.assertIn("不能据此断定", q["explanation"])
        self.assertIn("约定", self.app["mh2.9.2.2-q101"]["prompt"])

    def test_logic_and_regression_repair_conditions_are_in_question_not_just_answer(self):
        q = self.app["mh1.1.4.2-q101"]
        self.assertIn("当且仅当", q["prompt"])
        self.assertEqual(q["answer"], "充要条件")
        # Necessity alone permits p=true,q=false, so cannot establish iff.
        allowed_by_necessity = [(p, q) for p in (False, True) for q in (False, True)
                                if not q or p]
        self.assertIn((True, False), allowed_by_necessity)
        self.assertIn("只给出了q⇒p", q["explanation"])
        for qid in ("mh5.8.1.2-q102", "mh5.8.2.2-q101", "mh5.8.2.2-q102"):
            self.assertIn("含截距", self.app[qid]["prompt"])
            self.assertIn("最小二乘", self.app[qid]["prompt"])
        self.assertNotIn("剩下 19% 是其他因素", self.app["mh5.8.1.2-q101"]["explanation"])
        self.assertIn("预测误差", self.app["mh5.8.2.1-q101"]["explanation"])

    def test_corrected_explanations_do_not_keep_stale_derived_evidence_signals(self):
        corrected = (
            "mh1.1.4.2-q101", "mh2.9.1.3-q102", "mh2.9.2.1-q102",
            "mh2.9.2.2-q101", "mh2.9.2.2-q102", "mh5.8.1.1-q101",
            "mh5.8.1.2-q101", "mh5.8.1.2-q102", "mh5.8.2.1-q101",
            "mh5.8.2.2-q101", "mh5.8.2.2-q102",
        )
        for qid in corrected:
            with self.subTest(question=qid):
                q = self.app[qid]
                evidence = grade(q)
                self.assertEqual(q["evidence_signals"], evidence["signals"])
                self.assertEqual(q["evidence_level"], evidence["level"])
                self.assertEqual(q["evidence_notes"], evidence["notes"])

    def test_spatial_edits_keep_uses_without_mislabeling_explanation_evidence(self):
        cases = (("sanshang", "m3a.1.2-q3", True),
                 ("jiuxia", "m9b.33.2.2-q5", False))
        for name, qid, diagnosis_before in cases:
            data = json.loads((ROOT / "data/content" / f"{name}.json").read_text())
            q = next(q for q in data["questions"] if q["id"] == qid)
            with self.subTest(question=qid):
                evidence = grade(q)
                self.assertEqual(evidence["level"], "explained")
                self.assertTrue(evidence["signals"]["has_steps"])
                policy = question_policy(q)
                self.assertTrue(policy["practice_eligible"])
                self.assertEqual(policy["diagnosis_eligible"], diagnosis_before)
                self.assertEqual(policy["may_confirm_mastery"], diagnosis_before)
                self.assertFalse(policy["may_judge_weak"])
                self.assertTrue(policy["reviewer_pending"])
                if qid == "m9b.33.2.2-q5":
                    self.assertIs(q["diagnosis_eligible"], False)
                    self.assertIs(q["for_diagnosis"], False)

    def test_spatial_reverse_inference_is_limited_to_declared_candidates(self):
        for name, qid, answer in (("sanshang", "m3a.1.2-q3", "球"),
                                 ("jiuxia", "m9b.33.2.2-q5", "圆锥")):
            data = json.loads((ROOT / "data/content" / f"{name}.json").read_text())
            q = next(q for q in data["questions"] if q["id"] == qid)
            self.assertIn("候选", q["prompt"])
            self.assertIn("水平", q["prompt"])
            self.assertEqual(q["answer"], answer)
            self.assertIn("任意立体", q["explanation"])


if __name__ == "__main__":
    unittest.main()
