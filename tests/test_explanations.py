"""解析质量守卫：不引用选项字母、不解释别的题。

这两条都是真实事故留下的：选项打散让 6 条解析的字母指错，改写题干又让 7 条解析
在解释另一道题（问推拉窗、解析讲电梯）。见 docs/decisions.md D19、D20。
"""
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from validate import OPTION_REF_RE, check_questions, load_outline  # noqa: E402


class ExplanationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))

    def test_no_option_letter_reference(self):
        bad = [q["id"] for q in self.questions
               if OPTION_REF_RE.search(q.get("explanation") or "")]
        self.assertEqual(bad, [], "解析不能引用选项字母，选项重排后会指错")

    def test_explanation_not_empty(self):
        empty = [q["id"] for q in self.questions if not (q.get("explanation") or "").strip()]
        self.assertEqual(empty, [], "每题都要有解析，否则学生答错后拿不到反馈")

    def test_regression_shapes(self):
        """三条典型串题：数字不对、对象不对、观察方向不对。"""
        by_id = {q["id"]: q for q in self.questions}
        self.assertIn("1.5", by_id["m3b.6.2-q1"]["explanation"])
        self.assertIn("推拉窗", by_id["m3b.1.1-q3"]["explanation"])
        self.assertIn("正面", by_id["m5a.1.1-q3"]["explanation"])


if __name__ == "__main__":
    unittest.main()
