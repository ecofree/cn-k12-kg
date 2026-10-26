"""第五批「两步补题」的守门测试。

**为什么要有这个测试**：2026-10-25 审计发现小学有 74 个知识点只有单一难度
（全是 basic）。根因是自产计算题都是一步题——verify 只有一个运算符，
校准器按「几步运算」给难度，自然全落 basic。

第五批给其中 57 个可计算的点各补了一道两步题。这个测试固化四条纪律：

1. **两步题必须真的是两步**——verify 去掉括号内⑸容后至少 2 个运算符，
   否则 calibrate_difficulty 仍会给 basic，等于白补。
2. **答案必须机器可验**——answer 与 verify 的求值要一致。
3. **答案不能写成分数**——compare() 对非数值走字符串比对，
   "3/4" 与 0.75 永远判 mismatch（历史踩过）。
4. **知识点 id 必须真实**——STEP2_GENERATORS 里写错的 id 会被 build()
   静默跳过，题目就悄悄少了一批。
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


def _load_count_steps():
    """从 calibrate_difficulty 取出 count_steps——难度定档的唯一真源。"""
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
        """两步题的 verify 必须数出 ≥2 步，否则难度仍会被判 basic。"""
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
        """答案不得写成分数形式——compare() 会按字符串比，必然 mismatch。"""
        frac = []
        for tid, gen in self.step2.items():
            rnd = random.Random(f"test-frac:{tid}")
            for _ in range(20):
                answer = gen(rnd)[1]
                if "/" in str(answer):
                    frac.append((tid, answer))
                    break
        self.assertFalse(frac, f"答案写成了分数：{frac[:5]}")

    def test_applied_questions_are_not_basic(self):
        """入库后的两步题（id 以 -q95 结尾）难度必须高于 basic。

        这条测的是「承诺有没有真的兑现」：如果自动生成→入库→校准这条链
        任何一环掉了（比如 build() 没接 STEP2_GENERATORS、或者 apply
        因为去重把题丢了），难度就会退回 basic 而没有任何报错。
        """
        applied = [q for q in self.questions if q["id"].endswith("-q95")]
        self.assertEqual(len(applied), len(self.step2),
                         f"入库的两步题 {len(applied)} 道，应为 {len(self.step2)} 道")
        still_basic = [q["id"] for q in applied if q.get("difficulty") == "basic"]
        self.assertFalse(still_basic, f"两步题仍被判 basic：{still_basic[:5]}")

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
