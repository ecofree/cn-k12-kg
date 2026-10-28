"""Explicit curriculum scope and objective-level evidence inventory.

Read source content, never a hand-maintained 'complete' flag. Material readiness
is an authoring claim with inspectable references, not teacher approval or a
measurement of a learner's mastery. Topic-only associations do not count as an
explicit objective task.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLE_REQUIREMENTS = {
    "core": {"label": "核心学习", "learner_requirement": "所选课程范围内的目标均需学习；阅读解释、完成任务、处理未解决事项，再按需要观察迁移与间隔表现。",
             "author_requirement": "每个目标有具体解释、学习过程、目标自检与评价依据、练习和可保存的修订入口；课程单元另有定向支持及各阶段任务。"},
    "foundation_support": {"label": "基础支持", "learner_requirement": "按前置要求或已记录困难使用；需能解释基础意义、完成针对任务并保留困难，不要求把所有基础内容反复完成一遍。",
             "author_requirement": "每个登记基础目标仍须有完整可教材料、具体自检和修订支持；簇速查只作辅助，不能代替目标材料或降低承诺。"},
    "extension": {"label": "扩展活动", "learner_requirement": "可以选择参加；一旦选择，按活动目标留下过程、作品、自评与修订，不以完成所有扩展项目作为核心课程完成条件。",
             "author_requirement": "每个活动目标有对应操作步骤、成果要求、量规与反馈修订路径，明确数据和推断边界。"},
}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _stage(topic):
    grade = topic.get("grade", 0)
    return "primary" if grade <= 6 else "junior" if grade <= 9 else "senior"


def _resources(data, folder):
    return {value["id"]: (path, value) for path in sorted((data / folder).glob("*.json"))
            for value in [_read(path)]}


def scope_manifest(data=None):
    """Create the explicit denominator from existing stable goal identities.

    Writing this inventory is an authoring operation. Normal report/check calls
    compare it with sources and flag additions, removals, role or wording changes.
    """
    data = Path(data or ROOT / "data")
    topics, tiers = _read(data / "topics.json"), _read(data / "topic_tiers.json")["topics"]
    registry = _read(data / "skills/registry.json")
    topic_rows = []
    for topic in topics:
        tid = topic["id"]
        role = "core" if tiers[tid]["tier"] == "core" else "foundation_support"
        skills = registry["placements"][tid]["criterion_skill_ids"]
        topic_rows.append({"topic_id": tid, "title": topic["name"], "stage": _stage(topic), "volume": topic["volume"],
            "source_tier": tiers[tid]["tier"], "role": role,
            "goals": [{"goal_id": skills[index], "criterion_index": index, "statement": statement}
                      for index, statement in enumerate(topic.get("mastery_criteria", []))]})
    resources = {}
    for folder, kind, role in (("learning_units", "units", "core"), ("projects", "projects", "extension")):
        resources[kind] = [{"resource_id": rid, "title": resource["title"], "stage": resource["stage"], "role": role,
            "goals": [{"goal_id": registry["objective_skill_ids"][objective["id"]],
                       "objective_id": objective["id"], "statement": objective["statement"]}
                      for objective in resource["objectives"]]}
            for rid, (_, resource) in _resources(data, folder).items()]
    return {"schema": 1, "subject": "mathematics", "scope": "中国小学至高中数学：现有23册教材导航及明确登记的目标、单元和活动；不声明覆盖所有学科或未登记课程目标。",
            "roles": ROLE_REQUIREMENTS, "topics": topic_rows, **resources,
            "completion_boundary": "材料齐备、活动完成、自评达到与独立掌握分别记录；教师审核和真实学生效果验证继续暂缓。"}


def build_scope_report(data=None, *, manifest=None):
    data = Path(data or ROOT / "data")
    expected = scope_manifest(data)
    path = data / "curriculum_scope.json"
    manifest = manifest if manifest is not None else _read(path) if path.exists() else None
    errors = []
    if manifest is None:
        errors.append("缺 data/curriculum_scope.json：先显式登记完整课程范围")
    elif manifest != expected:
        errors.append("课程范围清单与当前目标、角色或完成要求不一致；请审查具体增删改后显式更新清单")
    # Always report the current real source inventory, including newly added
    # goals; an outdated manifest must never make a goal silently disappear.
    topics = {topic["id"]: topic for topic in _read(data / "topics.json")}
    tiers = _read(data / "topic_tiers.json")["topics"]
    raw_questions = _read(data / "questions.json")
    bank = {question["id"]: question for question in raw_questions}
    admission_rows, task_bindings = None, {}
    admission_path = data / "task_admissions.json"
    if admission_path.is_file():
        admission_manifest = _read(admission_path)
        if (not isinstance(admission_manifest, dict) or admission_manifest.get("schema") != 1
                or not isinstance(admission_manifest.get("tasks"), dict)):
            raise ValueError("课程范围无法验证显式准入：准入登记版本或结构不正确")
        admission_rows = admission_manifest["tasks"]
        if len(bank) != len(raw_questions) or set(admission_rows) != set(bank):
            raise ValueError("课程范围无法验证显式准入：准入登记与自有题目身份集合不一致")
        bindings_path = data / "skills/catalog-bindings.json"
        if not bindings_path.is_file():
            raise ValueError("课程范围无法验证显式准入：缺少答案契约绑定")
        bindings = _read(bindings_path)
        if (not isinstance(bindings, dict) or bindings.get("schema_version") != 1
                or not isinstance(bindings.get("tasks"), dict)):
            raise ValueError("课程范围无法验证显式准入：答案契约绑定版本或结构不正确")
        task_bindings = bindings["tasks"]
    elif (data / "catalog.json").is_file():
        raise ValueError("已有发布目录却缺少 task_admissions.json，课程范围不能退回旧准入规则")
    units, projects = _resources(data, "learning_units"), _resources(data, "projects")
    from tools.teach_cards import check_card
    from .evidence import question_policy
    # Reuse metadata only inside this source read. A new report always rereads
    # files and recalculates question policies, even when names or mtimes match.
    rows, hash_cache, file_refs, policy_cache = [], {}, {}, {}
    for source in ("topics.json", "topic_tiers.json", "skills/registry.json", "curriculum_scope.json", "questions.json",
                   "task_admissions.json", "skills/catalog-bindings.json"):
        source_path = data / source
        if source_path.exists():
            hash_cache["data/" + source] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    brief_refs = defaultdict(list)
    for brief_path in sorted((data / "teach/clusters").glob("*.json")):
        for tid in _read(brief_path).get("covers", []):
            brief_refs[tid].append({"path": "data/teach/clusters/" + brief_path.name, "reference_only": True})

    def ref(file, pointer, kind, identity):
        file = Path(file)
        metadata = file_refs.get(file)
        if metadata is None:
            relative = "data/" + file.relative_to(data).as_posix()
            if relative not in hash_cache:
                hash_cache[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
            metadata = file_refs[file] = (relative, hash_cache[relative])
        relative, source_hash = metadata
        endpoint = {"topic": "/api/teach/", "unit": "/api/learning/units/", "project": "/api/projects/"}[kind]
        return {"path": relative, "json_pointer": pointer, "resource_kind": kind, "resource_id": identity,
                "api_url": endpoint + identity, "content_sha256": source_hash}

    def practice_policy(question):
        qid = question["id"]
        if qid not in policy_cache:
            if admission_rows is None:
                # Small unpublished fixtures can still exercise source checks.
                policy_cache[qid] = question_policy(question)
            else:
                from .admissions import policies, resolved_question
                binding = task_bindings.get("bank:" + qid)
                if not isinstance(binding, dict) or binding.get("id") != qid:
                    raise ValueError(f"课程范围无法验证显式准入 {qid}：缺少对应题目的答案契约绑定")
                spec = question.get("answer_spec") or binding.get("answer_spec")
                if not isinstance(spec, dict):
                    raise ValueError(f"课程范围无法验证显式准入 {qid}：答案契约缺失")
                try:
                    policy_cache[qid] = policies(question, resolved_question(question, binding), admission_rows[qid])[1]
                except ValueError as exc:
                    raise ValueError(f"课程范围无法验证显式准入 {qid}：{exc}") from exc
        return policy_cache[qid]

    def row_base(scope, goal, kind, identity):
        return {"goal_id": goal["goal_id"], "objective_id": goal.get("objective_id"),
            "criterion_index": goal.get("criterion_index"), "statement": goal["statement"],
            "resource_kind": kind, "resource_id": identity, "title": scope["title"], "stage": scope["stage"],
            "role": scope["role"], "completion_requirement": ROLE_REQUIREMENTS[scope["role"]],
            "material_refs": [], "process_refs": [], "task_refs": [], "revision_refs": [],
            "conditions_refs": [], "representation_refs": [], "gaps": [], "teacher_review": "pending",
            "student_effect_validation": "deferred", "independent_mastery_evidence": False}

    for scope in expected["topics"]:
        tid = scope["topic_id"]
        card_path = data / "teach" / f"{tid}.json"
        card = _read(card_path) if card_path.exists() else {}
        card_errors = check_card(card, topics, bank) if card else ["缺单点完整讲解"]
        coverage = {entry.get("criterion_index"): (index, entry) for index, entry in enumerate(card.get("objective_coverage", []))}
        candidate_refs = brief_refs[tid]
        for goal in scope["goals"]:
            row = row_base(scope, goal, "topic", tid)
            row.update(source_tier=scope["source_tier"], volume=scope["volume"], cluster=tiers[tid].get("cluster"),
                       auxiliary_reference_refs=candidate_refs)
            matched = coverage.get(goal["criterion_index"])
            entry = matched[1] if matched and matched[1].get("title") == goal["statement"] else None
            if card_errors:
                row["gaps"].append({"code": "complete_teaching_card", "detail": "; ".join(card_errors)})
            if entry:
                prefix = f"/objective_coverage/{matched[0]}"
                row["material_refs"] = [ref(card_path, prefix + "/explanation", "topic", tid)]
                row["task_refs"] = [{**ref(card_path, prefix + "/self_check", "topic", tid),
                    "task_kind": "process_self_check", "explicit_goal_alignment": True,
                    "observes": entry.get("self_check", {}).get("criteria", [])}]
                row["revision_refs"] = [{**ref(card_path, prefix + "/self_check/criteria", "topic", tid),
                    "action": "save_self_check_revision", "source_ref": {"topic_id": tid, "criterion_index": goal["criterion_index"]}}]
                if entry.get("difficulty_support"):
                    row["revision_refs"].append(ref(card_path, prefix + "/difficulty_support", "topic", tid))
                elif card.get("pitfalls"):
                    row["revision_refs"].append(ref(card_path, "/pitfalls", "topic", tid))
                row["observation_boundary"] = entry.get("observation_note") or (
                    "开放自检观察所列量规项目，并保留过程与自评；原目标可能含多个行为，不能由一题、自报达到或看过参考推出全部掌握。")
                row["observation_boundary_basis"] = "authored_goal_note" if entry.get("observation_note") else "explicit_process_task_policy"
            else:
                row["gaps"].append({"code": "explicit_goal_material_and_task", "detail": "未找到标题与目标一致的逐目标解释、自检及量规；簇covers或同主题题目不抵消这个缺口。"})
                row["observation_boundary"] = "尚无本目标的明确任务映射，无法由主题练习结果判断这个目标是否已被观察。"
            if card:
                if card.get("steps") and card.get("worked_example"):
                    row["process_refs"] = [ref(card_path, "/steps", "topic", tid), ref(card_path, "/worked_example", "topic", tid)]
                if card.get("conditions"):
                    row["conditions_refs"] = [ref(card_path, "/conditions", "topic", tid)]
                elif card.get("when_to_use"):
                    row["conditions_refs"] = [ref(card_path, "/when_to_use", "topic", tid)]
                for field in ("representation_note", "diagram", "worked_example"):
                    if card.get(field):
                        row["representation_refs"].append(ref(card_path, "/" + field, "topic", tid))
                for index, practice in enumerate(card.get("practice", [])):
                    question = bank.get(practice.get("id"))
                    if question and practice_policy(question)["practice_eligible"]:
                        row["task_refs"].append({**ref(card_path, f"/practice/{index}", "topic", tid),
                            "task_id": question["id"], "task_kind": "practice",
                            "explicit_goal_alignment": goal["criterion_index"] in question.get("tested_criterion_indices", []),
                            "observation_limit": "未显式绑定目标的练习只作为主题支持，不计作该目标的独立证据。"})
            rows.append(row)

    for key, kind, resources in (("units", "unit", units), ("projects", "project", projects)):
        for scope in expected[key]:
            rid = scope["resource_id"]
            resource_path, resource = resources[rid]
            supports = resource.get("instructional_support", {}).get("supports", [])
            for goal in scope["goals"]:
                row = row_base(scope, goal, kind, rid)
                oid = goal["objective_id"]
                if kind == "unit":
                    row["material_refs"] = [ref(resource_path, "/explanation", kind, rid)]
                    row["conditions_refs"] = [ref(resource_path, "/explanation/conditions", kind, rid)]
                    row["process_refs"] = [ref(resource_path, "/worked_examples", kind, rid)]
                    for index, task in enumerate(resource.get("tasks", [])):
                        if oid in task.get("objective_ids", []):
                            row["task_refs"].append({**ref(resource_path, f"/tasks/{index}", kind, rid),
                                "task_id": task["id"], "phase": task.get("phase"), "task_kind": task["type"],
                                "explicit_goal_alignment": True, "observes": task.get("rubric", []),
                                "representation": task.get("representation"), "family_id": task.get("family_id")})
                    phases = {task.get("phase") for task in row["task_refs"]}
                    for phase in {"practice", "transfer", "delayed"} - phases:
                        row["gaps"].append({"code": "phase_task", "detail": f"目标缺{phase}阶段任务"})
                    matched = [(index, item) for index, item in enumerate(supports) if item.get("objective_id") == oid]
                    if matched:
                        index, support = matched[0]
                        row["revision_refs"] = [ref(resource_path, f"/instructional_support/supports/{index}", kind, rid)]
                        row["observation_boundary"] = support.get("observation_note", "指导后的正确仍需独立和跨情境观察。")
                    else:
                        row["gaps"].append({"code": "targeted_support", "detail": "缺对应目标的辨别、提示及指导练习支持记录"})
                        row["observation_boundary"] = "客观答案只观察该题所需行为；过程题保留为needs_review，自评不重评分，未证明独立迁移。"
                    if resource.get("diagram"):
                        row["representation_refs"] = [ref(resource_path, "/diagram", kind, rid)]
                else:
                    row["material_refs"] = [ref(resource_path, "/task_brief", kind, rid), ref(resource_path, "/materials", kind, rid)]
                    for index, step in enumerate(resource.get("steps", [])):
                        if oid in step.get("objective_ids", []):
                            reference = ref(resource_path, f"/steps/{index}", kind, rid)
                            row["process_refs"].append(reference)
                            row["task_refs"].append({**reference, "task_kind": "project_process", "explicit_goal_alignment": True,
                                                    "observes": [step.get("deliverable")]})
                    for index, criterion in enumerate(resource.get("rubric", [])):
                        if oid in criterion.get("objective_ids", []):
                            row["revision_refs"].append({**ref(resource_path, f"/rubric/{index}", kind, rid),
                                                         "action": "save_project_revision"})
                    row["observation_boundary"] = resource.get("assessment_note", "作品与量规自评为学习者报告，不能自动转换成分数或独立掌握结论。")
                    row["optional_participation"] = True
                rows.append(row)
    for row in rows:
        for field, code in (("material_refs", "material"), ("process_refs", "learning_process"),
                            ("task_refs", "observable_task"), ("revision_refs", "revision_route")):
            if not row[field]:
                row["gaps"].append({"code": code, "detail": f"缺{field}所需实际资源引用"})
        row["status"] = "materials_ready" if not row["gaps"] else "incomplete"
    groups = defaultdict(lambda: {"goals": 0, "materials_ready": 0, "incomplete": 0, "gap_codes": Counter()})
    for row in rows:
        group = groups[(row["role"], row["stage"], row.get("cluster") or row["resource_kind"])]
        group["goals"] += 1
        group[row["status"]] += 1
        group["gap_codes"].update(gap["code"] for gap in row["gaps"])
    return {"schema": 1, "scope": expected["scope"], "roles": ROLE_REQUIREMENTS,
        "source_version": hashlib.sha256(json.dumps(hash_cache, sort_keys=True).encode()).hexdigest(),
        "manifest_valid": not errors, "errors": errors,
        "summary": {"topic_count": len(expected["topics"]), "volume_count": len({topic["volume"] for topic in expected["topics"]}),
            "topic_goals": sum(len(topic["goals"]) for topic in expected["topics"]),
            "core_topics": sum(topic["role"] == "core" for topic in expected["topics"]),
            "foundation_topics": sum(topic["role"] == "foundation_support" for topic in expected["topics"]),
            "unit_goals": sum(len(unit["goals"]) for unit in expected["units"]),
            "project_goals": sum(len(project["goals"]) for project in expected["projects"]),
            "total_goals": len(rows), "materials_ready": sum(row["status"] == "materials_ready" for row in rows),
            "incomplete": sum(row["status"] == "incomplete" for row in rows)},
        "groups": [{"role": role, "stage": stage, "cluster": cluster, **counts, "gap_codes": dict(counts["gap_codes"])}
                   for (role, stage, cluster), counts in sorted(groups.items())],
        "goals": rows, "limits": [expected["completion_boundary"],
            "materials_ready只表示实际引用满足约定的作者材料结构；不等于教师审核、教学效果验证或学生已达到。",
            "只检查显式目标映射；同主题、同册、簇速查范围与题目数量不能替代逐目标教学和观察。"]}
