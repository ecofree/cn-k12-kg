r"""把外部题解析里「按字母指代选项」的写法换成按内容指代。

**为什么要改**：字母已经不再存进数据（M3.1），显示用的 A/B/C/D 是渲染时按位置
生成的。解析里写「选项 C 符合题意」，只要选项顺序一变就必然指错——
D19 就是这个事故。体检发现外部题里有 357 条解析带这类写法（其中一部分是
真指代，一部分是数学变量 a、b、c 的误报，见下）。

**为什么只收高精度模式**：直接套 validate.py 的 OPTION_REF_RE 会大量误报——
「若 b 是 a、c 的比例中项」里的 b/a/c 会被当成选项字母，「B 的坐标」也会被算上。
所以这里只认**明确带选项语义**的写法：

    选项 C        C 选项        D 项        故答案为: D

其它一律不动（宁可漏，不可改错）。

**替换成什么**：换成该选项的**内容**，用「」包起来。
    「选项 C 符合题意。」        → 「π 倍」符合题意。
    「B 选项不能围成三角形」      → 「4、5、9」不能围成三角形
这样解析与选项顺序彻底解耦。对不懂的选项（如字母超出选项数）不动。

**可逆**：改写前把原文存进 `explanation_original`，随时能对照回去。

用法：
    python tools/fix_explanation_letters.py          # 报告 + 改后样例
    python tools/fix_explanation_letters.py --apply  # 写库
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

# 只认这四种写法——都带明确的「选项/项/答案」语义，不会误伤数学变量
PATTERNS = [
    re.compile(r"选项\s*([A-D])"),
    re.compile(r"([A-D])\s*选项"),
    re.compile(r"(?<![A-Za-z0-9])([A-D])\s*项(?![a-zA-Z])"),
    re.compile(r"故答案为[:：]?\s*([A-D])(?![A-Za-z0-9])"),
]
LETTER_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}
MAX_TEXT = 40          # 选项内容过长就截断，避免把解析撑爆


def quote(text: str) -> str:
    text = str(text or "").strip().replace("\n", " ")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "…"
    return f"「{text}」"


def rewrite(explanation: str, choices: list[dict]) -> tuple[str, int, int]:
    """返回 (改写后的解析, 替换处数, 无法解析的引用处数)。

    「无法解析」指的是解析引用了**不存在的选项**（如写着「D 选项」而题目
    只存了 3 项）——这不是字母写法的问题，而是源数据的选项没导全。
    这种题不能靠改写救回来，交给调用方封存。
    """
    n = 0
    unresolvable = 0
    out = explanation
    for pat in PATTERNS:
        def sub(m: re.Match) -> str:
            nonlocal n, unresolvable
            idx = LETTER_INDEX[m.group(1)]
            if idx >= len(choices):
                unresolvable += 1
                return m.group(0)          # 字母超出选项数，不动
            n += 1
            return quote(choices[idx].get("text"))
        out = pat.sub(sub, out)
    return out, n, unresolvable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    ext = json.loads(EXT.read_text(encoding="utf-8"))
    touched, broken = [], []
    per_source = collections.Counter()
    for q in ext:
        if q.get("type") != "choice" or not q.get("explanation"):
            continue
        new, n, bad = rewrite(q["explanation"], q.get("choices") or [])
        if n:
            touched.append((q, new, n))
            per_source[q.get("source")] += 1
        if bad:
            broken.append((q, bad))

    total = sum(n for _, _, n in touched)
    print(f"命中解析 {len(touched)} 条，替换 {total} 处")
    print(f"按来源：{dict(per_source)}")
    print(f"引用了不存在选项（源数据选项缺失）的题：{len(broken)} 道")
    for q, bad in broken[:args.show]:
        print(f"  {q['id'][:26]} 引用 {bad} 处，但该题干下只有 "
              f"{len(q.get('choices') or [])} 个选项")
    print("\n--- 改后样例 ---")
    for q, new, n in touched[:args.show]:
        print(f"  {q['id'][:26]} （{n} 处）")
        print(f"    原: {q['explanation'][:88].replace(chr(10), ' ')}")
        print(f"    改: {new[:88].replace(chr(10), ' ')}")

    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 落地）")
        return 0

    for q, new, _ in touched:
        if "explanation_original" not in q:
            q["explanation_original"] = q["explanation"]
        q["explanation"] = new
    # 选项缺失的题退出诊断：改写救不了它，留着会被问出「D 选项」这种空指
    for q, _ in broken:
        # 同 repair_empty_options：封存必须连同准入等级一起降级，
        # 否则「cross-checked ⟹ 可诊断」这条不变式会被打破。
        q["usable_for_diagnosis"] = False
        q["admission_level"] = "broken"
        q["usable_reason"] = "题目残缺，退出诊断（证据仍在，但不可用）"
        q["broken_reason"] = "解析引用了不存在的选项，源数据选项缺失"
    EXT.write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {EXT.relative_to(ROOT)}：改写 {len(touched)} 条解析"
          f"（原文保存在 explanation_original），封存选项缺失题 {len(broken)} 道")
    return 0


if __name__ == "__main__":
    sys.exit(main())
