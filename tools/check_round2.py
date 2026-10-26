"""验收第二轮回传包：只检查"能不能收"，不改任何数据。

对应 docs/astra-brief-round2.md 第 2 节的验收标准。回传不完整时不报错退出，
而是逐项列出缺什么——因为"对方如实标了做不到"和"漏填"要分开处理。

    python tools/check_round2.py --bundle <回传目录>
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BADGE = re.compile(r"\[(needs_ocr|not_accessible|edition_unavailable)\]")


def read_csv(path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    args = ap.parse_args()
    b = pathlib.Path(args.bundle)

    problems, notes = [], []

    # P0 KC
    kc = read_csv(b / "kc_review_100.csv") or read_csv(b / "inbox" / "kc_review_100.csv")
    if kc is None:
        problems.append("缺少 kc_review_100.csv")
    else:
        allowed = {"accept", "reject", "uncertain"}
        empty = [r["kc_id"] for r in kc if not (r.get("adjudication") or "").strip()]
        bad = [r["kc_id"] for r in kc if (r.get("adjudication") or "").strip().lower() not in allowed
               and (r.get("adjudication") or "").strip()]
        weak_ev = [r["kc_id"] for r in kc
                   if (r.get("adjudication") or "").strip().lower() in ("accept", "reject")
                   and len((r.get("evidence") or "").strip()) < 20]
        if empty:
            problems.append(f"KC 还有 {len(empty)} 行未填 adjudication（例：{empty[:5]}）")
        if bad:
            problems.append(f"KC 有 {len(bad)} 行 adjudication 取值非法：{bad[:5]}")
        if weak_ev:
            problems.append(f"KC 有 {len(weak_ev)} 行 accept/reject 的 evidence 少于 20 字：{weak_ev[:5]}")
        counts = {k: sum(1 for r in kc if (r.get('adjudication') or '').strip().lower() == k)
                  for k in allowed}
        notes.append(f"KC 100 条：{counts}")

    # P1 课标
    std = read_csv(b / "standards.csv") or read_csv(b / "inbox" / "standards.csv")
    if std is None:
        problems.append("缺少 standards.csv")
    else:
        missing, false_true = [], []
        for r in std:
            text = (r.get("verbatim_text") or "").strip()
            ref = (r.get("page_or_ref") or "").strip()
            claim = (r.get("verbatim") or "").strip().lower() in ("true", "1", "是")
            if not text and not BADGE.search(ref):
                missing.append(r.get("id"))
            if claim and not text:
                false_true.append(r.get("id"))
            if claim and text and not re.search(r"\d", ref):
                false_true.append(r.get("id"))
        if missing:
            problems.append(f"课标有 {len(missing)} 行既无原文也没标 [needs_ocr]/[not_accessible]：{missing[:5]}")
        if false_true:
            problems.append(f"课标有 {len(false_true)} 行声称 verbatim=true 但缺原文或页码：{false_true[:5]}")
        supplied = sum(1 for r in std if (r.get("verbatim_text") or "").strip())
        notes.append(f"课标 78 条：提供原文 {supplied}，其余应带原因标记")

    # P2 复核
    rev = read_csv(b / "review.csv") or read_csv(b / "inbox" / "review.csv")
    if rev is None:
        problems.append("缺少 review.csv")
    else:
        empty = [r["question_id"] for r in rev if not (r.get("verdict") or "").strip()]
        bad_fail = [r["question_id"] for r in rev
                    if (r.get("verdict") or "").strip().lower() == "fail"
                    and not (r.get("note") or "").startswith("correct_option_ids=")]
        if empty:
            problems.append(f"复核还有 {len(empty)} 行未填 verdict（例：{empty[:5]}）")
        if bad_fail:
            problems.append(f"有 {len(bad_fail)} 条 fail 的 note 未以 correct_option_ids= 开头：{bad_fail[:5]}")
        notes.append(f"复核 160 条：verdict 已填 {len(rev) - len(empty)}")

    print("\n".join("  " + n for n in notes))
    if problems:
        print("\n不能直接收：")
        for p in problems:
            print("  [缺] " + p)
        return 3
    print("\n可以收：三份表都符合验收标准")
    return 0


if __name__ == "__main__":
    sys.exit(main())
