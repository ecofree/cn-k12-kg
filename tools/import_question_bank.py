"""把人工判过的候选写成 data/questions_ext.json。

前置：build/question-bank/import_review_<stage>.csv 的 keep 列已填 1/0。

本工具只做机器能做的四件事，做完仍不进诊断：
    1. 结构检查（选项数、选项互异、答案在选项内）
    2. 题面污染检查（题干里混进"故选X""分析:"这类解析残留）
    3. 答案一致性（解析里写的"故选B"必须与答案字段一致）
    4. 去重（与自有题、与已导入题）

答案一致性只能查"自相矛盾"，查不出"两边一起错"。所以导入后
`usable_for_diagnosis` 一律 false，要等人工解一遍才算过。
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "questions_ext.json"

POLLUTION = ("故选", "分析:", "分析：", "答案：", "点睛", "点评")
BARE_TEX = re.compile(r"\\[a-zA-Z]{2,}")
FIXES = json.loads((DATA / "text_fixes.json").read_text(encoding="utf-8"))["replacements"]
PICKED = re.compile(r"(?:故选|答案为|答案\s*[：:])\s*([A-D])")


def norm(text: str) -> str:
    return re.sub(r"[\s$\\{}]", "", text or "")


def fp(text: str) -> str:
    return hashlib.sha1(norm(text).encode()).hexdigest()


def apply_fixes(text: str) -> str:
    """机械性错字修复。只做一一对应的替换，不做语义猜测。"""
    for bad, good in FIXES.items():
        text = text.replace(bad, good)
    return text


def clean_row(row: dict) -> dict:
    """统一成 option_id 方案：选项带稳定 id，答案指向 id，字母由渲染时生成。"""
    choices = [{**c, "id": f"o{i + 1}", "text": apply_fixes(c["text"])}
               for i, c in enumerate(row["choices"])]
    answer = row["answer"]
    letters = "ABCDEFGH"
    if answer and answer in letters and answer not in {c["id"] for c in choices}:
        answer = f"o{ord(answer) - ord('A') + 1}"
    row = {**row, "prompt": apply_fixes(row["prompt"]), "choices": choices, "answer": answer}
    if row.get("explanation"):
        row["explanation"] = apply_fixes(row["explanation"])
    return row


def check_structure(row) -> str | None:
    if any(p in row["prompt"] for p in POLLUTION):
        return "题干混入解析残留"
    # 未定界的公式不在前端兜底（猜边界会把正常文字当公式），只能拒收
    bare = BARE_TEX.search(re.sub(r"\$[^$]*\$", "", row["prompt"]))
    if bare:
        return f"含裸 LaTeX（{bare.group(0)}…）"
    if row["prompt"].count("$") % 2:
        return "公式定界符不成对"
    # 花括号配对检查必须排除**转义**花括号 \{ \}。
    # 否则 `\left\{\begin{array}{l}...\end{array}\right.` 这种方程组写法
    # 会被误判成"花括号不成对"——实测初中池 85 道题因此被误拒。
    # 做法：先把 \{ 和 \} 换成占位符，再数剩下的分组花括号。
    braces = re.sub(r"\\[{}]", "", row["prompt"])
    if braces.count("{") != braces.count("}"):
        return "花括号不成对"
    ids = [c.get("id") for c in row["choices"]]
    texts = [c["text"].strip() for c in row["choices"]]
    if len(ids) < 3:
        return "选项少于 3 个"
    if len(set(ids)) != len(ids):
        return "选项 id 重复"
    if len(set(texts)) != len(texts):
        return "选项内容重复"
    if row["answer"] not in ids:
        return "答案不在选项内（可能是多选或答案缺失）"
    return None


def check_answer(row) -> tuple[str, str]:
    """解析里写了「故选 X」，与答案字段核对。

    解析里的字母是**位置**，答案字段是 **option_id**，两者要按同一顺序换算后再比。
    """
    exp = row.get("explanation") or ""
    m = PICKED.search(exp)
    if not m:
        return "no_signal", ""
    letter = m.group(1)
    ids = [c.get("id") for c in row.get("choices") or []]
    idx = ord(letter) - ord("A")
    implied = ids[idx] if 0 <= idx < len(ids) else letter
    if implied == row["answer"]:
        return "consistent", letter
    return "conflict", letter


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="senior")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pool", default="", help="候选池 JSONL，默认 cmm_<stage>.jsonl")
    ap.add_argument("--review", default="", help="人工判定表 CSV")
    ap.add_argument("--formula-bad", default="",
                    help="check_formulas.js 产出的报告，其中的坏题一律拒收")
    args = ap.parse_args()

    review_path = pathlib.Path(args.review) if args.review else (
        ROOT / "build" / "question-bank" / f"import_review_{args.stage}.csv")
    rows = list(csv.DictReader(review_path.open(encoding="utf-8-sig")))
    kept = [r for r in rows if r.get("keep") == "1"]

    pool_path = pathlib.Path(args.pool) if args.pool else (
        ROOT / "inbox" / "candidates" / f"cmm_{args.stage}.jsonl")
    pool = {json.loads(ln)["source_id"]: json.loads(ln)
            for ln in pool_path.open(encoding="utf-8") if ln.strip()}

    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    own_fp = {fp(q["prompt"]) for q in own}
    existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    ext_fp = {fp(q["prompt"]) for q in existing}
    ext_src = {q.get("source_id") for q in existing}

    bad_formula = set()
    if args.formula_bad:
        report = json.loads(pathlib.Path(args.formula_bad).read_text(encoding="utf-8"))
        bad_formula = set(report.get("bad_qids") or [])
        print(f"公式预检：{len(bad_formula)} 道题的公式渲染失败，将拒收")

    imported, rejected = [], []
    counter = collections.Counter()
    for row in kept:
        if row["source_id"] in ext_src:
            continue  # 已经导入过，重复跑不叠加
        if row["source_id"] in bad_formula:
            rejected.append((row["source_id"], "公式无法渲染"))
            continue
        src = pool.get(row["source_id"])
        if src is None:
            rejected.append((row["source_id"], "候选池里找不到"))
            continue
        src = clean_row(src)
        reason = check_structure(src)
        if reason:
            rejected.append((row["source_id"], reason))
            continue
        key = fp(src["prompt"])
        if key in own_fp:
            rejected.append((row["source_id"], "与自有题重复"))
            continue
        if key in ext_fp:
            rejected.append((row["source_id"], "与本批重复"))
            continue
        status, seen = check_answer(src)
        if status == "conflict":
            rejected.append((row["source_id"], f"解析写故选{seen}，答案字段却是{src['answer']}"))
            continue
        counter[status] += 1
        counter[src["type"]] += 1
        ext_fp.add(key)
        n = sum(1 for q in imported if q["topicId"] == row["topic_id"]) + 1
        imported.append({
            "id": f"ext-{row['topic_id']}-{n:02d}",
            "topicId": row["topic_id"],
            "difficulty": "basic",
            "difficulty_basis": "inherited_unverified",
            "type": src["type"],
            "prompt": src["prompt"],
            "choices": src["choices"],
            "answer": src["answer"],
            "explanation": src.get("explanation", ""),
            "source": src["source"],
            "source_id": src["source_id"],
            "source_license": src.get("license", ""),
            "meta": src.get("meta", {}),
            "mapping": {
                "method": "lexicon+manual",
                "confidence": "manual_verified",
                "reviewed_by": "author",
                "reviewed_at": "2026-10-24",
                "note": "知识点归属已人工逐条确认；答案正确性未独立复算",
            },
            "answer_check": {"method": "explanation_crosscheck", "status": status},
            "usable_for_diagnosis": False,
            "review_status": "imported",
        })

    print(json.dumps({
        "stage": args.stage,
        "kept_in_review": len(kept),
        "imported": len(imported),
        "rejected": len(rejected),
        "checks": dict(counter),
        "reject_reasons": dict(collections.Counter(r[1] for r in rejected).most_common(6)),
    }, ensure_ascii=False, indent=2))
    for r in rejected:
        print("  拒绝:", r[0], r[1])
    if args.dry_run:
        return
    merged = existing + imported
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已写入 {OUT.relative_to(ROOT)}：累计 {len(merged)} 道外部题")


if __name__ == "__main__":
    main()
