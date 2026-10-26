"""核验沙箱测试：python -m unittest tests.test_verify -v"""
from __future__ import annotations

import pathlib
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
