"""清洗 CMM 外部题 short 型答案的噪声（默认只读报告，--apply 才写库）。

**为什么做**：CMM 是小学段唯一的外部题源（小学 12515 个题点対里 91% 来自它），
但审计（tools/audit_cmm_answers.py）发现 short 题里 **78% 的答案带着噪声**：
`答案: $-7 \\quad+5$`、`解$60 万\\n\\n【分析】…`、`(1) 80 厘米;\\n\\n(2) 20 厘米`。
这样的答案既不能机器判分，也不能直接呈现给学生。

**为什么不直接改 answer 字段**：answer 是**原始证据**。清洗规则再稳也是启发式，
一旦规则出错，我们希望还能回到原文核对。所以落地时写的是两个新字段：
  · `answer_clean`      —— 清洗后的值（没有把握的题不写这个字段）
  · `answer_clean_kind` —— 这一眼判定：「single / judge / multi / unresolved」
answer 原字段**永远不动**。

**清洗纪律（保守优先）**
1. 先砍参考答案结构块：`【分析】…`、`【详解】…`、`【点睛】…` 及其之后全部丢弃。
2. 去答案前缀：`答案:`、`答案：`、`答：`、`解`、`详解`、`解答`——
   只在**开头**去，正文里的「答案」不动。
3. LaTeX 只做**白名单映射**（见 LATEX_MAP），认不出的命令留住不动，
   并把整题判为 `unresolved`——宁可不洗也不编。
4. 洗完之后仍含换行 / 小问编号 `(1)`（1）/`;` 分隔的多值，判为 `multi`：
   一道多问的题本来就不是「一个答案」，单值判分会失真，不假装它是单值。
5. 判断词（√ × 正确 错误）单独判为 `judge`——它是单值，但猜测率是 0.5，
   与填空题不是一个量级，必须能单独挑出来。

用法：
    python tools/clean_cmm_answers.py                      # 全学段 dry-run
    python tools/clean_cmm_answers.py --stage primary      # 只看小学
    python tools/clean_cmm_answers.py --apply              # 写库
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "questions_ext.json"

# ---- 步骤 1：参考答案结构块，出现即截断 ----
# 点拨/分析/详解/点睛/解析/考点/点评/解答——都是题源里的「讲解块」，
# 任何一种出现在答案里，其后内容一律丢弃（131+ 道曾被遗漏）
SECTION_MARK = re.compile(
    r"【(分析|详解|点睛|解析|考点|点评|解答|点拨|思路|总结|详解)】"
    # 题源 OCR 会把手写标记搞坏（见过【洋解】=【详解】），所以再加一条通则：
    # 「【 + 6 字以内的中文短标题 + 】」一律当讲解块的开头切掉，
    # 【答案】除外——它其实是答案的前缀写法。
    r"|【(?!答案|参考答案)[一-鿿]{1,6}】")

# ---- 步骤 2：答案前缀 ----
PREFIX = re.compile(
    r"^\s*(答案|参考答案|答|解|详解|解题思路)\s*[:：]?\s*"
    # 另一种写法：「【答案】 9」——整块作答需要当前缀剥掉，而不是整题丢掉
    r"|^\s*【(答案|参考答案|解答|详解)】\s*")

# ---- 步骤 3：LaTeX 白名单映射（认不出的命令不动） ----
LATEX_MAP = [
    # 括号类必须排在 \le/\geq 之前：`\left[` 里的 "le" 会被 \le 规则啃掉，
    # 实测洗出过 '≤ft[3/4, 3]'。每条命令再带 (?![a-zA-Z]) 兜底防前缀误吃。
    (re.compile(r"\\left(?![a-zA-Z])"), ""),
    (re.compile(r"\\right(?![a-zA-Z])"), ""),
    # 分隔类：\quad 在答案里几乎总是分隔多个空/多个得数，
    # 换成空格会把「-7 \quad +5」伪装成单值，必须换成保留多值语义的符号
    (re.compile(r"\\quad(?![a-zA-Z])|\\qquad(?![a-zA-Z])"), ";"),
    (re.compile(r"\\,(?![a-zA-Z])"), " "),
    (re.compile(r"\\;(?![a-zA-Z])"), " "),
    (re.compile(r"\\!"), ""),
    (re.compile(r"\\ "), " "),
    (re.compile(r"~"), " "),
    (re.compile(r"\\times(?![a-zA-Z])"), "×"),
    (re.compile(r"\\div(?![a-zA-Z])"), "÷"),
    (re.compile(r"\\pm(?![a-zA-Z])"), "±"),
    (re.compile(r"\\neq(?![a-zA-Z])|\\ne(?![a-zA-Z])"), "≠"),
    (re.compile(r"\\geq(?![a-zA-Z])|\\ge(?![a-zA-Z])"), "≥"),
    (re.compile(r"\\leq(?![a-zA-Z])|\\le(?![a-zA-Z])"), "≤"),
    (re.compile(r"\\cdot(?![a-zA-Z])"), "·"),
    (re.compile(r"\\circ(?![a-zA-Z])"), "°"),
    (re.compile(r"\\%(?![a-zA-Z])"), "%"),
    # 转义字符：\# \& \_ \{ \} 在答案里就是字面的符号
    (re.compile(r"\\#"), "#"),
    (re.compile(r"\\&"), "&"),
    (re.compile(r"\\_"), "_"),
    (re.compile(r"\\\{"), "{"),
    (re.compile(r"\\\}"), "}"),
    # √ ：题源里 **空** radicand 的 $\sqrt{ }$ 是判断题的「√」（与 $\times$ 配对的
    # 「√/×」），实测 746 条全是判断题；有 radicand 的就是真根号（\sqrt{2}→√2）
    (re.compile(r"\\sqrt\s*\{([^{}]*)\}"), r"√\1"),
    # 大概率出现在**答案值本身**里的确定符号；\because/\therefore/\begin{array}
    # 出现在馨考解答里，故意不收白名单，让它们留下判 unresolved
    (re.compile(r"\\leqslant(?![a-zA-Z])"), "≤"),
    (re.compile(r"\\geqslant(?![a-zA-Z])"), "≥"),
    (re.compile(r"\\approx(?![a-zA-Z])"), "≈"),
    (re.compile(r"\\equiv(?![a-zA-Z])"), "≡"),
    (re.compile(r"\\sim(?![a-zA-Z])"), "∽"),
    (re.compile(r"\\parallel(?![a-zA-Z])"), "∥"),
    (re.compile(r"\\perp(?![a-zA-Z])"), "⊥"),
    (re.compile(r"\\angle(?![a-zA-Z])"), "∠"),
    (re.compile(r"\\triangle(?![a-zA-Z])"), "△"),
    (re.compile(r"\\pi(?![a-zA-Z])"), "π"),
    (re.compile(r"\\mathrm\s*\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\text\s*\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\$"), ""),          # 行内定界符，去
]
# 分式：\frac{a}{b} → a/b（逐个替换的安全版见 clean_answer）
FRAC = [(re.compile(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r"\1/\2")]

# ---- 步骤 4：多值判定 ----
SUB_Q = re.compile(r"[（(]\s*[1-9]\s*[）)]")
# 控制字符（换行/回车之外的）：换页符、垂直制表符这类会把 LaTeX 命令名吃掉，
# 实测 127 条答案里有（如 '$\x0crac{1}{10}$'），这种源码洗不出来。
CTRL_CHAR = re.compile(r"[\x00-\x09\x0b-\x1f]")

MULTI = "multi"
# 多个纯数/纯符号词用空格连排 = 一题多空
# （'3 3000 100 1 24 210'、'= > < < < <' 都不能当单值判分）。
# 带单位的（'2 米'）不算——单位不是另一个答案。
MATH_TOKEN = re.compile(r"^[-+*/×÷=<>≤≥≠%()\[\],.、/\d]+$")

# ---- 步骤 5：判断词 ----
JUDGE_WORD = re.compile(
    r"^\s*[（(]?\s*(正确|错误|√|×|对|错|是的|不是|真假|T|F)\s*[）)]?\s*[。．]?\s*$",
    re.IGNORECASE)

# 源码本身就可能坏了：见过 '$-frac{3}{4}-0.75$'（应是 $-\frac{3}{4}$），
# 少了反斜杠，这种情况下「洗出来」的值不可信，判 unresolved 让人看原文。
BARE_CMD = re.compile(r"(?<![\\a-zA-Z])(frac|sqrt|times|div|mathrm|dfrac)\s*\{")

SINGLE = "single"
JUDGE = "judge"
PROSE = "prose"               # 汉字 ≥10 个——那多半是「解题思路」不是答案值
UNRESOLVED = "unresolved"


def clean_answer(raw: str) -> tuple[str, str]:
    """清洗单条答案，返回 (清洗后的值, 判定类别)。

    类别：
      single     单值且清洗干净（可直接用于判分）
      judge      单值判断词（√/×/正确/错误）
      multi      多值（多小问/列举），不可当单值判分
      unresolved 认不出的 LaTeX 结构，规则不敢动
    """
    text = str(raw or "").strip()
    if not text:
        return "", UNRESOLVED

    # 1. 砍掉参考答案结构块
    m = SECTION_MARK.search(text)
    if m:
        text = text[:m.start()]
    # 2. 去答案前缀（可能叠了多层：'详解答案:12'）
    for _ in range(3):
        new = PREFIX.sub("", text, count=1)
        if new == text:
            break
        text = new
    # 3. LaTeX 白名单映射
    for pat, rep in LATEX_MAP:
        text = pat.sub(rep, text)
    # 分式：\frac{a}{b} → a/b，**逐个替换并逐个复查**。
    #
    # 不能一次 sub 全替换完：嵌套分式 \frac{1}{\frac{2}{3}} 会先被换成
    # \frac{1}{2/3}，再换成 '1/2/3'——真值是 3/2，输出却是错的答案。
    # 所以每轮只换一个，并且换之前先确认它的两个参数是干净的
    # （不含 / 也不含别的 \），否则宁可整条判 unresolved。
    for _ in range(20):
        m = FRAC[0][0].search(text)
        if not m:
            break
        if "/" in m.group(1) or "/" in m.group(2) or "\\" in (m.group(1) + m.group(2)):
            return text.strip(), UNRESOLVED
        text = FRAC[0][0].sub(FRAC[0][1], text, count=1)

    # 还剩任何反斜杠，说明遇到了白名单外的结构（\begin{array}、\because、
    # 孤立的 \>），规则不敢妄改——数学答案里正常不会有裸反斜杠。
    if "\\" in text:
        return text.strip(), UNRESOLVED
    # 还剩【…】说明这是题源里的分版式结构（【小题1】…）——不是一个答案值
    if "【" in text:
        return text.strip(), UNRESOLVED

    # 4. 归一化空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    text = re.sub(r"^[;；\s]+|[;；\s]+$", "", text)     # 去首尾多余分隔符
    text = re.sub(r";{2,}", ";", text)
    # 去句末句号**之后**必须再 strip 一次：'故答案为: 236,240 。'
    # 去掉 。 后会留下尾随空格，不补一次 strip 的话清洗就不幂等
    # （实测 176 条会在二次清洗时再变一次）。
    text = text.rstrip("。．.").strip()

    if not text:
        return "", UNRESOLVED

    # 控制字符 = 源码已经被写坏：见过 '$\x0crac{1}{10}$'，\frac 的 \f
    # 被存成了真换页符，命令名都被吃掉了，规则无从复原。
    if CTRL_CHAR.search(text):
        return text, UNRESOLVED

    # 5. 多值判定：换行 / 小问编号 / 分号 / 多个纯数连排
    if "\n" in text or SUB_Q.search(text) or ";" in text or "；" in text:
        return text, MULTI
    toks = text.split(" ")
    if len(toks) >= 2 and all(MATH_TOKEN.match(t) for t in toks):
        return text, MULTI

    if BARE_CMD.search(text):
        return text.strip(), UNRESOLVED

    if JUDGE_WORD.match(text):
        return text.strip(), JUDGE

    # 汉字 ≥10 个的不会是答案值，是解题思路（'根据中位数的定义解答即可'）
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 10:
        return text, PROSE

    return text, SINGLE


def stage_of(tmap: dict[str, dict], tid: str) -> str:
    g = tmap.get(tid, {}).get("grade")
    if g is None:
        return "unknown"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=("primary", "junior", "senior", "all"))
    ap.add_argument("--apply", action="store_true", help="写入 questions_ext.json")
    ap.add_argument("--show", type=int, default=5, help="每类展示几条样本")
    args = ap.parse_args()

    topics = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    tmap = {t["id"]: t for t in topics}
    ext = json.loads(EXT.read_text(encoding="utf-8"))

    targets = [q for q in ext
               if q.get("source") == "cmm-math" and q.get("type") == "short"]
    if args.stage != "all":
        targets = [q for q in targets
                   if any(stage_of(tmap, t) == args.stage
                          for t in (q.get("topicIds") or []))]

    kinds: dict[str, list] = collections.defaultdict(list)
    for q in targets:
        cleaned, kind = clean_answer(q.get("answer"))
        kinds[kind].append((q, cleaned))

    total = len(targets)
    all_kinds = (SINGLE, JUDGE, MULTI, PROSE, UNRESOLVED)
    print(f"CMM short 题（{args.stage}）：{total} 道")
    print(f"{'类别':<12}{'题数':>8}{'占比':>8}")
    for kind in all_kinds:
        lst = kinds.get(kind, [])
        print(f"  {kind:<10}{len(lst):>8}{len(lst) / max(1, total) * 100:>7.0f}%")
    usable = len(kinds.get(SINGLE, [])) + len(kinds.get(JUDGE, []))
    print(f"\n可当作单值判分的：{usable} 道（{usable / max(1, total) * 100:.0f}%）")

    for kind in all_kinds:
        lst = kinds.get(kind, [])[:args.show]
        if not lst:
            continue
        print(f"\n--- {kind} 样本 ---")
        for q, cleaned in lst:
            raw = str(q.get("answer", "")).replace("\n", "\\n")[:56]
            print(f"    {q['id'][:26]:28s}")
            print(f"      原: {raw}")
            print(f"      洗: {cleaned.replace(chr(10), ' / ')[:56]}")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 落地）")
        return 0

    # 落地：只写新字段，answer 原字段不动
    changed = 0
    for kind, lst in kinds.items():
        for q, cleaned in lst:
            if kind == UNRESOLVED or not cleaned:
                continue                     # 没把握的不写 clean
            if q.get("answer_clean") != cleaned:
                changed += 1
            q["answer_clean"] = cleaned
            q["answer_clean_kind"] = kind
            # 判分口径：compare_short 先比 answer（带噪声的原文），再比 accept。
            # 把清洗后的值写进 accept，相当于告诉判分器「这个干净值也算对」。
            # 只给 single/judge——multi 是多空答案，prose 是解题思路，
            # 把整段话塞进 accept 会让「答了个大概」也被判对。
            if kind in (SINGLE, JUDGE):
                acc = q.get("accept")
                acc = list(acc) if isinstance(acc, list) else []
                if cleaned not in acc:
                    acc.append(cleaned)
                q["accept"] = acc
    EXT.write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {EXT.relative_to(ROOT)}：answer_clean 变动 {changed} 道，"
          f"answer 原字段未改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
