"""Rebuildable learning needs from immutable observations, not mastery scores.

Callers supply an owner's complete compact history. A newer phase, content
version or browser cannot erase an earlier need. Resolution always names the
later observation (or learner-reported revision) that closes that bounded task.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .review_timing import qualified_review, timestamp
from .task_families import canonical_family, exposure_family, family_exposure

_UNKNOWN_REPRESENTATIONS = {"", "unknown", "unclassified", "unspecified", "未知", "未分类"}


def known_representations(value):
    """Unknown metadata is absence of evidence, never a new representation."""
    values = value if isinstance(value, list) else [value]
    return {item.strip() for item in values if isinstance(item, str)
            and item.strip().casefold() not in _UNKNOWN_REPRESENTATIONS}


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


def feedback_is_public(record, question_id):
    """Use actual release markers, not a compact summary's private grade."""
    return bool(record and (record.get("completed_at")
                            or question_id in record.get("feedback_exposed", [])))


def public_learning_records(records, feedback_sources):
    """Project compact records without changing stored grades or losing exposure.

    Pending attempts retain their task/family/representation metadata for
    novelty decisions. Their result cannot create, resolve or count a public
    learning need until the source session has released that feedback.
    """
    pending_fields = ("question_id", "objective_ids", "skill_ids", "answered_at",
                      "family_id", "family_status", "representation", "purpose", "alignment",
                      "content_sha256", "resource_version", "topic_ids", "help_used")
    output = []
    for record in records:
        shown = copy.deepcopy(record)
        source = feedback_sources.get(record["id"], {})
        shown["answers"] = []
        for answer in record.get("answers", []):
            if feedback_is_public(source, answer.get("question_id")):
                shown["answers"].append(copy.deepcopy(answer))
            else:
                shown["answers"].append({**{key: copy.deepcopy(answer[key]) for key in pending_fields if key in answer},
                                          "_feedback_pending": True})
        output.append(shown)
    return output


def public_learning_events(events, feedback_sources, visible_need_ids):
    """Readable exports respect the same boundary; encrypted backups stay raw."""
    submission_fields = ("raw_answer", "normalized_answer", "skipped", "duration_ms", "client_elapsed_ms",
                         "duration_evidence", "skill_ids", "family_id", "family_status", "help_used",
                         "unit_id", "task_ids", "family_ids", "independent_evidence")
    output = []
    for event in events:
        payload = event.get("payload", {})
        if event.get("event_type") in {"learning_need_deferred", "learning_need_reopened"}:
            if payload.get("need_id") not in visible_need_ids:
                continue
        shown = copy.deepcopy(event)
        source = feedback_sources.get(event.get("session_id"), {})
        if event.get("question_id") and not feedback_is_public(source, event["question_id"]):
            if event.get("event_type") == "feedback_released":
                continue
            if event.get("event_type") == "answer_submitted":
                shown["payload"] = {key: copy.deepcopy(payload[key]) for key in submission_fields if key in payload}
        output.append(shown)
    return output


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


