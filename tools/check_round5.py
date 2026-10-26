"""第五轮复核包的验收自检器。

与第四轮同构，但多一张 generated_review.csv。
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

HEADER = ["question_id", "topic_id", "topic_name", "volume", "type", "difficulty",
          "prompt", "options", "answer", "accept", "explanation",
          "full_question_sha256", "verdict", "note"]
VERDICTS = {"pass", "fail", "skip", ""}
EXPECT_FILES = {"inbox/generated_review.csv", "inbox/review.csv", "manifest.json"}


def check_table(path: pathlib.Path, manifest: dict, key: str) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"缺少文件 {path.name}"]
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if not rows:
        return [f"{path.name} 是空的"]
    if list(rows[0].keys()) != HEADER:
        errors.append(f"{path.name} 表头不符：{list(rows[0].keys())}")
    if manifest and len(rows) != manifest.get(key):
        errors.append(f"{path.name} 行数 {len(rows)} 与 manifest {manifest.get(key)} 不符")
    seen = set()
    for r in rows:
        qid = r["question_id"]
        if qid in seen:
            errors.append(f"{path.name} 题目重复：{qid}")
        seen.add(qid)
        v = (r.get("verdict") or "").strip().lower()
        if v not in VERDICTS:
            errors.append(f"{qid}: verdict 非法 '{v}'")
        if v == "fail":
            note = (r.get("note") or "").strip()
            if not note.startswith("correct_option_ids="):
                errors.append(f"{qid}: fail 的 note 必须以 correct_option_ids= 开头")
        if not (r.get("full_question_sha256") or "").strip():
            errors.append(f"{qid}: 缺 full_question_sha256")
    return errors


def main() -> int:
    bundle = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else pathlib.Path(".")
    for i, a in enumerate(sys.argv):
        if a == "--bundle" and i + 1 < len(sys.argv):
            bundle = pathlib.Path(sys.argv[i + 1])

    errors: list[str] = []
    manifest = {}
    mpath = bundle / "manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    else:
        errors.append("缺少 manifest.json")

    for rel in EXPECT_FILES:
        if not (bundle / rel).exists():
            errors.append(f"缺少文件 {rel}")

    errors += check_table(bundle / "inbox" / "generated_review.csv", manifest, "generated_review")
    errors += check_table(bundle / "inbox" / "review.csv", manifest, "review")

    print(f"检查项：{len(errors)} 个错误")
    for e in errors[:40]:
        print("  ERROR:", e)
    if errors:
        print("验收未通过")
        return 1
    print("验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
