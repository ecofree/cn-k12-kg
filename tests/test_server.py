"""Web 层测试：判分与题面脱敏。python -m unittest tests.test_server -v"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web import server  # noqa: E402


class JudgeTest(unittest.TestCase):
    def test_choice_option_id(self):
        """正常路径：提交 option_id。"""
        q = {"type": "choice", "answer": "o3",
             "choices": [{"id": "o1", "text": "甲"}, {"id": "o2", "text": "乙"},
                         {"id": "o3", "text": "丙"}, {"id": "o4", "text": "丁"}]}
        self.assertEqual(server.judge(q, "o3"), "correct")
        self.assertEqual(server.judge(q, "o1"), "wrong")
        self.assertEqual(server.judge(q, ""), "wrong")

    def test_choice_letter_fallback(self):
        """兼容路径：旧客户端提交字母，按当前顺序换算。"""
        q = {"type": "choice", "answer": "o3",
             "choices": [{"id": "o1", "text": "甲"}, {"id": "o2", "text": "乙"},
                         {"id": "o3", "text": "丙"}, {"id": "o4", "text": "丁"}]}
        self.assertEqual(server.judge(q, "C"), "correct")
        self.assertEqual(server.judge(q, "c"), "correct")
        self.assertEqual(server.judge(q, "A"), "wrong")

    def test_numeric_tolerance(self):
        q = {"type": "numeric", "answer": "-1.5"}
        self.assertEqual(server.judge(q, "-1.5"), "correct")
        self.assertEqual(server.judge(q, " -1.50 "), "correct")
        self.assertEqual(server.judge(q, "-1"), "wrong")

    def test_numeric_rejects_text(self):
        q = {"type": "numeric", "answer": "4"}
        self.assertEqual(server.judge(q, "四"), "invalid")

    def test_short_exact_and_variants(self):
        q = {"type": "short", "answer": "平行", "accept": ["互相平行"]}
        self.assertEqual(server.judge(q, "平行"), "correct")
        self.assertEqual(server.judge(q, "  平行。 "), "correct")
        self.assertEqual(server.judge(q, "互相平行"), "correct")
        self.assertEqual(server.judge(q, "垂直"), "wrong")

    def test_short_fullwidth_and_percent(self):
        q = {"type": "short", "answer": "0.6827", "accept": ["68.27%"]}
        self.assertEqual(server.judge(q, "６８.２７％"), "correct")
        self.assertEqual(server.judge(q, "0.68"), "wrong")

    def test_short_numeric_equivalence(self):
        """短答里写 1 和 1.0 应视为同一个答案。"""
        q = {"type": "short", "answer": "1", "accept": []}
        self.assertEqual(server.judge(q, "1.0"), "correct")
        self.assertEqual(server.judge(q, "2"), "wrong")


class PayloadTest(unittest.TestCase):
    def test_question_payload_hides_answer(self):
        question = server.GRAPH.questions[0]
        payload = server.public_question(question)
        self.assertNotIn("answer", payload)
        self.assertNotIn("verify", payload)
        self.assertNotIn("explanation", payload)
        self.assertNotIn("accept", payload)
        self.assertIn("prompt", payload)

    def test_topics_payload_shape(self):
        data = server.build_topics()
        self.assertEqual(data["stats"]["topics"], len(server.GRAPH.topics))
        chapter = data["volumes"][0]["chapters"][0]
        self.assertTrue(chapter["topics"])
        self.assertIn("questionCount", chapter["topics"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