def _observations(records, *, include_pending=False):
    for record in records:
        for answer in record.get("answers", []):
            if not answer.get("question_id"):
                continue
            metadata = observation_metadata(record, answer)
            if metadata.get("_feedback_pending") and not include_pending:
                continue
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
    correct_by_task, support_by_need, followups, linked_observations = {}, {}, {}, {}
    primary_ids = {need_identity("process_revision" if answer.get("status") == "needs_review" else "answer_support",
                                _source(record, answer), _version(record))
                   for record, answer in observations if record.get("kind") != "support"
                   and (answer.get("status") in {"wrong", "not_attempted", "needs_review"} or answer.get("skipped"))}
    for record, answer in observations:
        for need_id in record.get("source_origin", {}).get("need_ids", []):
            linked_observations.setdefault(need_id, []).append((record, answer))
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
                                      "source_ref": _source(other, later), "observed_at": later.get("answered_at") or other.get("completed_at")
                                      or other.get("updated_at") or other.get("created_at"),
                                      "help_used": later.get("help_used"), "independent_evidence": False,
                                      "notice": "保留后来作答的具体结果与帮助记录；重复题或帮助后的正确不证明独立掌握。"}
        needs.append(need)
    support_need_ids = {need_identity("support_revision", {"session_id": record["id"]}, _version(record))
                        for record in records if record.get("kind") == "support"}
    for record in records:
        if (record.get("kind") != "support"
                or set(record.get("source_origin", {}).get("need_ids", [])) & (primary_ids | support_need_ids)):
            continue
        answers = [metadata for answer in record.get("answers", [])
                   for metadata in [observation_metadata(record, answer)] if not metadata.get("_feedback_pending")]
        failures = [answer for answer in answers if answer.get("status") in {"wrong", "not_attempted", "needs_review"}
                    or answer.get("skipped")]
        if not failures:
            continue
        last = failures[-1]
        source, version = {"session_id": record["id"]}, _version(record)
        confirmations = [answer for answer in answers if answer.get("status") == "correct" and not answer.get("skipped")
                         and answer.get("confirmation_eligible", True)
                         and answer.get("support_kind") == "confirmation" and record.get("completed_at")
                         and _when(record, answer) and _when(record, last) and _when(record, answer) > _when(record, last)]
        resolution = ({"kind": "supported_confirmation", "source_ref": _source(record, confirmations[-1]),
                       "help_used": confirmations[-1].get("help_used"), "independent_evidence": False,
                       "observed_at": confirmations[-1].get("answered_at") or record.get("completed_at"),
                       "notice": "本次指导后的终结确认已记录；这不证明独立掌握。"} if confirmations else None)
        identity = need_identity("support_revision", source, version)
        if resolution is None:
            for later_time, other, later in sorted(support_by_need.get(identity, []), key=lambda row: (row[0], row[1]["id"])):
                origin = other.get("source_origin", {})
                if (other.get("completed_at") and _when(record, last) and later_time > _when(record, last)
                        and origin.get("resource_version") == version
                        and identity in origin.get("need_ids", [])
                        and set(last.get("skill_ids", [])).issubset(later.get("skill_ids", []))
                        and later.get("confirmation_eligible", True)):
                    resolution = {"kind": "supported_confirmation", "source_ref": _source(other, later),
                        "help_used": True, "independent_evidence": False, "observed_at": later.get("answered_at"),
                        "notice": "与原事项、原版本和具体目标明确关联的重新指导已有有限确认；原失败记录保留，后续独立观察仍单独安排。"}
                    break
        unit_id = record.get("source_origin", {}).get("unit_id") or record.get("unit_id")
        stage = record.get("stage") or units.get(unit_id, {}).get("stage")
        needs.append({"need_id": identity, "kind": "support_revision",
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
        reported_difficulty = bool(work.get("revision_needed") or any(
            row.get("status") in {"partial", "revisit"} for row in outcomes))
        revision_basis = ("learner_reported_difficulty" if reported_difficulty else
                          "learner_reported_met" if work_is_reviewed(work) else "unassessed_criteria")
        notice = {"learner_reported_difficulty": "学习者明确记录了部分达到或需要重做，提交状态不会清除修订事项。",
                  "unassessed_criteria": "还有量规项目尚未核对；这不表示已发现错误，草稿与待核对事项继续保留。",
                  "learner_reported_met": "量规逐项自报达到只关闭自查待办，不改变任务评分或证明掌握。"}[revision_basis]
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
                      "skill_ids": list(work.get("skill_ids", [])), "objective_ids": [], "targets": [], "source_kind": work.get("kind"),
                      "source_completed": work.get("status") == "submitted", "work_status": work.get("status", "draft"),
                      "revision_basis": revision_basis, "observed_at": work.get("updated_at"),
                      "criterion_outcomes": work.get("criterion_outcomes", []),
                      "unresolved_criterion_indices": work.get("unresolved_criterion_indices", []),
                      "revision_needed": bool(work.get("revision_needed")), "resolution": resolution,
                      "evidence_basis": "learner_reported_revision",
                      "notice": notice})
    # A bounded correction closes the support task, not the obligation to
    # observe this target again without the recorded help. This second stable
    # identity survives sessions, deferral, updates and re-derivation from a
    # backup; an unrelated correct answer cannot silently discharge it.
    for original in list(needs):
        resolution = original.get("resolution") or {}
        if resolution.get("kind") not in {"supported_confirmation", "later_same_task_correct"}:
            continue
        source = {"parent_need_id": original["need_id"]}
        identity = need_identity("independent_followup", source, original.get("resource_version"))
        observed_at = resolution.get("observed_at")
        required = set(original.get("skill_ids", []))
        covered, evidence, followup_rows = set(), [], []
        # A reviewed historical identity may rule out a supposedly different
        # family even when an older saved eligibility flag predates the review.
        # This is a negative comparison only: keep the original classification,
        # skill observations and submitted grades exactly as recorded.
        original_exposure_family = exposure_family(original.get("family_id"),
            question_id=original.get("source_ref", {}).get("question_id"))
        for record, answer in linked_observations.get(identity, []):
            origin, basis = record.get("source_origin", {}), answer.get("followup_basis", {})
            when = _when(record, answer)
            followup_rows.append({"source_ref": _source(record, answer), "status": answer.get("status"),
                                  "help_used": answer.get("help_used"), "observed_at": answer.get("answered_at"),
                                  "resource_version": _version(record)})
            if (not required or not when or not timestamp(observed_at) or when <= timestamp(observed_at)
                    or record.get("kind") == "support" or not qualified_review(record)
                    or origin.get("resource_version") != original.get("resource_version")
                    or answer.get("status") != "correct" or answer.get("skipped")
                    or answer.get("help_used") is not False or basis.get("eligible") is not True
                    or basis.get("family_status") != "declared" or not basis.get("family_id")
                    or canonical_family(basis.get("family_id")) == original_exposure_family
                    or canonical_family(basis.get("family_id")) != canonical_family(answer.get("family_id"))
                    or not known_representations(basis.get("representation"))):
                continue
            matched = required & set(answer.get("skill_ids", []))
            if matched:
                covered.update(matched)
                evidence.append({"source_ref": _source(record, answer), "skill_ids": sorted(matched),
                                 "observed_at": answer.get("answered_at") or record.get("completed_at"),
                                 "family_id": answer.get("family_id"), "help_used": False,
                                 "resource_version": _version(record),
                                 "actual_quiet_seconds": record.get("context", {}).get("actual_quiet_seconds")})
        followup_resolution = None
        if required and required <= covered:
            followup_resolution = {"kind": "qualifying_followup_observation", "observations": evidence,
                "observed_at": max((row["observed_at"] for row in evidence), key=timestamp),
                "independent_evidence": False,
                "notice": "对应目标已有明确关联的后续正确记录，使用已登记的不同题族、已知表征并达到真实间隔，且没有记录到帮助；这完成本次观察待办，不证明独立作答或稳定掌握。"}
        needs.append({"need_id": identity, "parent_need_id": original["need_id"], "kind": "independent_followup",
            "status": "open", "source_ref": copy.deepcopy(original["source_ref"]),
            "source_refs": copy.deepcopy(original.get("source_refs", [])),
            "resource_version": original.get("resource_version"), "unit_id": original.get("unit_id"),
            "targets": list(original.get("targets", [])), "stages": list(original.get("stages", [])),
            "title": original["title"], "skill_ids": sorted(required),
            "objective_ids": list(original.get("objective_ids", [])), "family_id": original.get("family_id"),
            "family_status": original.get("family_status", "unclassified"),
            "representation": original.get("representation"), "observed_at": observed_at,
            "source_completed": True, "source_kind": original.get("source_kind"),
            "prior_confirmation": copy.deepcopy(resolution), "resolution": followup_resolution,
            "followup_observations": followup_rows, "observed_skill_ids": sorted(covered),
            "remaining_skill_ids": sorted(required - covered), "evidence_basis": "followup_after_supported_or_repeated_success",
            "notice": "原支持或修订事项已有具体后续记录；这些目标仍需换已知题族，在真实间隔后继续观察。新阶段或内容更新不会清除这项待办。"})
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
                "open_need_ids": [], "deferred_need_ids": [], "independent_followup_need_ids": [],
                "independent_mastery_evidence": False})
            status = answer.get("status", "unknown")
            row["status_counts"][status] = row["status_counts"].get(status, 0) + 1
            row["observations"].append({"source_ref": _source(record, answer), "status": status,
                "resource_version": _version(record), "family_id": answer.get("family_id"),
                "family_status": answer.get("family_status", "unclassified"), "representation": answer.get("representation"),
                "help_used": answer.get("help_used"), "phase": record.get("mode"),
                "review_interval_qualified": record.get("context", {}).get("review_interval_qualified", False),
                "actual_quiet_seconds": record.get("context", {}).get("actual_quiet_seconds")})
            if answer.get("family_status") == "declared" and answer.get("family_id"):
                row["family_ids"].append(canonical_family(answer["family_id"]))
            row["representations"].extend(known_representations(answer.get("representation")))
    for need in needs:
        if need["status"] not in {"open", "deferred"}:
            continue
        for sid in need.get("skill_ids", []):
            if sid in result:
                result[sid]["open_need_ids" if need["status"] == "open" else "deferred_need_ids"].append(need["need_id"])
                if need["kind"] == "independent_followup":
                    result[sid]["independent_followup_need_ids"].append(need["need_id"])
    for row in result.values():
        row["family_ids"] = sorted(set(row["family_ids"]))
        row["representations"] = sorted(set(row["representations"]))
        support_ids = set(row["open_need_ids"]) - set(row["independent_followup_need_ids"])
        row["next_action"] = ("targeted_support" if support_ids else "independent_followup"
                              if set(row["open_need_ids"]) & set(row["independent_followup_need_ids"])
                              else "further_observation")
        row["notice"] = "同题、同族、已看参考及帮助记录需要共同解释；单次结果与学习者自评不证明稳定迁移。"
    return [result[sid] for sid in sorted(result)]


