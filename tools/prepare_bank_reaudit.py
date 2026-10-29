"""Build deterministic, source-bound batches for a fresh semantic audit of the question bank.

This tool deliberately does not create review evidence. It exports the actual
question, answer, explanation, objective/family bindings, quarantine status and
prior review provenance so a reviewer can audit a bounded batch without losing
the distinction between historical review and a new review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.bank_review import digest, check_reviews  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_queue(root: Path):
    root = root.resolve()
    index = read_json(root / "docs/reviews/v8-bank-review-index.json")
    checked = check_reviews(root)
    if not checked["ok"]:
        raise ValueError("现有题库身份/历史审读绑定检查失败：" + "; ".join(checked["errors"][:10]))

    registry = read_json(root / "data/content_sources.json")
    bindings = read_json(root / "data/skills/catalog-bindings.json").get("tasks", {})
    catalog = read_json(root / "data/catalog.json").get("tasks", {})
    bank_tasks = {key: value for key, value in catalog.items()
                  if value.get("source_kind") == "bank"}
    questions, owners = {}, {}
    for entry in [*registry.get("content_sources", []), *registry.get("question_sources", [])]:
        relative = "data/" + entry["path"]
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"作者源路径无效：{relative}")
        payload = read_json(path)
        rows = payload.get("questions", []) if isinstance(payload, dict) else payload
        for question in rows:
            identity = question.get("id")
            if not isinstance(identity, str) or identity in questions:
                raise ValueError(f"题目身份缺失或重复：{identity}")
            questions[identity] = question
            owners[identity] = relative
    if set(questions) != set(bank_tasks):
        raise ValueError("作者源与题库目录身份集合不一致，拒绝生成不完整审计队列")

    previous = {}
    for ref in index["reviews"]:
        report = read_json(root / ref["path"])
        for row in report["tasks"]:
            identity = row.get(ref["identity_field"])
            previous[identity] = {
                "coverage_kind": ref["coverage_kind"],
                "review_file": ref["path"],
                "historical_source_hash": row.get(ref["source_hash_field"]),
                "historical_boundary": row.get(ref["boundary_field"]),
            }

    queue = []
    for identity in sorted(questions):
        q = questions[identity]
        task = bank_tasks[identity]
        binding = bindings.get("bank:" + identity, {})
        queue.append({
            "id": identity,
            "source": owners[identity],
            "source_sha256": digest(q),
            "prior_review": previous.get(identity),
            "availability": task.get("availability", "unknown"),
            "quarantined": task.get("availability") == "quarantined",
            "prompt": q.get("prompt", q.get("question", "")),
            "options": q.get("options"),
            "answer": q.get("answer", q.get("correct_answer")),
            "explanation": q.get("explanation", q.get("analysis", "")),
            "answer_spec": binding.get("answer_spec", task.get("answer_spec")),
            "skill_ids": task.get("skill_ids", binding.get("skill_ids", [])),
            "family_id": task.get("family_id", binding.get("family_id")),
            "representation": task.get("representation", binding.get("representation")),
            "audit": {
                "status": "not_reaudited",
                "answer_correct": None,
                "explanation_consistent": None,
                "conditions_sufficient": None,
                "skill_binding_valid": None,
                "quarantine_decision": "keep_quarantined" if task.get("availability") == "quarantined" else "not_applicable",
                "reviewer_reason": "",
            },
        })
    return queue


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--batch", type=int, default=1, help="1-based deterministic batch number")
    parser.add_argument("--output", type=Path, default=ROOT / "build/bank-semantic-reaudit-batch.json")
    args = parser.parse_args(argv)
    if args.batch_size < 1 or args.batch < 1:
        parser.error("--batch-size 与 --batch 必须为正整数")
    queue = build_queue(ROOT)
    start = (args.batch - 1) * args.batch_size
    rows = queue[start:start + args.batch_size]
    if not rows:
        parser.error(f"批次 {args.batch} 超出范围；当前共 {len(queue)} 道题")
    payload = {
        "schema": 1,
        "evidence_type": "reaudit_worklist_not_review_evidence",
        "source_count": len(queue),
        "batch_size": args.batch_size,
        "batch": args.batch,
        "start_offset": start,
        "remaining_after_batch": max(0, len(queue) - start - len(rows)),
        "reviewer_kind": None,
        "independent_teacher_review": False,
        "student_validation": False,
        "tasks": rows,
        "warning": "导出队列不构成语义审读；必须逐题完成数学判断并重新绑定当前源指纹。隔离题不得因进入队列自动恢复。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "source_count": len(queue), "batch": args.batch, "batch_size": len(rows),
        "remaining_after_batch": payload["remaining_after_batch"],
        "quarantined_in_batch": sum(row["quarantined"] for row in rows),
        "output": str(args.output),
        "evidence_created": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
