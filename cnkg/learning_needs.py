"""Rebuildable learning needs from immutable observations, not mastery scores.

Callers supply an owner's complete compact history. A newer phase, content
version or browser cannot erase an earlier need. Resolution always names the
later observation (or learner-reported revision) that closes that bounded task.
"""
from __future__ import annotations

import hashlib
import json

from .review_timing import timestamp


def _section(catalog, name):
    value = catalog.get(name, {}) if isinstance(catalog, dict) else getattr(catalog, name, {})
    return value() if callable(value) else value


def _version(record):
    return record.get("resource_version") or record.get("content_sha256")


def observation_metadata(record, answer):
    """Select metadata exclusively from the observation's frozen content."""
    qid = answer.get("question_id")
    snapshot = record.get("question_snapshots", {}).get(qid, {})
    if not snapshot:
        snapshot = next((task for task in record.get("unit_snapshot", {}).get("tasks", [])
                         if task.get("id") == qid), {})
    metadata = record.get("task_metadata", {}).get(qid, {})
    return {**snapshot, **metadata, **answer}


def need_identity(kind, source_ref, version):
    raw = json.dumps([kind, source_ref, version], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "need_" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def work_is_reviewed(work):
    if not work:
        return False
    outcomes = work.get("criterion_outcomes")
    if isinstance(outcomes, list):
        return bool(outcomes) and all(row.get("status") == "met" for row in outcomes)
    # Older compact summaries can only establish that a self-check was saved.
    # All real store summaries are rebuilt to explicit per-criterion outcomes.
    return bool(work.get("self_assessment_completed")) and not work.get("revision_needed")


def _observations(records):
    for record in records:
        for answer in record.get("answers", []):
            if not answer.get("question_id"):
                continue
            metadata = observation_metadata(record, answer)
            yield record, metadata


def _source(record, answer):
    return {"session_id": record["id"], "question_id": answer["question_id"]}


def _when(record, answer):
    return timestamp(answer.get("answered_at") or record.get("completed_at") or record.get("updated_at")
                     or record.get("created_at"))


def _task_key(record, answer):
    version = answer.get("content_sha256") or answer.get("resource_version") or _version(record)
    return (record.get("kind"), record.get("unit_id"), answer["question_id"], version) if version else None


def derive_needs(catalog, records, works=(), states=()):
    """Return every bounded need, including explicitly deferred/resolved ones."""
    records, works = list(records), list(works)
    observations = list(_observations(records))
    work_index = {work["resource_id"]: work for work in works if work.get("resource_id")}
    state_index = {row["need_id"]: row for row in states if row.get("need_id")}
    units, placements = _section(catalog, "units"), _section(catalog, "placements")
    # Index potential follow-ups once. Reading a long history must not compare
    # every wrong answer with every other answer in a quadratic scan.
    correct_by_task, support_by_need, followups = {}, {}, {}
    primary_ids = {need_identity("process_revision" if answer.get("status") == "needs_review" else "answer_support",
                                _source(record, answer), _version(record))
                   for record, answer in observations if record.get("kind") != "support"
                   and (answer.get("status") in {"wrong", "not_attempted", "needs_review"} or answer.get("skipped"))}
    for record, answer in observations:
        linked_ids = record.get("source_origin", {}).get("need_ids", []) if record.get("kind") == "support" else []
        for need_id in set(linked_ids) & primary_ids:
            followups.setdefault(need_id, []).append({"source_ref": _source(record, answer),
                "status": answer.get("status"), "help_used": answer.get("help_used"),
                "resource_version": _version(record), "observed_at": answer.get("answered_at"),
                "terminal_confirmation": need_id in answer.get("resolution_for_need_ids", [])})
        when = _when(record, answer)
        if answer.get("status") != "correct" or answer.get("skipped") or when is None:
            continue
        key = _task_key(record, answer)
        if key:
            correct_by_task.setdefault(key, []).append((when, record, answer))
        for need_id in set(linked_ids) & set(answer.get("resolution_for_need_ids", [])):
            support_by_need.setdefault(need_id, []).append((when, record, answer))
    needs, process_work_ids = [], set()
    for record, answer in observations:
        status = answer.get("status")
        if status not in {"wrong", "not_attempted", "needs_review"} and not answer.get("skipped"):
            continue
        if record.get("kind") == "support":
            # Guided errors are follow-up observations on the original need,
            # not duplicate orphan tasks with no independent revision route.
            continue
        kind = "process_revision" if status == "needs_review" else "answer_support"
        source, version = _source(record, answer), _version(record)
        identity = need_identity(kind, source, version)
        targets = answer.get("topic_ids") or record.get("targets", record.get("engine", {}).get("targets", []))
        unit = units.get(record.get("unit_id"), {})
        stages = sorted({value for value in [record.get("stage"), record.get("unit_snapshot", {}).get("stage"),
                                              unit.get("stage")]
                         if value} | {placements[tid].get("stage") for tid in targets
                                      if tid in placements and placements[tid].get("stage")})
        need = {"need_id": identity, "kind": kind, "status": "open", "source_ref": source,
                "source_refs": [source], "resource_version": version,
                "question_version": answer.get("content_sha256") or answer.get("resource_version"),
                "unit_id": record.get("unit_id"), "targets": list(targets), "stages": stages,
                "title": record.get("title") or unit.get("title") or "已保存的学习事项",
                "phase": record.get("mode"), "observed_status": status,
                "observed_at": answer.get("answered_at") or record.get("completed_at") or record.get("created_at"),
                "skill_ids": list(answer.get("skill_ids", [])), "objective_ids": list(answer.get("objective_ids", [])),
                "family_id": answer.get("family_id"), "family_status": answer.get("family_status", "unclassified"),
                "representation": answer.get("representation"), "help_used": answer.get("help_used"),
                "source_completed": bool(record.get("completed_at")), "source_kind": record.get("kind"),
                "evidence_basis": "recorded_answer" if kind == "answer_support" else "process_requires_review",
                "resolution": None,
                "followup_observations": followups.get(identity, []),
                "notice": "只描述这次任务留下的学习事项；完成修订或后来答对都不等于掌握。"}
        if kind == "process_revision":
            work_key = f"session_task:{record['id']}:{answer['question_id']}"
            process_work_ids.add(work_key)
            saved = work_index.get(work_key)
            need["work_resource_id"] = work_key
            need["criterion_outcomes"] = saved.get("criterion_outcomes", []) if saved else []
            need["unresolved_criterion_indices"] = saved.get("unresolved_criterion_indices", []) if saved else []
            need["revision_needed"] = bool(saved and saved.get("revision_needed"))
            if work_is_reviewed(saved):
                need["resolution"] = {"kind": "learner_reported_revision", "work_resource_id": work_key,
                                      "revision": saved.get("revision", saved.get("_revision")),
                                      "observed_at": saved.get("updated_at"), "independent_evidence": False,
                                      "notice": "量规逐项自报达到，只关闭自查待办；原始 needs_review 结果不变。"}
        else:
            original_time = _when(record, answer)
            later_matches = []
            exact = correct_by_task.get(_task_key(record, answer), [])
            for later_time, other, later in [*exact, *support_by_need.get(identity, [])]:
                if original_time is None or later_time <= original_time:
                    continue
                origin = other.get("source_origin", {})
                linked_support = (other.get("kind") == "support" and bool(other.get("completed_at"))
                    and identity in later.get("resolution_for_need_ids", [])
                    and identity in origin.get("need_ids", []) and version
                    and origin.get("resource_version") == version
                    and set(need["skill_ids"]).issubset(later.get("skill_ids", [])))
                if linked_support or (_task_key(record, answer) is not None
                                      and _task_key(record, answer) == _task_key(other, later)):
                    later_matches.append((later_time, other, later, bool(linked_support)))
            if later_matches:
                _, other, later, linked = min(later_matches, key=lambda item: (item[0], item[1]["id"]))
                need["resolution"] = {"kind": "supported_confirmation" if linked else "later_same_task_correct",
                                      "source_ref": _source(other, later), "observed_at": later.get("answered_at") or other.get("completed_at"),
                                      "help_used": later.get("help_used"), "independent_evidence": False,
                                      "notice": "保留后来作答的具体结果与帮助记录；重复题或帮助后的正确不证明独立掌握。"}
        needs.append(need)
    for record in records:
        if record.get("kind") != "support" or set(record.get("source_origin", {}).get("need_ids", [])) & primary_ids:
            continue
        answers = [observation_metadata(record, answer) for answer in record.get("answers", [])]
        failures = [answer for answer in answers if answer.get("status") in {"wrong", "not_attempted", "needs_review"}
                    or answer.get("skipped")]
        if not failures:
            continue
        last = failures[-1]
        source, version = {"session_id": record["id"]}, _version(record)
        confirmations = [answer for answer in answers if answer.get("status") == "correct" and not answer.get("skipped")
                         and answer.get("support_kind") == "confirmation" and record.get("completed_at")
                         and _when(record, answer) and _when(record, last) and _when(record, answer) > _when(record, last)]
        resolution = ({"kind": "supported_confirmation", "source_ref": _source(record, confirmations[-1]),
                       "help_used": confirmations[-1].get("help_used"), "independent_evidence": False,
                       "notice": "本次指导后的终结确认已记录；这不证明独立掌握。"} if confirmations else None)
        unit_id = record.get("source_origin", {}).get("unit_id") or record.get("unit_id")
        stage = record.get("stage") or units.get(unit_id, {}).get("stage")
        needs.append({"need_id": need_identity("support_revision", source, version), "kind": "support_revision",
                      "status": "open", "source_ref": source, "source_refs": [_source(record, last)],
                      "resource_version": version, "unit_id": unit_id, "targets": [],
                      "title": record.get("title", "继续指导练习"), "stages": [stage] if stage else [],
                      "skill_ids": list(last.get("skill_ids", [])), "objective_ids": list(last.get("objective_ids", [])),
                      "observed_at": last.get("answered_at") or record.get("created_at"), "observed_status": last.get("status"),
                      "source_kind": "support", "source_completed": bool(record.get("completed_at")),
                      "evidence_basis": "supported_activity", "resolution": resolution,
                      "notice": "本次支持活动还有待继续的确认；保留一个入口，指导中途的错误仍留在原记录中。"})
    for work in works:
        outcomes = work.get("criterion_outcomes", [])
        if (work.get("resource_id") in process_work_ids
                or not (outcomes or work.get("revision_needed"))):
            continue
        resource_id = work.get("resource_id")
        if not resource_id:
            continue
        source = {"work_resource_id": resource_id}
        resolution = ({"kind": "learner_reported_revision", "work_resource_id": resource_id,
                       "revision": work.get("revision", work.get("_revision")),
                       "observed_at": work.get("updated_at"), "independent_evidence": False,
                       "notice": "逐项自查已保存为达到；关闭作品待办，不改变任务评分或证明掌握。"}
                      if work_is_reviewed(work) else None)
        needs.append({"need_id": need_identity("work_revision", source, work.get("resource_version")),
                      "kind": "work_revision", "status": "open", "source_ref": source, "source_refs": [source],
                      "resource_version": work.get("resource_version"), "work_resource_id": resource_id,
                      "title": work.get("title", "已保存作品"), "stages": [work["stage"]] if work.get("stage") else [],
                      "skill_ids": [], "objective_ids": [], "targets": [], "source_kind": work.get("kind"),
                      "source_completed": True, "observed_at": work.get("updated_at"),
                      "criterion_outcomes": work.get("criterion_outcomes", []),
                      "unresolved_criterion_indices": work.get("unresolved_criterion_indices", []),
                      "revision_needed": bool(work.get("revision_needed")), "resolution": resolution,
                      "evidence_basis": "learner_reported_revision",
                      "notice": "学习者明确记录了部分达到或需要重做，提交状态不会清除修订事项。"})
    for need in needs:
        if need["resolution"]:
            need["status"] = "resolved"
        state = state_index.get(need["need_id"])
        if state:
            need["explicit_state"] = state
            if not need["resolution"]:
                need["status"] = state["status"]
    return sorted(needs, key=lambda item: (item.get("observed_at") or "", item["need_id"]))


def skill_evidence(catalog, records, needs):
    """Target-level observations and limitations; no synthetic proficiency score."""
    skills = _section(catalog, "skills")
    result = {}
    for record, answer in _observations(records):
        for sid in answer.get("skill_ids", []):
            row = result.setdefault(sid, {"skill_id": sid, "statement": skills.get(sid, {}).get("statement"),
                "observations": [], "status_counts": {}, "family_ids": [], "representations": [],
                "open_need_ids": [], "deferred_need_ids": [], "independent_mastery_evidence": False})
            status = answer.get("status", "unknown")
            row["status_counts"][status] = row["status_counts"].get(status, 0) + 1
            row["observations"].append({"source_ref": _source(record, answer), "status": status,
                "resource_version": _version(record), "family_id": answer.get("family_id"),
                "family_status": answer.get("family_status", "unclassified"), "representation": answer.get("representation"),
                "help_used": answer.get("help_used"), "phase": record.get("mode"),
                "review_interval_qualified": record.get("context", {}).get("review_interval_qualified", False),
                "actual_quiet_seconds": record.get("context", {}).get("actual_quiet_seconds")})
            if answer.get("family_status") == "declared" and answer.get("family_id"):
                row["family_ids"].append(answer["family_id"])
            if answer.get("representation"):
                value = answer["representation"]
                row["representations"].extend(value if isinstance(value, list) else [value])
    for need in needs:
        if need["status"] not in {"open", "deferred"}:
            continue
        for sid in need.get("skill_ids", []):
            if sid in result:
                result[sid]["open_need_ids" if need["status"] == "open" else "deferred_need_ids"].append(need["need_id"])
    for row in result.values():
        row["family_ids"] = sorted(set(row["family_ids"]))
        row["representations"] = sorted(set(row["representations"]))
        row["next_action"] = "targeted_support" if row["open_need_ids"] else "further_observation"
        row["notice"] = "同题、同族、已看参考及帮助记录需要共同解释；单次结果与学习者自评不证明稳定迁移。"
    return [result[sid] for sid in sorted(result)]


def support_routes(catalog, skill_ids, *, preferred_unit=None):
    """Consume explicit skill prerequisites, including ones within the same unit."""
    skills, units = _section(catalog, "skills"), _section(catalog, "units")
    prerequisites = sorted({pid for sid in skill_ids for pid in skills.get(sid, {}).get("prerequisite_skill_ids", [])})
    routes = []
    for sid in list(dict.fromkeys([*prerequisites, *skill_ids])):
        matches = []
        for uid, unit in units.items():
            objective_ids = [row["id"] for row in unit.get("objectives", []) if isinstance(row, dict)
                             and (row.get("skill_id") == sid or row.get("id") == sid)]
            if objective_ids or sid in unit.get("skill_ids", []):
                matches.append({"skill_id": sid, "unit_id": uid, "objective_ids": objective_ids,
                                "resource_version": _version(unit), "title": unit.get("title", uid),
                                "role": "prerequisite" if sid in prerequisites else "target",
                                "within_current_unit": uid == preferred_unit})
        matches.sort(key=lambda item: (item["unit_id"] != preferred_unit, item["unit_id"]))
        routes.extend(matches[:1])
    return prerequisites, routes


def task_options(catalog, skill_ids, records, exposures=None):
    """Rank concrete next observations with family, representation and help limits."""
    if not skill_ids:
        return []
    observations = list(_observations(records))
    seen_questions = {answer["question_id"] for _, answer in observations}
    seen_families = {answer["family_id"] for _, answer in observations
                     if answer.get("family_status") == "declared" and answer.get("family_id")}
    seen_representations = set()
    for _, answer in observations:
        if set(answer.get("skill_ids", [])) & set(skill_ids):
            value = answer.get("representation")
            seen_representations.update(value if isinstance(value, list) else [value] if value else [])
    exposures = exposures or {}
    question_exposures, family_exposures = exposures.get("questions", {}), exposures.get("families", {})
    options = []
    for tid, task in _section(catalog, "tasks").items():
        if task.get("availability", "active") != "active" or not set(task.get("skill_ids", [])) & set(skill_ids):
            continue
        family = task.get("family_id")
        declared = task.get("family_status") == "declared"
        qex = question_exposures.get(tid, {})
        fex = family_exposures.get(family, {}) if declared else {}
        exposure_rows = [qex, fex]
        helped = any(row.get("material_count", 0) or row.get("reference_count", 0) or row.get("feedback_count", 0)
                     for row in exposure_rows)
        repeat = tid in seen_questions or any(qex.get(field, 0) for field in
                                             ("presentation_count", "answer_count", "material_count", "reference_count", "feedback_count"))
        family_repeat = declared and (family in seen_families or any(fex.get(field, 0) for field in
                                             ("presentation_count", "answer_count", "material_count", "reference_count", "feedback_count")))
        representations = task.get("representation")
        representations = representations if isinstance(representations, list) else [representations] if representations else []
        options.append({"task_id": tid, "resource_version": task.get("resource_version"),
                        "skill_ids": task.get("skill_ids", []), "family_id": family,
                        "family_status": task.get("family_status", "unclassified"), "representation": task.get("representation"),
                        "repeated_question": bool(repeat), "repeated_family": bool(family_repeat),
                        "help_exposed": bool(helped), "new_representation": bool(set(representations) - seen_representations),
                        "independent_evidence": False,
                        "selection_reason": "换已声明题族与表征继续观察；仍需记录帮助和实际间隔。"
                            if declared and not repeat and not family_repeat and not helped
                            else "可用于有帮助的复习；重复、同族或已看参考不能冒充新的独立检查。"})
    options.sort(key=lambda row: (row["repeated_question"], row["repeated_family"], row["help_exposed"],
                                 row["family_status"] != "declared", not row["new_representation"], row["task_id"]))
    return options[:5]
