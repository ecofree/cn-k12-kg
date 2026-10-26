r"""体检外部题库（questions_ext.json）的内容质量——只读，不改数据。

**为什么单独做体检而不是直接改 validate**：外部题 23313 道，占全库 91%，
而 validate.py 的题库校验（题干泄露答案、选项 id、accept 列表……）
**从来只跑在 questions.json 上**。在把那些内容级规则套到外部题之前，
必须先看清楚：有多少题会违反、违反的是不是同一个根因、
哪些是设计上就该如此（比如解答题本来就没有可判分的答案）。

直接把规则开成 error 会让 VALIDATION 一次性炸出上万条错误，
那就等于关掉了这个检查——没人会看、也没人修。
所以先量，再决定「哪些开 error、哪些开 warn、哪些承认为设计取舍」。

用法：
    python tools/audit_ext_quality.py
    python tools/audit_ext_quality.py --by-source      # 按来源分解
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 与 validate.py 保持同一套判定口径（D19：解析不得用字母指代选项）
OPTION_REF_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:选项\s*)?[ABCDabcd](?:\s*(?:项|选项))?"
    r"(?=\s*(?:错|对|正确|错误|是|不是|不|符合|不符合|符合题|排除|应|应为|项))")


class Check:
    """一项体检：命中即记一条，可按来源分解。"""

    def __init__(self, name: str, applies, hit, note: str = ""):
        self.name = name
        self.applies = applies      # (q) -> bool，是否适用
        self.hit = hit              # (q) -> bool，是否命中
        self.note = note
        self.count = 0
        self.by_source = collections.Counter()
        self.examples: list[tuple[str, str]] = []

    def run(self, q: dict):
        if not self.applies(q):
            return
        if self.hit(q):
            self.count += 1
            self.by_source[q.get("source", "?")] += 1
            if len(self.examples) < 3:
                self.examples.append(
                    (q.get("id", "?")[:26],
                     (str(q.get("answer"))[:24] or "(空)") + " | "
                     + str(q.get("prompt", ""))[:40].replace("\n", " ")))


def is_choice(q):
    return q.get("type") == "choice"


def build_checks() -> list[Check]:
    return [
        Check("选择题：答案不在选项 id 内", is_choice,
              lambda q: q.get("answer") not in
              [c.get("id") for c in (q.get("choices") or [])],
              "答案指向不存在的选项 = 这题判不了分"),
        Check("选择题：选项少于 3 个", is_choice,
              lambda q: len(q.get("choices") or []) < 3),
        Check("选择题：选项 id 缺失或重复", is_choice,
              lambda q: (any(c.get("id") is None for c in (q.get("choices") or []))
                         or len({c.get("id") for c in (q.get("choices") or [])})
                         != len(q.get("choices") or []))),
        Check("选择题：选项里还留着 key 字段", is_choice,
              lambda q: any("key" in c for c in (q.get("choices") or [])),
              "字母已改为渲染时生成（M3.1），key 是残留"),
        Check("选择题：选项文本为空", is_choice,
              lambda q: any(not str(c.get("text", "")).strip()
                            for c in (q.get("choices") or []))),
        Check("数值题：缺 verify 表达式", lambda q: q.get("type") == "numeric",
              lambda q: not q.get("verify")),
        Check("短答题：缺 accept 列表", lambda q: q.get("type") == "short",
              lambda q: not isinstance(q.get("accept"), list)),
        Check("答案字段为空", lambda q: True,
              lambda q: q.get("answer") in (None, ""),
              "解答题按设计可为空（compound-reference），需分开看"),
        Check("答案为空但不是 compound-reference", lambda q: True,
              lambda q: q.get("answer") in (None, "")
              and q.get("admission_level") != "compound-reference",
              "这部分是**真**问题：没有答案却不是按设计排除的解答题"),
        Check("解析用字母指代选项", lambda q: bool(q.get("explanation")),
              lambda q: bool(OPTION_REF_RE.search(q.get("explanation") or "")),
              "D19：选项会重排，字母指代的解析必然指错"),
        Check("题干含 LaTeX 但未清洗", lambda q: q.get("type") == "short",
              lambda q: ("$" in str(q.get("answer", ""))
                         or "\\" in str(q.get("answer", "")))
              and not q.get("answer_clean"),
              "清洗器没覆盖到的（unresolved/非 short）"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-source", action="store_true", help="按来源分解")
    args = ap.parse_args()

    ext = json.loads((ROOT / "data" / "questions_ext.json").read_text(encoding="utf-8"))
    checks = build_checks()
    for q in ext:
        for c in checks:
            c.run(q)

    print(f"外部题 {len(ext)} 道")
    print(f"{'体检项':<32}{'命中':>8}{'占比':>8}")
    for c in checks:
        print(f"  {c.name:<30}{c.count:>8}{c.count / len(ext) * 100:>7.1f}%")
        if args.by_source and c.count:
            print(f"      按来源: {dict(c.by_source)}")
        for eid, sample in c.examples:
            print(f"      · {eid:<26} {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
