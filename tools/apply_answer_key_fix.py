"""把答案键重建结果写回主数据与内容源文件。

输入：
- `build/answer_key_decisions.json`：人工逐条确认的 {question_id: 正确选项字母}
- `build/answer_key_repair.json`：自动判定部分，未在人工清单里的用它

写回规则：
- 只改 `answer` 字段，不动选项顺序（选项已经打散过，不必再转）
- 同步 `data/content/*.json`，否则下次 `apply_content --update` 会把旧答案改回去
- 每条改动记进 `build/answer_key_changes.csv`，便于复查与回滚

    python tools/apply_answer_key_fix.py --dry-run
    python tools/apply_answer_key_fix.py
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"

# 人工复核后对自动判定的否决：这些题自动打分选错了
OVERRIDES = {
    # "以上都可以"是正确项，解释里列举了多个具体标准，打分器误选了"形状"
    "m2a.1.2-q1": "B",
}


def build_map(questions) -> dict:
    manual = {}
    p = BUILD / "answer_key_decisions.json"
    if p.exists():
        manual = json.loads(p.read_text(encoding="utf-8"))
    repair = json.loads((BUILD / "answer_key_repair.json").read_text(encoding="utf-8"))
    auto = {r["question_id"]: r["suggest_key"] for r in repair["rows"] if r.get("auto")}

    final, missing = {}, []
    for q in questions:
        if q.get("type") != "choice":
            continue
        qid = q["id"]
        if qid in manual:
            final[qid] = manual[qid]
        elif qid in auto:
            final[qid] = OVERRIDES.get(qid, auto[qid])
        else:
            missing.append(qid)
    return final, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    final, missing = build_map(questions)
    keys = {q["id"]: [c["key"] for c in q.get("choices") or []] for q in questions}

    bad = [qid for qid, k in final.items() if k not in keys.get(qid, [])]
    if bad:
        print("决策里的字母不在选项中：", bad[:10])
        return 2
    if missing:
        print(f"有 {len(missing)} 道选择题没有决策，保持原样：{missing[:8]}")

    changes = []
    for q in questions:
        if q.get("type") != "choice" or q["id"] not in final:
            continue
        want = final[q["id"]]
        if q["answer"] != want:
            old_text = next(c["text"] for c in q["choices"] if c["key"] == q["answer"])
            new_text = next(c["text"] for c in q["choices"] if c["key"] == want)
            changes.append({"question_id": q["id"], "topic_id": q["topicId"],
                            "old_key": q["answer"], "old_text": old_text,
                            "new_key": want, "new_text": new_text,
                            "prompt": q["prompt"]})
            q["answer"] = want

    print(f"选择题 {len(final)}，需改答案 {len(changes)}")
    if args.dry_run:
        for c in changes[:10]:
            print(f"  {c['question_id']}: {c['old_key']}->{c['new_key']} {c['new_text'][:20]}")
        return 0

    (DATA / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    want_map = {c["question_id"]: c["new_key"] for c in changes}
    synced = 0
    for content in sorted((DATA / "content").glob("*.json")):
        payload = json.loads(content.read_text(encoding="utf-8"))
        hit = 0
        for q in payload.get("questions", []):
            if q.get("id") in want_map and q.get("answer") != want_map[q["id"]]:
                q["answer"] = want_map[q["id"]]
                hit += 1
        if hit:
            content.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
            synced += hit
    print(f"同步内容文件 {synced} 处")

    with open(BUILD / "answer_key_changes.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["question_id", "topic_id", "old_key", "old_text",
                                           "new_key", "new_text", "prompt"])
        w.writeheader()
        w.writerows(changes)
    print(f"改动明细 build/answer_key_changes.csv（{len(changes)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
