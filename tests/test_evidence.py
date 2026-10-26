"""证据分级的语义测试。

这些测试盯的是**判据本身会不会误伤**——因为它们决定一道题能不能进诊断、
答错能不能判薄弱。判错一次的代价是：好题被封，或者一句"答案:C"
被当成 cross-checked。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import evidence  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


def q(**kw):
    base = {
        "id": "t1", "type": "choice", "prompt": "下列各数中最大的一个是（ ）",
        "choices": [{"id": "o1", "text": "1"}, {"id": "o2", "text": "2"},
                    {"id": "o3", "text": "3"}, {"id": "o4", "text": "4"}],
        "answer": "o3", "explanation": "故选 C.",
    }
    base.update(kw)
    return base


class TestConclusionLetter(unittest.TestCase):
    def test_strong_forms(self):
        for text, want in (("故选 D.", "D"), ("答案为：B", "B"),
                           ("应选 A", "A"), ("所以答案是 C", "C")):
            self.assertEqual(evidence.concluding_letter(text), want, text)

    def test_negative_statement_is_not_a_conclusion(self):
        # 「故 A 错误」说的是 A 错，不是结论；同一句里「故 B 正确」才是结论。
        self.assertEqual(evidence.concluding_letter("故 A 错误，故 B 正确．"), "B")
        self.assertIsNone(evidence.concluding_letter("故 A 不正确"))
        self.assertIsNone(evidence.concluding_letter("故 A 错误，故 B 也不合题意"))
        self.assertEqual(evidence.concluding_letter("故 B 正确．"), "B")

    def test_ambiguous_returns_none(self):
        self.assertIsNone(evidence.concluding_letter("故选 A. 故选 C."))


class TestArithmeticChain(unittest.TestCase):
    def test_chain_is_evaluated_as_a_whole(self):
        # 只抓第一段会得到 12*63/36=756，把正确的题判成错（实测 100% 误伤）
        ok, bad, last = evidence.arithmetic_chains(r"$12 \times 63 \div 36=756 \div 36=21$")
        self.assertEqual((ok, bad), (1, 0))
        self.assertAlmostEqual(last, 21)

    def test_both_sides_with_parentheses(self):
        ok, bad, _ = evidence.arithmetic_chains("125*97*8=97*(8*125)")
        self.assertEqual((ok, bad), (1, 0))

    def test_superscript_flattening_does_not_raise_defect(self):
        # 数据里 2² 常被展平成 22，会让正确连等式算不通；这不能升级为 defective
        card = q(explanation="先算 △＝22-4*3＝-8＜0，故方程无实根，故选 C.")
        self.assertEqual(evidence.grade(card)["level"], "explained")

    def test_safe_eval_rejects_code(self):
        self.assertIsNone(evidence.safe_eval("__import__('os').system('x')"))


class TestGradeLevels(unittest.TestCase):
    def test_bare_when_analysis_is_just_the_letter(self):
        self.assertEqual(evidence.grade(q(explanation="答案:C"))["level"], "bare")

    def test_verified_when_recompute_hits_the_key(self):
        card = q(prompt="计算 12+34 的值", type="numeric", choices=[],
                 answer="46", explanation="12+34=46，所以答案是 46。")
        self.assertEqual(evidence.grade(card)["level"], "verified")

    def test_broken_when_answer_not_in_options(self):
        self.assertEqual(evidence.grade(q(answer="o9"))["level"], "broken")

    def test_inverted_question_never_flagged_defective(self):
        # 「下列说法错误的是」时，解析里的「故 A 正确」不是结论
        card = q(prompt="下列说法中错误的是（ ）",
                 answer="o2", explanation="A 显然成立，故 A 正确；B 的反例是 x=0，故选 B.")
        self.assertNotEqual(evidence.grade(card)["level"], "defective")

    def test_defective_when_conclusion_contradicts_key(self):
        card = q(answer="o1", explanation="解得 2，故选 B.")
        self.assertEqual(evidence.grade(card)["level"], "defective")


class TestUseBoundary(unittest.TestCase):
    def test_only_independent_evidence_may_judge_weak(self):
        self.assertTrue(evidence.may_judge_weak("verified"))
        self.assertTrue(evidence.may_judge_weak("corroborated"))
        # 作者自述与只有答案的题，答错可能只是答案错，不能据此判孩子薄弱
        self.assertFalse(evidence.may_judge_weak("explained"))
        self.assertFalse(evidence.may_judge_weak("bare"))

    def test_may_confirm_mastery_is_a_wider_gate(self):
        self.assertTrue(evidence.may_confirm_mastery("explained"))
        self.assertFalse(evidence.may_confirm_mastery("bare"))


class TestGraphGate(unittest.TestCase):
    def test_external_pool_excludes_bare_questions(self):
        g = load_graph(include_external=True)
        for t in g.topics:
            for question in g.bank.get(t["id"], ()):
                if not evidence.is_own(question):
                    self.assertIn(question.get("evidence_level"),
                                  evidence.DIAGNOSABLE,
                                  f"{question['id']} 不该进诊断池")

    def test_empty_evidence_tuple_means_no_filter(self):
        g = load_graph(include_external=True, ext_evidence=())
        self.assertGreater(len(g.questions), 0)


if __name__ == "__main__":
    unittest.main()
