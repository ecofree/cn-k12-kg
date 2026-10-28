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
ALIGNMENT_FIELDS = ("tested_criterion_indices", "alignment_note", "target_alignment", "family_id", "variant_id",
                    "representation", "cognitive_demand", "reference_family_ids")


def _policy_error(value, *, exact=True):
    """Validate an archived use contract without coercing strings into consent.

    Old valid snapshots retain their original meaning. A malformed snapshot
    cannot fall back to today's permissive legacy heuristics or turn the text
    ``"false"`` into an enabled diagnostic permission.
    """
    if (not isinstance(value, dict) or not POLICY_FIELDS.issubset(value)
            or exact and set(value) != POLICY_FIELDS):
        return "准入用途字段不完整"
    for key in ("diagnosis_eligible", "practice_eligible", "may_confirm_mastery", "may_judge_weak", "reviewer_pending"):
        if type(value[key]) is not bool:
            return "准入用途必须为布尔值"
    for key in ("evidence_level", "confidence", "reason"):
        if not isinstance(value[key], str) or not value[key].strip():
            return "准入依据必须为非空文字"
    if (value["diagnosis_eligible"] and not value["practice_eligible"]
            or (value["may_confirm_mastery"] or value["may_judge_weak"]) and not value["diagnosis_eligible"]):
        return "准入用途互相矛盾"
    if (value["reason"] == "explicit_provisional_use_decision"
            and (value["may_confirm_mastery"] or value["may_judge_weak"])):
        return "暂定观察用途不能确认整主题掌握或薄弱"
    return None


def _closed_policy(reason):
    return {"diagnosis_eligible": False, "practice_eligible": False, "may_confirm_mastery": False,
            "may_judge_weak": False, "evidence_level": "bare", "reviewer_pending": True,
            "confidence": "screening", "reason": reason}


def fingerprint(question, version=2):
    from .evidence import question_content_sha256
    content = {"content": question_content_sha256(question), "admission_flags": {key: question.get(key) for key in FLAGS}}
    if version == 2:
        content.update(fingerprint_version=2, alignment={key: question.get(key) for key in ALIGNMENT_FIELDS})
        if "_effective_metadata" in question:
            content["effective_metadata"] = question["_effective_metadata"]
    elif version != 1:
        raise ValueError("不支持的准入指纹版本")
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolved_question(question, binding):
    """Bind the actual contract and alignment, including migration fallbacks.

    Author declarations have the same precedence as the catalog compiler.
    The extra snapshot keeps a binding-only edit from reusing an old decision.
    """
    result = copy.deepcopy(question)
    result["answer_spec"] = copy.deepcopy(question.get("answer_spec") or binding.get("answer_spec"))
    metadata = {field: copy.deepcopy(question.get(field, binding.get(field)))
                for field in ("family_id", "variant_id", "representation", "cognitive_demand")}
    metadata["family_status"] = "declared" if "family_id" in question else binding.get("family_status")
    metadata["type"] = binding.get("type", question.get("type"))
    result.update(metadata)
    result["_effective_metadata"] = copy.deepcopy(metadata)
    return result


def policies(question, resolved, row):
    if not isinstance(row, dict) or row.get("id") != question.get("id"):
        raise ValueError("准入登记缺失或身份不符")
    if row.get("source_sha256") != fingerprint(question) or row.get("resolved_sha256") != fingerprint(resolved):
        raise ValueError("题义、解析、答案契约或用途已改变，须先核对并更新显式准入记录")
    for field in ("source_policy", "policy"):
        value = row.get(field)
        error = _policy_error(value)
        if error:
            raise ValueError(error)
    basis = row.get("basis", {})
    if (not isinstance(basis, dict) or basis.get("independent_review") is not False
            or any(not isinstance(basis.get(key), str) or not basis[key].strip()
                   for key in ("method", "limitation"))):
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
    if "_admission" not in question:
        return None
    snapshot = question.get("_admission")
    if not isinstance(snapshot, dict):
        return _closed_policy("admission_contract_incomplete")
    version = snapshot.get("fingerprint_version", 1)
    if type(version) is not int or version not in (1, 2) or snapshot.get("content_sha256") != fingerprint(question, version):
        return _closed_policy("admission_version_mismatch")
    value = snapshot.get("policy", {})
    if _policy_error(value, exact=False):
        return _closed_policy("admission_contract_incomplete")
    return {key: copy.deepcopy(value[key]) for key in POLICY_FIELDS}
