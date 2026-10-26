"""导入上海中考题到 data/questions_ext.json。

**不复用 tools/import_question_bank.py**，因为中考题有三个它不覆盖的特征：

1. **填空题没有选项**。导入工具的 check_structure 要求 ≥3 选项，会把填空题全拒。
2. **公式没有 $ 定界符**。中考卷的公式本来就是位图，转出来的上标是裸的 `x^{2}`，
   不是 `x^2` 也不是 `$x^{2}$`。前端 math.js 只渲染带定界符的公式，
   所以入库时要补定界符，否则 KaTeX 不会处理。
3. **答案来源不同**。选择题的答案来自官方解析「故选：X」，且题目区与答案区的
   选项文本已做双向一致性校验（100% 通过）——这是比 CMM-Math 更强的证据。

**入库门槛**（比 D25 对 CMM 的更严，因为许可状态更差）：
- 全部 `usable_for_diagnosis: false`，不进诊断
- 标注 `mapping_basis: kaodian-tag`（考点标签映射）而非 `ai-mapped`
- 标注完整来源链路与许可状态
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAND = ROOT / "inbox" / "candidates" / "smse_junior.jsonl"
OUT = DATA / "questions_ext.json"

sys.path.insert(0, str(ROOT / "tools"))
from import_question_bank import POLLUTION, fp  # noqa: E402

# 裸 LaTeX 片段 → 加 $ 定界符
# 只处理明确的数学记号：x^{2}、\frac{}{}、\sqrt{}、\pi 等
# 底数可以是：字母、字母+数字、括号（(2a-b)²、(x+1)²）
MATH = re.compile(
    # 底数：全角或半角括号 / 字母+数字
    r"(?:[（(][^()（）]{1,20}[）)]|[a-zA-Z][0-9]{0,2})\^\{[^}]*\}"
    r"|\\[a-zA-Z]+\{[^}]*\}(?:\{[^}]*\})?"
    r"|\\(?:pi|alpha|beta|theta|phi|cdot|times|div|pm|leq|geq|neq|approx|in)\b"
)

# 已经是定界的（含 $）不处理
# 全角数学符号 → LaTeX 记号。
# KaTeX 能渲染全角括号，但会警告 "Unicode text character used in math mode"，
# 而 U+FE63（﹣ 小连字符）是**完全无法识别**的，会显示成缺字符号。
FULLWIDTH = {
    "（": "(", "）": ")",
    "﹣": "-", "－": "-", "−": "-", "–": "-", "—": "-",
    "＋": "+", "×": r"\times", "÷": r"\div",
    "＝": "=", "≤": r"\leq", "≥": r"\geq",
    "≠": r"\neq", "≈": r"\approx",
    "π": r"\pi", "α": r"\alpha", "β": r"\beta",
    "θ": r"\theta", "φ": r"\phi",
    "√": r"\sqrt", "∠": r"\angle", "∽": r"\sim",
    "△": r"\triangle", "⊙": r"\odot",
    "、": ",", "．": ".", "　": " ",
}


def to_latex(text: str) -> str:
    for a, b in FULLWIDTH.items():
        text = text.replace(a, b)
    return text


def add_delimiters(text: str) -> tuple[str, int]:
    """给数学记号加 $ 定界符。

    注意不能「整段含 $ 就跳过」——中考题里经常是「二次函数y＝2﹣（x+1）^{2}的顶点坐标」
    这种：已有定界的公式片段和不带定界的上标混在一起。
    正确做法是**跳过已在 $...$ 内的部分**，只处理裸露的记号。
    """
    if "$" not in text:
        out, n = MATH.subn(lambda m: f"${to_latex(m.group(0))}$", text)
        return out, n

    # 已经有定界符：只处理定界符之外的裸记号
    parts = re.split(r"(\$[^$]*\$)", text)
    out_parts = []
    n = 0
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out_parts.append(part)      # 已定界，原样保留
            continue
        new, cnt = MATH.subn(lambda m: f"${to_latex(m.group(0))}$", part)
        out_parts.append(new)
        n += cnt
    return "".join(out_parts), n


def check(row: dict) -> str | None:
    """中考题的结构检查。返回拒收原因，None 表示通过。"""
    if any(p in row["prompt"] for p in POLLUTION):
        return "题干混入解析残留"
    if row["type"] == "choice":
        choices = row.get("choices") or []
        if len(choices) < 3:
            return "选项少于 3 个"
        texts = [c["text"].strip() for c in choices]
        if len(set(texts)) != len(texts):
            return "选项内容重复"
        if row["answer"] not in {c["id"] for c in choices}:
            return "答案不在选项内"
    else:
        if not (row.get("answer") or "").strip():
            return "填空题无答案"
        # 答案里不能有丢公式留下的空壳
        if re.search(r"（\s*）|[=＝]\s*$|^[=＝]", row["answer"]):
            return "答案公式缺失"
    if not row.get("topicId"):
        return "无知识点归属"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="只导入某种类型：choice / fill")
    ap.add_argument("--max-per-topic", type=int, default=0,
                    help="每个知识点最多导入几道，0 表示不限")
    ap.add_argument("--prioritize-gaps", action="store_true",
                    help="优先补只有 2 道题的知识点，其余知识点不导")
    ap.add_argument("--max-per-gap", type=int, default=2,
                    help="每个缺口知识点最多导入几道")
    args = ap.parse_args()

    pool = [json.loads(ln) for ln in CAND.open(encoding="utf-8") if ln.strip()]
    if args.only:
        want = {"choice": "choice", "fill": "short"}[args.only]
        pool = [r for r in pool if r["type"] in (want, "numeric")]

    existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    own_fp = {fp(q["prompt"]) for q in own}
    ext_fp = {fp(q["prompt"]) for q in existing}
    ext_src = {q.get("source_id") for q in existing}

    # 缺口点：当前只有 2 道题的知识点。中考题优先补这些，
    # 因为它们是诊断引擎真正会问满的地方。
    own_cnt = collections.Counter(q["topicId"] for q in own)
    gap_topics = {q["topicId"] for q in own if own_cnt[q["topicId"]] == 2}

    imported, rejected = [], []
    counter = collections.Counter()
    per_topic = collections.Counter()

    if args.prioritize_gaps:
        pool = [r for r in pool if r["topicId"] in gap_topics]
        # 每个缺口点只取 max-per-gap 道。
        # 题型优先级：选择题 > 纯数值填空 > 表达式填空。
        # 理由：选择题有独立可验算的答案与选项，数值填空能机器验算，
        # 表达式填空只能靠 accept 列表，判题最脆。
        PRIORITY = {"choice": 0, "numeric": 1, "short": 2}
        by_tid = collections.defaultdict(list)
        for r in pool:
            by_tid[r["topicId"]].append(r)
        limited = []
        for tid, rs in sorted(by_tid.items()):
            # 同题型内按 source_id 排序 → 可复现
            rs.sort(key=lambda x: (PRIORITY.get(x["type"], 9), x["source_id"]))
            limited.extend(rs[: args.max_per_gap])
        pool = limited
        print(f"缺口优先：{len(gap_topics)} 个缺口点，筛选后 {len(pool)} 道候选")

    for row in pool:
        sid = row["source_id"]
        if sid in ext_src:
            continue
        reason = check(row)
        if reason:
            rejected.append((sid, reason))
            continue
        key = fp(row["prompt"])
        if key in own_fp:
            rejected.append((sid, "与自有题重复"))
            continue
        if key in ext_fp:
            rejected.append((sid, "与本批重复"))
            continue
        if args.max_per_topic:
            tid = row["topicId"]
            if per_topic[tid] >= args.max_per_topic:
                rejected.append((sid, "该知识点已达上限"))
                continue

        prompt, n_math = add_delimiters(row["prompt"])
        choices = []
        for c in row.get("choices") or []:
            t, _ = add_delimiters(c["text"])
            choices.append({"id": c["id"], "text": t})
        answer = row["answer"]
        if row["type"] != "choice":
            answer, _ = add_delimiters(answer)

        rec = {
            "id": f"ext-smse-{len(imported) + len(existing) + 1:04d}",
            "topicId": row["topicId"],
            "type": row["type"],
            "prompt": prompt,
            "choices": choices,
            "answer": answer,
            "explanation": row.get("explanation") or "",
            "difficulty": "basic",
            "difficulty_basis": "inherited_unverified",
            "source": row["source"],
            "source_id": sid,
            "source_license": row.get("license", ""),
            "source_chain": row.get("source_chain", ""),
            "meta": {
                "paper": row.get("paper"),
                "year_dir": row.get("year_dir"),
                "kaodian": row.get("kaodian"),
                "points": row.get("points"),
            },
            "mapping": {
                "method": "kaodian-tag",
                "confidence": "tag_mapped",
                "reviewed_by": "author",
                "reviewed_at": "2026-10-24",
                "note": ("知识点来自菁优网考点标签，经人工映射表 data/kc_tag_map.json 落地；"
                         "映射表本身由作者逐条核对，但未逐题复核标签与题目的对应关系"),
            },
            "mapping_basis": "kaodian-tag",
            "mapping_note": "知识点来自菁优网考点标签，经人工映射表 data/kc_tag_map.json 落地",
            "answer_basis": "official-solution",
            "answer_check": {
                "status": "consistent",
                "detail": "答案取自官方解析；选择题已做题目区/答案区选项文本双向一致性校验",
            },
            "usable_for_diagnosis": False,
            "usable_reason": "许可未声明（资源来自菁优网）；且未经过第二人复核",
            "review_status": "tag-mapped",
            "needs_kc": False,
        }
        imported.append(rec)
        per_topic[row["topicId"]] += 1
        counter[row["type"]] += 1

    if args.dry_run:
        print(f"试运行：将导入 {len(imported)} 道，拒收 {len(rejected)} 道")
        print("类型分布:", dict(counter))
        rc = collections.Counter(r for _, r in rejected)
        print("拒收原因:", dict(rc.most_common(10)))
        for sid, r in rejected[:8]:
            print(f"   {sid[-28:]}: {r}")
        return

    allq = existing + imported
    OUT.write_text(json.dumps(allq, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "imported": len(imported), "total_ext": len(allq),
        "by_type": dict(counter),
        "topics_covered": len({q["topicId"] for q in allq}),
        "rejected": len(rejected),
        "reject_reasons": dict(collections.Counter(r for _, r in rejected).most_common(8)),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
