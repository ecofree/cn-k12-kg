"""Catch a correct final answer whose explanation still contains a wrong intermediate step."""
import json
from pathlib import Path
import tempfile
import unittest

from cnkg.math_statements import inspect_resource, numerical_claims, scan


class MathStatementTests(unittest.TestCase):
    def test_incorrect_intermediate_union_count_is_caught_even_if_final_answer_is_correct(self):
        count, findings = inspect_resource({"answer": "13", "explanation": "至少一项25+20−8=25人，所以50−37=13人。"}, "source.json", "a" * 64)
        self.assertEqual(count, 2)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["left_value"], "37")
        self.assertEqual(findings[0]["right_value"], "25")

    def test_exact_decimal_fraction_and_percentage_claims_are_consistent(self):
        values = list(numerical_claims("0.1+0.2=0.3；25%=1/4；3²+4²=25；\\frac{1}{2}=0.5"))
        self.assertEqual(len(values), 4)
        self.assertTrue(all(value["equal"] for value in values))

    def test_never_extracts_a_numeric_suffix_from_an_algebraic_expression(self):
        for text in ("x+2+3=5", "S=ab", "1/3≈0.333", "x²+1=5", "f(2)=3", "2+3≤6"):
            with self.subTest(text=text):
                self.assertEqual(list(numerical_claims(text)), [])

    def test_numeric_middle_of_a_chain_can_be_checked_without_claiming_to_prove_the_variable(self):
        values = list(numerical_claims("S=2*3=6"))
        self.assertEqual(len(values), 1)
        self.assertTrue(values[0]["equal"])

    def test_complete_unsupported_notation_is_not_truncated_into_a_false_claim(self):
        for text in ("log₂8=3", "f′(2)=0", "∠1=90°", "□+7=16", "2:6=1:3", "2∶6=1∶3",
                     "30÷4=7余2", "17÷5=3……2", "100=10个十", "300+500=8个百",
                     "3050008=305万8", "20×3=6个十", "5!+1=121", "|−3|+2=5", "α+2=3",
                     "原来人数+7=16", "原来人数−7=16"):
            with self.subTest(text=text):
                self.assertEqual(list(numerical_claims(text)), [])

    def test_middle_dot_and_full_superscript_sequences_keep_their_numeric_meaning(self):
        values = list(numerical_claims("2·16/25−1=7/25；1000000=10⁶；10⁻²=0.01；2¹⁰=1024"))
        self.assertEqual(len(values), 4)
        self.assertTrue(all(value["equal"] for value in values))

    def test_teaching_hints_and_rubric_references_are_checked_but_question_choices_are_not_assertions(self):
        value = {"choices": [{"text": "2+3=6", "feedback": "请算2+3=5。"}],
                 "hints": [{"level": 1, "text": "3+4=8。"}],
                 "rubric": [{"criterion": "求总量", "example": "4+5=8。"}]}
        count, findings = inspect_resource(value, "source.json", "a" * 64)
        self.assertEqual(count, 3)
        self.assertEqual({finding["pointer"] for finding in findings}, {"/hints/0/text", "/rubric/0/example"})

    def test_intentionally_wrong_reference_is_not_silently_suppressed_by_a_keyword(self):
        _, findings = inspect_resource({"reference_answer": "错误做法：2+3=6。应逐个对应，得5。"}, "source.json", "a" * 64)
        self.assertEqual(len(findings), 1)
        _, changed = inspect_resource({"reference_answer": "2+3=6。"}, "source.json", "a" * 64)
        self.assertNotEqual(findings[0]["id"], changed[0]["id"])

    def test_explicit_counterexample_review_expires_when_its_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            (data / "content").mkdir(parents=True)
            (data / "question_sources").mkdir()
            def write(path, value):
                path.write_text(json.dumps(value, ensure_ascii=False))
            write(data / "content_sources.json", {"content_sources": [{"path": "content/a.json"}]})
            write(data / "task_admissions.json", {"tasks": {"q": {"policy": {"practice_eligible": True}}}})
            write(data / "question_sources/application.json", {"questions": []})
            source = data / "content/a.json"
            value = {"questions": [{"id": "q", "explanation": "错算示例：2+3=6。逐个点数可得正确结果5。"}]}
            write(source, value)
            first = scan(root)
            self.assertFalse(first["ok"])
            finding = first["findings"][0]
            write(data / "math_statement_reviews.json", {"schema": 1, "items": [{
                "id": finding["id"], "source_sha256": finding["source_sha256"],
                "decision": "intentional_counterexample", "reason": "明示错算，并给出点数检验与正确结果。",
                "reviewer_kind": "ai", "teacher_review": "pending"}]})
            self.assertTrue(scan(root)["ok"])
            # The claim has not changed, but its surrounding source has. The
            # prior review cannot silently authorize the new content version.
            value["questions"][0]["hint"] = "先分别数两组，再数合起来的物体。"
            write(source, value)
            changed = scan(root)
            self.assertFalse(changed["ok"])
            self.assertTrue(any("已过期" in error for error in changed["errors"]))


if __name__ == "__main__":
    unittest.main()
