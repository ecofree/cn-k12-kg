"""诊断引擎的「题型多样性」行为测试。

**这个测试守的是什么**：题量扩到每点 4–7 道之后，
如果引擎还是「猜测率低的题型优先」，同一点的第 1、2 问会都是数值题，
记忆检验题（选择题）永远抽不到——而那正是「记不记得公式」最直接的检验。

用户的原话是「每个知识点都需要储备足够不同类型的题」，
所以「储备了但抽不到」等于没储备。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 这个文件测的是「含外部题的大池」行为（题型轮换、池单调性），
# 缺文件时整组跳过。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

sys.path.insert(0, str(ROOT))
from cnkg import engine  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


class TestQuestionTypeVariety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_graph(include_external=True)

    def test_second_question_changes_type(self):
        """第 2 问的题型应该和第 1 问不同。"""
        topics = ["m9a.29.2", "m8b.23.2", "m8a.16.3", "m7b.11.1.2",
                  "m7b.7.2.2", "m3a.4.7", "m5a.6.2", "m2a.5.1"]
        changed = 0
        checked = 0
        for topic in topics:
            s = engine.create_session(self.graph, [topic], mode="diagnose")
            it1 = engine.next_item(self.graph, s)
            if it1 is None:
                continue
            q1 = it1["question"]
            engine.apply_answer(self.graph, s, "correct")
            it2 = engine.next_item(self.graph, s)
            if it2 is None:
                continue
            q2 = it2["question"]
            checked += 1
            if q1["type"] != q2["type"]:
                changed += 1
        self.assertGreaterEqual(changed, checked - 1,
                                f"只有 {changed}/{checked} 个点换了题型")

    def test_log_records_question_type(self):
        """log 必须记题型，否则第 2 问无从知道第 1 问是什么题型。"""
        s = engine.create_session(self.graph, ["m9a.29.2"], mode="diagnose")
        engine.next_item(self.graph, s)
        engine.apply_answer(self.graph, s, "correct")
        self.assertTrue(s["log"], "log 为空")
        self.assertIn("questionType", s["log"][0],
                      "log 没记 questionType")

    def test_generated_questions_are_reachable(self):
        """生成的记忆检验题必须能被抽到（否则储备等于白储备）。

        要带 rotate 偏移——服务端每次建会话都会给递增偏移，
        不带的话每次都抽同一道，测不出轮转是否有效。
        """
        gen = {q["id"] for q in self.graph.questions if q.get("generated")}
        self.assertTrue(gen, "图里没有生成题")
        hit = 0
        total = 0
        for topic in ("m9a.29.2", "m8b.23.2", "m8a.16.3", "m7b.7.2.2",
                      "m7b.11.1.2", "m3a.4.7", "m2a.5.1"):
            for rot in range(6):
                s = engine.create_session(self.graph, [topic], mode="diagnose", rotate=rot)
                it = engine.next_item(self.graph, s)
                if it is None:
                    break
                total += 1
                if it["question"]["id"] in gen:
                    hit += 1
        self.assertGreater(hit, 0, "一次都没抽到生成题")

    def test_repeated_sessions_get_different_questions(self):
        """同一知识点反复诊断，应该抽到不同的题。

        这是「储备足够不同类型的题」能生效的前提——
        储备了但每次都抽同一道，等于没储备。
        """
        topic = "m9a.29.2"
        picked = []
        for rot in range(6):
            s = engine.create_session(self.graph, [topic], mode="diagnose", rotate=rot)
            it = engine.next_item(self.graph, s)
            picked.append(it["question"]["id"])
        self.assertGreaterEqual(len(set(picked)), 4,
                                f"6 次会话只抽到 {len(set(picked))} 道不同的题：{picked}")

    def test_rotate_zero_is_stable(self):
        """rotate=0 时行为与原来一致（可复现）。"""
        a = engine.create_session(self.graph, ["m9a.29.2"], mode="diagnose", rotate=0)
        b = engine.create_session(self.graph, ["m9a.29.2"], mode="diagnose", rotate=0)
        qa = engine.next_item(self.graph, a)["question"]["id"]
        qb = engine.next_item(self.graph, b)["question"]["id"]
        self.assertEqual(qa, qb)

    def test_prefer_other_type_helper(self):
        """辅助函数本身的行为。"""
        pool = [{"type": "numeric", "id": "a"}, {"type": "choice", "id": "b"},
                {"type": "numeric", "id": "c"}]
        out = engine._prefer_other_type(pool, {"numeric"})
        self.assertEqual(out[0]["id"], "b", "没用过的题型应该排前面")
        # 没指定已用题型时保持原序
        self.assertEqual(engine._prefer_other_type(pool, set())[0]["id"], "a")


if __name__ == "__main__":
    unittest.main()
