"""审计 CMM 外部题的答案质量（只读，不改数据）。

**为什么单独做这个审计**：CMM 是小学段唯一的外部题源（小学 12515 个题点対
里 91% 来自它），但它是按 tool 入口进库的 tag-mapped 题，**当前不进诊断**。
想知道它将来值不值得放进来，先要看它的答案能不能用——
如果一半的答案带着 LaTeX 噪声、或者根本没有确定的答案位，
再多的题量也是虚的。

这份审计回答三个问题：
1. 标成 short 的题里，有多少其实是**判断题**（答案只有「正确/错误/√/×」）。
   判断题和填空题的猜测率完全不是一个量级（0.5 vs 接近 0），
   混为一谈会让诊断引擎的猜测率统计失真。
2. 有多少答案带着 **LaTeX 或解析噪声**（`\\frac`、「解：」、整段解析）。
   这类答案既不能机器判分，也不能直接给学生看。
3. 噪声集中在哪些形态上，能不能用规则清洗掉。

用法：
    python tools/audit_cmm_answers.py            # 全量
    python tools/audit_cmm_answers.py --stage primary
    python tools/audit_cmm_answers.py --stage primary --show 10
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 纯判断词答案：可能带括号、可能带句号，但不该有别的内容
JUDGE_ANSWER = re.compile(
    r"^\s*[（(]?\s*(正确|错误|√|×|对|错|是|否|T|F|True|False)\s*[）)]?\s*[。．]?\s*$",
    re.IGNORECASE)

# LaTeX 与解析噪声：出现即说明答案不是「一个干净的答案值」
NOISE = [
    ("LaTeX 命令", re.compile(r"\\[a-zA-Z]+")),
    ("LaTeX 定界符", re.compile(r"\$")),
    ("整抉解析", re.compile(r"(解答|解析|分析|详解)[:：]")),
    ("解字头", re.compile(r"^解[:：]")),
    ("换行", re.compile(r"[\r\n]")),
    ("乱码占位", re.compile(r"(\[a\]|\ufffd|\?\?\?)")),
]


def stage_of(topics: dict[str, dict], tid: str) -> str:
    g = topics.get(tid, {}).get("grade")
    if g is None:
        return "unknown"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def load(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="primary",
                    choices=("primary", "junior", "senior", "all"))
    ap.add_argument("--show", type=int, default=6, help="每类噪声展示几条样本")
    ap.add_argument("--diagnosable", action="store_true",
                    help="只审计已进诊断的题（真正会被用来判分的那批）")
    args = ap.parse_args()

    topics = {t["id"]: t for t in load("topics.json")}
    ext = load("questions_ext.json")
    cmm = [q for q in ext if q.get("source") == "cmm-math"]
    if args.stage != "all":
        cmm = [q for q in cmm
               if any(stage_of(topics, t) == args.stage
                      for t in (q.get("topicIds") or []))]
    if args.diagnosable:
        cmm = [q for q in cmm if q.get("usable_for_diagnosis")]
        print("口径：只统计 usable_for_diagnosis=True 的题")

    print(f"CMM 题（{args.stage} 口径）：{len(cmm)} 道")
    print("题型分布：", dict(collections.Counter(q.get("type") for q in cmm)))

    shorts = [q for q in cmm if q.get("type") == "short"]
    print(f"\n其中 short 题：{len(shorts)} 道")

    judges = [q for q in shorts if JUDGE_ANSWER.match(str(q.get("answer", "")))]
    print(f"  判为判断题（答案只有判断词）：{len(judges)} 道")

    # 噪声统计：一题可能同时命中多种，分别计数再算「至少一种」
    counts: dict[str, list] = collections.defaultdict(list)
    for q in shorts:
        ans = str(q.get("answer", ""))
        for label, pat in NOISE:
            if pat.search(ans):
                counts[label].append(q)
    any_noise = {id(q) for lst in counts.values() for q in lst}
    print(f"  答案带噪声：{len(any_noise)} 道（占 short 的 "
          f"{len(any_noise) / max(1, len(shorts)) * 100:.0f}%）")
    for label, lst in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        print(f"    {label:14s} {len(lst):5d} 道")
        for q in lst[:args.show]:
            print(f"        {q['id'][:24]:26s} {str(q.get('answer'))[:60]!r}")

    print("\n判断题样本：")
    for q in judges[:args.show]:
        print(f"    {q['id'][:24]:26s} {str(q.get('answer'))[:20]!r}  "
              f"{str(q.get('prompt'))[:50]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
