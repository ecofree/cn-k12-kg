r"""CMM 答案清洗的守门测试。

**为什么要有这个测试**：清洗规则是启发式，而它洗的是 **7135 道外部题的答案**。
规则错了不会报错，只会静默产出一批「看似干净其实是错的」值——
一旦这批值将来被用于判分，错误会被放大成错误的诊断结论。

这个测试固化四条底线：
1. **历史 bug 不得复活**——`\left[` 曾被 `\le` 规则啃成 '≤ft['；
   `\quad` 曾被当成空格，把多空题伪装成单值。
2. **洗出来的值必须真的干净**（无 `$`、无残留命令、无参考答案块）。
3. **answer 原字段永远不动**——清洗是增补字段，不是覆盖证据。
4. **清洗必须幂等**——同一 irreducible 值洗两次结果相同，否则每次重跑
   都会产生不同的 `answer_clean`，数据会一直在漂。
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 缺文件时整组跳过，而不是报失败——否则 clone 下来第一件事就是红一片。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

sys.path.insert(0, str(ROOT))

from tools.clean_cmm_answers import (  # noqa: E402
    JUDGE, MULTI, PROSE, SINGLE, UNRESOLVED, clean_answer,
)

# 洗完之后不允许出现的东西
FORBIDDEN = [
    ("LaTeX 定界符", lambda t: "$" in t),
    ("残留命令", lambda t: "\\" in t),
    ("参考答案块", lambda t: "【" in t),
    ("答案前缀", lambda t: t.startswith(("答案", "参考答案", "详解"))),
    # 历史事故：\left[ 曾被 \le 规则啃成 '≤ft['，必须显式守住
    ("\\left 被 \\le 啃掉", lambda t: "≤ft" in t),
]
# multi 类别**故意**保留换行：多小问的答案本来就是多行，
# 要求它没有换行等于逼规则把多值硬拼成一个假的单值。
FORBIDDEN_NON_MULTI = [("换行", lambda t: "\n" in t or "\r" in t)]


def apply_in_memory(ext: list[dict]) -> list[dict]:
    """复刻 clean_cmm_answers 的落地逻辑，但不碰磁盘。"""
    out = copy.deepcopy(ext)
    for q in out:
        if q.get("source") != "cmm-math" or q.get("type") != "short":
            continue
        cleaned, kind = clean_answer(q.get("answer"))
        if kind == UNRESOLVED or not cleaned:
            continue                       # 没把握的不写
        q["answer_clean"] = cleaned
        q["answer_clean_kind"] = kind
    return out


class TestCleanCmmAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ext = json.loads(
            (ROOT / "data" / "questions_ext.json").read_text(encoding="utf-8"))
        cls.shorts = [q for q in cls.ext
                      if q.get("source") == "cmm-math" and q.get("type") == "short"]

    # ---------- 历史事故 ----------
    def test_left_bracket_not_eaten_by_le(self):
        """`\\left[` 不得被 `\\le` 规则啃成 '≤ft['（2026-10-25 实际发生过）。"""
        cleaned, kind = clean_answer(r"$\left[\frac{3}{4}, 3\right]$")
        self.assertNotIn("≤ft", cleaned, f"\\left 规则优先级又退化了：{cleaned!r}")
        self.assertEqual(kind, MULTI)

    def test_quad_is_separator_not_space(self):
        """`\\quad` 是多空分隔符：'$-7 \\quad +5$' 必须判 multi 而不是 single。"""
        cleaned, kind = clean_answer(r"$-7 \quad+5$")
        self.assertEqual(kind, MULTI, f"多空被当成单值：{cleaned!r} {kind}")

    def test_unit_is_not_another_answer(self):
        """带单位的是单值：'2 米' 不能被估值成 multi。"""
        cleaned, kind = clean_answer("2 米")
        self.assertEqual(kind, SINGLE, f"{cleaned!r} 被误判为 {kind}")

    def test_sqrt_empty_is_judge_mark(self):
        """原式用 $\\sqrt{ }$ 当判断题的「√」，不是根号。"""
        self.assertEqual(clean_answer(r"解$\sqrt{ }$"), ("√", JUDGE))

    def test_sqrt_with_radicand_is_value(self):
        """有真 radicand 的是根号值，别当成判断题。"""
        cleaned, kind = clean_answer(r"$\sqrt{2}$")
        self.assertEqual(cleaned, "√2")
        self.assertEqual(kind, SINGLE)

    def test_judge_words_survive_prefix_stripping(self):
        """'解$\\times$' 洗完必须落在 judge 而不是 unknown 类别。"""
        cleaned, kind = clean_answer(r"解$\times$")
        self.assertEqual((cleaned, kind), ("×", JUDGE))

    def test_analysis_section_is_dropped(self):
        """参考答案块整段丢弃，只留答案本身。"""
        cleaned, kind = clean_answer("7\n\n【分析】根据题意可知, 开始时间是 14 时 30 分")
        self.assertEqual(cleaned, "7")
        self.assertEqual(kind, SINGLE)

    def test_prose_is_not_an_answer_value(self):
        """汉字 ≥10 个的是解题思路，不是可判分的答案值。"""
        cleaned, kind = clean_answer("根据中位数的定义解答即可")
        self.assertEqual(kind, PROSE)

    def test_corrupt_source_is_unresolved(self):
        """少了反斜杠的源码（'$-frac{3}{4}$'）不得擅自「修好」。"""
        cleaned, kind = clean_answer(r"$-frac{3}{4}-0.75$")
        self.assertEqual(kind, UNRESOLVED, f"损坏源码被当成了值：{cleaned!r}")

    def test_nested_frac_is_unresolved(self):
        """嵌套分式不处理，交 unresolved。"""
        _, kind = clean_answer(r"$\frac{1}{\frac{2}{3}}$")
        self.assertEqual(kind, UNRESOLVED)

    def test_empty_answer_is_unresolved(self):
        cleaned, kind = clean_answer("")
        self.assertEqual(cleaned, "")
        self.assertEqual(kind, UNRESOLVED)

    # ---------- 全库层面 ----------
    def test_cleaned_values_are_really_clean(self):
        """洗出来写进库的值，必须真的没有残余噪声。"""
        bad = []
        for q in self.shorts:
            cleaned, kind = clean_answer(q.get("answer"))
            if kind == UNRESOLVED or not cleaned:
                continue
            cleaned, kind = clean_answer(q.get("answer"))
            if kind == UNRESOLVED or not cleaned:
                continue
            for label, test in FORBIDDEN:
                if test(cleaned):
                    bad.append((q["id"], label, cleaned[:30]))
            if kind != MULTI:
                for label, test in FORBIDDEN_NON_MULTI:
                    if test(cleaned):
                        bad.append((q["id"], label, cleaned[:30]))
        self.assertFalse(bad, f"有值没洗干净（共 {len(bad)} 条）：{bad[:3]}")

    def test_idempotent(self):
        """清洗幂等：洗一次和洗两次必须一样，否则数据会一直漂。"""
        drift = []
        for q in self.shorts:
            once, k1 = clean_answer(q.get("answer"))
            if k1 == UNRESOLVED or not once:
                continue
            twice, _ = clean_answer(once)
            if twice != once:
                drift.append((q["id"], once[:30], twice[:30]))
        self.assertFalse(drift, f"清洗不幂等（共 {len(drift)} 条）：{drift[:3]}")

    def test_judge_values_are_from_the_allowed_set(self):
        """judge 类别的取值只能是那几个判断词——多一个就说明混进了别的东西。"""
        allowed = {"√", "×", "对", "错", "正确", "错误", "是的", "不是",
                   "真假", "T", "F", "是", "否"}
        outside = {(q["id"], c) for q in self.shorts
                   for c, k in [clean_answer(q.get("answer"))]
                   if k == JUDGE and c.strip() not in allowed}
        self.assertFalse(outside, f"judge 取值越界：{list(outside)[:5]}")

    def test_apply_never_touches_original_answer(self):
        """落地逻辑不得改动 answer 原字段——清洗是增补，不是覆盖证据。"""
        before = {q["id"]: q.get("answer") for q in self.shorts}
        applied = apply_in_memory(self.ext)
        after = {q["id"]: q.get("answer") for q in applied if q["id"] in before}
        self.assertEqual(before, after, "answer 原字段被清洗逻辑改动了")

        written = [q for q in applied if "answer_clean" in q]
        self.assertTrue(written, "一道题都没写进去——落地逻辑坏了？")
        # 写进去的每一条都必须带上类别，否则下游不知道能不能拿它判分
        self.assertTrue(all(q.get("answer_clean_kind") for q in written))


if __name__ == "__main__":
    unittest.main()
