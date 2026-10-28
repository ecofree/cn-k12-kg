"""Bind cumulative, individually authored bank reviews to the current sources.

This gate checks coverage and provenance, not the truth of a mathematical
explanation. A previous review is explicitly distinguished from this round;
mechanically scanning a file can never create a review entry.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _path(root, name, prefixes):
    if (not isinstance(name, str) or not name.startswith(prefixes) or not name.endswith(".json")
            or "\\" in name or ".." in Path(name).parts or Path(name).is_absolute()):
        raise ValueError("审查证据必须使用明确的仓库 JSON 路径")
    result = root / name
    if not result.resolve().is_relative_to(root):
        raise ValueError("审查路径越界")
    current = root
    for part in Path(name).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("审查来源不得使用符号链接")
    return result


def check_reviews(root, *, index_name="docs/reviews/v8-bank-review-index.json"):
    root = Path(root).resolve()
    errors, records, counts, sources = [], {}, Counter(), {}
    try:
        index = json.loads(_path(root, index_name, ("docs/reviews/",)).read_text())
        if (index.get("schema") != 1 or not isinstance(index.get("reviews"), list)
                or index.get("reviewer_kind") != "ai" or index.get("independent_teacher_review") is not False
                or index.get("student_validation") is not False):
            raise ValueError("逐题审查索引必须保留实际审查者及未完成的外部验证状态")
        bindings = json.loads((root / "data/skills/catalog-bindings.json").read_text())["tasks"]
        bank_bindings = {key: value for key, value in bindings.items() if key.startswith("bank:")}
        if index.get("bank_answer_bindings_sha256") != digest(bank_bindings):
            errors.append("题库登记契约与此次逐题审查索引不一致，须核对实际变更")
        catalog = json.loads((root / "data/catalog.json").read_text())
        bank = {identity: task for identity, task in catalog["tasks"].items() if task["source_kind"] == "bank"}
        registry = json.loads((root / "data/content_sources.json").read_text())
        authored_owners = {}
        for entry in [*registry.get("content_sources", []), *registry.get("question_sources", [])]:
            name = "data/" + entry["path"]
            source_path = _path(root, name, ("data/content/", "data/question_sources/"))
            data = json.loads(source_path.read_text())
            questions = data.get("questions", []) if isinstance(data, dict) else data
            sources[name] = {question["id"]: question for question in questions}
            if len(sources[name]) != len(questions):
                raise ValueError("登记作者源包含重复题目身份")
            for identity in sources[name]:
                if identity in authored_owners:
                    raise ValueError("同一题目出现在多个登记作者源")
                authored_owners[identity] = name
        if set(authored_owners) != set(bank):
            errors.append("登记作者源与实际目录题库身份集合不一致")
        if index.get("bank_task_count") != len(bank):
            errors.append("审查索引的题库总量与实际目录不一致")
        reports = set()
        for ref in index["reviews"]:
            path = _path(root, ref.get("path"), ("docs/reviews/",))
            if ref["path"] in reports:
                raise ValueError("逐题审查索引重复引用同一报告")
            reports.add(ref["path"])
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
                errors.append(f"{ref['path']}: 逐题审查报告指纹已改变")
                continue
            review = json.loads(raw)
            if (review.get("reviewer_kind") != "ai" or review.get("independent_teacher_review") is not False
                    or not isinstance(review.get("tasks"), list)):
                raise ValueError("逐题审查报告须保留 AI 审读与教师独立审核的区别")
            if ref.get("coverage_kind") not in {"previous_round", "this_round"}:
                raise ValueError("必须明确哪些题是沿用前轮审查，哪些是本轮逐题复核")
            if ref.get("task_count") != len(review["tasks"]):
                errors.append(f"{ref['path']}: 声明的审查数与实际逐题记录不一致")
            for row in review["tasks"]:
                identity = row.get(ref["identity_field"])
                if not isinstance(identity, str) or identity not in bank:
                    errors.append(f"{ref['path']}: 未知或缺失题目身份 {identity}")
                    continue
                if identity in records:
                    errors.append(f"{identity}: 两份审查重复计入覆盖；如需重审须明确替换关系")
                    continue
                source = row.get("source")
                source_path = _path(root, source, ("data/content/", "data/question_sources/"))
                # Bank catalog entries refer to the reproducibly derived
                # data/questions.json. Resolve original ownership through the
                # explicit author-source registry, not that derived filename.
                if source != authored_owners.get(identity):
                    errors.append(f"{identity}: 逐题审查来源与登记的实际作者源不一致")
                if source not in sources:
                    data = json.loads(source_path.read_text())
                    questions = data.get("questions", []) if isinstance(data, dict) else data
                    sources[source] = {question["id"]: question for question in questions}
                    if len(sources[source]) != len(questions):
                        raise ValueError("逐题审查来源包含重复题目身份")
                question = sources[source].get(identity)
                bound = row.get(ref["source_hash_field"])
                if not question or not isinstance(bound, str) or not re.fullmatch("[a-f0-9]{64}", bound) or digest(question) != bound:
                    errors.append(f"{identity}: 作者题目已偏离实际逐题审查的版本")
                if (row.get("reviewer_kind", review.get("reviewer_kind")) != "ai"
                        or row.get("independent_teacher_review", review.get("independent_teacher_review")) is not False):
                    errors.append(f"{identity}: 审查者或独立审核状态不实")
                for label, field in (("数学判断", "mathematical_reason"), ("观察边界", ref["boundary_field"])):
                    if not isinstance(row.get(field), str) or not row[field].strip():
                        errors.append(f"{identity}: 缺少实际逐题的{label}说明")
                calculation = row.get("independent_prompt_calculation")
                if calculation is not None and calculation.get("agreement") is not True:
                    errors.append(f"{identity}: 独立题面复算仍有未解决的差异")
                records[identity] = {"task_id": identity, "source": source, "review": ref["path"],
                    "coverage_kind": ref["coverage_kind"], "availability": bank[identity]["availability"],
                    "source_task_sha256": bound}
                counts[ref["coverage_kind"]] += 1
                counts[bank[identity]["availability"]] += 1
        missing = sorted(set(bank) - set(records))
        errors.extend(f"{identity}: 尚无当前版本的逐题语义审查记录" for identity in missing)
        if index.get("this_round_task_count") != counts["this_round"]:
            errors.append("本轮复核计数与实际证据不一致，不能把前轮审查改称本轮重审")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append("逐题审查索引无法核对：" + str(exc))
        bank = locals().get("bank", {})
    return {"schema": 1, "ok": not errors, "errors": errors,
            "summary": {"bank_tasks": len(bank), "covered_tasks": len(records),
                        "previous_round_tasks": counts["previous_round"], "this_round_tasks": counts["this_round"],
                        "active_tasks": counts["active"], "quarantined_tasks": counts["quarantined"]},
            "tasks": [records[key] for key in sorted(records)],
            "limits": ["程序只核对逐题证据、覆盖范围与作者版本，不替代数学语义审读。",
                       "隔离题仍独立统计；完成审读不自动恢复其诊断或掌握判断用途。",
                       "AI 作者复核不等于教师独立审核或真实学生教学效果验证。"]}
