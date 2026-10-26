"""接收 Astra 第二轮回传包，按证据分层落盘（对应 D22）。

分层规则（不因为对方说"通过了"就合并）：
    standards.csv  → data/standards.json        补 verbatim_text / page_or_ref / verbatim
    kc_review_100  → data/external/<label>/     KC 候选判定，不进诊断
    review.csv     → data/reviews.json          逐题复核，合并保留历史

硬约束：
    1. 工具**永不**修改题目的 prompt / answer / choices / explanation。
       review.csv 里带了这三列的只读快照，本工具会逐题比对，不一致直接中止。
    2. reviews.json 是"每题最新判定 + history"，不是覆盖。
    3. 课标 verbatim=true 只针对 verbatim_text 这一列；
       作者自撰的 summary 另记 summary_status，不借 verbatim 洗白。

用法：
    python tools/ingest_round2.py --bundle build/inbox_round2 --dry-run
    python tools/ingest_round2.py --bundle build/inbox_round2
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
LABEL = "astra_round2_2026-10-24"
DATE = "2026-10-24"


def read_csv(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def dump(path: pathlib.Path, rows) -> None:
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def ingest_standards(bundle: pathlib.Path, dry: bool) -> dict:
    rows = read_csv(bundle / "inbox" / "standards.csv")
    standards = json.loads((DATA / "standards.json").read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in standards}

    missing = [r["id"] for r in rows if r["id"] not in by_id]
    if missing:
        raise SystemExit(f"课标 id 对不上：{missing[:5]}")

    paused, conflicts = 0, 0
    for row in rows:
        std = by_id[row["id"]]
        std["verbatim_text"] = row["verbatim_text"].strip()
        std["page_or_ref"] = row["page_or_ref"].strip()
        std["verbatim"] = row["verbatim"].strip().lower() == "true"
        std["verbatim_source"] = LABEL
        std["verbatim_checked_by"] = "ai"  # 不是教师审核
        # 作者自撰摘要的独立状态：不因 verbatim=true 变成"原文"
        std["summary_status"] = "author_summary"
        note = ""
        m = re.search(r"摘要对应限制[：:]\s*(.+)$", std["page_or_ref"], re.S)
        if m:
            note = m.group(1).strip()
            std["summary_status"] = "needs_author_review"
            std["summary_note"] = note
            conflicts += 1
        elif "非项目概述的逐字认证" in std["page_or_ref"]:
            std["summary_status"] = "author_summary"
        if row["page_or_ref"].strip().endswith("待核"):
            paused += 1

    if not dry:
        dump(DATA / "standards.json", standards)
    return {
        "rows": len(rows),
        "verbatim_true": sum(1 for r in rows if r["verbatim"].lower() == "true"),
        "summary_needs_review": conflicts,
        "still_pending": paused,
    }


def ingest_kc(bundle: pathlib.Path, dry: bool) -> dict:
    rows = read_csv(bundle / "inbox" / "kc_review_100.csv")
    out_dir = DATA / "external" / LABEL
    records = []
    for row in rows:
        records.append(
            {
                "external_id": row.get("kc_id") or row.get("id"),
                "external_name": row.get("kc_name"),
                "local_topic_id": row.get("local_topic_id") or row.get("topic_id"),
                "adjudication": row.get("adjudication"),
                "relation": row.get("relation"),
                "evidence": row.get("evidence"),
                "note": row.get("note"),
                "reviewer_type": "ai",
                "teacher_reviewed": False,
                "source": LABEL,
                "date": DATE,
            }
        )
    if not dry:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "kc_reviews.json").write_text(
            json.dumps({"label": LABEL, "records": records}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
    return {
        "rows": len(records),
        "adjudication": dict(collections.Counter(r["adjudication"] for r in records)),
        "relation": dict(collections.Counter(r["relation"] for r in records)),
    }


def ingest_reviews(bundle: pathlib.Path, dry: bool) -> dict:
    rows = read_csv(bundle / "inbox" / "review.csv")
    questions = {q["id"]: q for q in json.loads((DATA / "questions.json").read_text(encoding="utf-8"))}
    reviews = json.loads((DATA / "reviews.json").read_text(encoding="utf-8"))

    mismatched = []
    for row in rows:
        qid = row["question_id"]
        q = questions.get(qid)
        if q is None:
            mismatched.append((qid, "题目不存在"))
            continue
        if (row.get("prompt") or "").strip() != (q.get("prompt") or "").strip():
            mismatched.append((qid, "题干已变"))
        if (row.get("explanation") or "").strip() != (q.get("explanation") or "").strip():
            mismatched.append((qid, "解析已变"))
        ans = (row.get("answer") or "").strip()
        # 选择题回传格式是「键. 文本」，只比键
        if q.get("type") == "choice":
            if not ans.startswith(str(q.get("answer"))):
                mismatched.append((qid, f"答案键不一致 {ans[:12]}"))
        elif ans != str(q.get("answer")).strip():
            mismatched.append((qid, f"答案不一致 {ans[:12]}"))
    if mismatched:
        raise SystemExit(f"只读字段比对失败（{len(mismatched)} 条）：{mismatched[:5]}")

    merged, new = 0, 0
    for row in rows:
        qid = row["question_id"]
        prior = reviews.get(qid)
        note = (row.get("note") or "").strip()
        opt = None
        m = re.match(r"correct_option_ids\s*=\s*([^;]*);", note)
        if m:
            opt = [x.strip() for x in m.group(1).split("+") if x.strip()]
        record = {
            "verdict": row["verdict"].strip(),
            "note": note,
            "reviewer_type": "ai",
            "teacher_reviewed": False,
            "date": DATE,
            "source": LABEL,
            "issue_codes": ["needs_revision"] if row["verdict"].strip() == "fail" else [],
            "evidence": note,
            "correct_option_ids": opt,
        }
        if prior:
            history = prior.get("history") or []
            history.append({k: prior.get(k) for k in ("verdict", "date", "source", "note")})
            record["history"] = history
        else:
            new += 1
        reviews[qid] = record
        merged += 1

    if not dry:
        (DATA / "reviews.json").write_text(
            json.dumps(reviews, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    return {
        "rows": len(rows),
        "merged": merged,
        "new_to_reviews": new,
        "verdict": dict(collections.Counter(r["verdict"].strip() for r in rows)),
        "fail_ids": [r["question_id"] for r in rows if r["verdict"].strip() == "fail"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    bundle = pathlib.Path(args.bundle)

    result = {
        "label": LABEL,
        "dry_run": args.dry_run,
        "standards": ingest_standards(bundle, args.dry_run),
        "kc": ingest_kc(bundle, args.dry_run),
        "reviews": ingest_reviews(bundle, args.dry_run),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
