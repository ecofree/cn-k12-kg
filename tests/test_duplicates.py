"""查重工具的核心函数测试：归一化、二元组、Jaccard。

工具本身只负责把 1000+ 道题缩到几十对候选，判不判重复仍然靠人；
但"怎么算相似"这件事必须是确定性的，否则每次跑出来的候选都在变。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from find_duplicates import bigrams, collect, jaccard, normalize  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_strips_punctuation_and_spaces(self):
        self.assertEqual(normalize("8 的立方根是多少？（只填数字）"),
                         "8的立方根是多少只填数字")

    def test_lowercase_latin(self):
        self.assertEqual(normalize("SAS 判定"), "sas判定")

    def test_empty(self):
        self.assertEqual(normalize(None), "")
        self.assertEqual(normalize(""), "")


class SimilarityTest(unittest.TestCase):
    def test_identical_is_one(self):
        a = bigrams(normalize("掷一枚均匀骰子，出现偶数点的概率是多少"))
        self.assertAlmostEqual(jaccard(a, a), 1.0)

    def test_disjoint_is_zero(self):
        a = bigrams(normalize("完全不相干的内容"))
        b = bigrams(normalize("另一段毫无关系的文字"))
        self.assertEqual(jaccard(a, b), 0.0)

    def test_single_char(self):
        self.assertEqual(bigrams("a"), {"a"})
        self.assertEqual(bigrams(""), set())


class CollectTest(unittest.TestCase):
    def test_finds_near_pair(self):
        q1 = {"id": "q1", "topicId": "t", "prompt": "掷一枚均匀骰子，出现偶数点的概率是多少？", "answer": "0.5"}
        q2 = {"id": "q2", "topicId": "t", "prompt": "掷一枚均匀骰子，出现偶数点的概率是多少？（填小数）", "answer": "0.5"}
        q3 = {"id": "q3", "topicId": "t", "prompt": "圆的面积公式是什么？", "answer": "πr²"}
        items = [(q, bigrams(normalize(q["prompt"]))) for q in (q1, q2, q3)]
        pairs = collect(items, 0.8)
        ids = {frozenset((a["id"], b["id"])) for _, a, b in pairs}
        self.assertIn(frozenset(("q1", "q2")), ids)
        self.assertNotIn(frozenset(("q1", "q3")), ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
