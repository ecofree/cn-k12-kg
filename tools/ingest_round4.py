"""接入 Astra 第四轮回传。

第四轮与第三轮的差别：
- review.csv 320 行（第三轮 300），verdict pass 306 / fail 14
- fix_recheck.csv 20 行（第三轮修订的复检），pass 19 / fail 1

**这一轮 Astra 自己加了一套严格自检器**（check_round4_strict.py + audit/ 目录），
比前几轮都严：验证只读列未被改动、行数精确匹配、跨轮 ID 覆盖。
我仍然独立复核 fail，不因为自检器过了就采信。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REVIEWS = DATA / "reviews.json"
LABEL = "astra_round4_2026-10-24"

FIX_FILE = "data/fixes/round4.json"


def sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_csv(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(path.open(encoding="utf-8-sig")))


def ingest_reviews(bundle: pathlib.Path, dry_run: bool) -> dict:
    """把 320 条复核结论写入 reviews.json（合并，不覆盖）。"""
    rows = read_csv(bundle / "inbox" / "review.csv")
    if not rows:
        return {"rows": 0, "note": "review.csv 不存在"}
    reviews = json.loads(REVIEWS.read_text(encoding="utf-8")) if REVIEWS.exists() else {}
    added = updated = 0
    for r in rows:
        qid = r["question_id"]
        verdict = (r.get("verdict") or "").strip().lower()
        if verdict not in ("pass", "fail", "skip"):
            continue
        note = r.get("note") or ""
        rec = {
            "verdict": verdict,
            "reviewer_type": "ai",
            "teacher_reviewed": False,
            "reviewed_at": "2026-10-24",
            "source": LABEL,
            "note": note,
        }
        if qid in reviews:
            # 已有记录：保留历史 fail，只追加本轮结论
            old = reviews[qid]
            if old.get("verdict") == "fail" and (old.get("resolution") or {}).get("status") == "fixed_pending_recheck":
                rec["prior_verdict"] = old["verdict"]
                rec["resolution"] = old.get("resolution")
            reviews[qid].update(rec)
            updated += 1
        else:
            reviews[qid] = rec
            added += 1
    if not dry_run:
        REVIEWS.write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rows": len(rows), "added": added, "updated": updated}


def close_fixes(bundle: pathlib.Path, dry_run: bool) -> dict:
    """复检通过 → 关闭上一轮的「已修订待复检」状态。"""
    rows = read_csv(bundle / "inbox" / "fix_recheck.csv")
    if not rows:
        return {"rows": 0}
    reviews = json.loads(REVIEWS.read_text(encoding="utf-8")) if REVIEWS.exists() else {}
    closed = failed = 0
    for r in rows:
        qid = r["question_id"]
        verdict = (r.get("verdict") or "").strip().lower()
        rec = reviews.get(qid)
        if not rec:
            continue
        res = rec.get("resolution") or {}
        if verdict == "pass":
            res["status"] = "recheck_passed"
            res["rechecked_at"] = "2026-10-24"
            rec["resolution"] = res
            rec["verdict"] = "pass"
            closed += 1
        elif verdict == "fail":
            res["status"] = "fixed_pending_recheck"
            res["recheck_failed_at"] = "2026-10-24"
            res["recheck_note"] = r.get("note") or ""
            rec["resolution"] = res
            failed += 1
    if not dry_run:
        REVIEWS.write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rows": len(rows), "closed": closed, "still_failing": failed}


def mark_new_fixes(dry_run: bool) -> dict:
    """把本轮 14 条 fail 标为「已修订待复检」（verdict 保留 fail）。"""
    fixes = json.loads((ROOT / FIX_FILE).read_text(encoding="utf-8"))
    reviews = json.loads(REVIEWS.read_text(encoding="utf-8")) if REVIEWS.exists() else {}
    n = 0
    for qid, patch in fixes.items():
        rec = reviews.get(qid)
        if not rec:
            continue
        rec["resolution"] = {
            "status": "fixed_pending_recheck",
            "date": "2026-10-24",
            "applied_from": FIX_FILE,
            "changed_fields": list(patch.keys()),
            "note": "修订依据 Astra 第四轮复审；verdict 保留 fail 作为历史判定，复检通过后再改。",
            "answer_changed": "answer" in patch,
        }
        rec["note_stale"] = "原 note 中的 full_question_sha256 指向修订前版本，已失效。"
        n += 1
    if not dry_run:
        REVIEWS.write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"marked": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    bundle = pathlib.Path(args.bundle)

    # 顺序很重要：先接入复核结论（fail 的题才有 reviews 记录），
    # 再标记新修复，最后关闭上一轮的待复检状态。
    result = {
        "label": LABEL,
        "dry_run": args.dry_run,
        "reviews": ingest_reviews(bundle, args.dry_run),
        "new_fixes": mark_new_fixes(args.dry_run),
        "closed_fixes": close_fixes(bundle, args.dry_run),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
