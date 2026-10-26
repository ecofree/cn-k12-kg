"""Version-bound use decisions for bank tasks, separate from prose lint.

The first registry preserves the previous provisional decisions. It is an
auditable migration baseline, not a new mathematical or teacher endorsement.
Changing wording cannot silently add or remove diagnostic eligibility.
"""
from __future__ import annotations

import copy
import hashlib
import json

POLICY_FIELDS = {"diagnosis_eligible", "practice_eligible", "may_confirm_mastery", "may_judge_weak",
                 "evidence_level", "reviewer_pending", "confidence", "reason"}
FLAGS = ("challenge", "review_status", "quarantined", "diagnosis_eligible", "practice_eligible",
         "for_diagnosis", "evidence_level", "quality_review", "cross_source_evidence")


def fingerprint(question):
    from .evidence import question_content_sha256
    content = {"content": question_content_sha256(question), "admission_flags": {key: question.get(key) for key in FLAGS}}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def policies(question, resolved, row):
    if not isinstance(row, dict) or row.get("id") != question.get("id"):
        raise ValueError("准入登记缺失或身份不符")
    if row.get("source_sha256") != fingerprint(question) or row.get("resolved_sha256") != fingerprint(resolved):
        raise ValueError("题义、解析、答案契约或用途已改变，须先核对并更新显式准入记录")
    for field in ("source_policy", "policy"):
        value = row.get(field)
        if not isinstance(value, dict) or set(value) != POLICY_FIELDS:
            raise ValueError("准入用途字段不完整")
        for key in ("diagnosis_eligible", "practice_eligible", "may_confirm_mastery", "may_judge_weak", "reviewer_pending"):
            if type(value.get(key)) is not bool:
                raise ValueError("准入用途必须为布尔值")
        if (value["diagnosis_eligible"] and not value["practice_eligible"]
                or (value["may_confirm_mastery"] or value["may_judge_weak"]) and not value["diagnosis_eligible"]):
            raise ValueError("准入用途互相矛盾")
    basis = row.get("basis", {})
    if (basis.get("independent_review") is not False or not basis.get("method") or not basis.get("limitation")):
        raise ValueError("准入依据须明确其检查方法及尚未独立审核的边界")
    if (row["policy"]["diagnosis_eligible"] and
            (resolved.get("diagnosis_eligible") is False or resolved.get("for_diagnosis") is False)):
        raise ValueError("显式准入不能覆盖作者题源禁止诊断的声明")
    if row["policy"]["practice_eligible"] and resolved.get("practice_eligible") is False:
        raise ValueError("显式准入不能覆盖作者题源禁止练习的声明")
    # Hard exclusions and actual arithmetic failure always override a record.
    from .evidence import _legacy_question_policy
    checked = _legacy_question_policy(resolved)
    if checked["reason"] in {"quarantined", "challenge_only", "verify_failed", "evidence_rejected",
                             "missing_prompt_or_answer", "broken_choices", "memory_drill_requires_independent_review"}:
        if row["policy"]["practice_eligible"] or row["policy"]["diagnosis_eligible"]:
            raise ValueError("显式准入不能覆盖隔离、损坏或实际复算失败")
    return copy.deepcopy(row["source_policy"]), copy.deepcopy(row["policy"])


def runtime_policy(question):
    snapshot = question.get("_admission")
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("content_sha256") != fingerprint(question):
        return {"diagnosis_eligible": False, "practice_eligible": False, "may_confirm_mastery": False,
                "may_judge_weak": False, "evidence_level": "bare", "reviewer_pending": True,
                "confidence": "screening", "reason": "admission_version_mismatch"}
    value = snapshot.get("policy", {})
    if not POLICY_FIELDS.issubset(value):
        return {"diagnosis_eligible": False, "practice_eligible": False, "may_confirm_mastery": False,
                "may_judge_weak": False, "evidence_level": "bare", "reviewer_pending": True,
                "confidence": "screening", "reason": "admission_contract_incomplete"}
    return {key: copy.deepcopy(value[key]) for key in POLICY_FIELDS}