def support_routes(catalog, skill_ids, *, preferred_unit=None):
    """Resolve declared target identities to real material/support/task routes.

    Never infer a route from a similar title or an enclosing topic. A missing
    route is returned explicitly, and an observed upstream activity does not
    remove the prerequisite or declare it mastered.
    """
    skills, units = _section(catalog, "skills"), _section(catalog, "units")
    placements, tasks = _section(catalog, "placements"), _section(catalog, "tasks")
    prerequisites = sorted({pid for sid in skill_ids for pid in skills.get(sid, {}).get("prerequisite_skill_ids", [])})
    routes = []
    for sid in list(dict.fromkeys([*prerequisites, *skill_ids])):
        matches = []
        role = "prerequisite" if sid in prerequisites else "target"
        reason = ("作者为当前具体目标登记了这个前置目标，可检查所需基础；看过材料或完成活动不等于已掌握。"
                  if role == "prerequisite" else "这是当前明确选择或原事项绑定的目标，任务和帮助按稳定目标身份定位。")
        for uid, unit in units.items():
            objectives = [row for row in unit.get("objectives", []) if isinstance(row, dict)
                          and (row.get("skill_id") == sid or row.get("id") == sid)]
            objective_ids = [row["id"] for row in objectives]
            if objective_ids or sid in unit.get("skill_ids", []):
                support_ids = sorted({identity for row in objectives for identity in row.get("support_ids", [])})
                unit_tasks = unit.get("task_ids", [row.get("id") if isinstance(row, dict) else row
                                                  for row in unit.get("tasks", [])])
                task_ids = [tid for tid in unit_tasks if tid in tasks and sid in tasks[tid].get("skill_ids", [])
                            and tasks[tid].get("availability", "active") == "active"]
                by_phase = {phase: [tid for tid in task_ids if tasks[tid].get("phase") == phase]
                            for phase in ("practice", "transfer", "delayed")}
                actions = [{"action": "open_unit_goal", "unit_id": uid, "skill_id": sid,
                            "objective_ids": objective_ids}]
                actions += [{"action": "start_support", "unit_id": uid, "support_id": identity,
                             "skill_ids": [sid]} for identity in support_ids]
                if by_phase["practice"]:
                    actions.append({"action": "start_session", "unit_id": uid, "mode": "practice",
                                    "focus_skill_ids": [sid], "task_ids": by_phase["practice"]})
                matches.append({"skill_id": sid, "unit_id": uid, "objective_ids": objective_ids,
                                "resource_version": _version(unit), "title": unit.get("title", uid),
                                "statement": skills.get(sid, {}).get("statement"), "role": role,
                                "within_current_unit": uid == preferred_unit, "resource_kind": "unit",
                                "available": True, "support_ids": support_ids,
                                "support_id": support_ids[0] if support_ids else None,
                                "task_ids_by_phase": by_phase, "actions": actions, "selection_reason": reason})
        if matches:
            matches.sort(key=lambda item: (item["unit_id"] != preferred_unit, item["unit_id"]))
            routes.extend(matches[:1])
            continue
        for tid, placement in placements.items():
            ids = placement.get("criterion_skill_ids", placement.get("skill_ids", []))
            if sid not in ids:
                continue
            index = ids.index(sid)
            checks = [task["id"] for task in tasks.values() if task.get("source_kind") == "self_check"
                      and task.get("material_id") == "topic:" + tid and sid in task.get("skill_ids", [])
                      and task.get("availability", "active") == "active"]
            material = _section(catalog, "materials").get("topic:" + tid, {})
            matches.append({"skill_id": sid, "statement": skills.get(sid, {}).get("statement"),
                "role": role, "resource_kind": "topic", "topic_id": tid, "criterion_index": index,
                "title": placement.get("title", tid), "resource_version": material.get("resource_version"),
                "available": bool(checks), "self_check_task_ids": checks, "selection_reason": reason,
                "actions": [{"action": "open_topic_goal", "topic_id": tid, "criterion_index": index,
                             "skill_id": sid}] if checks else [],
                "availability_notice": "已明确绑定教材目标与逐目标自检。" if checks else
                                       "目标已有教材定位，但没有可用的逐目标自检；不按同主题题目假定覆盖。"})
        if not matches:
            for pid, project in _section(catalog, "projects").items():
                objectives = [row for row in project.get("objectives", []) if row.get("skill_id") == sid]
                if objectives:
                    matches.append({"skill_id": sid, "statement": skills.get(sid, {}).get("statement"),
                        "role": role, "resource_kind": "project", "project_id": pid,
                        "objective_ids": [row["id"] for row in objectives], "title": project.get("title", pid),
                        "resource_version": project.get("resource_version"), "available": True,
                        "selection_reason": reason,
                        "actions": [{"action": "open_project_goal", "project_id": pid, "skill_id": sid}]})
        routes.extend(matches[:1] or [{"skill_id": sid, "statement": skills.get(sid, {}).get("statement"),
            "role": role, "resource_kind": None, "available": False, "actions": [],
            "selection_reason": reason, "availability_notice": "尚无此稳定目标的明确材料或活动入口，保留缺口；没有按相似标题猜测路径。"}])
    return prerequisites, routes


