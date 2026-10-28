"""Explicit observation plans and authored tasks for existing stable goals.

Components are separately inspectable pieces of a goal, not new mastery claims.
The original skill identities, task contracts and frozen learner records are
unchanged. Schema/link checks establish usable resources, never mathematical
independence or an external review of the learner's work.
"""
from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path

from .task_families import canonical_family


def section(catalog, name):
    value = catalog.get(name, {}) if isinstance(catalog, dict) else getattr(catalog, name, {})
    return value() if callable(value) else value


def load_sources(data, *, schema_dir=None):
    """Read explicit authored files; no inferred goals, families or task IDs."""
    from .contracts import validate_resource
    from .learning import _grading_errors

    data = Path(data)
    sources, errors = [], []
    for path in sorted((data / "assessments").glob("*.json")):
        if path.is_symlink() or not path.resolve().is_relative_to(data.resolve()):
            errors.append(f"观察任务源越界：{path.name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        problems = validate_resource("assessment-source", payload, schema_dir=schema_dir)
        errors.extend(f"{path.name}: {error}" for error in problems)
        if problems:
            continue
        for task in payload["tasks"]:
            errors.extend(_grading_errors(task, task["id"]))
        sources.append((path, payload))
    return sources, errors


def validate_links(plans, tasks, skills, placements, units, projects, *, require_complete=False):
    """Validate actual task/component membership, retaining objective gaps."""
    errors, components, used_new = [], {}, set()
    for sid, plan in plans.items():
        if sid not in skills:
            errors.append(f"{sid}: 观察计划没有对应的稳定目标")
            continue
        kind, source = plan["source_kind"], plan["source_id"]
        if kind == "topic":
            owned = placements.get(source, {}).get("criterion_skill_ids", [])
        else:
            owned = (units if kind == "unit" else projects).get(source, {}).get("skill_ids", [])
        if sid not in owned:
            errors.append(f"{sid}: 来源 {kind}:{source} 没有声明这个目标")
        related = []
        for tid in plan["task_ids"]:
            task = tasks.get(tid)
            if not task or task.get("availability") != "active" or sid not in task.get("skill_ids", []):
                errors.append(f"{sid}: 任务 {tid} 不存在、已隔离或没有明确观察这个目标")
                continue
            related.append(task)
            if task["source_kind"] == "assessment":
                used_new.add(tid)
        for component in plan["components"]:
            identity = component["id"]
            if identity in components:
                errors.append(f"重复的观察项身份：{identity}")
            components[identity] = (sid, component)
            for tid in component["task_ids"]:
                if tid not in plan["task_ids"]:
                    errors.append(f"{identity}: 任务 {tid} 不在本目标计划内")
                task = tasks.get(tid, {})
                if task.get("source_kind") == "assessment" and identity not in task.get("component_ids", []):
                    errors.append(f"{identity}: 新任务 {tid} 未明确绑定这项过程")
        all_families = {canonical_family(task["family_id"]) for task in related
                        if task.get("family_status") == "declared"}
        distinct = set()
        for row in plan["family_distinctions"]:
            left, right = canonical_family(row["left_family_id"]), canonical_family(row["right_family_id"])
            if left == right or left not in all_families or right not in all_families:
                errors.append(f"{sid}: 结构比较必须指向本目标已有的两个不同规范题族")
            key = tuple(sorted((left, right)))
            if key in distinct:
                errors.append(f"{sid}: 重复的结构比较 {key}")
            distinct.add(key)
        if plan["assessment_mode"] in {"mixed", "process"} and not any(t["type"] == "rubric" for t in related):
            errors.append(f"{sid}: 过程目标没有可提交的量规任务")
    for tid, task in tasks.items():
        if task.get("source_kind") != "assessment":
            continue
        if tid not in used_new:
            errors.append(f"{tid}: 新观察任务没有实际目标计划入口")
        if set(task.get("objective_ids", [])) != set(task["skill_ids"]):
            errors.append(f"{tid}: 观察任务的目标与技能绑定不一致")
        for identity in task.get("component_ids", []):
            pair = components.get(identity)
            if not pair or pair[0] not in task["skill_ids"] or tid not in pair[1]["task_ids"]:
                errors.append(f"{tid}: 观察项 {identity} 没有反向任务绑定")
        if set(task["skill_ids"]) - {components[c][0] for c in task.get("component_ids", []) if c in components}:
            errors.append(f"{tid}: 每个声明目标都须有实际分项对应")
    if require_complete:
        errors.extend(f"{sid}: 仍缺逐目标观察计划" for sid in sorted(set(skills) - set(plans)))
    return errors


def report(catalog, *, require_complete=False):
    from .alignment import task_dimensions

    plans, tasks, skills = (section(catalog, name) for name in ("assessment_plans", "tasks", "skills"))
    errors = validate_links(plans, tasks, skills, section(catalog, "placements"),
                            section(catalog, "units"), section(catalog, "projects"),
                            require_complete=require_complete)
    rows = []
    for sid, plan in plans.items():
        related = [tasks[tid] for tid in plan["task_ids"] if tid in tasks and tasks[tid].get("availability") == "active"]
        classified = [task for task in related if task_dimensions(task)["known_family"]
                      and task_dimensions(task)["known_representation"]]
        objective = sorted({canonical_family(t["family_id"]) for t in classified
                            if t.get("family_status") == "declared" and t["type"] in {"numeric", "choice", "short"}})
        process = sorted({canonical_family(t["family_id"]) for t in classified
                          if t.get("family_status") == "declared" and t["type"] == "rubric"})
        rows.append({"skill_id": sid, "source_kind": plan["source_kind"], "source_id": plan["source_id"],
                     "statement": skills.get(sid, {}).get("statement"), "stage": skills.get(sid, {}).get("stage"),
                     "original_granularity": skills.get(sid, {}).get("granularity"),
                     "assessment_mode": plan["assessment_mode"], "component_count": len(plan["components"]),
                     "objective_family_ids": objective, "process_family_ids": process,
                     "objective_breadth_gap": plan["source_kind"] != "project" and len(objective) < 2,
                     "remaining_observation_requirements": copy.deepcopy(plan["unobserved_aspects"]),
                     "task_ids": list(plan["task_ids"]), "independent_mastery_evidence": False})
    new_tasks = [task for task in tasks.values() if task.get("source_kind") == "assessment"]
    return {"schema": 1, "ok": not errors, "errors": errors,
            "summary": {"registered_goals": len(skills), "planned_goals": len(plans),
                        "components": sum(len(p["components"]) for p in plans.values()),
                        "modes": dict(Counter(p["assessment_mode"] for p in plans.values())),
                        "new_tasks": len(new_tasks), "task_types": dict(Counter(t["type"] for t in new_tasks)),
                        "planned_topic_and_unit_goals": sum(r["source_kind"] != "project" for r in rows),
                        "planned_project_goals": sum(r["source_kind"] == "project" for r in rows),
                        "goals_with_two_objective_families": sum(r["source_kind"] != "project" and len(r["objective_family_ids"]) >= 2 for r in rows),
                        "objective_breadth_gaps": sum(r["objective_breadth_gap"] for r in rows),
                        "goals_with_process_tasks": sum(bool(r["process_family_ids"]) for r in rows)},
            "goals": rows, "limits": ["分项、过程任务和客观题族分别统计；过程作品不消除客观题族不足的事实。",
                "任务和量规是观察机会，不是已经取得的学生表现或教师审核。",
                "新组件不重分配旧目标身份，也不将一次结果自动提升为父目标掌握。"]}


def public_plan(catalog, skill_id):
    """Navigation and authored criteria only; never return unseen references."""
    plan = section(catalog, "assessment_plans").get(skill_id)
    if not plan:
        return None
    result = {key: copy.deepcopy(plan[key]) for key in
              ("skill_id", "source_kind", "source_id", "assessment_mode", "scope_note",
               "task_ids", "unobserved_aspects", "resource_version")}
    # Author criteria and pairwise mathematical justifications may contain
    # worked results. They are not sent with navigation before a task starts.
    result["components"] = [{key: copy.deepcopy(component[key]) for key in
                              ("id", "statement", "submission_requirements", "task_ids")}
                             for component in plan["components"]]
    result["independent_mastery_evidence"] = False
    result["notice"] = "每项分别保存原始作答、过程和修订。客观结果只对应本题，量规作品保持待查看；完成分项不会自动认定整个目标。"
    return result


def observation_unit(catalog, skill_id):
    """Adapt explicit tasks to the existing frozen unit-session contract.

Only newly authored observation tasks enter this adapter. Existing bank,
self-check and project tasks keep their original start actions and contracts.
"""
    plans, tasks, skills = (section(catalog, name) for name in ("assessment_plans", "tasks", "skills"))
    plan = plans.get(skill_id)
    if plan is None or skill_id not in skills:
        raise KeyError(skill_id)
    chosen = [copy.deepcopy(tasks[tid]) for tid in plan["task_ids"]
              if tasks[tid].get("source_kind") == "assessment" and tasks[tid].get("availability") == "active"]
    return {"schema": 1, "id": "assessment:" + skill_id, "title": skills[skill_id]["statement"] + " · 分项观察",
            "stage": skills[skill_id]["stage"], "topic_ids": sorted({tid for task in chosen for tid in task["topic_ids"]}),
            "objectives": [{"id": skill_id, "skill_id": skill_id, "statement": skills[skill_id]["statement"],
                            "granularity": skills[skill_id]["granularity"]}],
            "skill_ids": [skill_id], "tasks": chosen, "task_ids": [t["id"] for t in chosen],
            "observation_plan": copy.deepcopy(plan), "assessment_goal_id": skill_id,
            "resource_version": plan["resource_version"], "review_status": "reviewed",
            "teacher_review": {"status": "pending", "independent_review": False},
            "independent_mastery_evidence": False}
