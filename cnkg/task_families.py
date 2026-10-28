"""Reviewed family equivalence for comparisons, never a rewrite of learner data.

Historical names remain in saved tasks, answers, events and exposure indexes.
An explicitly reviewed alias must not make an old structure look unseen after
a content release. No identity is inferred from names, grades or question IDs.
"""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_aliases(path):
    value = json.loads(Path(path).read_text())
    if value.get("schema") != 1 or not isinstance(value.get("aliases"), list):
        raise ValueError("题族别名登记格式不正确")
    direct = {}
    for row in value["aliases"]:
        alias, canonical = row.get("alias"), row.get("canonical")
        if (not all(isinstance(item, str) and item.strip() and not item.startswith("unclassified:")
                    and item not in {"unknown", "unclassified"} for item in (alias, canonical))
                or alias == canonical or alias in direct or not row.get("reason", "").strip()
                or row.get("reviewer_kind") != "ai" or row.get("independent_review") is not False):
            raise ValueError("题族别名必须有唯一明确关系和实际语义审查理由")
        direct[alias] = canonical
    result = {}
    for alias in direct:
        current, seen = alias, set()
        while current in direct:
            if current in seen:
                raise ValueError("题族别名不能循环")
            seen.add(current)
            current = direct[current]
        result[alias] = current
    return result


# Freeze the reviewed mapping at module import, like the running grader. A new
# release requires a restart; changing a file cannot relabel in-memory rules.
ALIASES = load_aliases(ROOT / "data/task_family_aliases.json")


def canonical_family(identity):
    return ALIASES.get(identity, identity)


def task_contract(question, binding):
    """Exact learner task and bound answer contract used by the V7 review."""
    return {**{key: question.get(key) for key in ("type", "prompt", "choices", "answer", "accept", "verify", "answer_spec")},
            "bound_answer_spec": binding.get("answer_spec")}