def task_start_action(catalog, task, *, preferred_skill_id=None):
    """Only return exact, server-checkable navigation; no answer or prompt."""
    tid, material = task.get("id"), task.get("material_id", "")
    if task.get("availability", "active") != "active":
        return None
    if task.get("source_kind") == "assessment":
        plans = _section(catalog, "assessment_plans")
        available = [sid for sid in task.get("skill_ids", []) if tid in plans.get(sid, {}).get("task_ids", [])]
        sid = preferred_skill_id if preferred_skill_id in available else next(iter(available), None)
        if sid and task.get("phase") in {"practice", "transfer", "delayed"}:
            return {"action": "start_session", "assessment_goal_id": sid, "mode": task["phase"],
                    "focus_skill_ids": [sid], "task_ids": [tid]}
    if task.get("source_kind") == "unit" and material.startswith("unit:"):
        uid = material[5:]
        unit = _section(catalog, "units").get(uid, {})
        ids = unit.get("task_ids", [row.get("id") if isinstance(row, dict) else row for row in unit.get("tasks", [])])
        if tid in ids and task.get("phase") in {"practice", "transfer", "delayed"}:
            return {"action": "start_session", "unit_id": uid, "mode": task["phase"],
                    "focus_skill_ids": list(task.get("skill_ids", [])), "task_ids": [tid]}
    if task.get("source_kind") == "bank" and material.startswith("topic:"):
        topic = material[6:]
        if topic in _section(catalog, "placements") and "practice" in task.get("purpose", []):
            return {"action": "start_session", "targets": [topic], "mode": "practice", "task_ids": [tid], "limit": 1}
    if task.get("source_kind") == "self_check" and material.startswith("topic:"):
        topic, index = material[6:], task.get("criterion_index")
        placement = _section(catalog, "placements").get(topic, {})
        ids = placement.get("criterion_skill_ids", placement.get("skill_ids", []))
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(ids) and ids[index] in task.get("skill_ids", []):
            return {"action": "open_topic_goal", "topic_id": topic, "criterion_index": index, "skill_id": ids[index]}
    if task.get("source_kind") == "project_step" and material.startswith("project:"):
        pid = material[8:]
        project = _section(catalog, "projects").get(pid, {})
        if tid in project.get("task_ids", []):
            return {"action": "open_project_goal", "project_id": pid, "task_id": tid,
                    "objective_ids": task.get("objective_ids", [])}
    return None


