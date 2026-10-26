"""第四轮复核包的验收自检器。

放在包内，让 Astra 也能跑——提交前先自查格式，避免来回。
检查项：
1. 必须有且只有这三个文件
2. CSV 表头与预期一致
3. 只读列（题目内容、指纹）不得被改动
4. verdict 只能是 pass / fail / skip
5. fail 的 note 必须以 correct_option_ids= 开头（选择题）
6. manifest.json 与实际行数一致
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sys

EXPECT_FILES = {"inbox/fix_recheck.csv", "inbox/review.csv", "manifest.json"}
VERDICTS = {"pass", "fail", "skip", ""}

FIX_HEADER = ["question_id", "topic_id", "type", "prompt", "options", "answer",
              "accept", "explanation", "full_question_sha256", "verdict", "note"]
REVIEW_HEADER = ["question_id", "topic_id", "topic_name", "volume", "type", "difficulty",
                 "prompt", "options", "answer", "accept", "explanation",
                 "full_question_sha256", "verdict", "note"]


def sha256_row(row: dict, header: list[str], skip: list[str]) -> str:
    payload = {k: (row.get(k) or "") for k in header if k not in skip}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    # 自己解析 --bundle，不要直接取 sys.argv[1]：
    # 第三轮的 check_round3.py 就是这么写的，结果 bundle 变成了 "--bundle"，
    # 于是所有文件都被报成缺失——错误信息反而把真实问题掩盖了。
    bundle = pathlib.Path(".")
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--bundle" and i + 1 < len(args):
            bundle = pathlib.Path(args[i + 1])
        elif a.startswith("--bundle="):
            bundle = pathlib.Path(a.split("=", 1)[1])
    errors: list[str] = []
    notes: list[str] = []

    for rel in EXPECT_FILES:
        if not (bundle / rel).exists():
            errors.append(f"缺少文件 {rel}")

    manifest = {}
    mpath = bundle / "manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))

    # --- fix_recheck ---
    fpath = bundle / "inbox" / "fix_recheck.csv"
    if fpath.exists():
        rows = list(csv.DictReader(fpath.open(encoding="utf-8-sig")))
        if list(rows[0].keys()) != FIX_HEADER:
            errors.append(f"fix_recheck.csv 表头不符：{list(rows[0].keys())}")
        if manifest and len(rows) != manifest.get("fix_recheck"):
            errors.append(f"fix_recheck 行数 {len(rows)} 与 manifest {manifest.get('fix_recheck')} 不符")
        for r in rows:
            v = (r.get("verdict") or "").strip().lower()
            if v not in VERDICTS:
                errors.append(f"{r['question_id']}: verdict 非法 '{v}'")
            if v == "fail":
                note = (r.get("note") or "").strip()
                if not note.startswith("correct_option_ids="):
                    errors.append(f"{r['question_id']}: fail 的 note 必须以 correct_option_ids= 开头")
            if not (r.get("full_question_sha256") or "").strip():
                errors.append(f"{r['question_id']}: 缺 full_question_sha256")

    # --- review ---
    rpath = bundle / "inbox" / "review.csv"
    if rpath.exists():
        rows = list(csv.DictReader(rpath.open(encoding="utf-8-sig")))
        if list(rows[0].keys()) != REVIEW_HEADER:
            errors.append(f"review.csv 表头不符：{list(rows[0].keys())}")
        if manifest and len(rows) != manifest.get("review"):
            errors.append(f"review 行数 {len(rows)} 与 manifest {manifest.get('review')} 不符")
        ids = set()
        for r in rows:
            qid = r["question_id"]
            if qid in ids:
                errors.append(f"review.csv 题目重复：{qid}")
            ids.add(qid)
            v = (r.get("verdict") or "").strip().lower()
            if v not in VERDICTS:
                errors.append(f"{qid}: verdict 非法 '{v}'")
            if v == "fail":
                note = (r.get("note") or "").strip()
                if not note.startswith("correct_option_ids="):
                    errors.append(f"{qid}: fail 的 note 必须以 correct_option_ids= 开头")
            if not (r.get("full_question_sha256") or "").strip():
                errors.append(f"{qid}: 缺 full_question_sha256")

    print(f"检查项：{len(errors)} 个错误，{len(notes)} 个提示")
    for e in errors[:40]:
        print("  ERROR:", e)
    for n in notes[:10]:
        print("  NOTE :", n)
    if errors:
        print("验收未通过")
        return 1
    print("验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
