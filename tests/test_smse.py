"""上标还原规则的回归测试。

这类启发式规则最容易出「改一个坏三个」，所以每条规则都要有对例和反例。
反例（不该还原）比正例更重要——把 A．1：4 改成 A⁴ 比不改更糟。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from fix_superscript import restore  # noqa: E402


class TestSuperscript(unittest.TestCase):
    def assertRestored(self, src: str, expect_n: int, expect_contains: str = "") -> None:
        out, n = restore(src)
        self.assertEqual(n, expect_n, f"{src!r} → {out!r}，期望 {expect_n} 处")
        if expect_contains:
            self.assertIn(expect_contains, out)

    # --- 该还原的 ---

    def test_quadratic_in_function(self):
        self.assertRestored("抛物线y＝x2+4x的顶点坐标是", 1, "x^{2}")

    def test_quadratic_in_equation(self):
        self.assertRestored("已知关于x的方程x2﹣6x+m﹣1=0有两个不相等的实数根", 1, "x^{2}")

    def test_cubic(self):
        self.assertRestored("分解因式：2x3﹣4x", 1, "x^{3}")

    def test_paren_power(self):
        self.assertRestored("抛物线y＝2（x﹣3）2+4向左平移3个单位", 1, "（x﹣3）^{2}")

    def test_multiple_occurrences(self):
        out, n = restore("作y＝x2，y＝x2+2，y＝﹣2x2+1的图象")
        self.assertEqual(n, 3)
        self.assertEqual(out.count("^{2}"), 3)

    # --- 不该还原的 ---

    def test_ratio_not_power(self):
        """A．1：4 是比，不是 A⁴。"""
        self.assertRestored("A．1：4\tB．1：2", 0)

    def test_subscript_not_power(self):
        """比较y1与y2的大小 —— y2 是下标不是幂。"""
        self.assertRestored("比较y1与y2的大小：y1 y2", 0)

    def test_subscript_context_disables_all(self):
        """只要句中出现下标变量，全句都不做上标还原。"""
        self.assertRestored("若x1＜x2＜0，则y1 y2", 0)

    def test_coordinate_not_power(self):
        self.assertRestored("点P（3，4）", 0)

    def test_length_not_power(self):
        self.assertRestored("矩形的长为5cm", 0)

    def test_uppercase_word_not_power(self):
        self.assertRestored("AB2", 0)

    def test_angle_triangle_not_power(self):
        """∠C＝90° 与 ABCDE 这些不能被当成幂。"""
        out, n = restore("在Rt△ABC中，∠C＝90°")
        self.assertEqual(n, 0)


class TestAnswerIntegrity(unittest.TestCase):
    """答案里的公式丢失必须被识别出来。"""

    def test_empty_paren(self):
        import parse_smse
        self.assertTrue(parse_smse.answer_is_broken("（）"))
        self.assertTrue(parse_smse.answer_is_broken("2x（x+）（x﹣）"))
        self.assertTrue(parse_smse.answer_is_broken("x＝"))

    def test_valid_answers_not_broken(self):
        import parse_smse
        for ans in ("（0，1）", "3（﹣1）", "（x2+2）（x+1）（x﹣1）", "9", "m＜10",
                    "y＝﹣x2+2x", "0.2或0.8", "1：3"):
            self.assertFalse(parse_smse.answer_is_broken(ans), ans)


class TestDelimiters(unittest.TestCase):
    """中考题的公式必须带 $ 定界符，且 KaTeX 能渲染。"""

    def test_add_delimiters(self):
        import import_smse
        out, n = import_smse.add_delimiters("抛物线y＝2﹣（x+1）^{2}的顶点坐标是")
        self.assertEqual(n, 1)
        self.assertIn("$(x+1)^{2}$", out)

    def test_fullwidth_converted(self):
        """全角括号和小连字符必须转成 LaTeX 记号，否则 KaTeX 显示缺字符号。"""
        import import_smse
        out, _ = import_smse.add_delimiters("计算：（2a﹣b）^{2}= ．")
        self.assertIn("$(2a-b)^{2}$", out)
        self.assertNotIn("﹣", out.split("$")[1])

    def test_already_delimited_not_doubled(self):
        import import_smse
        out, n = import_smse.add_delimiters("已知$x^{2}$，求y")
        self.assertEqual(n, 0)
        self.assertEqual(out.count("$"), 2)

    def test_mixed_delimited_and_bare(self):
        """已有定界 + 裸上标混在一起时，裸的也要处理。"""
        import import_smse
        out, n = import_smse.add_delimiters("二次函数y＝2﹣（x+1）^{2}的顶点坐标")
        self.assertEqual(n, 1)
        self.assertIn("$", out)


class TestTagMap(unittest.TestCase):
    """考点映射表里的 id 必须真实存在且是初中知识点。"""

    def test_all_ids_valid(self):
        import json
        root = Path(__file__).resolve().parent.parent
        m = json.loads((root / "data" / "kc_tag_map.json").read_text(encoding="utf-8"))
        topics = {x["id"]: x for x in json.loads(
            (root / "data" / "topics.json").read_text(encoding="utf-8"))}
        for tag, tid in m["map"].items():
            if tid is None:
                self.assertIn(tag, m["out_of_scope"], f"{tag} 映射为 null 但未列入 out_of_scope")
                continue
            self.assertIn(tid, topics, f"{tag} → {tid} 不存在")
            self.assertIn(topics[tid]["grade"], (7, 8, 9),
                          f"{tag} → {tid} 不是初中知识点")


if __name__ == "__main__":
    unittest.main()
