"""历史两步题与正式作者改写的回归。

保留57个旧生成器的算式、数值和身份检查；正式发布由作者JSON决定。
稳定的-q95后缀不承诺题目永远保持复合运算。V5逐题审查将三条低年级
任务改为十与一或凑十的适龄基础练习，须绑定实际改写内容和观察范围。
其余题仍须满足原来的复合算式与非basic设计要求，不能泛化放行。

运算符数量只是历史算式结构检查，不是实测学生难度。旧生成器继续保持
整数/小数输出约定；这不表示现行精确判分不支持分数。
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.verify import check_question  # noqa: E402
from tools import gen_calc_questions as G  # noqa: E402

REVIEWED_SINGLE_OPERATION = {
    "m1a.5.5-q95": {"verify": "8+1", "answer": "9", "family_id": "primary.additive-structure.continue-from-current", "criterion_indices": []},
    "m1a.4.1-q95": {"verify": "10+8", "answer": "18", "family_id": "placevalue.ten-and-ones", "criterion_indices": [1]},
    "m1a.5.1-q95": {"verify": "9+3", "answer": "12", "family_id": "addition.make-ten", "criterion_indices": [0]},
    "m1a.5.2-q95": {"verify": "8+8", "answer": "16", "family_id": "addition.make-ten", "criterion_indices": [0]},
}


def _load_count_steps():
    """复用旧校准器的算式计数，不把它当作现行难度的唯一依据。"""
    spec = importlib.util.spec_from_file_location(
        "calibrate_difficulty", ROOT / "tools" / "calibrate_difficulty.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.count_steps


class TestStep2Questions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
        cls.tmap = {t["id"]: t for t in cls.topics}
        cls.count_steps = staticmethod(_load_count_steps())
        cls.step2 = G.STEP2_GENERATORS
        cls.questions = json.loads(
            (ROOT / "data" / "questions.json").read_text(encoding="utf-8"))

    def test_topic_ids_are_real(self):
        """STEP2_GENERATORS 的每个 key 都必须是真实知识点 id。"""
        bogus = [tid for tid in self.step2 if tid not in self.tmap]
        self.assertFalse(bogus, f"第五批引用了不存在的知识点：{bogus}")

    def test_all_targets_are_primary(self):
        """第五批的靶点是小学单一难度点——建表的本意，防止误扩到初高中。"""
        wrong = [tid for tid in self.step2
                 if (self.tmap[tid].get("grade") or 99) > 6]
        self.assertFalse(wrong, f"第五批混入了非小学点：{wrong}")

    def test_questions_are_really_two_step(self):
        """历史生成器继续输出至少两个运算，不静默退化其原约定。"""
        too_shallow = []
        for tid, gen in self.step2.items():
            rnd = random.Random(f"test-steps:{tid}")
            for _ in range(20):
                verify = gen(rnd)[2]
                if self.count_steps(verify) < 2:
                    too_shallow.append((tid, verify))
                    break
        self.assertFalse(too_shallow, f"这些题其实只有一步：{too_shallow[:5]}")

    def test_verifiable_and_consistent(self):
        """三种子压测：answer 必须与 verify 的求值一致。"""
        bad = []
        for tid, gen in self.step2.items():
            for tag in ("t1", "t2", "t3"):
                rnd = random.Random(f"{tag}:{tid}")
                for i in range(15):
                    prompt, answer, verify = gen(rnd)[:3]
                    res = check_question({"id": f"{tid}-{i}",
                                          "answer": answer, "verify": verify})
                    if res["status"] != "pass":
                        bad.append((tid, prompt[:30], answer, verify, res["detail"][:40]))
        self.assertFalse(bad, f"有题验算不过：{bad[:5]}")

    def test_answers_are_not_fractions(self):
        """保留旧生成器的整数/小数输出约定，非现行判分能力限制。"""
        frac = []
        for tid, gen in self.step2.items():
            rnd = random.Random(f"test-frac:{tid}")
            for _ in range(20):
                answer = gen(rnd)[1]
                if "/" in str(answer):
                    frac.append((tid, answer))
                    break
        self.assertFalse(frac, f"答案写成了分数：{frac[:5]}")

    def test_published_structure_and_design_level_follow_explicit_content_review(self):
        """只放行有逐题语义核对的四条基础任务，其余仍保留复合要求。"""
        applied = [q for q in self.questions if q["id"].endswith("-q95")]
        expected_ids = {tid + "-q95" for tid in self.step2}
        self.assertEqual(expected_ids, {q["id"] for q in applied})
        self.assertEqual(len(expected_ids), len(applied), "稳定身份不可缺失或重复")
        review = json.loads((ROOT / "docs/reviews/v5-batch2-primary-ai-review.json").read_text(encoding="utf-8"))
        reviewed = {row["id"]: row for row in review["tasks"]}
        v6 = json.loads((ROOT / "docs/reviews/v6-corrections-ai-review.json").read_text(encoding="utf-8"))
        reviewed.update({row["id"]: row for row in v6["tasks"]})
        for question in applied:
            qid = question["id"]
            with self.subTest(question=qid):
                if qid not in REVIEWED_SINGLE_OPERATION:
                    self.assertGreaterEqual(self.count_steps(question["verify"]), 2)
                    self.assertIn(question["difficulty"], ("standard", "advanced"))
                    continue
                expected, decision = REVIEWED_SINGLE_OPERATION[qid], reviewed[qid]
                self.assertTrue(decision["semantic_change"])
                self.assertEqual("data/content/yishang.json", decision["source"])
                self.assertEqual(question["prompt"], decision["new_prompt"])
                self.assertEqual(question["answer"], decision["new_answer"])
                self.assertEqual(question["alignment_note"], decision["observation_boundary"])
                self.assertEqual(expected["verify"], question["verify"])
                self.assertEqual(expected["answer"], question["answer"])
                self.assertEqual(expected["family_id"], question["family_id"])
                self.assertEqual(expected["criterion_indices"], question.get("tested_criterion_indices", []))
                self.assertEqual(expected["criterion_indices"], decision["new_tested_criterion_indices"])
                self.assertEqual(1, self.count_steps(question["verify"]))
                self.assertEqual("basic", question["difficulty"])
                self.assertEqual("component_practice_only", question["target_alignment"])
                self.assertFalse(question["diagnosis_eligible"])

    def test_prompts_are_unique(self):
        """同一批内题干不得重复——重复题会让「不同问法」的要求失效。"""
        seen, dupes = set(), []
        for tid, gen in self.step2.items():
            prompt = gen(random.Random(f"{G.SEED if hasattr(G, 'SEED') else 20261024}"
                                       f":step2:{tid}"))[0]
            if prompt in seen:
                dupes.append(tid)
            seen.add(prompt)
        self.assertFalse(dupes, f"题干重复的点：{dupes}")


if __name__ == "__main__":
    unittest.main()
