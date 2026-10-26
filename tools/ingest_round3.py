"""接收 Astra 第三轮回传包，按证据分层落盘（延续 D22 / D24）。

三张表分别落到三处：
    review.csv               → data/reviews.json（300 条，逐题判定，保留历史）
    fix_recheck.csv          → 关掉上一轮 5 条「已修订待复检」的状态（5 条全 pass）
    standards_summary_gaps.csv → data/standards.json（37 条摘要改写）

硬约束：
    1. 只读字段（题干/解析/答案/原文）逐条比对，不一致直接中止。
    2. 复核结论只落 reviews.json，**不自动改题**。fail 的题由人工逐条改。
    3. kc_map.csv / question_stats.csv 里的示例行不导入（对方也提醒了）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LABEL = "astra_round3_2026-10-24"
DATE = "2026-10-24"


def read_csv(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def dump(path: pathlib.Path, rows) -> None:
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def ingest_reviews(bundle: pathlib.Path, dry: bool) -> dict:
    rows = read_csv(bundle / "inbox" / "review.csv")
    questions = {q["id"]: q for q in json.loads((DATA / "questions.json").read_text(encoding="utf-8"))}
    reviews = json.loads((DATA / "reviews.json").read_text(encoding="utf-8"))

    bad = []
    for row in rows:
        q = questions.get(row["question_id"])
        if q is None:
            bad.append((row["question_id"], "题目不存在"))
            continue
        if (row.get("prompt") or "").strip() != (q.get("prompt") or "").strip():
            bad.append((row["question_id"], "题干已变"))
        if (row.get("explanation") or "").strip() != (q.get("explanation") or "").strip():
            bad.append((row["question_id"], "解析已变"))
        ans = (row.get("answer") or "").strip()
        if q.get("type") == "choice":
            if not ans.startswith(str(q.get("answer"))):
                bad.append((row["question_id"], f"答案键不一致 {ans[:12]}"))
        elif ans != str(q.get("answer")).strip():
            bad.append((row["question_id"], f"答案不一致 {ans[:12]}"))
    if bad:
        raise SystemExit(f"只读字段比对失败（{len(bad)} 条）：{bad[:5]}")

    merged = 0
    for row in rows:
        qid = row["question_id"]
        note = (row.get("note") or "").strip()
        m = re.match(r"correct_option_ids\s*=\s*([^;]*);", note)
        opt = [x.strip() for x in m.group(1).split("+") if x.strip()] if m else None
        prior = reviews.get(qid)
        record = {
            "verdict": row["verdict"].strip(),
            "note": note,
            "reviewer_type": "ai",
            "teacher_reviewed": False,
            "date": DATE,
            "source": LABEL,
            "issue_codes": (["answer_key_error"] if opt and opt != ["none"] else
                            ["needs_revision"] if row["verdict"].strip() == "fail" else []),
            "evidence": note,
            "correct_option_ids": opt,
        }
        if prior:
            history = prior.get("history") or []
            history.append({k: prior.get(k) for k in ("verdict", "date", "source", "note")})
            record["history"] = history
        reviews[qid] = record
        merged += 1

    if not dry:
        (DATA / "reviews.json").write_text(
            json.dumps(reviews, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"rows": len(rows), "merged": merged,
            "verdict": dict(collections.Counter(r["verdict"].strip() for r in rows)),
            "fail_ids": [r["question_id"] for r in rows if r["verdict"].strip() == "fail"]}


def close_fixes(bundle: pathlib.Path, dry: bool) -> dict:
    """上一轮 5 条已修订待复检的，本轮复检通过 → 关掉待复检状态。"""
    rows = read_csv(bundle / "inbox" / "fix_recheck.csv")
    reviews = json.loads((DATA / "reviews.json").read_text(encoding="utf-8"))
    closed, still = [], []
    for row in rows:
        qid = row["question_id"]
        rec = reviews.get(qid)
        if not rec:
            continue
        verdict = row["verdict"].strip()
        if verdict != "pass":
            still.append((qid, verdict))
            continue
        res = rec.get("resolution") or {}
        res.update({
            "status": "closed",
            "rechecked_at": DATE,
            "recheck_source": LABEL,
            "recheck_note": (row.get("note") or "").strip(),
        })
        rec["resolution"] = res
        rec["verdict"] = "pass"
        rec["issue_codes"] = []
        rec["note"] = f"上一轮判 fail 并已修订；本轮复检通过。{(row.get('note') or '').strip()}"
        rec["date"] = DATE
        rec["source"] = LABEL
        closed.append(qid)
    if not dry:
        (DATA / "reviews.json").write_text(
            json.dumps(reviews, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"closed": closed, "not_closed": still}


def ingest_summaries(bundle: pathlib.Path, dry: bool) -> dict:
    rows = read_csv(bundle / "inbox" / "standards_summary_gaps.csv")
    standards = json.loads((DATA / "standards.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in standards}

    missing = [r["id"] for r in rows if r["id"] not in by_id]
    if missing:
        raise SystemExit(f"课标 id 对不上：{missing[:5]}")

    # 只读校验：对方回传的原文必须与我们已采信的逐字相同
    mismatch = []
    for row in rows:
        std = by_id[row["id"]]
        if (row.get("verbatim_text") or "").strip() != (std.get("verbatim_text") or "").strip():
            mismatch.append(row["id"])
    if mismatch:
        raise SystemExit(f"原文与已采信版本不一致：{mismatch[:5]}")

    revised, kept = [], []
    for row in rows:
        std = by_id[row["id"]]
        decision = row["decision"].strip()
        if decision == "revise":
            new = (row.get("suggested_summary") or "").strip()
            if not new:
                continue
            std["summary_previous"] = std.get("summary")
            std["summary"] = new
            std["summary_status"] = "author_summary"
            std["summary_revised_at"] = DATE
            std["summary_revision_source"] = LABEL
            std["summary_scope_note"] = (row.get("gap_note") or "").strip()
            revised.append(row["id"])
        elif decision == "keep":
            std["summary_status"] = "author_summary"
            std["summary_keep_reason"] = (row.get("gap_note") or "").strip()
            kept.append(row["id"])
    if not dry:
        dump(DATA / "standards.json", standards)
    return {"rows": len(rows), "revised": len(revised), "kept": len(kept),
            "still_pending": sum(1 for s in standards if s.get("summary_status") == "needs_author_review")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    bundle = pathlib.Path(args.bundle)

    result = {
        "label": LABEL,
        "dry_run": args.dry_run,
        "reviews": ingest_reviews(bundle, args.dry_run),
        "closed_fixes": close_fixes(bundle, args.dry_run),
        "standards": ingest_summaries(bundle, args.dry_run),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
