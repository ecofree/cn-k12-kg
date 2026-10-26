"""按册提交内容：把 data/content/<册>.json 合并进 topics / dependencies / questions。

为什么要单独一层：
  topics.json 是 300+ 条的合并产物，逐册填内容时手改容易冲突；
  每册内容独立成文件，既能单独 review，也能幂等重放（已存在的不覆盖题目）。

内容文件格式：
{
  "volume": "必修一",
  "topics": {"mh1.1.1": {"description": "...", "mastery_criteria": ["...", "..."],
                          "type": "conceptual", "standards": [...], "root_reason": "..."}},
  "dependencies": [{topicId, prerequisiteId, strength, reason}],
  "questions": [{id, topicId, difficulty, type, prompt, answer, ...}]
}

用法：
    python tools/apply_content.py --file data/content/bx1.json --dry-run
    python tools/apply_content.py --file data/content/bx1.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from cnkg.graph import load_graph  # noqa: E402


def dump(path: pathlib.Path, rows: list):
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", action="store_true",
                        help="同 id 的题目用内容文件覆盖（默认跳过，防止误改已入库的题）")
    args = parser.parse_args()

    payload = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
    volume = payload["volume"]
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    deps = json.loads((DATA / "dependencies.json").read_text(encoding="utf-8"))
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))

    by_id = {t["id"]: t for t in topics}
    graph = load_graph()
    known = set(by_id)

    errors = []
    filled = 0
    for tid, patch in payload.get("topics", {}).items():
        topic = by_id.get(tid)
        if topic is None:
            errors.append(f"{tid}: 知识点不存在")
            continue
        if topic["volume"] != volume:
            errors.append(f"{tid}: 不属于 {volume}")
            continue
        if not patch.get("mastery_criteria") or len(patch["mastery_criteria"]) < 2:
            errors.append(f"{tid}: mastery_criteria 至少 2 条")
            continue
        topic.update(patch)
        topic["review_status"] = "draft"
        filled += 1

    new_deps = []
    existing_pairs = {(d["topicId"], d["prerequisiteId"]) for d in deps}
    for edge in payload.get("dependencies", []):
        a, b = edge["topicId"], edge["prerequisiteId"]
        if a not in known or b not in known:
            errors.append(f"边 {a} <- {b}: 端点不存在")
            continue
        if (a, b) in existing_pairs:
            continue
        if edge.get("strength") not in ("hard", "soft") or not edge.get("reason"):
            errors.append(f"边 {a} <- {b}: 缺少 strength 或 reason")
            continue
        existing_pairs.add((a, b))
        new_deps.append(edge)
        deps.append(edge)

    existing_q = {q["id"]: i for i, q in enumerate(questions)}
    new_q = []
    for q in payload.get("questions", []):
        if q["id"] in existing_q:
            if not args.update:
                continue
            questions[existing_q[q["id"]]] = q   # 显式 --update 时才覆盖
            new_q.append(q)
            continue
        if q.get("topicId") not in known:
            errors.append(f"{q['id']}: topicId 不存在")
            continue
        if not graph.by_id[q["topicId"]]:
            errors.append(f"{q['id']}: topicId 无效")
            continue
        existing_q[q["id"]] = len(questions)
        new_q.append(q)
        questions.append(q)

    if errors:
        print("合并失败：")
        for err in errors:
            print("  -", err)
        return 1

    print(f"{volume}: 更新知识点 {filled}，新增先修边 {len(new_deps)}，新增题目 {len(new_q)}")
    if args.dry_run:
        return 0

    dump(DATA / "topics.json", topics)
    dump(DATA / "dependencies.json", deps)
    dump(DATA / "questions.json", questions)
    print("已写入 topics.json / dependencies.json / questions.json")
    print("下一步：python tools/validate.py && python tools/verify_questions.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
