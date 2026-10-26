"""Explainable next steps from recorded activity; never a mastery classifier.

The 1/3/7-day intervals are a transparent product default, not an empirically
validated adaptive model. Drafts and self-assessments stay learner reports.
Callers must pass owner-filtered scope summaries, not a truncated history page.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RULE_VERSION = "activity-plan-v1"
REVIEW_INTERVAL_DAYS = (1, 3, 7)
_PHASES = {"practice", "transfer", "delayed"}


def _time(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        # Legacy timestamps without a timezone cannot establish a review gap.
        return None
    return result.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat(timespec="milliseconds")


def _units(catalog):
    if catalog is None:
        return []
    if isinstance(catalog, dict):
        values = catalog.get("units", catalog)
    else:
        values = catalog.units() if callable(catalog.units) else catalog.units
    return list(values.values()) if isinstance(values, dict) else list(values)


def _tasks(unit, catalog):
    result = []
    for task in unit.get("tasks", unit.get("task_ids", [])):
        if isinstance(task, dict):
            result.append(task)
        elif isinstance(catalog, dict):
            found = catalog.get("tasks", {}).get(task)
            if found:
                result.append(found)
        elif catalog is not None:
            found = catalog.task(task)
            if found:
                result.append(found)
    return result


def _version(unit):
    return unit.get("resource_version") or unit.get("content_sha256")


def _record_version(record):
    return record.get("resource_version") or record.get("content_sha256")


def _stamp(record):
    return (_time(record.get("completed_at")) or _time(record.get("updated_at"))
            or _time(record.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))


def _needs_practice(record):
    return any(answer.get("skipped") or answer.get("status") in {"wrong", "not_attempted"}
               for answer in record.get("answers", []))


def _observed_completion(record):
    return (bool(record.get("completed_at")) and _time(record.get("completed_at")) is not None
            and (bool(record.get("answers")) or record.get("answered_count", 0) > 0)
            and record.get("mode") in _PHASES)


def plan(catalog, records, works=(), *, now=None, limit=12, stage=None):
    """Return ranked, version-aware actions from observable activity.

    records can be full saved sessions or LearningStore.latest_scope_records()
    summaries. Work summaries only add editing suggestions; they never complete
    phases or satisfy prerequisites. Missing evidence is stated as missing.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit 必须为 1—100 的整数")
    current = datetime.now(timezone.utc) if now is None else _time(now)
    if current is None:
        raise ValueError("now 必须包含明确时区")
    records = list(records)
    works = list(works)
    units = {unit["id"]: unit for unit in _units(catalog)}
    skills_to_units = {}
    for unit in units.values():
        for skill in unit.get("objectives", []):
            identities = (skill.get("id"), skill.get("skill_id")) if isinstance(skill, dict) else (skill,)
            for identity in identities:
                if identity:
                    skills_to_units.setdefault(identity, set()).add(unit["id"])
        for identity in unit.get("skill_ids", []):
            skills_to_units.setdefault(identity, set()).add(unit["id"])
    by_unit = {}
    for record in records:
        identity = record.get("unit_id")
        if identity in units:
            by_unit.setdefault(identity, []).append(record)
    for unit_records in by_unit.values():
        unit_records.sort(key=_stamp, reverse=True)
    completed_units = {identity for identity, history in by_unit.items()
                       if any(_observed_completion(record)
                              and (not _version(units[identity]) or _record_version(record) == _version(units[identity]))
                              for record in history)}
    candidates = []

    def add(action, unit, reason, priority, **extra):
        item = {"action": action, "unit_id": unit["id"], "title": unit.get("title", unit["id"]),
                "resource_version": _version(unit), "reason": reason, "ready": True,
                "evidence_basis": "recorded_activity", "rule_version": RULE_VERSION, **extra}
        candidates.append((priority, item))

    # Topic sessions also live in SQLite. Restoring a page must not depend on a
    # session ID surviving in sessionStorage. Their frozen graph, rather than
    # the current catalog's version, determines whether they may resume.
    placements = catalog.get("placements", {}) if isinstance(catalog, dict) else getattr(catalog, "placements", {})
    topic_scopes = {}
    for record in records:
        if record.get("kind") == "topic":
            targets = record.get("targets", record.get("engine", {}).get("targets", []))
            scope = record.get("scope_id") or tuple(sorted(targets))
            topic_scopes.setdefault(scope, []).append(record)
    for history in topic_scopes.values():
        finished = [record for record in history if record.get("completed_at") and _time(record["completed_at"])]
        last_finished = max(finished, key=_stamp) if finished else None
        active = [record for record in history if not record.get("completed_at")
                  and not record.get("resume_blocked") and (last_finished is None or _stamp(record) > _stamp(last_finished))]
        if not active:
            continue
        record = max(active, key=_stamp)
        targets = record.get("targets", record.get("engine", {}).get("targets", []))
        stages = {placements[target].get("stage") for target in targets if target in placements}
        if stage is not None and stage not in stages:
            continue
        candidates.append((90, {"action": "continue", "kind": "topic", "session_id": record["id"],
            "targets": targets, "title": record.get("title", "知识点学习记录"),
            "resource_version": _record_version(record), "phase": record.get("mode"), "ready": True,
            "reason": "这次知识点学习还有未完成的任务，可以按已保存的内容继续。",
            "evidence_basis": "recorded_activity", "rule_version": RULE_VERSION}))

    for identity, unit in units.items():
        if stage is not None and unit.get("stage") != stage:
            continue
        history = by_unit.get(identity, [])
        version = _version(unit)
        matching = [record for record in history if not version or _record_version(record) == version]
        tasks = _tasks(unit, catalog)
        phases = {task.get("phase") for task in tasks}
        completed = [record for record in matching if _observed_completion(record)]
        latest = max(completed, key=_stamp) if completed else None
        all_completed = [record for record in history if _observed_completion(record)]
        latest_in_scope = max(all_completed, key=_stamp) if all_completed else None
        open_records = [record for record in history if not record.get("completed_at")
                        and not record.get("resume_blocked") and record.get("mode") in _PHASES
                        and (not version or _record_version(record) == version or record.get("resume_blocked") is False)
                        and (latest_in_scope is None or _stamp(record) > _stamp(latest_in_scope))]
        if open_records:
            record = max(open_records, key=_stamp)
            reason = ("这个单元保存的是较早内容版本，可以按原内容从已保存位置继续。"
                      if version and _record_version(record) != version
                      else "这个内容版本还有未完成的任务，可从已保存位置继续。")
            add("continue", unit, reason, 90, session_id=record["id"], phase=record.get("mode"),
                resource_version=_record_version(record))
            continue
        if latest:
            unresolved = [answer for answer in latest.get("answers", []) if answer.get("status") == "needs_review"]
            pending_questions = []
            for answer in unresolved:
                work_key = f"session_task:{latest['id']}:{answer['question_id']}"
                saved = next((work for work in works if work.get("resource_id") == work_key), None)
                if not saved or not saved.get("self_assessment_reported"):
                    pending_questions.append(answer["question_id"])
            if pending_questions:
                single = ({"question_id": pending_questions[0],
                           "work_resource_id": f"session_task:{latest['id']}:{pending_questions[0]}"}
                          if len(pending_questions) == 1 else {})
                add("continue", unit,
                    f"本组还有 {len(pending_questions)} 道过程题待自查。可一起对照量规、补充理由并保存修订；自评作为学习记录保留。", 75,
                    session_id=latest["id"], question_ids=pending_questions,
                    phase=latest.get("mode"), continuation="reflect_and_revise",
                    evidence_basis="learner_reported_revision", **single)
            if _needs_practice(latest) and "practice" in phases:
                add("practice", unit, "最近一次任务仍有答错或跳过的项目，建议回看相关讲解后再练习。", 80,
                    phase="practice", previous_session_id=latest["id"], review_material=True)
                continue
            latest_phase = latest.get("mode")
            if latest_phase == "practice" and "transfer" in phases:
                add("transfer", unit, "练习阶段已有完成记录，可以换一个情境检验方法是否仍适用。", 60,
                    phase="transfer", previous_session_id=latest["id"])
                continue
            if "delayed" in phases:
                delayed = [record for record in completed if record.get("mode") == "delayed"]
                # Aggregated summaries retain the total so an old review is not
                # forgotten by pagination. A review count is not a success count.
                review_count = max([len(delayed)] + [int(record.get("phase_completion_count", 0)) for record in delayed])
                interval = REVIEW_INTERVAL_DAYS[min(review_count, len(REVIEW_INTERVAL_DAYS) - 1)]
                due = _time(latest["completed_at"]) + timedelta(days=interval)
                ready = current >= due
                add("due_review", unit,
                    ("按产品默认间隔，已到再次观察这个单元表现的时间。" if ready
                     else "已为这个单元保留下一次间隔观察时间；可先继续其他学习。"),
                    100 if ready else 10, phase="delayed", ready=ready, due_at=_iso(due),
                    interval_days=interval, previous_session_id=latest["id"],
                    timing_basis="last_completed_same_scope", review_rule="product_default_1_3_7_days")
                continue
        if "practice" not in phases:
            continue
        prerequisites = list(unit.get("prerequisite_unit_ids", []))
        unobserved_skills = []
        for skill in unit.get("prerequisite_skill_ids", []):
            sources = skills_to_units.get(skill, set()) - {identity}
            if sources & completed_units:
                continue
            unobserved_skills.append(skill)
            if sources:
                # Any containing unit is a route to inspect this prerequisite;
                # the UI need not suggest completing every alternative unit.
                prerequisites.append(sorted(sources)[0])
        unobserved = sorted(set(prerequisites) - completed_units)
        reason = "还没有这个内容版本的练习完成记录，可先阅读讲解并开始练习。"
        if history and version and not matching:
            reason = "这个单元的内容已更新。原版本记录仍保留，可从新版本讲解与练习开始。"
        if unobserved:
            reason += " 尚未看到部分前置单元的完成记录，可先回看这些基础。"
        add("practice", unit, reason, 15 if unobserved else 20, phase="practice",
            review_material=True, prerequisite_unit_ids=unobserved,
            prerequisite_skill_ids=unobserved_skills,
            prerequisite_evidence="completed_activity_only_not_mastery")

    recommended_work_keys = {item.get("work_resource_id") for _, item in candidates}
    for work in works:
        resource_id = work.get("resource_id")
        if not resource_id or resource_id in recommended_work_keys:
            continue
        if work.get("kind") == "session_task" or work.get("status") == "submitted":
            continue
        if stage is not None and work.get("stage") != stage:
            continue
        if not (work.get("has_draft") or work.get("has_reflection") or work.get("self_assessment_reported")):
            continue
        candidates.append((40, {"action": "continue", "kind": work.get("kind"), "work_resource_id": resource_id,
            "source_ref": work.get("source_ref"), "title": work.get("title", work.get("source_ref", "已保存作品")),
            "resource_version": work.get("resource_version"), "revision": work.get("revision", work.get("_revision")),
            "reason": "这份作品已保存，可以继续补充、对照量规自评或修改反思；历次修订都会保留。",
            "ready": True, "continuation": "edit_work", "evidence_basis": "learner_reported_revision",
            "rule_version": RULE_VERSION}))
    candidates.sort(key=lambda pair: (-pair[0], pair[1].get("due_at", ""), pair[1].get("unit_id", ""),
                                      pair[1].get("work_resource_id", ""), pair[1].get("question_id", "")))
    return {"schema": 1, "generated_at": _iso(current), "rule_version": RULE_VERSION,
            "policy": {"review_intervals_days": list(REVIEW_INTERVAL_DAYS),
                "basis": "product_default_not_validated_adaptive_model",
                "notice": "建议依据已保存的学习活动生成。1、3、7 天是产品默认间隔；完成记录、自评与草稿不等于掌握，也不证明独立作答。"},
            "next_steps": [item for _, item in candidates[:limit]], "total_candidates": len(candidates)}
