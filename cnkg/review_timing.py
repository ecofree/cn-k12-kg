"""Conservative review timing from recorded activity, never a mastery claim.

An old client-selected session cannot override more recent activity. A review
qualifies for the product's interval ladder only when the current server has
recorded a matching content version and an uninterrupted 24-hour interval.
Legacy records remain readable, but missing timing evidence is not invented.
"""
from __future__ import annotations

from datetime import datetime, timezone

TIMING_POLICY = "latest_scope_activity_v1"
MINIMUM_INTERVAL_SECONDS = 86400


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def latest_timestamp(values):
    valid = [(timestamp(value), value) for value in values]
    valid = [pair for pair in valid if pair[0] is not None]
    return max(valid, key=lambda pair: pair[0])[1] if valid else None


def interval_context(record, completed, *, requested_previous=None, requested_same_scope=False, latest_activity=None):
    """All ``completed`` records must already be owner/scope filtered."""
    candidates = [item for item in completed if timestamp(item.get("completed_at")) is not None
                  and (item.get("answered_count", 0) or item.get("answers"))]
    previous = max(candidates, key=lambda item: timestamp(item["completed_at"])) if candidates else None
    start = timestamp(record.get("created_at"))
    context = {"timing_policy": TIMING_POLICY, "actual_delay_seconds": None,
               "actual_quiet_seconds": None, "delay_window_met": False,
               "same_content_version": False, "review_interval_qualified": False,
               "independent_evidence": False,
               "interval_reason": "no_completed_same_scope",
               "interval_notice": "还没有这个学习范围的完成记录；本次可继续练习，不计作有间隔的复测。"}
    if requested_previous is not None:
        context["requested_previous_session_id"] = requested_previous["id"]
        context["requested_previous_same_scope"] = requested_same_scope
    if previous is None:
        if requested_previous is not None:
            context.update(previous_session_id=requested_previous["id"], same_learning_scope=requested_same_scope,
                           previous_created_at=requested_previous.get("created_at"),
                           previous_completed_at=requested_previous.get("completed_at"))
        return context
    context.update(previous_session_id=previous["id"], same_learning_scope=True,
                   previous_created_at=previous.get("created_at"), previous_completed_at=previous["completed_at"])
    version = record.get("resource_version") or record.get("content_sha256")
    prior_version = previous.get("resource_version") or previous.get("content_sha256")
    context["same_content_version"] = bool(version and prior_version and version == prior_version)
    activity_at = latest_activity.get("occurred_at") if latest_activity else None
    last_activity = latest_timestamp([previous["completed_at"], activity_at])
    context["latest_scope_activity_at"] = last_activity
    later_exposure = bool(timestamp(activity_at) and timestamp(activity_at) > timestamp(previous["completed_at"]))
    context["latest_scope_activity_type"] = latest_activity["event_type"] if later_exposure else "session_completed"
    context["timing_evidence"] = "recorded_completion_and_resource_exposure_not_proof_of_reading_or_independence"
    previous_time, activity_time = timestamp(previous["completed_at"]), timestamp(last_activity)
    if (start is None or previous_time > start or activity_time > start
            or latest_activity is not None and timestamp(activity_at) is None):
        context.update(interval_reason="invalid_or_future_timestamp",
                       interval_notice="记录时间不能确认有效间隔；原记录保留，本次不推进复测间隔。")
        return context
    context["actual_delay_seconds"] = (start - previous_time).total_seconds()
    context["actual_quiet_seconds"] = (start - activity_time).total_seconds()
    context["delay_window_met"] = context["actual_quiet_seconds"] >= MINIMUM_INTERVAL_SECONDS
    context["review_interval_qualified"] = bool(record.get("mode") == "delayed"
        and context["delay_window_met"] and context["same_content_version"])
    if not context["delay_window_met"]:
        if later_exposure:
            material = context["latest_scope_activity_type"] in {"material_requested", "material_provided", "reference_opened"}
            context.update(interval_reason="recent_material_or_reference" if material else "recent_task_activity",
                interval_notice=("近期已提供这个范围的讲解或参考，距离这次材料记录不足24小时；本次可复习，但不推进复测间隔。"
                                 if material else "近期还有这个范围的题目、作答或反馈记录，间隔不足24小时；本次可复习，但不推进复测间隔。"))
        else:
            context.update(interval_reason="completion_interval_short",
                           interval_notice="距离这个范围最近一次完成不足24小时；立即重做可以复习，但不推进复测间隔。")
    elif not context["same_content_version"]:
        context.update(interval_reason="content_version_changed",
                       interval_notice="内容版本与前次完成不一致；记录保留，本次不作为同版本的间隔复测。")
    else:
        context.update(interval_reason="recorded_interval_met",
                       interval_notice="同一内容版本的相关活动记录已间隔至少24小时。这只说明记录的间隔，不证明独立作答或已经掌握。")
    return context


def qualified_review(record):
    """Count a completed interval observation, not correctness or mastery."""
    context = record.get("context", {})
    quiet = context.get("actual_quiet_seconds")
    return bool(record.get("mode") == "delayed" and timestamp(record.get("completed_at"))
                and any(not answer.get("skipped") and answer.get("status") != "not_attempted"
                        for answer in record.get("answers", []))
                and context.get("timing_policy") == TIMING_POLICY
                and context.get("same_learning_scope") is True
                and context.get("same_content_version") is True
                and context.get("delay_window_met") is True
                and isinstance(quiet, (int, float)) and not isinstance(quiet, bool)
                and quiet >= MINIMUM_INTERVAL_SECONDS
                and context.get("review_interval_qualified") is True)