def task_options(catalog, skill_ids, records, exposures=None, *, allowed_task_ids=None, limit=5):
    """Rank concrete next observations with family, representation and help limits."""
    if not skill_ids:
        return []
    observations = list(_observations(records, include_pending=True))
    seen_questions = {answer["question_id"] for _, answer in observations}
    # Historical placeholders may block novelty after an explicit source-bound
    # review. They still cannot contribute to positive skill observations.
    seen_families = {family for _, answer in observations
                     if (family := exposure_family(answer.get("family_id"), question_id=answer.get("question_id")))}
    seen_representations = set()
    for _, answer in observations:
        if set(answer.get("skill_ids", [])) & set(skill_ids):
            seen_representations.update(known_representations(answer.get("representation")))
    exposures = exposures or {}
    question_exposures, family_exposures = exposures.get("questions", {}), exposures.get("families", {})
    options = []
    for tid, task in _section(catalog, "tasks").items():
        if allowed_task_ids is not None and tid not in allowed_task_ids:
            continue
        if task.get("availability", "active") != "active" or not set(task.get("skill_ids", [])) & set(skill_ids):
            continue
        family = task.get("family_id")
        declared = task.get("family_status") == "declared"
        qex = question_exposures.get(tid, {})
        fex = family_exposure(family_exposures, family, questions=question_exposures) if declared else {}
        exposure_rows = [qex, fex]
        helped = any(row.get("material_count", 0) or row.get("reference_count", 0) or row.get("feedback_count", 0)
                     for row in exposure_rows)
        repeat = tid in seen_questions or any(qex.get(field, 0) for field in
                                             ("presentation_count", "answer_count", "material_count", "reference_count", "feedback_count"))
        family_repeat = declared and (canonical_family(family) in seen_families or any(fex.get(field, 0) for field in
                                             ("presentation_count", "answer_count", "material_count", "reference_count", "feedback_count")))
        representations = known_representations(task.get("representation"))
        known = bool(representations)
        eligible = bool(declared and family and not repeat and not family_repeat and not helped and known
                        and task.get("type") in {"numeric", "choice", "short"})
        reason = ("题族尚未分类，只能作为继续练习，不能判定为新的独立观察。" if not declared else
                  "题族已登记，但表征尚未核对；不能把未知表征算作新表征。" if not known else
                  "换已声明题族与已知表征继续观察；仍需记录帮助和实际间隔。" if eligible else
                  "可继续复习；重复、同族或已看参考不能冒充新的独立检查。")
        options.append({"task_id": tid, "resource_version": task.get("resource_version"),
                        "skill_ids": task.get("skill_ids", []), "family_id": family,
                        "family_status": task.get("family_status", "unclassified"), "representation": task.get("representation"),
                        "repeated_question": bool(repeat), "repeated_family": bool(family_repeat),
                        "help_exposed": bool(helped), "new_representation": bool(representations - seen_representations),
                        "known_representation_ids": sorted(representations), "representation_known": known,
                        "eligible_for_followup": eligible, "phase": task.get("phase"),
                        "source_kind": task.get("source_kind"), "start_action": task_start_action(catalog, task,
                            preferred_skill_id=next((sid for sid in skill_ids if sid in task.get("skill_ids", [])), None)),
                        "independent_evidence": False, "selection_reason": reason})
    options.sort(key=lambda row: (row["repeated_question"], row["repeated_family"], row["help_exposed"],
                                 row["family_status"] != "declared", not row["new_representation"], row["task_id"]))
    # A short recommendation list should expose actual method choices before
    # filling all slots with number changes from one family. Reviewed aliases
    # share a slot; unknown labels never establish a second method.
    family_counts, ranked = {}, []
    for index, option in enumerate(options):
        key = (option["eligible_for_followup"], canonical_family(option["family_id"]))
        occurrence = family_counts.get(key, 0) if option["family_status"] == "declared" else 0
        family_counts[key] = occurrence + 1
        ranked.append((not option["eligible_for_followup"], occurrence, index, option))
    options = [row[-1] for row in sorted(ranked, key=lambda row: row[:3])]
    return options if limit is None else options[:limit]


