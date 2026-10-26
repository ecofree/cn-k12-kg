"""验收第三轮回传包：只检查"能不能收"，不改任何数据。

三张表各有各的坑：
    fix_recheck.csv            复检 5 条修订。必须重新判，不能直接抄我写的 what_changed。
    review.csv                 300 道新题。fail 的 note 必须以 correct_option_ids= 开头。
    standards_summary_gaps.csv 38 条摘要差异。要么给建议表述，要么明确判定"现状可接受"。

    python tools/check_round3.py --bundle <回传目录>
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read_csv(path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def check_recheck(rows, problems, notes):
    if rows is None:
        problems.append("缺少 fix_recheck.csv")
        return
    if len(rows) != 5:
        problems.append(f"fix_recheck.csv 应为 5 行，实际 {len(rows)}")
    ids = [r["question_id"] for r in rows]
    expect = {"m7a.5.1.2-q3", "m7a.6.2.2-q3", "m7b.7.1.2-q1", "m3a.1.1-q1", "mh1.5.7-q3"}
    if set(ids) != expect:
        problems.append(f"题目 id 对不上：{sorted(set(ids) ^ expect)}")
    empty = [r["question_id"] for r in rows if not (r.get("verdict") or "").strip()]
    if empty:
        problems.append(f"还有 {len(empty)} 条未填 verdict：{empty}")
    allowed = {"pass", "fail", "skip"}
    bad = [r["question_id"] for r in rows
           if (r.get("verdict") or "").strip().lower() not in allowed | {""}]
    if bad:
        problems.append(f"verdict 取值不合法：{bad}")


def check_review(rows, problems, notes):
    if rows is None:
        problems.append("缺少 review.csv")
        return
    if len(rows) < 300:
        problems.append(f"review.csv 只有 {len(rows)} 行，要求 300")
    empty = [r["question_id"] for r in rows if not (r.get("verdict") or "").strip()]
    if empty:
        problems.append(f"还有 {len(empty)} 条未填 verdict（例：{empty[:5]}）")
    allowed = {"pass", "fail", "skip"}
    bad = [r["question_id"] for r in rows
           if (r.get("verdict") or "").strip().lower() not in allowed | {""}]
    if bad:
        problems.append(f"verdict 取值不合法（例：{bad[:5]}）")
    fails = [r for r in rows if (r.get("verdict") or "").strip().lower() == "fail"]
    no_prefix = [r["question_id"] for r in fails
                 if not re.match(r"\s*correct_option_ids\s*=", r.get("note") or "")]
    if no_prefix:
        problems.append(f"{len(no_prefix)} 条 fail 的 note 没以 correct_option_ids= 开头：{no_prefix[:5]}")
    if fails:
        notes.append(f"本批 fail {len(fails)} 条，会对应用户逐条改题")


def check_standards(rows, problems, notes):
    if rows is None:
        problems.append("缺少 standards_summary_gaps.csv")
        return
    empty = [r["id"] for r in rows if not (r.get("decision") or "").strip()]
    if empty:
        problems.append(f"还有 {len(empty)} 条未填 decision（例：{empty[:5]}）")
    allowed = {"revise", "keep", "rewrite_needed"}
    bad = [r["id"] for r in rows if (r.get("decision") or "").strip().lower() not in allowed | {""}]
    if bad:
        problems.append(f"decision 取值不合法（只接受 revise / keep / rewrite_needed）：{bad[:5]}")
    revise_no_text = [r["id"] for r in rows
                      if (r.get("decision") or "").strip().lower() == "revise"
                      and not (r.get("suggested_summary") or "").strip()]
    if revise_no_text:
        problems.append(f"{len(revise_no_text)} 条标了 revise 却没给 suggested_summary：{revise_no_text[:5]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    args = ap.parse_args()
    b = pathlib.Path(args.bundle)
    inbox = b / "inbox" if (b / "inbox").exists() else b

    problems, notes = [], []
    check_recheck(read_csv(inbox / "fix_recheck.csv"), problems, notes)
    check_review(read_csv(inbox / "review.csv"), problems, notes)
    check_standards(read_csv(inbox / "standards_summary_gaps.csv"), problems, notes)

    print("== 第三轮验收 ==")
    for n in notes:
        print(f"  [NOTE] {n}")
    if problems:
        print("\n不予接收：")
        for p in problems:
            print(f"  [FAIL] {p}")
        return 1
    print("\n可以收：三张表都符合验收标准")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
