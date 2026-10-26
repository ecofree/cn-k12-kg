"""Frozen, objective-specific help with bounded follow-up observations.

Help is a learning activity, never an independent test or a mastery decision.
The server keeps unreleased hints, choices' feedback and follow-up answers.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import uuid

from .answers import grade_answer
from . import learning, session_content
from .runtime_version import freeze_grader

# Capture after the grading dependencies have been imported, before the first
# request. Later edits on disk must not relabel these already loaded functions.
_GRADER_RUNTIME = freeze_grader("support")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def grader_version():
    return _GRADER_RUNTIME.version


def check_supports(unit):
    package = unit.get("instructional_support")
    if package is None:
        return []  # The quality-scope release gate checks required coverage.
    errors = []
    if not isinstance(package, dict) or package.get("schema") != 1:
        return ["instructional_support.schema 必须为 1"]
    supports = package.get("supports")
    if not isinstance(supports, list):
        return ["instructional_support.supports 必须为列表"]
    goals = {o["id"] for o in unit.get("objectives", [])}
    tasks = {t["id"]: t for t in unit.get("tasks", [])}
    seen, covered = set(), set()
    for support in supports:
        if not isinstance(support, dict):
            errors.append("support 必须为对象")
            continue
        label = support.get("id", "support")
        if not learning._text(label) or label in seen:
            errors.append(f"{label}: 支持 ID 缺失或重复")
        seen.add(label)
        objective = support.get("objective_id")
        if objective not in goals:
            errors.append(f"{label}: 引用了未定义目标")
        covered.add(objective)
        for field in ("title", "observation_note"):
            if not learning._text(support.get(field)):
                errors.append(f"{label}: {field} 必须说明学习事项及观察边界")
        if not learning._string_list(support.get("hypotheses"), nonempty=True):
            errors.append(f"{label}: 必须说明待辨别的可能困难")
        hints = support.get("hints", [])
        if (not isinstance(hints, list) or [h.get("level") for h in hints if isinstance(h, dict)] != [1, 2, 3]
                or not all(isinstance(h, dict) and learning._text(h.get("text")) for h in hints)):
            errors.append(f"{label}: 提示须按 1、2、3 级提供具体内容")
        for field in ("diagnostic", "guided_task"):
            task = support.get(field, {})
            if not isinstance(task, dict):
                errors.append(f"{label}.{field}: 任务必须为对象")
                continue
            errors.extend(learning._grading_errors(task, f"{label}.{field}"))
            for name in ("prompt", "explanation"):
                if not learning._text(task.get(name)):
                    errors.append(f"{label}.{field}.{name} 不能为空")
            if not task.get("answer_spec"):
                errors.append(f"{label}.{field}: 需要显式答案契约")
        probe = support.get("diagnostic", {})
        if probe.get("type") == "choice":
            for choice in probe.get("choices", []):
                if not learning._text(choice.get("feedback")):
                    errors.append(f"{label}: 每个诊断选项都需要辨别性反馈")
        for key in support.get("branch_hints", {}):
            if key not in {c.get("id") for c in probe.get("choices", [])}:
                errors.append(f"{label}: 提示分支引用未知选项 {key}")
        refs = support.get("followup_task_ids", [])
        if not learning._string_list(refs, nonempty=True):
            errors.append(f"{label}: 必须提供实际后续任务")
        for ref in refs:
            if ref not in tasks or objective not in tasks[ref].get("objective_ids", []):
                errors.append(f"{label}: 后续任务 {ref} 没有显式观察这个目标")
    missing = goals - covered
    if missing:
        errors.append(f"instructional_support 缺少目标：{sorted(missing)}")
    return errors


def support_index(unit):
    return [{key: copy.deepcopy(row[key]) for key in
             ("id", "objective_id", "title", "observation_note", "hypotheses")}
            for row in unit.get("instructional_support", {}).get("supports", [])]


def _public_question(task, record):
    shown = {key: copy.deepcopy(task[key]) for key in
             ("id", "prompt", "type", "answer_spec", "objective_ids") if key in task}
    if task.get("choices"):
        shown["choices"] = [{"id": c["id"], "text": c["text"]} for c in task["choices"]]
    if task.get("rubric"):
        shown["rubric"] = [{"criterion": r["criterion"], "points": r["points"]} for r in task["rubric"]]
    if task.get("diagram"):
        shown["diagram"] = session_content.public_diagram(record, task["diagram"])
    return shown


def _event(record, event_type, when, question=None, **payload):
    metadata = record.get("task_metadata", {}).get(question, {})
    family = metadata.get("family_id") if metadata.get("family_status") == "declared" else None
    return {"event_type": event_type, "occurred_at": when, "session_id": record["id"],
            "resource_id": "unit:" + record["source_origin"]["unit_id"],
            "question_id": question, "content_sha256": record["content_sha256"],
            "payload": {"unit_id": record["source_origin"]["unit_id"],
                        "task_ids": [question] if question else [],
                        "family_ids": [family] if family else [],
                        "independent_evidence": False, **payload}}


def _present(record, when):
    if record["index"] >= len(record["task_ids"]):
        return []
    qid = record["task_ids"][record["index"]]
    record["presentations"].setdefault(qid, {"presented_at": when})
    return [_event(record, "question_presented", when, qid)]


def create(unit, support_id, owner, catalog, exposures, needs, when, *, assets=None):
    support = next((s for s in unit.get("instructional_support", {}).get("supports", [])
                    if s["id"] == support_id), None)
    if not support:
        raise ValueError("这个目标还没有对应的指导材料")
    objective = next(o for o in catalog.units[unit["id"]]["objectives"]
                     if o["id"] == support["objective_id"])
    sid = objective["skill_id"]
    versions = {n["resource_version"] for n in needs}
    if len(versions) > 1:
        raise ValueError("不同内容版本的事项须分别跟进，不能合并清除")
    if any(n["kind"] not in {"answer_support", "process_revision"} or sid not in n["skill_ids"] for n in needs):
        raise ValueError("指导目标与原学习事项不一致")
    options = [catalog.task(tid) for tid in support["followup_task_ids"] if tid in catalog.tasks]
    # Use the actual exposure index. Unknown families never count as novel.
    def score(task):
        question = exposures.get("questions", {}).get(task["id"], {})
        family = exposures.get("families", {}).get(task.get("family_id"), {})
        old_representations = {n.get("representation") for n in needs}
        return (bool(question), task.get("type") == "rubric",
                task.get("family_status") != "declared" or bool(family),
                task.get("representation") in old_representations or task.get("representation") == "unknown",
                question.get("presentation_count", 0), task["phase"] == "delayed", task["id"])
    options.sort(key=score)
    if not options:
        raise ValueError("没有已登记的后续任务")
    followup = copy.deepcopy(options[0])
    snapshots, metadata, ids = {}, {}, []
    for kind, raw in (("diagnostic", support["diagnostic"]), ("guided", support["guided_task"]),
                      ("confirmation", followup)):
        task = copy.deepcopy(raw)
        qid = followup["id"] if kind == "confirmation" else support_id + ":" + kind
        task.update(id=qid, objective_ids=[support["objective_id"]])
        snapshots[qid] = task
        # A help probe's family is unclassified until an author declares it.
        metadata[qid] = {"skill_ids": [sid], "objective_ids": [support["objective_id"]],
            "topic_ids": list(unit["topic_ids"]), "support_kind": kind, "purpose": ["support"],
            "family_id": task.get("family_id", "unclassified:" + qid),
            "family_status": task.get("family_status", "unclassified"),
            "representation": task.get("representation", "unknown"), "alignment": "explicit_component",
            "resource_version": task.get("resource_version", digest(task))}
        ids.append(qid)
    referenced = {t.get("diagram", {}).get("src") for t in snapshots.values()}
    frozen_assets = ({src: copy.deepcopy(value) for src, value in assets.items() if src in referenced}
                     if assets is not None else session_content.capture_assets(snapshots.values()))
    snapshot = {"support": copy.deepcopy(support), "questions": snapshots,
                "metadata": metadata, "unit_version": catalog.units[unit["id"]]["resource_version"]}
    snapshot["support"]["_asset_hashes"] = {src: value["sha256"] for src, value in frozen_assets.items()}
    record = {"id": uuid.uuid4().hex, "owner_id": owner, "kind": "support", "mode": "support",
        "resource_id": "support:" + unit["id"] + ":" + support_id, "title": support["title"],
        "stage": unit["stage"], "created_at": when, "answers": [], "index": 0,
        "task_ids": ids, "question_snapshots": snapshots, "task_metadata": metadata,
        "support_snapshot": snapshot, "content_sha256": digest(snapshot),
        "grader_sha256": grader_version(),
        "resource_version": catalog.units[unit["id"]]["resource_version"],
        "source_origin": {"unit_id": unit["id"], "need_ids": [n["need_id"] for n in needs],
                          "resource_version": next(iter(versions), None),
                          "source_ref": [n["source_ref"] for n in needs]},
        "asset_snapshots": frozen_assets,
        "presentations": {}, "feedback_exposed": [], "hints_opened": [],
        "feedback_policy": "immediate", "context": {"independent_evidence": False, "help_used": True},
        "selection_basis": {"candidate_ids": [t["id"] for t in options],
            "selected_id": followup["id"], "same_family_exposed": bool(exposures.get("families", {}).get(followup["family_id"])),
            "notice": "先避免已呈现题；有未呈现且可客观判分的对应任务时，优先用于这次有限确认，再比较已知题族和表征。帮助后的确认不作为独立检查或间隔复测。"}}
    return record, [_event(record, "support_started", when), *_present(record, when)]


def version_problem(record):
    snapshot = record.get("support_snapshot", {})
    if (digest(snapshot) != record.get("content_sha256")
            or snapshot.get("questions") != record.get("question_snapshots")
            or snapshot.get("metadata") != record.get("task_metadata")):
        return "保存的指导内容校验失败，原作答仍保留。"
    try:
        expected = snapshot["support"].get("_asset_hashes", {})
        assets = record.get("asset_snapshots", {})
        if set(expected) != set(assets) or any(
                expected[src] != asset.get("sha256") or hashlib.sha256(base64.b64decode(asset["body_base64"], validate=True)).hexdigest() != expected[src]
                for src, asset in assets.items()):
            return "保存的指导图示校验失败，原作答仍保留。"
    except (KeyError, TypeError, ValueError):
        return "保存的指导图示不完整，原作答仍保留。"
    if not record.get("completed_at") and record.get("grader_sha256") != grader_version():
        return "答案规则已经更新。原指导记录与问题继续保留，请重新开始当前版本的指导。"
    return None


def public_session(record):
    index = record["index"]
    support = record["support_snapshot"]["support"]
    question = record["question_snapshots"][record["task_ids"][index]] if index < len(record["task_ids"]) else None
    return {"session": {"id": record["id"], "kind": "support", "mode": "support", "title": record["title"],
                       "unit_id": record["source_origin"]["unit_id"], "context": record["context"],
                       "content_sha256": record["content_sha256"], "resume_blocked": bool(version_problem(record))},
            "support": {**support_index({"instructional_support": {"supports": [support]}})[0],
                        "step": index, "hints_opened": copy.deepcopy(record["hints_opened"]),
                        "suggested_hint_level": record.get("suggested_hint_level", 1)},
            "item": _public_question(question, record) if question else None,
            "done": question is None, "answers": copy.deepcopy(record["answers"]),
            "selection_basis": copy.deepcopy(record["selection_basis"]),
            "notice": "分层帮助用于辨别与练习。后续正确只记录本次有帮助的表现；原题的作答与评分保持原样。"}


def answer(record, question_id, value, when, skipped=False):
    if version_problem(record):
        raise ValueError(version_problem(record))
    if record["index"] >= len(record["task_ids"]) or question_id != record["task_ids"][record["index"]]:
        raise ValueError("任务已经提交或当前任务已改变，请恢复已保存的指导活动")
    if not isinstance(value, str) or len(value) > 8000:
        raise ValueError("请提交不超过 8000 字符的答案")
    task = record["question_snapshots"][question_id]
    grade = grade_answer(task, value) if not skipped else {"status": "not_attempted"}
    if grade["status"] == "invalid":
        return {"status": "invalid", "reason": grade.get("reason", "请检查答案格式")}, []
    metadata = record["task_metadata"][question_id]
    row = {**copy.deepcopy(metadata), "question_id": question_id, "prompt": task["prompt"],
           "raw_answer": value, "normalized_answer": grade.get("normalized_answer"),
           "status": grade["status"], "skipped": bool(skipped), "answered_at": when,
           "presented_at": record["presentations"][question_id]["presented_at"],
           "content_sha256": metadata["resource_version"], "help_used": True,
           "explanation": task.get("explanation", ""), "answer": task.get("answer", ""),
           "feedback_available_at": when}
    if task.get("rubric"):
        row["rubric"] = copy.deepcopy(task["rubric"])
    if metadata["support_kind"] == "diagnostic":
        selected = next((c for c in task.get("choices", []) if c["id"] == value), None)
        if selected:
            row["feedback"] = selected.get("feedback", "")
            record["suggested_hint_level"] = selected.get("next_hint_level", 1)
            record["diagnostic_choice"] = value
    if metadata["support_kind"] == "confirmation":
        row["resolution_for_need_ids"] = copy.deepcopy(record["source_origin"]["need_ids"])
    record["answers"].append(row)
    record["feedback_exposed"].append(question_id)
    record["index"] += 1
    events = [_event(record, "answer_submitted", when, question_id, status=row["status"], help_used=True),
              _event(record, "feedback_released", when, question_id)]
    if record["index"] >= len(record["task_ids"]):
        record["completed_at"] = when
        events.append(_event(record, "support_completed", when))
    else:
        events += _present(record, when)
    return row, events


def hint(record, level, when):
    if isinstance(level, bool) or level not in (1, 2, 3):
        raise ValueError("提示级别须为 1、2、3")
    if record["index"] == 0:
        raise ValueError("先作答或选择暂时不会，再按需要打开提示")
    if version_problem(record):
        raise ValueError(version_problem(record))
    support = record["support_snapshot"]["support"]
    value = copy.deepcopy(support["hints"][level - 1])
    branches = support.get("branch_hints", {}).get(record.get("diagnostic_choice"), [])
    if len(branches) >= level:
        branch = branches[level - 1]
        value["branch_text"] = branch.get("text") if isinstance(branch, dict) else branch
    if not any(h["level"] == level for h in record["hints_opened"]):
        record["hints_opened"].append({**value, "opened_at": when})
    # Include follow-up family exposure: this help may share its method.
    return value, [_event(record, "reference_opened", when, record["task_ids"][-1],
                         hint_level=level, help_used=True, evidence="hint_delivered_not_proof_of_reading")]