def goal_pathway(catalog, skill_id, records=(), *, exposures=None, preferred_unit=None, needs=()):
    """A goal's exact pathways and evidence limits without exposing test content."""
    skills = _section(catalog, "skills")
    if skill_id not in skills:
        raise KeyError(skill_id)
    if preferred_unit is not None:
        unit = _section(catalog, "units").get(preferred_unit)
        if not unit or skill_id not in unit.get("skill_ids", []):
            raise ValueError("目标不属于指定单元")
    skill = skills[skill_id]
    prerequisites, routes = support_routes(catalog, [skill_id], preferred_unit=preferred_unit)
    refined_by = [{"skill_id": sid, "statement": row["statement"], "granularity": row["granularity"]}
                  for sid, row in skills.items() if skill_id in row.get("refines_skill_ids", [])]
    broader_goals = [{"skill_id": sid, "statement": skills[sid]["statement"],
                      "granularity": skills[sid].get("granularity")}
                     for sid in skill.get("refines_skill_ids", []) if sid in skills]
    options = task_options(catalog, [skill_id], records, exposures, limit=None)
    tasks = _section(catalog, "tasks")
    objective_options = [option for option in options if option["start_action"] and
                         tasks[option["task_id"]].get("type") in {"numeric", "choice", "short"}]
    known = [option for option in objective_options if option["family_status"] == "declared"
             and option["family_id"] and option["representation_known"]]
    families = {canonical_family(option["family_id"]) for option in known}
    available = {canonical_family(option["family_id"]) for option in known if option["eligible_for_followup"]}
    opportunity = {"task_count": len(options), "objective_task_count": len(objective_options),
        "known_objective_family_count": len(families), "available_followup_family_count": len(available),
        "multiple_known_families": len(families) >= 2, "independent_mastery_evidence": False,
        "notice": ("当前尚无明确绑定到这个目标的可用任务，观察缺口继续保留。" if not options else
                   "当前对应任务以过程记录或自查为主，尚缺可客观记录的对应任务；继续保存过程，不能据此确认整个目标。" if not objective_options else
                   "当前可用任务还不足两种已核对的客观题族；可以练习，但不同数值或同族改名不能补足这个缺口。" if len(families) < 2 else
                   "已有多种已核对的客观题族，仍只观察这些具体任务；不能据此认定整个目标已经掌握。"),
        "availability_notice": (f"按当前记录，尚有 {len(available)} 种未呈现、未记录同族参考的已知题族可继续观察；实际帮助与间隔仍须核对。" if available else
                                "当前没有符合未呈现且未记录同族参考条件的已知题族。复习和过程修订仍可继续，待观察事项会保留。")}
    from .assessments import public_plan
    observation_plan = public_plan(catalog, skill_id)
    if observation_plan:
        for component in observation_plan["components"]:
            component["tasks"] = [{"task_id": tid, "type": tasks[tid]["type"],
                                    "start_action": task_start_action(catalog, tasks[tid], preferred_skill_id=skill_id)}
                                   for tid in component["task_ids"] if tid in tasks]
    return {"schema": 1, "skill_id": skill_id, "statement": skill["statement"],
        "granularity": skill.get("granularity"), "stage": skill.get("stage"),
        "prerequisite_skill_ids": prerequisites, "routes": routes,
        "refines_skill_ids": list(skill.get("refines_skill_ids", [])), "refined_by": refined_by,
        "broader_goals": broader_goals, "observation_opportunities": opportunity,
        "observation_plan": observation_plan,
        "task_options": options[:20],
        "pending_need_ids": [need["need_id"] for need in needs if skill_id in need.get("skill_ids", [])
                             and need.get("status") in {"open", "deferred"}],
        "independent_mastery_evidence": False,
        "notice": "这些入口来自明确目标关系和现有活动。先修是检查所需基础的路线，不是必须刷完所有前置内容的门槛；完成活动不自动证明掌握。"}
