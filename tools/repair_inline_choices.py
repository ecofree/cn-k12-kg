r"""把题干里用 LaTeX 宏写死的选项（\choicesfive{...}{...}）还原成真正的选项。

**发现经过**：给外部题开内容级校验时，有 2 道题的解析在讲「选项 C 正确」，
但该题的 choices 数组是空的。追下去发现 269 道高考题把选项写成 LaTeX 宏
塞在**题干里**：

    函数 y = √(lg(1-x)) 的定义域是______
    \choicesfive {$(-\infty,+\infty)$} {$(-\infty, 0]$} {$(-\infty, 0)$} ...

这批题因此被当成 short 题（没有选项数组），答案写成 'C.'，
其中 19 道还是 cross-checked、当前就在诊断集里——学生被问到时看不到任何选项。

**为什么只修这 19 道**：另外 250 道是 compound-reference，答案字段为空是
**设计如此**（解答题/无法判定答案的题留作复合覆盖），本来就不进诊断。
给它们造选项没有答案可填，反而会造出「有选项但答案空的」更糟的数据。

处置：
  1. 能解析出选项、且答案是字母（A/B/C/D/E）→ 还原成真正的选择题：
     choices 建成 o1..oN，answer 由字母换成 option_id，题干里的宏去掉
     （原文存 prompt_original）。
  2. 选项里有 \choicebitmap（图片选项，无法转成可渲染的文本）→ 封存退出诊断。
  3. 答案不是字母 → 封存退出诊断（猜不出正确项，不能拿去考学生）。

用法：
    python tools/repair_inline_choices.py
    python tools/repair_inline_choices.py --apply
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

MACRO = re.compile(r"\\(?:choicesfive|choicesfour|choices)\s*")
LETTER = re.compile(r"^([A-Ea-e])\s*[.．。]?$")


def brace_groups(text: str, start: int) -> list[str]:
    """从 start 起连续抓取顶层 {...}，支持括号嵌套（如 {$\\frac{1}{2}$}）。"""
    out, i, n = [], start, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n or text[i] != "{":
            break
        depth, j = 0, i
        while j < n:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append(text[i + 1:j].strip())
        i = j + 1
    return out


def plan(q: dict):
    """返回 (处置, 选项列表)。处置 ∈ {restore, quarantine:原因}"""
    prompt = q.get("prompt") or ""
    m = MACRO.search(prompt)
    if not m:
        return "skip", []
    groups = brace_groups(prompt, m.end())
    if len(groups) < 3:
        return f"quarantine:只解析出 {len(groups)} 个选项", groups
    if any("choicebitmap" in g for g in groups):
        return "quarantine:选项是图片（\\choicebitmap），无法转成可渲染文本", groups
    lm = LETTER.match(str(q.get("answer") or "").strip())
    if not lm:
        raw = str(q.get("answer") or "").strip()
        # 多选题（'AC' 这类）：当前 schema 的 answer 是单个 option_id，装不下
        if re.fullmatch(r"[A-Ea-e]{2,}", raw):
            return f"quarantine:多选题（答案 {raw!r}），当前 schema 只支持单选", groups
        return f"quarantine:答案 {raw!r} 不是选项字母", groups
    idx = "ABCDE".index(lm.group(1).upper())
    if idx >= len(groups):
        return f"quarantine:答案 {lm.group(1)} 超出选项数 {len(groups)}", groups
    return "restore", groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=4)
    args = ap.parse_args()

    ext = json.loads(EXT.read_text(encoding="utf-8"))
    restore, quarantine, skipped = [], [], 0
    for q in ext:
        # 只处理「有答案」的那批——无答案的 compound-reference 是本就应该没有
        if not MACRO.search(q.get("prompt") or ""):
            continue
        if not str(q.get("answer") or "").strip():
            skipped += 1
            continue
        action, groups = plan(q)
        if action == "restore":
            restore.append((q, groups))
        elif action.startswith("quarantine"):
            quarantine.append((q, action.split(":", 1)[1], groups))

    print(f"题干含 LaTeX 选项宏、且有答案的题：{len(restore) + len(quarantine)} 道"
          f"（另有 {skipped} 道无答案的 compound-reference，按设计不动）")
    print(f"  可还原成选择题：{len(restore)} 道")
    print(f"  需封存：{len(quarantine)} 道")
    for q, why, _ in quarantine[:args.show]:
        print(f"    {q['id'][:26]} {why}")
    print("\n--- 还原样例 ---")
    for q, groups in restore[:args.show]:
        lm = LETTER.match(str(q.get("answer")).strip())
        print(f"  {q['id'][:26]} 答案 {q.get('answer')!r} → "
              f"o{'ABCDE'.index(lm.group(1).upper()) + 1}，{len(groups)} 个选项")
        for g in groups:
            print(f"      · {g[:56]}")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 落地）")
        return 0

    for q, groups in restore:
        lm = LETTER.match(str(q.get("answer")).strip())
        idx = "ABCDE".index(lm.group(1).upper())
        prompt = q.get("prompt") or ""
        if "prompt_original" not in q:
            q["prompt_original"] = prompt
        q["prompt"] = MACRO.sub("", prompt).rstrip()
        q["type"] = "choice"
        q["choices"] = [{"id": f"o{i + 1}", "text": g} for i, g in enumerate(groups)]
        q["answer"] = f"o{idx + 1}"
        q["accept"] = []
    for q, why, _ in quarantine:
        q["usable_for_diagnosis"] = False
        q["admission_level"] = "broken"
        q["usable_reason"] = "题目残缺，退出诊断（证据仍在，但不可用）"
        q["broken_reason"] = f"题干内嵌 LaTeX 选项：{why}"

    EXT.write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {EXT.relative_to(ROOT)}：还原 {len(restore)} 道，"
          f"封存 {len(quarantine)} 道")
    return 0


if __name__ == "__main__":
    sys.exit(main())
