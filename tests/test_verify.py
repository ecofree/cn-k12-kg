"""核验沙箱测试：python -m unittest tests.test_verify -v"""
from __future__ import annotations

import pathlib
import sys
import unittest
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import verify  # noqa: E402


class EvaluateTest(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertEqual(verify.evaluate("3+1"), 4)
        self.assertEqual(verify.evaluate("-3+(-5)"), -8)
        self.assertEqual(verify.evaluate("(-2)**4"), 16)
        self.assertAlmostEqual(verify.evaluate("1/(-2/3)"), -1.5)

    def test_whitelisted_functions(self):
        self.assertEqual(verify.evaluate("abs(-9)"), 9)
        self.assertAlmostEqual(verify.evaluate("round(3.14159,2)"), 3.14)
        self.assertEqual(verify.evaluate("max(3,5)"), 5)

    def test_rejects_code_injection(self):
        for expr in ("__import__('os').system('echo hi')",
                     "open('x')",
                     "(1).__class__",
                     "[x for x in range(3)]",
                     "3 if 1 else 2",
                     "3**99"):
            with self.assertRaises(verify.VerifyError, msg=expr):
                verify.evaluate(expr)

    def test_rejects_non_numeric(self):
        with self.assertRaises(verify.VerifyError):
            verify.evaluate("'abc'")

    def test_decimal_and_fraction_arithmetic_remain_exact(self):
        self.assertEqual(verify.evaluate_precise("0.1+0.2"), Fraction(3, 10))
        self.assertEqual(verify.evaluate_precise("1/3+1/6"), Fraction(1, 2))
        self.assertEqual(verify.evaluate_precise("9007199254740993-9007199254740992"), 1)

    def test_roots_have_exact_proofs_when_possible(self):
        for expr, expected in (("125**(1/3)", 5), ("16**(3/4)", 8),
                               ("sqrt(18)/sqrt(2)", 3), ("sqrt(2)*sqrt(8)", 4),
                               ("0/sqrt(2)", 0), ("(-8)**(1/3)", -2)):
            result = verify.evaluate_precise(expr)
            self.assertIsInstance(result, Fraction, expr)
            self.assertEqual(result, expected, expr)

    def test_computed_limits_and_undefined_values_are_rejected(self):
        attacks = ("9**(9**9)", "True+1", "False", "0**0", "(2-2)**0",
                   "1/0", "sqrt(-1)", "(1e100)**12", "min()",
                   "(" * 250 + "1" + ")" * 250, "+" * 40 + "1",
                   "+".join(["1"] * 200), "1" * 4097)
        for expr in attacks:
            with self.assertRaises(verify.VerifyError, msg=expr[:80]):
                verify.evaluate(expr)


class CompareTest(unittest.TestCase):
    def test_numeric_tolerance(self):
        self.assertTrue(verify.compare("0.1", 0.1))
        self.assertTrue(verify.compare("-1.5", -1.5))
        self.assertTrue(verify.compare("2", 2.0000001))
        self.assertFalse(verify.compare("2", 2.1))

    def test_string_fallback(self):
        self.assertTrue(verify.compare("两两垂直", " 两两垂直 "))
        self.assertFalse(verify.compare("A", "B"))

    def test_equivalent_fraction_answers(self):
        for answer in ("1/2", "2 / 4", "０．５", "5e-1", "+1 / +2"):
            self.assertTrue(verify.compare(answer, "0.5"), answer)
        self.assertTrue(verify.compare("−3/4", "-0.75"))
        self.assertFalse(verify.compare("1/3", "0.5"))

    def test_invalid_numeric_input_is_never_evaluated(self):
        for answer in ("1/0", "nan", "inf", "1e999", "1+1", "sqrt(4)",
                       "__import__('os')", "1" * 129, "50%", "3人", ""):
            self.assertIsNone(verify.to_number(answer), answer)
        self.assertFalse(verify.compare("nan", "nan"))

    def test_numeric_judge_accepts_equivalence_and_distinguishes_format(self):
        from web.server import judge
        question = {"type": "numeric", "answer": "0.5"}
        self.assertEqual(judge(question, "1/2"), "correct")
        self.assertEqual(judge(question, "1/3"), "wrong")
        self.assertEqual(judge(question, "1/0"), "invalid")


class QuestionTest(unittest.TestCase):
    def test_pass_case(self):
        result = verify.check_question({"id": "q1", "answer": "4", "verify": "3+1"})
        self.assertEqual(result["status"], "pass")

    def test_mismatch_case(self):
        result = verify.check_question({"id": "q2", "answer": "5", "verify": "3+1"})
        self.assertEqual(result["status"], "mismatch")

    def test_unverifiable_case(self):
        result = verify.check_question({"id": "q3", "answer": "A", "type": "choice"})
        self.assertEqual(result["status"], "unverifiable")

    def test_author_verification_has_no_tolerance_for_rational_arithmetic(self):
        for answer, expr in (("0", "5*1.2*10**-7"), ("100000000001", "100000000000"),
                             ("9007199254740992", "9007199254740993"),
                             ("0.3333333333333333", "1/3")):
            result = verify.check_question({"answer": answer, "verify": expr})
            self.assertEqual(result["status"], "mismatch", result)
        self.assertEqual(verify.check_question({"answer": "0.3", "verify": "0.1+0.2"})["status"], "pass")

    def test_approximate_author_checks_require_declared_precision(self):
        q = {"answer": "1.41", "verify": "sqrt(2)"}
        self.assertEqual(verify.check_question(q)["status"], "error")
        q["answer_spec"] = {"kind": "number", "comparison": "rounded", "decimal_places": 2}
        self.assertEqual(verify.check_question(q)["status"], "pass")
        q["answer"] = "1.42"
        self.assertEqual(verify.check_question(q)["status"], "mismatch")
        q["answer"] = "1.414"
        q["answer_spec"] = {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "0.001"}
        self.assertEqual(verify.check_question(q)["status"], "pass")
        self.assertEqual(verify.check_question({"answer": "1.41", "verify": "round(sqrt(2),2)"})["status"], "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
