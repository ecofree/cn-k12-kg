"""Real grading regressions and bounded exact mathematical equivalence."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

from cnkg.answers import grade_answer, infer_answer_spec, validate_answer_spec
from cnkg.graph import load_graph
from cnkg.learning import grade_task
from cnkg.verify import to_fraction
from tools.query import _run_session
from web.server import judge

ROOT = Path(__file__).resolve().parents[1]


def task(answer, kind="number", **spec):
    return {"type": "numeric" if kind == "number" else "short", "answer": answer,
            "answer_spec": {"version": 1, "kind": kind, **spec}}


class ExactNumberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = {q["id"]: q for q in json.loads((ROOT / "data/questions.json").read_text())}

    def test_small_quantity_has_no_absolute_tolerance_floor(self):
        question = self.questions["m8a.18.4-q101"]
        self.assertEqual(judge(question, "0.0000006"), "correct")
        for value in ("0", "0.0000001", "0.0000016", "-0.0000006"):
            self.assertEqual(judge(question, value), "wrong", value)

    def test_large_place_value_requires_every_digit(self):
        for qid in ("m4a.1.2-q90", "m4a.1.2-q91", "m4a.1.2-q92", "m4a.1.2-q93"):
            q = self.questions[qid]
            self.assertEqual(judge(q, q["answer"]), "correct")
            self.assertEqual(judge(q, str(int(q["answer"]) + 1)), "wrong")
        big = task("9007199254740993")
        self.assertEqual(grade_answer(big, "9007199254740992")["status"], "wrong")
        self.assertEqual(grade_answer(big, "9007199254740993.0")["status"], "correct")

    def test_exact_fraction_equivalence_and_nearby_non_equivalence(self):
        q = task("1/3")
        for value in ("2/6", "+1 / +3", "−1/-3"):
            self.assertEqual(grade_answer(q, value)["status"], "correct")
        for value in ("0.3333333333333333", "0.333333", "1/3000000"):
            self.assertEqual(grade_answer(q, value)["status"], "wrong")
        self.assertEqual(to_fraction("100000000000000000001") - to_fraction("100000000000000000000"), 1)

    def test_migration_does_not_confuse_mentioned_fraction_with_requested_component(self):
        for qid in ("m5b.2.1-q1", "m6a.2.2-q90", "m6a.2.2-q91", "m6a.2.2-q92", "m6a.2.2-q93", "mh1.4.1.1-q202"):
            q = self.questions[qid]
            self.assertEqual(grade_answer(q, q["answer"])["status"], "correct", qid)

    def test_explicit_tolerance_has_no_implicit_relative_component(self):
        q = task("1000000000", comparison="tolerance", absolute_tolerance="0.01")
        self.assertEqual(grade_answer(q, "1000000000.01")["status"], "correct")
        self.assertEqual(grade_answer(q, "1000000000.02")["status"], "wrong")
        q = task("0.0000006", comparison="tolerance", relative_tolerance="0.01")
        self.assertEqual(grade_answer(q, "0")["status"], "wrong")
        self.assertEqual(grade_answer(q, "0.000000601")["status"], "correct")

    def test_rounding_and_required_decimal_form_are_distinct(self):
        q = task("1.01", comparison="rounded", decimal_places=2, rounding="half_up")
        self.assertEqual(grade_answer(q, "1.005")["status"], "correct")
        self.assertEqual(grade_answer(q, "1.0049")["status"], "wrong")
        q["answer_spec"]["representation"] = "decimal"
        self.assertEqual(grade_answer(q, "1.005")["reason"], "representation")
        self.assertEqual(grade_answer(q, "1.01")["status"], "correct")
        negative = task("-1.01", comparison="rounded", decimal_places=2)
        self.assertEqual(grade_answer(negative, "-1.005")["status"], "correct")
        even = task("1.00", comparison="rounded", decimal_places=2, rounding="half_even")
        self.assertEqual(grade_answer(even, "1.005")["status"], "correct")

    def test_representation_contracts_are_preserved(self):
        q = task("3/4", representation="simplest_fraction")
        self.assertEqual(grade_answer(q, "3/4")["status"], "correct")
        for value in ("6/8", "0.75", "-3/-4"):
            self.assertEqual(grade_answer(q, value)["reason"], "representation")
        scientific = task("0.000006", representation="scientific")
        self.assertEqual(grade_answer(scientific, "6e-6")["status"], "correct")
        self.assertEqual(grade_answer(scientific, "60e-7")["reason"], "representation")
        self.assertEqual(grade_answer(scientific, "0.000006")["reason"], "representation")

    def test_invalid_numeric_input_does_not_reach_math_evaluation(self):
        for raw in (True, None, {}, [], "1/0", "nan", "inf", "1e9999", "2+3",
                    "__import__('os').system('echo NO')", "1" * 129):
            result = grade_answer(task("5"), raw)
            self.assertEqual(result["status"], "invalid", raw)
            self.assertNotIn("answer", result)


class StructuredAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = {q["id"]: q for q in json.loads((ROOT / "data/questions.json").read_text())}

    def test_actual_coordinate_question_accepts_equivalent_components(self):
        q = self.questions["m7b.9.1.1-q102"]
        for value in ("(4.0,4.0)", "（8/2，12/3）", "(+4,4)"):
            self.assertEqual(judge(q, value), "correct", value)
        self.assertEqual(judge(q, "(4,5)"), "wrong")
        self.assertEqual(judge(q, "(4,4,4)"), "invalid")

    def test_coordinate_order_and_number_set_order_are_different(self):
        q = task("(2,3)", kind="coordinate", dimensions=2)
        self.assertEqual(grade_answer(q, "(3,2)")["status"], "wrong")
        q = task("{-2,3}", kind="number_set")
        self.assertEqual(grade_answer(q, "{3.0,-4/2}")["status"], "correct")
        self.assertEqual(grade_answer(q, "{-2,3,4}")["status"], "wrong")
        q = task("-2,3", kind="number_list", order="ascending")
        self.assertEqual(grade_answer(q, "3,-2")["status"], "wrong")

    def test_interval_preserves_endpoints_and_open_closed_conditions(self):
        q = task("2<x≤5", kind="interval")
        for value in ("(2,5]", "5>=x>2", "2.0<x<=10/2"):
            self.assertEqual(grade_answer(q, value)["status"], "correct", value)
        for value in ("[2,5]", "(2,5)", "x>2"):
            self.assertEqual(grade_answer(q, value)["status"], "wrong", value)
        half_line = task("x>2", kind="interval")
        self.assertEqual(grade_answer(half_line, "(2,+∞)")["status"], "correct")
        self.assertEqual(grade_answer(half_line, "(2,+∞]")["status"], "invalid")

    def test_actual_factorization_accepts_factor_order_and_sign_changes(self):
        q = self.questions["m8a.17.1-q201"]
        for value in ("3yx(2x-3y)", "(3y-2x)*(-3xy)", "3*x*y*(2*x-3*y)"):
            self.assertEqual(judge(q, value), "correct", value)
        self.assertEqual(judge(q, "3xy(2x+3y)"), "wrong")
        self.assertEqual(grade_answer(q, "6x²y-9xy²")["reason"], "representation")

    def test_complete_factorization_rejects_a_partial_or_fake_factorization(self):
        q = self.questions["m8a.17.1-q202"]
        self.assertEqual(grade_answer(q, "2(x+2)x(x-2)")["status"], "correct")
        for raw in ("2x(x^2-4)", "1*(2x^3-8x)", "2x^3-8x"):
            self.assertEqual(grade_answer(q, raw)["reason"], "representation", raw)

    def test_expansion_requires_collected_monomials(self):
        q = task("x^2-x-6", kind="polynomial", variables=["x"], representation="expanded")
        self.assertEqual(grade_answer(q, "-6-x+x^2")["status"], "correct")
        self.assertEqual(grade_answer(q, "(x+2)(x-3)")["reason"], "representation")
        self.assertEqual(grade_answer(q, "x^2+2x-3x-6")["reason"], "representation")
        linear = task("2x", kind="polynomial", variables=["x"], representation="expanded")
        for raw in ("x*(x-x+2)", "x*(1+1)", "-(x-x-2)*x"):
            self.assertEqual(grade_answer(linear, raw)["reason"], "representation", raw)

    def test_integer_factorization_retains_explicit_fractional_factors(self):
        q = task("(x-1)(x+1)", kind="polynomial", variables=["x"],
                 representation="factored", integer_coefficients=True, complete_factorization=True)
        self.assertEqual(grade_answer(q, "-(1-x)(x+1)")["status"], "correct")
        for raw in ("(2x-2)*(2x+2)/4", "(1/2)*(2x-2)*(x+1)"):
            self.assertEqual(grade_answer(q, raw)["reason"], "representation", raw)
        common = task("3xy(2x-3y)", kind="polynomial", variables=["x", "y"],
                      representation="factored", integer_coefficients=True, complete_factorization=True)
        self.assertEqual(grade_answer(common, "xy(6x-9y)")["reason"], "representation")
        self.assertEqual(grade_answer(common, "3yx(2x-3y)")["status"], "correct")

    def test_equivalence_is_exact_polynomial_identity_not_sampled_points(self):
        q = task("x^2+2xy+y^2", kind="polynomial", variables=["x", "y"])
        self.assertEqual(grade_answer(q, "(x+y)^2")["status"], "correct")
        self.assertEqual(grade_answer(q, "(x-y)^2")["status"], "wrong")
        zero = task("0", kind="polynomial", variables=["x"])
        self.assertEqual(grade_answer(zero, "x(x-1)")["status"], "wrong")
        self.assertEqual(grade_answer(zero, "x-x")["status"], "correct")

    def test_unsafe_or_excessive_symbolic_input_is_rejected(self):
        q = task("x", kind="polynomial", variables=["x"])
        attacks = ("__import__('os')", "x.__class__", "[x for x in x]", "x^9999", "x^(2^12)",
                   "(" * 25 + "x" + ")" * 25, "(x+1)^12*(x+1)^12*(x+1)",
                   "x" * 600, "x/0", "x/x", "1.2.3x", "xxz", "x;print(1)",
                   "x+0^0-1", "x+(x-x)^0-1")
        for raw in attacks:
            self.assertEqual(grade_answer(q, raw)["status"], "invalid", raw)


class ContractIntegrationTests(unittest.TestCase):
    def test_bad_metadata_fails_closed_without_penalizing_learner(self):
        specs = ({"kind": "number", "comparison": "tolerance"},
                 {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "-1"},
                 {"kind": "number", "comparison": "rounded"},
                 {"kind": "number", "representation": []},
                 {"kind": "polynomial", "variables": ["x", "x"]},
                 {"kind": "number", "unknown": "x"},
                 {"kind": "number", "version": 2})
        for spec in specs:
            self.assertTrue(validate_answer_spec(spec), spec)
            self.assertEqual(grade_answer({"type": "numeric", "answer": "1", "answer_spec": spec}, "1")["status"], "needs_review")

    def test_question_and_unit_use_same_explicit_contract(self):
        q = {"type": "numeric", "answer": "0.0000006", "verify": "5*1.2*10**-7",
             "answer_spec": {"kind": "number", "comparison": "exact"}}
        for raw in ("0", "0.0000006", "1/0"):
            self.assertEqual(judge(q, raw), grade_task(q, raw)["status"])
        q = task("(2,3)", kind="coordinate", dimensions=2)
        for raw in ("(2.0,6/2)", "(3,2)", "x"):
            self.assertEqual(judge(q, raw), grade_task(q, raw)["status"])

    def test_literal_aliases_and_open_responses_are_not_invented(self):
        q = task("平行", kind="text")
        q["accept"] = ["互相平行"]
        self.assertEqual(grade_answer(q, " 互相平行。 ")["status"], "correct")
        self.assertEqual(grade_answer(q, "大概平行")["status"], "wrong")
        q = {"type": "rubric", "answer_spec": {"kind": "rubric"}}
        self.assertEqual(grade_answer(q, "完整证明")["status"], "needs_review")
        self.assertNotIn("score", grade_answer(q, "完整证明"))

    def test_explicit_contract_overrides_legacy_prompt_heuristic(self):
        q = {"type": "short", "answer": "5/12", "prompt": "写出结果",
             "answer_spec": {"kind": "number", "comparison": "exact", "representation": "simplest_fraction"}}
        self.assertEqual(grade_answer(q, "10/24")["reason"], "representation")
        self.assertEqual(grade_answer(q, "5/12")["status"], "correct")
        q["answer_spec"]["representation"] = "any"
        self.assertEqual(grade_answer(q, "10/24")["status"], "correct")


class CliContractTests(unittest.TestCase):
    def test_documented_cli_diagnosis_completes_choices(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/query.py"), "diagnose", "m1a.3.1",
                                 "--answers", "c,c", "--limit", "2"], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("一个立体模型有六个完全相同的正方形面，相邻的面按盒角相接。它的外形是什么？", result.stdout)
        self.assertIn("A.", result.stdout)
        self.assertIn("参考答案：", result.stdout)
        self.assertNotIn("参考答案：o", result.stdout)
        self.assertIn("模拟作答", result.stdout)

    def test_cli_answer_label_follows_option_order_without_mutating_ids(self):
        graph = load_graph()
        question = graph.bank["m1a.3.1"][0]
        before = copy.deepcopy(question)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            _run_session(graph, ["m1a.3.1"], "diagnose", ["correct"], limit=1)
        right = next((i, c) for i, c in enumerate(question["choices"]) if c["id"] == question["answer"])
        self.assertIn(f"参考答案：{chr(65 + right[0])}. {right[1]['text']}", output.getvalue())
        self.assertEqual(question, before)


if __name__ == "__main__":
    unittest.main()
