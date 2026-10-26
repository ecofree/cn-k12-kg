"""Explainable next steps from recorded activity; never a mastery classifier.

The 1/3/7-day intervals are a transparent product default, not an empirically
validated adaptive model. Drafts and self-assessments stay learner reports.
Callers must pass owner-filtered scope summaries, not a truncated history page.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .review_timing import qualified_review
from .learning_needs import derive_needs, skill_evidence, support_routes, task_options

RULE_VERSION = "activity-plan-v4"
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


def _work_continuation(work, need=None):
    """Rank saved activity separately from reported difficulty and missing checks."""
    need = need or {}
    has_content = work.get("has_draft") or work.get("has_reflection") or work.get("self_assessment_reported")
    if work.get("status") != "submitted" and has_content:
        return (80, "unfinished_draft",
                "这份作品已有未完成的保存内容，可以继续补充、对照量规自评或修改反思；历次修订都会保留。")
    if (need.get("revision_basis") == "learner_reported_difficulty" or work.get("revision_needed")
            or any(row.get("status") in {"partial", "revisit"}
                   for row in need.get("criterion_outcomes", work.get("criterion_outcomes", [])))):
        return (76, "learner_reported_difficulty",
                "这份作品已自报部分达到或需要重做，可以按具体量规项目继续修订；这些自评不等于独立掌握的证据。")
    return (35, "unassessed_criteria",
            "这份作品仍有尚未核对的量规项目，可以逐项自查；未核对不表示答错或已发现学习困难。")


def plan(catalog, records, works=(), *, now=None, limit=12, stage=None, need_states=(), exposures=None):
    """Return ranked, version-aware actions from observable activity.

    Pass full owner-filtered records or LearningStore.planning_records(), never
    a latest-phase summary or recent-history page. Works never complete formal
    phases or establish mastery. Need states are explicit deferrals, not grades.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit 必须为 1—100 的整数")
    current = datetime.now(timezone.utc) if now is None else _time(now)
    if current is None:
        raise ValueError("now 必须包含明确时区")
    records = list(records)
    works = list(works)
    work_index = {work["resource_id"]: work for work in works if work.get("resource_id")}
    units = {unit["id"]: unit for unit in _units(catalog)}
    needs = derive_needs(catalog, records, works, need_states)
    evidence = skill_evidence(catalog, records, needs)
    visible_needs = [need for need in needs if stage is None or stage in need.get("stages", [])]
    open_needs = [need for need in visible_needs if need["status"] == "open"]
    unit_support = {}
    for need in open_needs:
        if need["kind"] == "answer_support" and need.get("source_completed") and need.get("unit_id") in units:
            unit_support.setdefault(need["unit_id"], []).append(need)
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

    # Unfinished process work belongs to the source observation, not whichever
    # phase happened to be most recent. A content update also keeps this route.
    revisions = {}
    for need in open_needs:
        if need["kind"] == "process_revision":
            revisions.setdefault(need["source_ref"]["session_id"], []).append(need)
        elif need["kind"] == "support_revision":
            candidates.append((80, {"action": "continue", "kind": "support", "continuation": "resume_support",
                "session_id": need["source_ref"]["session_id"], "unit_id": need.get("unit_id"),
                "title": need["title"], "resource_version": need.get("resource_version"),
                "need_ids": [need["need_id"]], "skill_ids": need["skill_ids"], "phase": "support", "ready": True,
                "reason": "这次指导练习仍有待确认的项目，可以继续原活动并保留帮助与修订记录。",
                "evidence_basis": "supported_activity", "rule_version": RULE_VERSION}))
        elif need["kind"] == "work_revision":
            priority, basis, reason = _work_continuation(work_index.get(need["work_resource_id"], {}), need)
            candidates.append((priority, {"action": "continue", "kind": need.get("source_kind"),
                "work_resource_id": need["work_resource_id"], "title": need["title"],
                "resource_version": need.get("resource_version"), "need_ids": [need["need_id"]],
                "criterion_outcomes": need.get("criterion_outcomes", []),
                "unresolved_criterion_indices": need.get("unresolved_criterion_indices", []),
                "reason": reason, "continuation_basis": basis, "ready": True,
                "continuation": "edit_work", "evidence_basis": "learner_reported_revision",
                "rule_version": RULE_VERSION}))
    for session_id, pending in revisions.items():
        first = pending[0]
        questions = sorted({need["source_ref"]["question_id"] for need in pending})
        single = {"question_id": questions[0], "work_resource_id": first["work_resource_id"]} if len(questions) == 1 else {}
        candidates.append((75, {"action": "continue", "unit_id": first.get("unit_id"), "title": first["title"],
            "resource_version": first.get("resource_version"), "session_id": session_id, "question_ids": questions,
            "phase": first.get("phase"), "continuation": "reflect_and_revise", "ready": True,
            "need_ids": [need["need_id"] for need in pending],
            "criterion_outcomes": [{"question_id": need["source_ref"]["question_id"],
                                    "outcomes": need.get("criterion_outcomes", [])} for need in pending],
            "reason": f"本组还有 {len(questions)} 道过程题待自查或修订。部分达到、需要重做和未核对的项目会继续保留；新阶段不会清除它们。",
            "evidence_basis": "learner_reported_revision", "rule_version": RULE_VERSION, **single}))

    # Finished topic sessions need a durable path too. Group unresolved answers
    # from every session, retaining the precise source IDs in the action.
    topic_support = {}
    for need in open_needs:
        if need["kind"] == "answer_support" and need.get("source_kind") == "topic" and need.get("source_completed"):
            topic_support.setdefault(tuple(sorted(need.get("targets", []))), []).append(need)
    for targets, pending in topic_support.items():
        recent = pending[-1]
        target_skills = sorted({sid for need in pending for sid in need["skill_ids"]})
        prereq, routes = support_routes(catalog, target_skills)
        candidates.append((80, {"action": "practice", "kind": "topic", "targets": list(targets),
            "title": recent["title"], "resource_version": recent.get("resource_version"),
            "previous_session_id": recent["source_ref"]["session_id"], "phase": "practice", "ready": True,
            "review_material": True, "need_ids": [need["need_id"] for need in pending],
            "source_refs": [need["source_ref"] for need in pending], "skill_ids": target_skills,
            "prerequisite_skill_ids": prereq, "support_routes": routes,
            "task_options": task_options(catalog, target_skills, records, exposures),
            "reason": "已完成的知识点学习仍有答错或跳过的项目。先回看相应讲解并练习，再保存后续确认；开始新会话不会清除原事项。",
            "evidence_basis": "specific_unresolved_observations", "rule_version": RULE_VERSION}))

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
        if unit_support.get(identity) and "practice" in phases:
            pending = unit_support[identity]
            target_skills = sorted({sid for need in pending for sid in need["skill_ids"]})
            prereq, routes = support_routes(catalog, target_skills, preferred_unit=identity)
            add("practice", unit, "保存的任务中仍有具体答错或跳过的项目。先按对应目标和前置技能学习，再完成后续确认。", 80,
                phase="practice", previous_session_id=pending[-1]["source_ref"]["session_id"], review_material=True,
                need_ids=[need["need_id"] for need in pending], source_refs=[need["source_ref"] for need in pending],
                skill_ids=target_skills, objective_ids=sorted({oid for need in pending for oid in need["objective_ids"]}),
                prerequisite_skill_ids=prereq, support_routes=routes,
                task_options=task_options(catalog, target_skills, records, exposures),
                evidence_basis="specific_unresolved_observations")
            continue
        if latest:
            latest_phase = latest.get("mode")
            if latest_phase == "practice" and "transfer" in phases:
                add("transfer", unit, "练习阶段已有完成记录，可以换一个情境检验方法是否仍适用。", 60,
                    phase="transfer", previous_session_id=latest["id"])
                continue
            if "delayed" in phases:
                delayed = [record for record in completed if record.get("mode") == "delayed"]
                # Immediate repeats and legacy sessions with unknown timing
                # cannot accelerate the interval ladder. This counts interval
                # observations, never correct answers or established mastery.
                review_count = max([sum(qualified_review(record) for record in delayed)]
                                   + [int(record.get("qualified_review_count", 0)) for record in delayed])
                interval = REVIEW_INTERVAL_DAYS[min(review_count, len(REVIEW_INTERVAL_DAYS) - 1)]
                due = _time(latest["completed_at"]) + timedelta(days=interval)
                ready = current >= due
                add("due_review", unit,
                    ("按产品默认间隔，已到再次观察这个单元表现的时间。" if ready
                     else "已为这个单元保留下一次间隔观察时间；可先继续其他学习。"),
                    100 if ready else 10, phase="delayed", ready=ready, due_at=_iso(due),
                    interval_days=interval, previous_session_id=latest["id"],
                    qualified_review_count=review_count,
                    timing_basis="last_completed_same_scope", review_rule="product_default_1_3_7_days",
                    progression_basis="completed_same_version_reviews_with_recorded_24h_quiet_interval")
                continue
        if "practice" not in phases:
            continue
        prerequisites = list(unit.get("prerequisite_unit_ids", []))
        declared_skills = sorted({sid for sid in unit.get("skill_ids", [])} |
                                 {objective.get("skill_id") for objective in unit.get("objectives", [])
                                  if isinstance(objective, dict) and objective.get("skill_id")})
        skill_prerequisites, routes = support_routes(catalog, declared_skills, preferred_unit=identity)
        observed_skills = {answer_sid for item in evidence for answer_sid in [item["skill_id"]]
                           if item["observations"]}
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
        # Explicit skill relations remain visible even when their support route
        # is inside this very unit. They do not manufacture unit-level cycles.
        for skill in skill_prerequisites:
            if skill not in observed_skills and skill not in unobserved_skills:
                unobserved_skills.append(skill)
                sources = skills_to_units.get(skill, set()) - {identity}
                if sources and not sources & completed_units:
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
            support_routes=routes,
            prerequisite_evidence="completed_activity_only_not_mastery")

    recommended_work_keys = {item.get("work_resource_id") for _, item in candidates}
    deferred_work_keys = {need["work_resource_id"] for need in needs
                          if need["kind"] == "work_revision" and need["status"] == "deferred"}
    for work in works:
        resource_id = work.get("resource_id")
        if not resource_id or resource_id in recommended_work_keys or resource_id in deferred_work_keys:
            continue
        if work.get("kind") == "session_task" or work.get("status") == "submitted":
            continue
        if stage is not None and work.get("stage") != stage:
            continue
        if not (work.get("has_draft") or work.get("has_reflection") or work.get("self_assessment_reported")):
            continue
        priority, basis, reason = _work_continuation(work)
        candidates.append((priority, {"action": "continue", "kind": work.get("kind"), "work_resource_id": resource_id,
            "source_ref": work.get("source_ref"), "title": work.get("title", work.get("source_ref", "已保存作品")),
            "resource_version": work.get("resource_version"), "revision": work.get("revision", work.get("_revision")),
            "reason": reason, "continuation_basis": basis,
            "ready": True, "continuation": "edit_work", "evidence_basis": "learner_reported_revision",
            "rule_version": RULE_VERSION}))

    def candidate_order(pair):
        item = pair[1]
        resource_id = item.get("work_resource_id")
        work = work_index.get(resource_id, {})
        # Within one work priority, resume the oldest known saved activity.
        # Unzoned/unknown times cannot establish age and sort after known ones;
        # IDs only make otherwise equal ordering deterministic.
        saved_at = (_time(work.get("updated_at")) or _time(work.get("created_at"))
                    or datetime.max.replace(tzinfo=timezone.utc))
        return (-pair[0], item.get("due_at") or "", item.get("unit_id") or "",
                bool(resource_id), saved_at, resource_id or "", item.get("question_id") or "")

    candidates.sort(key=candidate_order)
    return {"schema": 1, "generated_at": _iso(current), "rule_version": RULE_VERSION,
            "learning_needs": needs, "skill_evidence": evidence,
            "need_counts": {status: sum(need["status"] == status for need in needs)
                            for status in ("open", "deferred", "resolved")},
            "policy": {"review_intervals_days": list(REVIEW_INTERVAL_DAYS),
                "basis": "product_default_not_validated_adaptive_model",
                "notice": "建议依据已保存的学习活动生成。1、3、7 天是产品默认间隔；只有内容版本相同且记录间隔达到24小时的复测才推进间隔，立即重做不推进。完成、自评与草稿不等于掌握，也不证明独立作答。"},
            "next_steps": [item for _, item in candidates[:limit]], "total_candidates": len(candidates)}
