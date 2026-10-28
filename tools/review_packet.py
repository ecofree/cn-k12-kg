"""导出与具体内容版本绑定的人工审核包，核验填写后的审核记录。"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.learning import load_units, unit_content_sha256
from cnkg.projects import load_projects, project_content_sha256

CHECKS = ("mathematical_accuracy", "conditions_and_counterexamples", "objective_alignment",
          "answers_and_rubrics", "age_suitability", "classroom_feasibility")


def resources():
    out = {}
    for kind, items, fingerprint in (
        ("unit", load_units(), unit_content_sha256),
        ("project", load_projects(), project_content_sha256),
    ):
        for key, item in items.items():
            out[f"{kind}:{key}"] = {"kind": kind, "id": key, "title": item["title"],
                                   "content_sha256": fingerprint(item), "content": item}
    return out


def packet(current=None):
    current = resources() if current is None else current
    return {"schema": 1, "purpose": "教师/学科专家独立审核；生成模板不等于完成审核",
            "identity_note": "维护者需核对实际审核者身份；工具只检查声明字段、记录完整性和内容版本。",
            "records": [{**copy.deepcopy(value),
                         "review": {"status": "pending", "reviewer_type": None, "reviewer_id": None,
                                    "record_id": None, "reviewed_at": None,
                                    "content_sha256": value["content_sha256"],
                                    "checks": {key: None for key in CHECKS}, "notes": ""}}
                        for value in current.values()]}


def check_packet(document, current=None):
    current = resources() if current is None else current
    errors, pending, accepted, seen = [], 0, 0, set()
    if document.get("schema") != 1 or not isinstance(document.get("records"), list):
        return {"errors": ["审核包须为 schema=1 且包含 records 列表"], "pending": 0, "accepted": 0}
    for row in document["records"]:
        key = f"{row.get('kind')}:{row.get('id')}"
        if key in seen:
            errors.append(f"{key}: 重复记录")
        seen.add(key)
        known, review = current.get(key), row.get("review", {})
        if not known:
            errors.append(f"{key}: 内容不存在")
            continue
        if (row.get("content_sha256") != known["content_sha256"]
                or review.get("content_sha256") != known["content_sha256"]):
            errors.append(f"{key}: 内容版本已变化，请重新审核")
            continue
        if review.get("status") == "pending":
            pending += 1
            continue
        if review.get("status") not in ("approved", "revisions_requested"):
            errors.append(f"{key}: 审核状态无效")
            continue
        if (review.get("reviewer_type") not in ("teacher", "subject_expert")
                or any(not isinstance(review.get(k), str) or not review[k].strip()
                       for k in ("reviewer_id", "record_id", "reviewed_at"))):
            errors.append(f"{key}: 缺少独立人工审核身份、日期或记录编号")
            continue
        if review["status"] == "approved" and any(review.get("checks", {}).get(k) is not True for k in CHECKS):
            errors.append(f"{key}: 批准需要各审核项明确通过")
            continue
        if review["status"] == "revisions_requested" and not str(review.get("notes", "")).strip():
            errors.append(f"{key}: 请写出修订依据")
            continue
        accepted += 1
    return {"errors": errors, "pending": pending, "accepted": accepted,
            "scope": "仅记录/哈希校验；审核者身份与判断仍需维护者核实，不自动批准发布"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/review-packet.json"))
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check:
        result = check_packet(json.loads(args.check.read_text(encoding="utf-8")))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return bool(result["errors"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"审核模板已生成（状态全部 pending）：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