def load_history(path, *, root):
    """Read source/evidence-bound placeholders for exposure comparisons only.

    Ordinary aliases deliberately reject unknown families. This separate
    registry can only reduce claims of novelty; it cannot supply observations
    or change a saved task's classification, goals, grade or source version.
    """
    value = json.loads(Path(path).read_text())
    if value.get("schema") != 1 or not isinstance(value.get("aliases"), list):
        raise ValueError("历史题族曝光登记格式不正确")
    root = Path(root).resolve()
    result, tasks, evidence_reviews = {}, {}, {}
    task_hash_keys = ("baseline_source_task_sha256", "current_source_task_sha256",
                      "baseline_task_contract_sha256", "current_task_contract_sha256")
    binding_path = root / "data/skills/catalog-bindings.json"
    bindings = json.loads(binding_path.read_text()).get("tasks", {}) if binding_path.exists() else {}
    for row in value["aliases"]:
        alias, target, qid = row.get("alias"), row.get("canonical"), row.get("task_id")
        hashes = [row.get(key) for key in (*task_hash_keys, "review_evidence_sha256")]
        if (not isinstance(qid, str) or not qid or alias != "unclassified:" + qid or alias in result
                or not isinstance(target, str) or not target.strip() or target.startswith("unclassified:")
                or target in {"unknown", "unclassified"} or row.get("use") != "exposure_only"
                or row.get("alias_kind") != "reviewed_historical_task_identity"
                or row.get("reviewer_kind") != "ai" or row.get("independent_review") is not False
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()
                or not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
                or not isinstance(row.get("baseline_commit"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", row["baseline_commit"])):
            raise ValueError("历史题族只能按唯一题ID、完整指纹与明确审查作曝光比较")
        source, evidence = row.get("source"), row.get("review_evidence")
        for name, prefixes in ((source, ("data/content/", "data/question_sources/")),
                               (evidence, ("docs/reviews/",))):
            if (not isinstance(name, str) or not name.startswith(prefixes) or not name.endswith(".json")
                    or "\\" in name or ".." in Path(name).parts or Path(name).is_absolute()
                    or not (root / name).resolve().is_relative_to(root)):
                raise ValueError("历史题族来源与审查证据须位于明确的仓库路径")
            current = root
            for part in Path(name).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("历史题族来源及证据不得使用符号链接")
        if evidence not in evidence_reviews:
            evidence_bytes = (root / evidence).read_bytes()
            review = json.loads(evidence_bytes)
            if not isinstance(review, dict) or not isinstance(review.get("tasks"), list):
                raise ValueError("历史题族审查证据须有逐题审查列表")
            reviewed_tasks = {}
            for reviewed_task in review["tasks"]:
                if (not isinstance(reviewed_task, dict) or not isinstance(reviewed_task.get("id"), str)
                        or not reviewed_task["id"].strip() or reviewed_task["id"] in reviewed_tasks):
                    raise ValueError("历史题族逐题审查须有唯一明确题ID")
                reviewed_tasks[reviewed_task["id"]] = reviewed_task
            evidence_reviews[evidence] = {"sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                "baseline_commit": review.get("baseline_commit"), "tasks": reviewed_tasks}
        review = evidence_reviews[evidence]
        if review["sha256"] != row["review_evidence_sha256"]:
            raise ValueError("历史题族审查证据指纹已改变")
        reviewed_task = review["tasks"].get(qid, {})
        exposure_review = reviewed_task.get("history_exposure_review")
        if (review["baseline_commit"] != row["baseline_commit"]
                or reviewed_task.get("reviewer_kind") != "ai"
                or reviewed_task.get("independent_teacher_review") is not False
                or not isinstance(exposure_review, dict)
                or exposure_review.get("decision") != "allow_same_structure"
                or not isinstance(exposure_review.get("reason"), str)
                or not exposure_review["reason"].strip()):
            raise ValueError("历史题族逐题证据未按原版本明确允许同结构曝光比较")
        if any(reviewed_task.get(key) != row[key] for key in task_hash_keys):
            raise ValueError("历史题族逐题审查指纹与登记不一致")
        # The hashes bind the reviewed source versions, not mathematical
        # equivalence. A documented same-structure review may allow changed
        # numbers or conditions with different task-contract hashes.
        if source not in tasks:
            data = json.loads((root / source).read_text())
            questions = data.get("questions", []) if isinstance(data, dict) else data
            tasks[source] = {question["id"]: question for question in questions}
            if len(tasks[source]) != len(questions):
                raise ValueError("历史题族来源包含重复题ID")
        question = tasks[source].get(qid)
        actual = hashlib.sha256(json.dumps(question, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        if not question or actual != row["current_source_task_sha256"]:
            raise ValueError("历史题族所绑定的当前题目已改变，须重新审查")
        binding = bindings.get("bank:" + qid, {})
        contract_hash = hashlib.sha256(json.dumps(task_contract(question, binding), ensure_ascii=False,
                                                sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if contract_hash != row["current_task_contract_sha256"]:
            raise ValueError("历史题族所绑定的当前判分合同已改变，须重新审查")
        family = question.get("family_id") or binding.get("family_id")
        if canonical_family(family) != canonical_family(target):
            raise ValueError("历史题族目标与当前题目实际声明不一致")
        result[alias] = canonical_family(target)
    return result


HISTORICAL_FAMILIES = load_history(ROOT / "data/task_family_history.json", root=ROOT)


def load_task_transitions(path, *, root):
    """Correct a particular old task's family without merging its old family.

For example, a static missing-part task may have been labelled as a dynamic
take-away task. Only that task's actual historical exposure may carry forward;
other tasks with the old label do not become equivalent by association.
"""
    root = Path(root).resolve()
    value = json.loads(Path(path).read_text())
    if value.get("schema") != 1 or not isinstance(value.get("tasks"), list):
        raise ValueError("逐题题族迁移须有明确逐题语义审查")
    bindings = json.loads((root / "data/skills/catalog-bindings.json").read_text())["tasks"]
    result, sources = {}, {}
    digest = lambda item: hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":")).encode()).hexdigest()
    for row in value["tasks"]:
        identity, source = row.get("task_id"), row.get("source")
        previous, current = row.get("baseline_task"), row.get("current_task")
        if (not isinstance(identity, str) or identity in result or not identity
                or row.get("use") != "exposure_only" or row.get("reviewer_kind") != "ai"
                or row.get("independent_teacher_review") is not False
                or row.get("decision") != "allow_same_structure"
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()
                or not isinstance(row.get("baseline_commit"), str)
                or not re.fullmatch("[a-f0-9]{40}", row["baseline_commit"])
                or not isinstance(previous, dict) or not isinstance(current, dict)
                or previous.get("id") != identity or current.get("id") != identity):
            raise ValueError("逐题题族迁移缺少实际旧题、新题或同结构审查决定")
        if (not isinstance(source, str) or not source.startswith(("data/content/", "data/question_sources/"))
                or not source.endswith(".json") or "\\" in source or ".." in Path(source).parts
                or Path(source).is_absolute() or not (root / source).resolve().is_relative_to(root)):
            raise ValueError("逐题题族迁移来源越界")
        current_path = root
        for part in Path(source).parts:
            current_path = current_path / part
            if current_path.is_symlink():
                raise ValueError("逐题题族迁移来源不得是符号链接")
        if source not in sources:
            data = json.loads((root / source).read_text())
            questions = data.get("questions", []) if isinstance(data, dict) else data
            sources[source] = {question["id"]: question for question in questions}
            if len(sources[source]) != len(questions):
                raise ValueError("逐题题族迁移来源含重复题目身份")
        if (digest(previous) != row.get("baseline_source_task_sha256")
                or digest(current) != row.get("current_source_task_sha256")
                or digest(sources[source].get(identity)) != row.get("current_source_task_sha256")
                or digest(task_contract(current, bindings.get("bank:" + identity, {}))) != row.get("current_task_contract_sha256")):
            raise ValueError("逐题题族迁移绑定的题目或判分合同已改变，必须重新审查")
        old_family, new_family = row.get("historical_family_id"), row.get("canonical")
        if (not all(isinstance(family, str) and family.strip() and not family.startswith("unclassified:")
                    and family not in {"unknown", "unclassified"} for family in (old_family, new_family))
                or canonical_family(old_family) == canonical_family(new_family)
                or previous.get("family_id") != old_family
                or canonical_family(current.get("family_id")) != canonical_family(new_family)):
            raise ValueError("逐题题族迁移必须绑定旧题与当前题实际声明的不同题族")
        result[identity] = {"historical_family_id": old_family, "canonical": canonical_family(new_family)}
    return result


TASK_TRANSITIONS = load_task_transitions(ROOT / "data/task_family_transitions.json", root=ROOT)


def exposure_family(identity, *, question_id=None):
    """Conservative comparison key; never use this to award positive evidence."""
    transition = TASK_TRANSITIONS.get(question_id)
    if transition and (not identity or identity in {"unknown", "unclassified"}
                       or canonical_family(identity) in {canonical_family(transition["historical_family_id"]), transition["canonical"]}):
        return transition["canonical"]
    if identity in HISTORICAL_FAMILIES:
        return HISTORICAL_FAMILIES[identity]
    if (not identity or identity in {"unknown", "unclassified"}) and question_id:
        historical = HISTORICAL_FAMILIES.get("unclassified:" + question_id)
        if historical:
            return historical
    return canonical_family(identity)


def historical_question_ids(identities):
    if isinstance(identities, str):
        raise ValueError("历史曝光查询需要题族ID列表")
    selected = {exposure_family(identity) for identity in identities}
    return sorted({alias.removeprefix("unclassified:") for alias, family in HISTORICAL_FAMILIES.items()
                   if canonical_family(family) in selected}
                  | {identity for identity, row in TASK_TRANSITIONS.items() if row["canonical"] in selected})


def equivalent_names(identities):
    """Expand a selected query to all reviewed historical names, not all data."""
    canonical = {exposure_family(identity) for identity in identities}
    return sorted(canonical | {alias for alias, target in ALIASES.items() if target in canonical}
                  | {alias for alias, target in HISTORICAL_FAMILIES.items() if canonical_family(target) in canonical})


def matching_exposures(index, identity, *, questions=None):
    target = exposure_family(identity)
    if not target:
        return []
    rows = [row for name, row in index.items() if exposure_family(name) == target]
    rows += [(questions or {})[qid] for qid in historical_question_ids([target]) if qid in (questions or {})]
    return rows


def family_exposure(index, identity, *, questions=None):
    """Presence/counter bounds for selection; never claim a unique event count.

    One event may have indexed both historical names, so summing aggregate
    counters could double-count it. The maximum preserves every positive help
    or repetition flag; original indexed rows remain available unchanged.
    """
    rows = matching_exposures(index, identity, questions=questions)
    if not rows:
        return {}
    return {"family_id": canonical_family(identity), "count_basis": "maximum_per_indexed_name",
            **{field: max(row.get(field, 0) for row in rows) for field in
               ("presentation_count", "answer_count", "feedback_count", "material_count", "reference_count")}}
