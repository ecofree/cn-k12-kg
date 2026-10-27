"""Inspect alignment dimensions without converting metadata into quality scores.

All promised goals remain in the report. The named focus is an additional
release requirement, not a smaller curriculum denominator. Unknowns outside
that focus remain visible and never count as teaching or independent evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

from .learning_needs import known_representations

ROOT = Path(__file__).resolve().parents[1]


def _section(catalog, name):
    value = catalog.get(name, {}) if isinstance(catalog, dict) else getattr(catalog, name, {})
    return value() if callable(value) else value


def task_dimensions(task):
    declared = task.get("family_status") == "declared" and bool(task.get("family_id"))
    known = known_representations(task.get("representation"))
    return {"explicit_goals": bool(task.get("skill_ids")), "known_family": bool(declared),
            "known_representation": bool(known), "representations": sorted(known),
            "automatic_result": task.get("type") in {"numeric", "choice", "short"},
            "independent_mastery_evidence": False}


def goal_dimensions(goal, skill, tasks, authored):
    """Count different structures, never different IDs or mere wording changes."""
    kind = goal["resource_kind"]
    if kind == "topic":
        entries = authored.get("objective_coverage", [])
        entry = next((entry for entry in entries if entry.get("criterion_index") == goal.get("criterion_index")), {})
        support = entry.get("difficulty_support", {})
        specific_help = all(isinstance(support.get(key), str) and support[key].strip()
                            for key in ("discriminating_prompt", "hint", "revision_prompt"))
        boundary = bool(entry.get("observation_note", "").strip())
    elif kind == "unit":
        support = next((entry for entry in authored.get("instructional_support", {}).get("supports", [])
                        if entry.get("objective_id") == goal.get("objective_id")), {})
        specific_help = bool(support.get("diagnostic") and support.get("guided_task") and support.get("hints"))
        boundary = bool(support.get("observation_note", "").strip())
    else:
        specific_help = bool(goal.get("revision_refs"))
        boundary = bool(authored.get("assessment_note", "").strip())
    classified = [task for task in tasks if task_dimensions(task)["known_family"]
                  and task_dimensions(task)["known_representation"]]
    objective = [task for task in classified if task_dimensions(task)["automatic_result"]]
    families = sorted({task["family_id"] for task in classified})
    reps = sorted({rep for task in classified for rep in known_representations(task.get("representation"))})
    objective_families = sorted({task["family_id"] for task in objective})
    gaps = []
    if not specific_help:
        gaps.append("goal_specific_help")
    if not boundary:
        gaps.append("authored_observation_boundary")
    if not classified:
        gaps.append("known_task_structure_and_representation")
    if kind != "project" and len(objective_families) < 2:
        gaps.append("multiple_objective_task_families")
    return {"goal_specific_help": specific_help, "authored_observation_boundary": boundary,
            "granularity": skill.get("granularity", "unknown"),
            "explicit_task_ids": [task["id"] for task in tasks],
            "classified_task_ids": [task["id"] for task in classified],
            "family_ids": families, "representations": reps,
            "objective_task_family_ids": objective_families,
            "multiple_objective_task_families": len(objective_families) >= 2,
            "gaps": gaps, "independent_mastery_evidence": False}


def build_alignment_report(data=None, *, catalog=None, scope_report=None):
    data = Path(data or ROOT / "data")
    if catalog is None:
        from .catalog import load_catalog
        catalog = load_catalog(data)
    if scope_report is None:
        from .quality_scope import build_scope_report
        scope_report = build_scope_report(data)
    target_path = data / "alignment_targets.json"
    targets = json.loads(target_path.read_text(encoding="utf-8")) if target_path.exists() else {}
    errors = []
    if targets.get("schema") != 1:
        errors.append("缺少或不支持 alignment_targets.json；不能把未声明范围当作全部通过")
    focus_values = {}
    for name in ("topic_ids", "unit_ids"):
        values = targets.get(name, [])
        if (not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values)
                or len(values) != len(set(values))):
            errors.append(f"加强验收范围 {name} 必须为无重复、非空的真实身份列表")
            focus_values[name] = set()
        else:
            focus_values[name] = set(values)
    focus_topics, focus_units = focus_values["topic_ids"], focus_values["unit_ids"]
    tasks, skills = _section(catalog, "tasks"), _section(catalog, "skills")
    active = [task for task in tasks.values() if task.get("availability") == "active"]
    by_skill = defaultdict(list)
    for task in active:
        for sid in task.get("skill_ids", []):
            by_skill[sid].append(task)
    authored = {}
    goals, inventory = [], defaultdict(Counter)
    for task in active:
        kind = task["source_kind"]
        dims = task_dimensions(task)
        inventory[kind]["tasks"] += 1
        for key in ("explicit_goals", "known_family", "known_representation"):
            inventory[kind][key] += int(dims[key])
        inventory[kind]["unknown_family"] += int(not dims["known_family"])
        inventory[kind]["unknown_representation"] += int(not dims["known_representation"])
        inventory[kind]["unmapped_goals"] += int(not dims["explicit_goals"])
        if kind == "bank" and set(task.get("topic_ids", [])) & focus_topics:
            for key in ("known_family", "known_representation"):
                if not dims[key]:
                    errors.append(f"{task['id']}: 加强范围的活跃题仍缺 {key}")
            if not task.get("alignment_note", "").strip():
                errors.append(f"{task['id']}: 缺本题观察的具体行为或保留未映射的理由")
    resource_ids = defaultdict(set)
    for goal in scope_report["goals"]:
        kind, rid = goal["resource_kind"], goal["resource_id"]
        resource_ids[kind].add(rid)
        key = (kind, rid)
        if key not in authored:
            folder = {"topic": "teach", "unit": "learning_units", "project": "projects"}[kind]
            path = data / folder / (rid + ".json")
            authored[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        dims = goal_dimensions(goal, skills.get(goal["goal_id"], {}), by_skill[goal["goal_id"]], authored[key])
        in_focus = kind == "topic" and rid in focus_topics or kind == "unit" and rid in focus_units
        if in_focus:
            for requirement in ("goal_specific_help", "authored_observation_boundary"):
                if not dims[requirement]:
                    errors.append(f"{goal['goal_id']}: 加强范围缺 {requirement}")
            if kind == "topic":
                checks = [task for task in by_skill[goal["goal_id"]] if task["source_kind"] == "self_check"]
                if not checks or any(not task_dimensions(task)["known_family"] or not task_dimensions(task)["known_representation"]
                                     for task in checks):
                    errors.append(f"{goal['goal_id']}: 加强范围的自检仍缺实际题族或表征")
        route = {"topic": "teach", "unit": "unit", "project": "project"}[kind]
        goals.append({"goal_id": goal["goal_id"], "title": goal["title"], "statement": goal["statement"],
            "stage": goal["stage"], "role": goal["role"], "resource_kind": kind, "resource_id": rid,
            "criterion_index": goal.get("criterion_index"), "objective_id": goal.get("objective_id"),
            "path": f"/?{route}={rid}&goal={goal['goal_id']}", "focus_requirement": in_focus,
            "structural_material_status": goal["status"], **dims})
    for value in focus_topics - resource_ids["topic"]:
        errors.append(f"加强验收主题不存在：{value}")
    for value in focus_units - resource_ids["unit"]:
        errors.append(f"加强验收单元不存在：{value}")
    gap_counts = Counter(gap for goal in goals for gap in goal["gaps"])
    return {"schema": 1, "subject": "mathematics", "catalog_version": getattr(catalog, "version", None),
        "focus": {"topic_ids": sorted(focus_topics), "unit_ids": sorted(focus_units),
                  "notice": "加强验收范围是本轮深入建设的学习链。下方仍展示全部承诺目标；范围以外的缺口没有被删除或降格。"},
        "summary": {"goals": len(goals), "focus_goals": sum(g["focus_requirement"] for g in goals),
            "goal_specific_help": sum(g["goal_specific_help"] for g in goals),
            "authored_observation_boundary": sum(g["authored_observation_boundary"] for g in goals),
            "known_task_structure_and_representation": sum(bool(g["classified_task_ids"]) for g in goals),
            "multiple_objective_task_families": sum(g["multiple_objective_task_families"] for g in goals),
            "gap_counts": dict(gap_counts)},
        "task_inventory": {kind: dict(counts) for kind, counts in sorted(inventory.items())},
        "focus_errors": errors, "focus_requirements_met": not errors, "goals": goals,
        "limits": ["这些是可重算的资源与关系检查，不是综合质量分、课标完整覆盖率或学习效果。",
                   "两种题族只表示作者声明了不同数学结构；仍须结合真实帮助、曝光、时间和具体表现安排后续。",
                   "开放过程、口头表达和纸笔操作不能仅靠键入一个答案确认。",
                   "多种客观题族不足表示自动安排后续观察受限；口头、作图、证明等目标仍以过程作品与量规为主，不为凑数量改成客观题。",
                   "教师独立审核与真实学生效果验证继续按用户要求暂缓。"]}
