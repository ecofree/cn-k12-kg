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

from .answers import grade_answer, option_id
from . import learning, session_content
from .runtime_version import freeze_grader
from .task_families import family_exposure

# Capture after the grading dependencies have been imported, before the first
# request. Later edits on disk must not relabel these already loaded functions.
_GRADER_RUNTIME = freeze_grader("support")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def grader_version():
    return _GRADER_RUNTIME.version


def asset_resources(unit):
    """Every frozen candidate, including guides not selected for presentation."""
    resources = list(unit.get("tasks", []))
    for support in unit.get("instructional_support", {}).get("supports", []):
        resources.extend([support.get("diagnostic", {}), support.get("guided_task", {}),
                          support.get("skip_route", {}).get("guided_task", {})])
        resources.extend(branch.get("guided_task", {}) for branch in support.get("guided_branches", []))
    return resources


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
        for hint in hints if isinstance(hints, list) else []:
            if isinstance(hint, dict) and "reference_family_ids" in hint:
                refs = hint["reference_family_ids"]
                if not learning._string_list(refs) or len(refs) != len(set(refs)):
                    errors.append(f"{label}: 提示题族必须为不重复的明确身份列表")
        branch_rows = support.get("guided_branches", [])
        if not isinstance(branch_rows, list):
            errors.append(f"{label}: guided_branches 必须为列表")
            branch_rows = []
        if any(not isinstance(branch, dict) for branch in branch_rows):
            errors.append(f"{label}: 每条指导分支必须为对象")
            branch_rows = [branch for branch in branch_rows if isinstance(branch, dict)]
        probe = support.get("diagnostic", {})
        if branch_rows:
            if not 2 <= len(branch_rows) <= 4 or probe.get("type") != "choice":
                errors.append(f"{label}: 真实分支须为2至4条，并由选择型辨别任务触发")
            route_ids, matched = set(), set()
            wrong_choices = {c.get("id") for c in probe.get("choices", [])} - {probe.get("answer")}
            for branch in branch_rows:
                bid = branch.get("id")
                choices = branch.get("choice_ids", [])
                if (not learning._text(bid) or bid in route_ids or bid in {"default", "skip"}
                        or not learning._string_list(choices, nonempty=True)
                        or len(set(choices)) != len(choices) or not set(choices) <= wrong_choices
                        or set(choices) & matched):
                    errors.append(f"{label}: 分支身份或错误选项路由冲突")
                route_ids.add(bid)
                matched.update(choices)
            if matched != wrong_choices:
                errors.append(f"{label}: 每个错误选项必须有明确指导路线，正确选项保留默认路线")
            for route in [*branch_rows, support.get("default_route", {}), support.get("skip_route", {})]:
                if not isinstance(route, dict) or any(not learning._text(route.get(key)) for key in ("title", "rationale", "observation_note")):
                    errors.append(f"{label}: 每条路线须有标题、选择依据与观察边界")
            guides = [branch.get("guided_task", {}) for branch in branch_rows]
            prompts = [task.get("prompt") for task in guides if isinstance(task, dict) and learning._text(task.get("prompt"))]
            if (len(set(prompts)) != len(prompts) or support.get("guided_task", {}).get("prompt") in prompts):
                errors.append(f"{label}: 分支必须改变实际指导任务，不能只改提示或复用同一题面")
        task_fields = [("diagnostic", support.get("diagnostic", {})), ("guided_task", support.get("guided_task", {}))]
        task_fields += [("branch:" + str(row.get("id")), row.get("guided_task", {})) for row in branch_rows]
        if isinstance(support.get("skip_route"), dict):
            task_fields.append(("skip_route", support["skip_route"].get("guided_task", {})))
        for field, task in task_fields:
            if not isinstance(task, dict):
                errors.append(f"{label}.{field}: 任务必须为对象")
                continue
            errors.extend(learning._grading_errors(task, f"{label}.{field}"))
            for name in ("prompt", "explanation"):
                if not learning._text(task.get(name)):
                    errors.append(f"{label}.{field}.{name} 不能为空")
            if not task.get("answer_spec"):
                errors.append(f"{label}.{field}: 需要显式答案契约")
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
    return [{**{key: copy.deepcopy(row[key]) for key in
             ("id", "objective_id", "title", "observation_note", "hypotheses")},
             "authored_error_routes": len(row.get("guided_branches", []))}
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
    families = [family] if family else []
    if event_type in {"feedback_released", "reference_opened"}:
        families += metadata.get("reference_family_ids", [])
    return {"event_type": event_type, "occurred_at": when, "session_id": record["id"],
            "resource_id": "unit:" + record["source_origin"]["unit_id"],
            "question_id": question, "content_sha256": record["content_sha256"],
            "payload": {"unit_id": record["source_origin"]["unit_id"],
                        "task_ids": [question] if question else [],
                        "family_ids": sorted(set(families)),
                        "independent_evidence": False, **payload}}


def _present(record, when):
    if record["index"] >= len(record["task_ids"]):
        return []
    qid = record["task_ids"][record["index"]]
    record["presentations"].setdefault(qid, {"presented_at": when})
    return [_event(record, "question_presented", when, qid)]


def _selected_route(record, diagnostic_answer):
    """Choose solely from frozen authored routes and the saved probe answer."""
    routing = record["support_snapshot"]["routing"]
    probe = record["question_snapshots"][routing["diagnostic_task_id"]]
    skipped = diagnostic_answer.get("skipped") is True
    choice = option_id(probe, diagnostic_answer.get("raw_answer")) if probe.get("type") == "choice" and not skipped else None
    route_id = (routing["skip_route_id"] if skipped else
                routing["choice_routes"].get(choice, routing["default_route_id"]))
    route = routing["routes"][route_id]
    return {"route_id": route_id, "guided_task_id": route["guided_task_id"],
            "diagnostic_task_id": routing["diagnostic_task_id"], "diagnostic_choice_id": choice,
            "trigger": "skipped" if skipped else "authored_choice" if choice in routing["choice_routes"] else "default",
            "selected_at": diagnostic_answer["answered_at"], "title": route["title"],
            "selection_reason": route["rationale"], "observation_note": route["observation_note"]}


def _confirmation_ready(prior_answers, final):
    guides = [item for item in prior_answers if item.get("support_kind") == "guided"]
    return (len(prior_answers) == 2 and len(guides) == 1 and guides[0].get("status") == "correct"
            and prior_answers[0].get("status") in {"correct", "wrong"}
            and not any(item.get("skipped") for item in prior_answers)
            and final.get("status") == "correct" and not final.get("skipped"))


def route_problem(record):
    """Return None or an error for a frozen route; never consult current content.

    Recovery uses this independently of the current grader version. Legacy
    snapshots have no routing table and retain their original three-task path.
    """
    snapshot = record.get("support_snapshot", {})
    if "routing" not in snapshot:
        return None
    message = "保存的指导分支与辨别作答或任务顺序不一致，原记录仍保留。"
    try:
        routing = snapshot["routing"]
        if (not isinstance(routing, dict) or type(routing.get("schema")) is not int or routing["schema"] != 1
                or routing.get("default_route_id") != "default" or routing.get("skip_route_id") != "skip"
                or set(routing) != {"schema", "diagnostic_task_id", "confirmation_task_id", "default_route_id", "skip_route_id", "choice_routes", "routes"}):
            return message
        questions, metadata = snapshot["questions"], snapshot["metadata"]
        routes, choices = routing["routes"], routing["choice_routes"]
        diagnostic, confirmation = routing["diagnostic_task_id"], routing["confirmation_task_id"]
        if (not isinstance(routes, dict) or not isinstance(choices, dict) or not routes
                or set(questions) != set(metadata) or diagnostic == confirmation
                or metadata[diagnostic]["support_kind"] != "diagnostic"
                or metadata[confirmation]["support_kind"] != "confirmation"
                or not set(choices.values()) <= set(routes)):
            return message
        if (not set(choices) <= {choice["id"] for choice in questions[diagnostic].get("choices", [])}
                or questions[diagnostic].get("answer") in choices):
            return message
        objective = snapshot["support"]["objective_id"]
        skills = metadata[diagnostic]["skill_ids"]
        if (not isinstance(skills, list) or len(skills) != 1 or not isinstance(skills[0], str)
                or any(task.get("id") != qid or task.get("objective_ids") != [objective]
                       or metadata[qid].get("objective_ids") != [objective]
                       or metadata[qid].get("skill_ids") != skills for qid, task in questions.items())):
            return message
        guides = set()
        for route_id, route in routes.items():
            if (not isinstance(route_id, str) or not isinstance(route, dict)
                    or set(route) != {"guided_task_id", "title", "rationale", "observation_note"}
                    or any(not isinstance(value, str) or not value.strip() for value in route.values())):
                return message
            qid = route["guided_task_id"]
            if metadata[qid]["support_kind"] != "guided":
                return message
            guides.add(qid)
        if set(questions) != {diagnostic, confirmation, *guides}:
            return message
        default = routes[routing["default_route_id"]]["guided_task_id"]
        if routing["skip_route_id"] not in routes:
            return message
        answers = record.get("answers", [])
        index = record.get("index")
        if (not isinstance(answers, list) or not isinstance(index, int) or isinstance(index, bool)
                or not 0 <= index <= 3 or index != len(answers)
                or bool(record.get("completed_at")) != (index == 3)):
            return message
        selection = _selected_route(record, answers[0]) if answers else None
        if record.get("route_selection") != selection:
            return message
        ids = [diagnostic, selection["guided_task_id"] if selection else default, confirmation]
        if record.get("task_ids") != ids or [row["question_id"] for row in answers] != ids[:index]:
            return message
        expected_presentations = set(ids[:min(index + 1, 3)])
        if set(record.get("presentations", {})) != expected_presentations:
            return message
        if any(row.get("route_id") != selection["route_id"]
               or any(row.get(key) != metadata[row["question_id"]].get(key)
                      for key in ("support_kind", "skill_ids", "objective_ids")) for row in answers):
            return message
        if index == 3:
            final = answers[-1]
            eligible = _confirmation_ready(answers[:2], final)
            expected = record.get("source_origin", {}).get("need_ids", []) if eligible else []
            if final.get("confirmation_eligible") is not eligible or final.get("resolution_for_need_ids") != expected:
                return message
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return message
    return None


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
    if any(n["kind"] not in {"answer_support", "process_revision", "support_revision"} or sid not in n["skill_ids"] for n in needs):
        raise ValueError("指导目标与原学习事项不一致")
    options = [catalog.task(tid) for tid in support["followup_task_ids"] if tid in catalog.tasks]
    # Use the actual exposure index. Unknown families never count as novel.
    def score(task):
        question = exposures.get("questions", {}).get(task["id"], {})
        family = family_exposure(exposures.get("families", {}), task.get("family_id"))
        old_representations = {n.get("representation") for n in needs}
        return (bool(question), task.get("type") == "rubric",
                task.get("family_status") != "declared" or bool(family),
                task.get("representation") in old_representations or task.get("representation") == "unknown",
                question.get("presentation_count", 0), task["phase"] == "delayed", task["id"])
    options.sort(key=score)
    if not options:
        raise ValueError("没有已登记的后续任务")
    followup = copy.deepcopy(options[0])
    snapshots, metadata = {}, {}
    diagnostic_id, guided_id = support_id + ":diagnostic", support_id + ":guided"
    default_route = support.get("default_route", {"title": "按本目标继续指导",
        "rationale": "使用这个目标原有的指导任务；当前内容没有为不同选项配置不同活动。",
        "observation_note": support["observation_note"]})
    skip_route = support.get("skip_route", {"title": "先沿原指导任务继续",
        "rationale": "这次选择暂时不会，没有足够信息判断具体困难。当前内容尚未配置单独的基础活动，继续使用本目标原有的指导任务。",
        "observation_note": "暂时不会不会被记成某一种错误原因，也不会自动关闭原事项。"})
    routes = {"default": {key: copy.deepcopy(default_route[key]) for key in ("title", "rationale", "observation_note")},
              "skip": {key: copy.deepcopy(skip_route[key]) for key in ("title", "rationale", "observation_note")}}
    routes["default"]["guided_task_id"] = guided_id
    rows = [("diagnostic", support["diagnostic"], diagnostic_id), ("guided", support["guided_task"], guided_id),
            ("confirmation", followup, followup["id"])]
    if skip_route.get("guided_task"):
        routes["skip"]["guided_task_id"] = support_id + ":guided:skip"
        rows.append(("guided", skip_route["guided_task"], routes["skip"]["guided_task_id"]))
    else:
        routes["skip"]["guided_task_id"] = guided_id
    choices = {}
    for branch in support.get("guided_branches", []):
        bid, qid = branch["id"], support_id + ":guided:" + branch["id"]
        routes[bid] = {key: copy.deepcopy(branch[key]) for key in ("title", "rationale", "observation_note")}
        routes[bid]["guided_task_id"] = qid
        choices.update({choice: bid for choice in branch["choice_ids"]})
        rows.append(("guided", branch["guided_task"], qid))
    ids = [diagnostic_id, guided_id, followup["id"]]
    routing = {"schema": 1, "diagnostic_task_id": diagnostic_id, "confirmation_task_id": followup["id"],
               "default_route_id": "default", "skip_route_id": "skip", "choice_routes": choices, "routes": routes}
    for kind, raw, qid in rows:
        task = copy.deepcopy(raw)
        task.update(id=qid, objective_ids=[support["objective_id"]])
        snapshots[qid] = task
        # A help probe's family is unclassified until an author declares it.
        metadata[qid] = {"skill_ids": [sid], "objective_ids": [support["objective_id"]],
            "topic_ids": list(unit["topic_ids"]), "support_kind": kind, "purpose": ["support"],
            "family_id": task.get("family_id", "unclassified:" + qid),
            "family_status": task.get("family_status", "declared" if task.get("family_id") else "unclassified"),
            "representation": task.get("representation", "unknown"), "alignment": "explicit_component",
            "reference_family_ids": copy.deepcopy(task.get("reference_family_ids", [])),
            "resource_version": task.get("resource_version", digest(task))}
    referenced = {t.get("diagram", {}).get("src") for t in snapshots.values()}
    frozen_assets = ({src: copy.deepcopy(value) for src, value in assets.items() if src in referenced}
                     if assets is not None else session_content.capture_assets(snapshots.values()))
    snapshot = {"support": copy.deepcopy(support), "questions": snapshots, "routing": routing,
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
        "presentations": {}, "feedback_exposed": [], "hints_opened": [], "route_selection": None,
        "feedback_policy": "immediate", "context": {"independent_evidence": False, "help_used": True},
        "selection_basis": {"candidate_ids": [t["id"] for t in options],
            "selected_id": followup["id"], "same_family_exposed": bool(family_exposure(exposures.get("families", {}), followup["family_id"])),
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
    if problem := route_problem(record):
        return problem
    if not record.get("completed_at") and record.get("grader_sha256") != grader_version():
        return "答案规则已经更新。原指导记录与问题继续保留，请重新开始当前版本的指导。"
    return None


def public_session(record):
    index = record["index"]
    support = record["support_snapshot"]["support"]
    question = record["question_snapshots"][record["task_ids"][index]] if index < len(record["task_ids"]) else None
    retry_need_ids = list(record["source_origin"]["need_ids"])
    if not retry_need_ids and record.get("completed_at") and any(
            row.get("status") in {"wrong", "not_attempted", "needs_review"} or row.get("skipped") for row in record["answers"]):
        from .learning_needs import need_identity
        retry_need_ids = [need_identity("support_revision", {"session_id": record["id"]}, record["resource_version"])]
    return {"session": {"id": record["id"], "kind": "support", "mode": "support", "title": record["title"],
                       "unit_id": record["source_origin"]["unit_id"], "context": record["context"],
                       "content_sha256": record["content_sha256"], "resume_blocked": bool(version_problem(record)),
                       "retry_need_ids": retry_need_ids},
            "support": {**support_index({"instructional_support": {"supports": [support]}})[0],
                        "step": index, "hints_opened": copy.deepcopy(record["hints_opened"]),
                        "suggested_hint_level": record.get("suggested_hint_level", 1),
                        "route": copy.deepcopy(record.get("route_selection")),
                        "step_kind": record.get("task_metadata", {}).get(question.get("id") if question else None, {}).get("support_kind", "complete")},
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
        selected_id = option_id(task, value) if task.get("type") == "choice" and not skipped else None
        selected = next((c for c in task.get("choices", []) if c["id"] == selected_id), None)
        if selected:
            row["feedback"] = selected.get("feedback", "")
            record["suggested_hint_level"] = selected.get("next_hint_level", 1)
            record["diagnostic_choice"] = selected_id
        if "routing" in record["support_snapshot"]:
            selection = _selected_route(record, row)
            record["route_selection"] = selection
            record["task_ids"][1] = selection["guided_task_id"]
    if record.get("route_selection"):
        row["route_id"] = record["route_selection"]["route_id"]
    if metadata["support_kind"] == "confirmation":
        if "routing" in record["support_snapshot"]:
            ready = _confirmation_ready(record["answers"], row)
            row["confirmation_eligible"] = ready
            row["confirmation_notice"] = ("本条指导与后续确认均留下正确记录，且未跳过步骤；这是有帮助的有限确认，仍需之后观察。" if ready else
                "前面的步骤仍有跳过、指导未答对或过程待审核，或本次确认尚未答对；原事项继续保留，不能用最后一个数覆盖前面的困难。")
            row["resolution_for_need_ids"] = copy.deepcopy(record["source_origin"]["need_ids"]) if ready else []
        else:
            # Completed historical sessions retain their original bounded rule.
            row["resolution_for_need_ids"] = copy.deepcopy(record["source_origin"]["need_ids"])
    record["answers"].append(row)
    record["feedback_exposed"].append(question_id)
    record["index"] += 1
    events = [_event(record, "answer_submitted", when, question_id, status=row["status"], help_used=True),
              _event(record, "feedback_released", when, question_id)]
    if metadata["support_kind"] == "diagnostic" and record.get("route_selection"):
        events.append(_event(record, "support_route_selected", when, question_id,
                             **copy.deepcopy(record["route_selection"]), help_used=True))
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
    authored_families = "reference_family_ids" in value
    families = set(value.get("reference_family_ids", []))
    branches = support.get("branch_hints", {}).get(record.get("diagnostic_choice"), [])
    if len(branches) >= level:
        branch = branches[level - 1]
        value["branch_text"] = branch.get("text") if isinstance(branch, dict) else branch
        if isinstance(branch, dict) and "reference_family_ids" in branch:
            families.update(branch["reference_family_ids"])
        else:
            # A legacy branch hint has no exact family declaration. Keep the
            # old conservative scope rather than pretending its text is covered
            # by the new common hint's narrower declaration.
            authored_families = False
    if not any(h["level"] == level for h in record["hints_opened"]):
        record["hints_opened"].append({**value, "opened_at": when})
    if not authored_families:
        # Historical snapshots keep their original bounded attribution. Do not
        # consult current author files or add frozen but unselected branches.
        exposed = [record["task_ids"][-1]]
        if record.get("route_selection"):
            exposed.append(record["route_selection"]["guided_task_id"])
        for qid in exposed:
            metadata = record["task_metadata"][qid]
            if metadata.get("family_status") == "declared":
                families.add(metadata["family_id"])
            families.update(metadata.get("reference_family_ids", []))
    # A declared hint exposes the mathematical structures actually delivered at
    # this level, not the unseen confirmation's text, later hints, or other
    # branches. Preserve the old question attribution only for legacy snapshots.
    question_id = None if authored_families else record["task_ids"][-1]
    return value, [_event(record, "reference_opened", when, question_id,
                         hint_level=level, help_used=True, family_ids=sorted(families),
                         family_attribution="authored_hint" if authored_families else "legacy_selected_tasks",
                         route_id=record.get("route_selection", {}).get("route_id") if record.get("route_selection") else None,
                         evidence="hint_delivered_not_proof_of_reading")]
