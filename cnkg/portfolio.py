"""Versioned learner work. A self-assessment never changes a task's grade."""
from __future__ import annotations

import copy
import re

SELF_ASSESSMENT = frozenset({"not_checked", "met", "partial", "revisit"})


def descriptor(kind, source_ref, version, title, fields, criteria, *, stage=None, work_id=None):
    if not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{64}", version):
        raise ValueError("作品必须绑定明确的内容版本")
    if work_id is None:
        work_id = f"{kind}:{source_ref}@{version[:16]}"
    snapshot = {"title": title, "fields": copy.deepcopy(fields), "criteria": copy.deepcopy(criteria)}
    if stage:
        snapshot["stage"] = stage
    return {"resource_id": work_id, "kind": kind, "source_ref": source_ref,
            "resource_version": version, "resource_snapshot": snapshot}


def from_work(work):
    payload = work["payload"]
    return {"resource_id": work["resource_id"], **{key: copy.deepcopy(payload[key]) for key in
            ("kind", "source_ref", "resource_version", "resource_snapshot")}}


def learner_payload(resource, values):
    """Only editable learner fields are accepted; references come from the server."""
    draft = values.get("draft", {})
    fields = {field["id"] for field in resource["resource_snapshot"]["fields"]}
    if not isinstance(draft, dict) or set(draft) - fields:
        raise ValueError("草稿中包含不属于这个任务的字段")
    if any(not isinstance(text, str) or len(text) > 16000 for text in draft.values()):
        raise ValueError("每项过程记录须为不超过16000字的文字")
    reflection = values.get("reflection", "")
    if not isinstance(reflection, str) or len(reflection) > 8000:
        raise ValueError("反思须为不超过8000字的文字")
    assessment = values.get("self_assessment", [])
    criteria = resource["resource_snapshot"]["criteria"]
    if not isinstance(assessment, list) or len(assessment) > len(criteria):
        raise ValueError("自评项目与任务量规不一致")
    seen = set()
    for row in assessment:
        if not isinstance(row, dict) or set(row) - {"criterion_index", "status", "comment"}:
            raise ValueError("自评格式不正确")
        index = row.get("criterion_index")
        if (isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(criteria)
                or index in seen or not isinstance(row.get("status"), str) or row["status"] not in SELF_ASSESSMENT):
            raise ValueError("自评项目或状态不正确")
        if not isinstance(row.get("comment", ""), str) or len(row.get("comment", "")) > 2000:
            raise ValueError("自评说明须为不超过2000字的文字")
        seen.add(index)
    status = values.get("status", "draft")
    if not isinstance(status, str) or status not in {"draft", "submitted"}:
        raise ValueError("作品状态只允许草稿或已提交自查")
    return {key: copy.deepcopy(resource[key]) for key in
            ("kind", "source_ref", "resource_version", "resource_snapshot")} | {
        "draft": copy.deepcopy(draft), "reflection": reflection,
        "self_assessment": copy.deepcopy(assessment), "status": status,
        "assessment_basis": "learner_reported_not_automatic_grade"}
