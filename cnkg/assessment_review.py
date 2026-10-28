"""Verify exact review coverage for an authored assessment continuation.

This checks provenance, retained identities and honest denominators. It does
not decide whether a mathematical argument or a family distinction is valid.
Those decisions must already exist in the explicitly cited, per-task reviews.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .assessments import report as assessment_report
from .bank_review import digest


INDEX = "docs/reviews/v9-assessment-review-index.json"


def _read(root, name, *, sha256=None, prefix="docs/reviews/"):
    if (not isinstance(name, str) or not name.startswith(prefix) or not name.endswith(".json")
            or "\\" in name or ".." in Path(name).parts or Path(name).is_absolute()):
        raise ValueError("审读来源必须是明确的仓库 JSON 路径")
    path = root / name
    current = root
    for part in Path(name).parts:
        current /= part
        if current.is_symlink():
            raise ValueError("审读来源不得使用符号链接")
    if not path.resolve().is_relative_to(root):
        raise ValueError("审读来源越界")
    raw = path.read_bytes()
    if sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError(f"{name}: 审读证据指纹已经改变")
    return json.loads(raw)


def _reference(root, ref):
    if not isinstance(ref.get("sha256"), str) or len(ref["sha256"]) != 64:
        raise ValueError("审读证据必须绑定原始文件 SHA-256")
    return _read(root, ref["path"], sha256=ref["sha256"])


def _ai_scope(value):
    if (value.get("reviewer_kind") != "ai" or value.get("independent_teacher_review") is not False
            or value.get("student_validation") is not False):
        raise ValueError("AI 审读必须与教师独立审核、真实学生验证分别记录")


def _task_assets(root, task):
    """Bind the rendered input as well as its unchanged URL and alt text."""
    diagram = task.get("diagram")
    if diagram is None:
        return {}
    src = diagram.get("src")
    if not isinstance(src, str) or not re.fullmatch(r"/static/diagrams/[A-Za-z0-9][A-Za-z0-9_.-]*\.svg", src):
        raise ValueError("审读图示必须使用明确的仓库 SVG 路径")
    name = "web" + src
    path = root
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("审读图示不得使用符号链接")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("审读图示不存在或越界")
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()}


def _rows(root, refs, field):
    seen = set()
    for ref in refs:
        if ref["path"] in seen:
            raise ValueError("同一审读报告不能重复计数")
        seen.add(ref["path"])
        review = _reference(root, ref)
        _ai_scope(review)
        rows = review.get(field)
        if not isinstance(rows, list) or ref.get("count") != len(rows):
            raise ValueError(f"{ref['path']}: 审读数量与实际逐项记录不一致")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{ref['path']}: 每项审读必须是明确的对象")
            # A document header cannot conceal a contradictory claim in a row.
            _ai_scope({**review, **row})
            yield ref["path"], row


def check_reviews(root, *, index_name=INDEX):
    root = Path(root).resolve()
    errors, sources, tasks, owners, reviewed, crossed, decisions = [], {}, {}, {}, {}, {}, {}
    baseline, current_goals, gaps, new_ids, objective_ids = {}, {}, set(), set(), set()
    try:
        index = _read(root, index_name)
        _ai_scope(index)
        if index.get("schema") != 1:
            raise ValueError("观察任务审读索引版本不正确")
        baseline = _reference(root, index["baseline"])
        if baseline.get("schema") != 1 or baseline.get("commit") != index.get("baseline_commit"):
            raise ValueError("观察任务续修基线身份不一致")
        for ref in baseline["previous_review_evidence"]:
            _reference(root, ref)
        catalog = _read(root, "data/catalog.json", prefix="data/")
        if digest(catalog["skills"]) != baseline["skills_sha256"]:
            errors.append("原稳定目标对象发生变化；不能通过删改目标缩小本轮分母")
        aliases = _read(root, "data/task_family_aliases.json", prefix="data/")
        if digest(aliases) != baseline["family_aliases_sha256"]:
            errors.append("规范题族别名发生变化，不能凭重命名继承原结构比较")
        for path in sorted((root / "data/assessments").glob("*.json")):
            name = path.relative_to(root).as_posix()
            source = _read(root, name, prefix="data/assessments/")
            sources[name] = source
            for task in source["tasks"]:
                identity = task["id"]
                if identity in tasks:
                    raise ValueError("观察任务作者身份重复：" + identity)
                tasks[identity], owners[identity] = task, name
        compiled = {key: task for key, task in catalog["tasks"].items()
                    if task.get("source_kind") == "assessment"}
        if set(tasks) != set(compiled):
            errors.append("观察任务作者源与编译目录的身份集合不一致")
        for identity, before in baseline["tasks"].items():
            if (owners.get(identity) != before["source"]
                    or digest(tasks.get(identity)) != before["source_task_sha256"]):
                errors.append(f"{identity}: 沿用的原任务被改变，旧审读不能自动继承")
            if before.get("asset_sha256", {}) != _task_assets(root, tasks.get(identity, {})):
                errors.append(f"{identity}: 原题图示字节发生变化，旧审读不能自动继承")
        for identity, task in compiled.items():
            policy = task.get("policy", {})
            if any(policy.get(key) is not False for key in
                   ("independent_evidence", "may_confirm_mastery", "may_judge_weak")):
                errors.append(f"{identity}: 观察资源不能自动升级为独立掌握或薄弱证据")
        coverage = assessment_report(catalog, require_complete=True)
        errors.extend(coverage["errors"])
        current_goals = {row["skill_id"]: row for row in coverage["goals"]}
        if set(current_goals) != set(baseline["goals"]):
            errors.append("续修目标计划集合与原完整范围不一致")
        for sid, before in baseline["goals"].items():
            plan = catalog["assessment_plans"].get(sid, {})
            if any(plan.get(key) != before[key] for key in ("source_kind", "source_id")):
                errors.append(f"{sid}: 原观察目标的来源身份被替换")
            if not set(before["task_ids"]).issubset(plan.get("task_ids", [])):
                errors.append(f"{sid}: 原目标任务入口被删除")
            if not set(before["component_ids"]).issubset(c["id"] for c in plan.get("components", [])):
                errors.append(f"{sid}: 原过程分项身份被删除")
        gaps = {sid for sid, row in baseline["goals"].items() if row["objective_breadth_gap"]}
        new_ids = set(tasks) - set(baseline["tasks"])
        objective_ids = {identity for identity in new_ids if tasks[identity]["type"] in {"numeric", "choice", "short"}}
        for review_path, row in _rows(root, index["task_reviews"], "tasks"):
            identity = row.get("task_id")
            if identity not in new_ids or identity in reviewed:
                errors.append(f"{review_path}: 新任务审读未知或重复：{identity}")
                continue
            if (row.get("source") != owners[identity]
                    or row.get("source_task_sha256") != digest(tasks[identity])):
                errors.append(f"{identity}: 逐题审读与当前完整任务对象不一致")
            if row.get("asset_sha256", {}) != _task_assets(root, tasks[identity]):
                errors.append(f"{identity}: 逐题审读没有绑定当前实际图示字节")
            for field in ("mathematical_reason", "observation_boundary", "family_reason"):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    errors.append(f"{identity}: 缺少具体的 {field} 审读")
            old_families = {family for sid in tasks[identity]["skill_ids"]
                            for family in baseline["goals"][sid]["canonical_family_ids"]}
            if not old_families.issubset(row.get("compared_family_ids", [])):
                errors.append(f"{identity}: 比较遗漏本目标既有规范题族")
            if row.get("decision") != "accept_with_limits":
                errors.append(f"{identity}: 数学审读仍有未解决事项")
            reviewed[identity] = {"task_id": identity, "review": review_path,
                                  "source_task_sha256": row.get("source_task_sha256")}
        for review_path, row in _rows(root, index["objective_cross_reviews"], "tasks"):
            identity = row.get("task_id")
            if identity not in objective_ids or identity in crossed:
                errors.append(f"{review_path}: 客观题交叉审读未知或重复：{identity}")
                continue
            if row.get("source_task_sha256") != digest(tasks[identity]):
                errors.append(f"{identity}: 交叉审读没有绑定当前题目")
            if row.get("asset_sha256", {}) != _task_assets(root, tasks[identity]):
                errors.append(f"{identity}: 交叉审读没有绑定当前实际图示字节")
            if reviewed.get(identity, {}).get("review") == review_path:
                errors.append(f"{identity}: 同一报告不能兼任作者审读和交叉审读")
            if (not isinstance(row.get("mathematical_reason"), str)
                    or not row["mathematical_reason"].strip()
                    or row.get("decision") != "accept_with_limits"):
                errors.append(f"{identity}: 交叉数学判断未完成")
            crossed[identity] = review_path
        errors.extend(f"{identity}: 新观察任务没有当前版本逐题审读" for identity in sorted(new_ids - set(reviewed)))
        errors.extend(f"{identity}: 新客观题缺少当前版本交叉审读" for identity in sorted(objective_ids - set(crossed)))
        for review_path, row in _rows(root, index["goal_reviews"], "goals"):
            sid = row.get("skill_id")
            if sid not in gaps or sid in decisions:
                errors.append(f"{review_path}: 原不足目标审读未知或重复：{sid}")
                continue
            before, after = baseline["goals"][sid], current_goals.get(sid, {})
            plan = {key: value for key, value in catalog["assessment_plans"][sid].items()
                    if key not in {"source_path", "resource_version"}}
            if row.get("current_plan_sha256") != digest(plan):
                errors.append(f"{sid}: 逐目标审读与当前完整分项计划不一致")
            for field in ("task_ids", "objective_family_ids"):
                if row.get("baseline_" + field) != before[field] or row.get("current_" + field) != after.get(field):
                    errors.append(f"{sid}: {field} 的前后审读范围与实际资源不一致")
            if (not isinstance(row.get("mathematical_reason"), str) or not row["mathematical_reason"].strip()
                    or row.get("independent_mastery_evidence") is not False
                    or row.get("objective_breadth_gap") is not after.get("objective_breadth_gap")):
                errors.append(f"{sid}: 逐目标数学判断或保留缺口记录不完整")
            decisions[sid] = review_path
        errors.extend(f"{sid}: 原不足目标未逐项复查，不能从清单移除" for sid in sorted(gaps - set(decisions)))
        counts = {"new_tasks": len(new_ids), "new_objective_tasks": len(objective_ids),
                  "baseline_breadth_gaps": len(gaps)}
        if index.get("expected_counts") != counts:
            errors.append("审读索引计数与实际任务及原不足分母不一致")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append("观察任务逐题审读无法核对：" + str(exc))
    summary = {"assessment_tasks": len(tasks), "baseline_tasks": len(baseline.get("tasks", {})),
               "new_tasks": len(new_ids), "new_tasks_reviewed": len(reviewed),
               "new_objective_tasks": len(objective_ids), "new_objective_cross_reviewed": len(crossed),
               "baseline_breadth_gaps": len(gaps), "gap_goals_reviewed": len(decisions),
               "current_breadth_gaps": sum(r["objective_breadth_gap"] for r in current_goals.values())}
    return {"schema": 1, "ok": not errors, "errors": errors, "summary": summary,
            "tasks": [reviewed[key] for key in sorted(reviewed)],
            "limits": ["版本与覆盖门禁不替代具体数学语义、图示和题族差异审查。",
                       "原题沿用原版本的审读，不能计作本轮再次逐题复核。",
                       "原不足目标逐项保留；过程作品不抵扣客观族缺口。",
                       "AI 作者和交叉核对均不表示教师独立审核或真实学生已掌握。"]}
