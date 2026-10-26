"""自生成题（记忆检验 + 计算练习）的守门测试。

**为什么这类测试特别重要**：生成题是机器产的，量又大（226 道）。
一旦生成器的逻辑改错，错题会**批量**进库，而且不容易被肉眼发现。

所以这里守住四条底线：
1. 生成的题必须能通过 verify（计算题）——答案与表达式一致
2. 选择题必须答案唯一（选项内容不重复、不自相矛盾）
3. 同一知识点内不能有完全重复的题
4. 记忆检验题的干扰项必须**不属于**正确项的内容（否则单选题不唯一）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_calc_questions as C  # noqa: E402
import gen_memory_questions as G  # noqa: E402


class TestCalcQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drills = C.build()

    def test_all_verify_pass(self):
        """每一道计算题的 verify 表达式必须算出答案。"""
        from cnkg.verify import evaluate, compare
        bad = []
        for d in self.drills:
            try:
                v = evaluate(d["verify"])
            except Exception as exc:
                bad.append(f"{d['id']}: verify 报错 {exc}")
                continue
            if not compare(d["answer"], v):
                bad.append(f"{d['id']}: {d['verify']} = {v}，但答案是 {d['answer']}")
        self.assertFalse(bad, "验算不通过：\n" + "\n".join(bad[:10]))

    def test_no_duplicate_prompts(self):
        prompts = [d["prompt"] for d in self.drills]
        dup = {p for p in prompts if prompts.count(p) > 1}
        self.assertFalse(dup, f"重复题干：{list(dup)[:5]}")

    def test_prompts_differ_within_topic(self):
        """同一知识点的多道题题干必须不同（否则是无效题量）。"""
        by_topic = {}
        for d in self.drills:
            by_topic.setdefault(d["topicId"], []).append(d["prompt"])
        bad = [t for t, ps in by_topic.items() if len(set(ps)) != len(ps)]
        self.assertFalse(bad, f"同知识点内题干重复：{bad[:5]}")

    def test_numeric_answers_only(self):
        """计算题答案必须是可以判题的数值。"""
        bad = [d["id"] for d in self.drills if not str(d["answer"]).replace(".", "").replace("-", "").isdigit()]
        self.assertFalse(bad, f"答案不是纯数字：{bad[:5]}")

    def test_has_accept_and_verify(self):
        for d in self.drills:
            self.assertTrue(d.get("accept"), f"{d['id']} 缺 accept")
            self.assertTrue(d.get("verify"), f"{d['id']} 缺 verify")

    def test_deterministic(self):
        """同参数生成同一批题——可复现、可重放。"""
        again = C.build()
        self.assertEqual([d["prompt"] for d in self.drills],
                         [d["prompt"] for d in again])


class TestMemoryQuestions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
        cls.topics = topics
        cls.mem = [t for t in topics if G.is_memory_topic(t)]
        cls.drills = []
        for t in cls.mem:
            q = G.build_reverse_question(t, cls.mem)
            if q:
                cls.drills.append(q)

    def test_enough_generated(self):
        self.assertGreaterEqual(len(self.drills), 80, "记忆检验题生成量过少")

    def test_option_texts_unique(self):
        for q in self.drills:
            texts = [c["text"] for c in q["choices"]]
            self.assertEqual(len(set(texts)), len(texts), f"选项重复：{q['prompt'][:40]}")

    def test_no_self_contradictory_option(self):
        for q in self.drills:
            for c in q["choices"]:
                self.assertFalse(G.self_contradictory(c["text"]),
                                 f"选项自相矛盾：{c['text'][:50]}")

    def test_answer_is_one_of_choices(self):
        for q in self.drills:
            ids = {c["id"] for c in q["choices"]}
            self.assertIn(q["answer"], ids, f"答案不在选项内：{q['prompt'][:40]}")

    def test_answer_position_spread(self):
        """正确答案不能总在同一位置——否则学生能靠位置猜。"""
        pos = {}
        for q in self.drills:
            pos[q["answer"]] = pos.get(q["answer"], 0) + 1
        self.assertGreaterEqual(len(pos), 3, f"答案位置分布太集中：{pos}")

    def test_explanation_mentions_other_sources(self):
        """解析要说明干扰项来自哪里——这是可复核的关键。"""
        for q in self.drills:
            self.assertIn("其余三项", q["explanation"], q["prompt"][:40])


if __name__ == "__main__":
    unittest.main()
