"""Compiled mathematics skills, tasks and material exposure references.

The checked-in registries allocate identities once. Compilation resolves those
identities against current authoring sources; it never generates IDs from a
title, list order or textbook position, nor infers a student's answer contract.
Legacy topic criteria remain explicitly composite. A topic-level association
does not assert that an item tests every skill placed in that topic.

The Web runtime loads this file with the standard library only. JSON Schema
validation belongs to the compiler and release checks.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path

from .answers import grade_answer, validate_answer_spec
from .evidence import question_policy

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CATALOG_VERSION = 1


class CatalogError(ValueError):
    def __init__(self, errors):
        self.errors = [str(x) for x in errors]
        super().__init__("资源目录校验失败：\n" + "\n".join(self.errors))


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_hash(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _read(path):
    def reject(value):
        raise ValueError(f"JSON 不允许 {value}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject)
    except (OSError, ValueError) as exc:
        raise CatalogError([f"{path}: {exc}"]) from exc


def _stage(topic):
    if str(topic["id"]).startswith("mh"):
        return "senior"
    return "primary" if topic.get("grade", 7) <= 6 else "junior"


def _task_key(kind, resource_id, locator):
    return f"{kind}:{resource_id}:{locator}"


class Catalog:
    """Versioned registry; accessors return copies to protect session snapshots.

    Public dictionary attributes are owned by this instance. Callers should not
    mutate them. task()/material()/unit()/skill() return independent snapshots.
    No accessor makes a mastery or independent-evidence claim.
    """

    def __init__(self, payload):
        if payload.get("schema_version") != CATALOG_VERSION:
            raise CatalogError(["未知 catalog 版本，请重新编译"])
        self.data = copy.deepcopy(payload)
        self.skills = self.data["skills"]
        self.placements = self.data["placements"]
        self.tasks = self.data["tasks"]
        self.active_tasks = {key: task for key, task in self.tasks.items() if task["availability"] == "active"}
        self.retired_tasks = {key: task for key, task in self.tasks.items() if task["availability"] != "active"}
        self.materials = self.data["materials"]
        self.families = self.data["families"]
        self.units = self.data["units"]
        self.projects = self.data["projects"]
        self.source_index = self.data["source_index"]
        self.version = self.data["content_sha256"]
        self.coverage = self.data["coverage"]

    def task(self, identifier):
        return copy.deepcopy(self.tasks[identifier])

    def skill(self, identifier):
        return copy.deepcopy(self.skills[identifier])

    def material(self, identifier):
        return copy.deepcopy(self.materials[identifier])

    def placement(self, topic_id):
        return copy.deepcopy(self.placements[topic_id])

    def unit(self, identifier):
        return copy.deepcopy(self.units[identifier])

    def self_check(self, topic_id, criterion_index):
        key = _task_key("self_check", topic_id, criterion_index)
        return self.task(self.source_index[key])

    def project_steps(self, project_id):
        return [self.task(tid) for tid in self.projects[project_id]["task_ids"]]

    def tasks_for_skill(self, skill_id, purpose=None, *, include_quarantined=False):
        if skill_id not in self.skills:
            raise KeyError(skill_id)
        return [copy.deepcopy(task) for task in self.tasks.values()
                if skill_id in task["skill_ids"]
                and (include_quarantined or task["availability"] == "active")
                and (purpose is None or purpose in task["purpose"])]


def load_catalog(data_dir=None, *, check_sources=True):
    data = Path(data_dir or DATA_DIR)
    payload = _read(data / "catalog.json")
    digest = payload.get("content_sha256")
    if digest != content_hash({k: v for k, v in payload.items() if k != "content_sha256"}):
        raise CatalogError(["catalog 内容指纹不匹配，请运行 tools/compile_catalog.py --write"])
    if check_sources:
        errors = []
        for name, expected in payload.get("source_hashes", {}).items():
            path = data.parent / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                errors.append(f"目录源已改变：{name}；请重新编译")
        if errors:
            raise CatalogError(errors)
    return Catalog(payload)


def compile_catalog(data_dir=None, *, schema_dir=None):
    """Compile registered sources deterministically, without writing files."""
    from .contracts import validate_resource

    data = Path(data_dir or DATA_DIR)
    registry = _read(data / "skills/registry.json")
    bindings = _read(data / "skills/catalog-bindings.json")
    errors = []
    errors.extend(validate_resource("skills-registry", registry, schema_dir=schema_dir))
    errors.extend(validate_resource("catalog-bindings", bindings, schema_dir=schema_dir))
    if errors:
        raise CatalogError(errors)

    source_paths = {data / "skills/registry.json", data / "skills/catalog-bindings.json",
                    data / "topics.json", data / "questions.json"}
    retired_source_ids = set()
    if (data / "content_sources.json").is_file():
        manifest = _read(data / "content_sources.json")
        source_paths.add(data / "content_sources.json")
        for source in manifest.get("content_sources", []) + manifest.get("question_sources", []):
            path = data / source["path"]
            if not path.is_file() or not path.resolve().is_relative_to(data.resolve()):
                errors.append(f"目录作者源缺失或越界：{source['path']}")
                continue
            source_paths.add(path)
            if source.get("status") == "retired":
                retired_source_ids.update(q["id"] for q in _read(path).get("questions", []))
    topic_rows = _read(data / "topics.json")
    questions = _read(data / "questions.json")
    if not isinstance(topic_rows, list) or not isinstance(questions, list):
        raise CatalogError(["topics.json 和 questions.json 必须是数组"])
    for kind, rows in (("topic", topic_rows), ("question", questions)):
        for row in rows:
            errors.extend(validate_resource(kind, row, schema_dir=schema_dir))
    if errors:
        raise CatalogError(errors)
    topics = {row["id"]: row for row in topic_rows}
    if len(topics) != len(topic_rows):
        errors.append("教材节点 ID 重复")
    cards, units, projects = {}, {}, {}
    for folder, kind, target, id_key in (("teach", "teaching-card", cards, "topicId"),
                                          ("learning_units", "learning-unit", units, "id"),
                                          ("projects", "project", projects, "id")):
        for path in sorted((data / folder).glob("*.json")):
            row = _read(path)
            problems = validate_resource(kind, row, schema_dir=schema_dir)
            if problems:
                errors.extend(f"{path.name}: {problem}" for problem in problems)
                continue
            if row[id_key] in target:
                errors.append(f"{folder}: 重复资源 ID {row[id_key]}")
            target[row[id_key]] = row
            source_paths.add(path)
    if errors:
        raise CatalogError(errors)
    for tid in cards:
        if tid not in topics:
            errors.append(f"讲解卡引用未知教材节点 {tid}")
    for unit in units.values():
        for tid in unit["topic_ids"]:
            if tid not in topics:
                errors.append(f"{unit['id']}: 未知教材位置 {tid}")
    skills = copy.deepcopy(registry["skills"])
    objective_skill_ids = registry["objective_skill_ids"]
    placements, tasks, materials, families, source_index = {}, {}, {}, {}, {}
    used_bindings, used_examples, used_skills, used_objectives = set(), set(), set(), set()

    def skill_ref(sid, label):
        if sid not in skills:
            errors.append(f"{label}: 未注册技能 {sid}")
            return False
        used_skills.add(sid)
        return True

    for sid, skill in skills.items():
        if skill["id"] != sid:
            errors.append(f"技能字典键与 ID 不一致：{sid}")
        for ref in skill["prerequisite_skill_ids"] + skill["refines_skill_ids"]:
            if ref == sid or ref not in skills:
                errors.append(f"{sid}: 无效技能关系 {ref}")

    for tid, topic in topics.items():
        placement = registry["placements"].get(tid)
        if placement is None:
            errors.append(f"{tid}: 未注册教材位置映射；不能按位置自动生成技能 ID")
            continue
        ids = placement["criterion_skill_ids"]
        criteria = topic.get("mastery_criteria", [])
        if len(ids) != len(criteria):
            errors.append(f"{tid}: 目标数量已改变，需要显式更新 placement")
        for index, sid in enumerate(ids):
            if skill_ref(sid, tid) and index < len(criteria) and skills[sid]["statement"] != criteria[index]:
                errors.append(f"{tid}: 目标 {index} 已重排或改写，需核对稳定技能映射")
        placements[tid] = {**copy.deepcopy(placement), "skill_ids": list(ids),
                           "title": topic["name"], "stage": _stage(topic),
                           "curriculum_version": str(topic.get("curriculum_version", "unknown")),
                           "edition": topic.get("edition", "unknown"),
                           "volume": topic.get("volume", ""),
                           "standard_ids": [s["id"] for s in topic.get("standards", [])]}
    for tid in registry["placements"]:
        if tid not in topics:
            errors.append(f"placement 指向不存在的教材节点 {tid}")

    def objective_skills(resource):
        result = {}
        for objective in resource["objectives"]:
            oid = objective["id"]
            if oid in used_objectives:
                errors.append(f"跨资源目标 ID 重复：{oid}")
            used_objectives.add(oid)
            sid = objective_skill_ids.get(oid)
            if sid is None or not skill_ref(sid, oid):
                errors.append(f"目标 {oid} 尚未登记稳定技能 ID")
                continue
            if skills[sid]["statement"] != objective["statement"]:
                errors.append(f"{oid}: 目标文字已变化，需核对技能注册记录")
            if skills[sid]["granularity"] != objective.get("granularity", "composite"):
                errors.append(f"{oid}: 目标粒度与技能注册记录不一致")
            result[oid] = sid
        return result

    objective_maps = {identifier: objective_skills(resource)
                      for identifier, resource in [*units.items(), *projects.items()]}
    objective_owners = {o["id"]: resource["id"] for resource in [*units.values(), *projects.values()]
                        for o in resource["objectives"]}
    for oid in objective_skill_ids:
        if oid not in used_objectives:
            errors.append(f"技能目标映射已失去来源：{oid}")
    for resource in [*units.values(), *projects.values()]:
        prereq_ids = []
        for oid in resource.get("prerequisite_objective_ids", []):
            sid = objective_skill_ids.get(oid)
            if sid is not None:
                prereq_ids.append(sid)
                if objective_owners.get(oid) not in resource.get("prerequisite_unit_ids", []):
                    errors.append(f"{resource['id']}: 先修目标 {oid} 不属于声明的先修单元")
        for objective in resource["objectives"]:
            sid = objective_skill_ids.get(objective["id"])
            if sid not in skills:
                continue
            refinements = []
            for oid in objective.get("refines_objective_ids", []):
                ref = objective_skill_ids.get(oid)
                if ref is None or ref == sid:
                    errors.append(f"{objective['id']}: 无效细化目标 {oid}")
                else:
                    refinements.append(ref)
            skills[sid]["refines_skill_ids"] = sorted(set(skills[sid]["refines_skill_ids"] + refinements))
            skills[sid]["prerequisite_skill_ids"] = sorted(set(skills[sid]["prerequisite_skill_ids"] + prereq_ids))

    def diagram_assets(value, label):
        assets = {}
        def visit(item):
            if isinstance(item, dict):
                if isinstance(item.get("src"), str) and item["src"].startswith("/static/diagrams/"):
                    path = data.parent / "web" / item["src"].lstrip("/")
                    if not path.is_file() or not path.resolve().is_relative_to((data.parent / "web/static/diagrams").resolve()):
                        errors.append(f"{label}: 无效图示 {item['src']}")
                    else:
                        source_paths.add(path)
                        assets[item["src"]] = hashlib.sha256(path.read_bytes()).hexdigest()
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
        visit(value)
        return assets

    def add_family(fid, status, source_key):
        declared = bindings["families"].get(fid)
        record = copy.deepcopy(declared) if declared else {
            "id": fid, "status": status,
            "basis": "author_declared" if status == "declared" else "unclassified",
            "description": "作者声明的任务结构族。" if status == "declared" else
                           "尚未完成数学结构分族；ID 不同不构成独立任务证据。"}
        if record["status"] != status:
            errors.append(f"{source_key}: 题族声明状态矛盾 {fid}")
        if fid in families and families[fid]["status"] != record["status"]:
            errors.append(f"{source_key}: 同一题族状态冲突 {fid}")
        if fid not in families:
            families[fid] = {**record, "task_ids": [], "material_ids": [],
                             "independence_established": False}

    def metadata(key, raw):
        registered = bindings["tasks"].get(key)
        if registered is None:
            errors.append(f"任务 {key} 尚未注册；编译器不会推断评分契约或生成 ID")
            return None
        used_bindings.add(key)
        if key.startswith("project_step:") and registered.get("source_anchor") != raw.get("title"):
            errors.append(f"{key}: 项目步骤已改名或重排，需显式核对固定任务映射")
        result = copy.deepcopy(registered)
        result.pop("source_anchor", None)
        contract_source = result.pop("answer_spec_source")
        if contract_source == "author":
            if "answer_spec" not in raw:
                errors.append(f"{key}: 指定的作者答案契约缺失")
                return None
            result["answer_spec"] = copy.deepcopy(raw["answer_spec"])
        elif "answer_spec" in raw:
            errors.append(f"{key}: 契约已归 binding，作者文件不能再保存第二份 answer_spec")
        # Current author declarations supersede the initial migration record.
        for field in ("family_id", "variant_id", "representation", "cognitive_demand"):
            if field in raw:
                result[field] = copy.deepcopy(raw[field])
        if "family_id" in raw:
            result["family_status"] = "declared"
        for message in validate_answer_spec(result["answer_spec"]):
            errors.append(f"{key}: {message}")
        return result

    def add_task(key, raw, *, source_kind, path, legacy_id, locator, material_id,
                 topic_ids, skill_ids, alignment, purpose, policy=None):
        meta = metadata(key, raw)
        if meta is None:
            return None
        tid = meta["id"]
        if source_kind in ("bank", "unit") and raw.get("id") != tid:
            errors.append(f"{key}: 旧任务 ID 不得重编号")
        if tid in tasks:
            errors.append(f"任务 ID 重复：{tid}")
            return None
        for sid in skill_ids:
            skill_ref(sid, tid)
        task = copy.deepcopy(raw)
        task.update(meta)
        if task["type"] != "rubric":
            checked = grade_answer(task, task.get("answer"))
            if checked["status"] != "correct":
                errors.append(f"{tid}: 参考答案不满足显式评分契约 ({checked['status']})")
        if policy is None:
            policy = {"practice_eligible": True, "diagnosis_eligible": False,
                      "may_confirm_mastery": False, "may_judge_weak": False,
                      "reviewer_pending": True, "reason": "authored_learning_task"}
        policy = copy.deepcopy(policy)
        policy["independent_evidence"] = False
        if not policy["practice_eligible"]:
            purpose = []
        task.update(resource_id="task:" + tid, source_kind=source_kind,
                    source_ref={"path": path, "legacy_id": legacy_id, "locator": str(locator)},
                    material_id=material_id, topic_ids=list(topic_ids),
                    skill_ids=sorted(set(skill_ids)), alignment=alignment,
                    purpose=sorted(set(purpose)), policy=policy,
                    availability="active" if policy["practice_eligible"] else "quarantined")
        task["resource_version"] = content_hash({"task": task, "diagram_assets": diagram_assets(task, tid),
                                                  "skills": {sid: skills[sid] for sid in task["skill_ids"] if sid in skills}})
        tasks[tid], source_index[key] = task, tid
        add_family(task["family_id"], task["family_status"], key)
        families[task["family_id"]]["task_ids"].append(tid)
        return tid

    def example_families(key, example):
        registered = bindings["examples"].get(key)
        if registered is None:
            errors.append(f"例题 {key} 尚未注册曝光绑定")
            return []
        used_examples.add(key)
        fids = [example["family_id"]] if example.get("family_id") else registered["family_ids"]
        status = "declared" if example.get("family_id") else registered["family_status"]
        for fid in fids:
            add_family(fid, status, key)
        return fids

    def add_material(mid, kind, raw, *, task_ids, reference_families, topic_ids):
        reference_families = sorted(set(reference_families))
        task_ids = [tid for tid in task_ids if tid is not None]
        material = {"id": mid, "kind": kind, "title": raw.get("title", raw.get("name", "")),
                    "topic_ids": list(topic_ids), "task_ids": task_ids,
                    "family_ids": reference_families,
                    "assessment_family_ids": sorted({tasks[t]["family_id"] for t in task_ids}),
                    "reference_scope": "provided_examples_and_self_check_references"}
        assets = diagram_assets(raw, mid)
        material["resource_version"] = content_hash({"content": raw, "catalog": material,
                                                     "diagram_assets": assets,
                                                     "task_versions": {tid: tasks[tid]["resource_version"] for tid in task_ids}})
        materials[mid] = material
        for fid in reference_families:
            families[fid]["material_ids"].append(mid)
        return material

    topic_task_ids = defaultdict(list)
    policy_changes, original_bank_policy, compiled_bank_policy = [], Counter(), Counter()
    for question in questions:
        qid, tid = question["id"], question["topicId"]
        indices = question.get("tested_criterion_indices", [])
        ids = []
        for index in indices:
            if tid not in placements or index >= len(placements[tid]["criterion_skill_ids"]):
                errors.append(f"{qid}: 不存在的目标下标 {index}")
            else:
                ids.append(placements[tid]["criterion_skill_ids"][index])
        meta = metadata("bank:" + qid, question)
        policy = question_policy({**question, "answer_spec": meta["answer_spec"]}) if meta else {}
        if not meta:
            continue
        if qid in retired_source_ids and (policy["practice_eligible"] or policy["diagnosis_eligible"]):
            errors.append(f"{qid}: 退役题源仍声明可练或可诊断，必须核对准入依据")
        original_policy = question_policy(question)
        for field in ("practice_eligible", "diagnosis_eligible"):
            original_bank_policy[field] += original_policy[field]
            compiled_bank_policy[field] += policy[field]
            if policy[field] and not original_policy[field]:
                errors.append(f"{qid}: 元数据迁移不能自动扩大 {field}，请在题目作者源明确修订契约与准入依据")
        if original_policy != policy:
            policy_changes.append({"task_id": qid, "before": original_policy, "after": policy})
        purpose = (["practice"] if policy["practice_eligible"] else []) + (["check"] if policy["diagnosis_eligible"] else [])
        item = add_task("bank:" + qid, question, source_kind="bank", path="data/questions.json",
                        legacy_id=qid, locator=qid, material_id="topic:" + tid,
                        topic_ids=question.get("topicIds") or [tid], skill_ids=ids,
                        alignment="explicit_component" if ids else "topic_only", purpose=purpose, policy=policy)
        topic_task_ids[tid].append(item)

    for tid, topic in topics.items():
        card = cards.get(tid)
        mid = "topic:" + tid
        reference_families = []
        card_tasks = list(topic_task_ids[tid])
        if card:
            reference_families += example_families("example:topic:" + tid, card["worked_example"])
            for row in card.get("objective_coverage", []):
                index = row["criterion_index"]
                check = row["self_check"]
                sid = placements.get(tid, {}).get("criterion_skill_ids", [])
                if index >= len(sid):
                    errors.append(f"{tid}: 自检引用未知目标 {index}")
                    continue
                raw = {"type": "rubric", "prompt": check["prompt"], "reference_answer": check["reference_answer"],
                       "rubric": [{"criterion": c, "points": 1, "example": check["reference_answer"]}
                                  for c in check["criteria"]],
                       "explanation": row["explanation"], "criterion_index": index}
                task_id = add_task(_task_key("self_check", tid, index), raw, source_kind="self_check",
                                   path=f"data/teach/{tid}.json", legacy_id=tid, locator=index,
                                   material_id=mid, topic_ids=[tid], skill_ids=[sid[index]],
                                   alignment="explicit_component", purpose=["self_check"])
                card_tasks.append(task_id)
                if task_id:
                    reference_families.append(tasks[task_id]["family_id"])
        add_material(mid, "topic", card or topic, task_ids=card_tasks,
                     reference_families=reference_families, topic_ids=[tid])

    compiled_units = {}
    for uid, unit in units.items():
        mid = "unit:" + uid
        mapped = objective_maps[uid]
        unit_tasks = []
        for task in unit["tasks"]:
            ids = [mapped[o] for o in task["objective_ids"] if o in mapped]
            if len(ids) != len(task["objective_ids"]):
                errors.append(f"{task['id']}: 引用了未知单元目标")
            task_id = add_task(_task_key("unit", uid, task["id"]), task, source_kind="unit",
                               path=f"data/learning_units/{uid}.json", legacy_id=task["id"], locator=task["id"],
                               material_id=mid, topic_ids=unit["topic_ids"], skill_ids=ids,
                               alignment="explicit", purpose=[task["phase"]])
            unit_tasks.append(task_id)
        reference_families = []
        for index, example in enumerate(unit["worked_examples"]):
            reference_families += example_families(_task_key("example:unit", uid, index), example)
        material = add_material(mid, "unit", unit, task_ids=unit_tasks,
                                reference_families=reference_families, topic_ids=unit["topic_ids"])
        prereq_units = unit.get("prerequisite_unit_ids", [])
        for ref in prereq_units:
            if ref == uid or ref not in units:
                errors.append(f"{uid}: 不存在或自指的先修单元 {ref}")
        prereq_skills = []
        for oid in unit.get("prerequisite_objective_ids", []):
            sid = objective_skill_ids.get(oid)
            if sid is None or oid in mapped:
                errors.append(f"{uid}: 无效先修目标 {oid}")
            else:
                prereq_skills.append(sid)
        compiled_units[uid] = {k: copy.deepcopy(unit[k]) for k in
                               ("id", "title", "stage", "topic_ids", "objectives", "standard_ids")}
        for objective in compiled_units[uid]["objectives"]:
            if objective["id"] in mapped:
                objective["skill_id"] = mapped[objective["id"]]
                objective["granularity"] = skills[mapped[objective["id"]]]["granularity"]
        compiled_units[uid].update(resource_id=mid, resource_version=material["resource_version"],
                                   skill_ids=sorted(mapped.values()), task_ids=[x for x in unit_tasks if x],
                                   prerequisite_unit_ids=list(prereq_units),
                                   prerequisite_skill_ids=sorted(set(prereq_skills)))
        compiled_units[uid]["tasks"] = [public_task(tasks[x]) for x in unit_tasks if x]

    compiled_projects = {}
    for pid, project in projects.items():
        mid, mapped = "project:" + pid, objective_maps[pid]
        task_ids = []
        for index, step in enumerate(project["steps"]):
            objective_ids = step["objective_ids"]
            rubric = [copy.deepcopy(row) for row in project["rubric"]
                      if set(row["objective_ids"]) & set(objective_ids)]
            raw = {"type": "rubric", "prompt": step["instructions"], "title": step["title"],
                   "deliverable": step["deliverable"], "objective_ids": objective_ids,
                   "rubric": rubric, "explanation": project["assessment_note"]}
            task_id = add_task(_task_key("project_step", pid, index), raw, source_kind="project_step",
                               path=f"data/projects/{pid}.json", legacy_id=pid, locator=index,
                               material_id=mid, topic_ids=[], skill_ids=[mapped[o] for o in objective_ids if o in mapped],
                               alignment="explicit", purpose=["project"])
            task_ids.append(task_id)
        material = add_material(mid, "project", project, task_ids=task_ids,
                                reference_families=[], topic_ids=[])
        compiled_projects[pid] = {k: copy.deepcopy(project[k]) for k in ("id", "title", "stage", "objectives")}
        compiled_projects[pid].update(resource_id=mid, resource_version=material["resource_version"],
                                     task_ids=[x for x in task_ids if x], skill_ids=sorted(mapped.values()))

    for key in set(bindings["tasks"]) - used_bindings:
        errors.append(f"任务注册失去来源：{key}；删除应显式登记迁移，不能默默漂移")
    for key in set(bindings["examples"]) - used_examples:
        errors.append(f"例题注册失去来源：{key}")
    for sid in set(skills) - used_skills:
        errors.append(f"技能注册失去来源：{sid}")
    for uid in compiled_units:
        _check_cycle(uid, compiled_units, "prerequisite_unit_ids", errors)
    for sid in skills:
        _check_cycle(sid, skills, "prerequisite_skill_ids", errors)
        _check_cycle(sid, skills, "refines_skill_ids", errors)
    if errors:
        raise CatalogError(errors)
    for family in families.values():
        family["task_ids"] = sorted(set(family["task_ids"]))
        family["material_ids"] = sorted(set(family["material_ids"]))
    source_hashes = {str(path.relative_to(data.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted(source_paths)}
    coverage = coverage_report(skills, tasks, families, placements)
    coverage["bank_policy_counts"] = {"original": dict(original_bank_policy), "compiled": dict(compiled_bank_policy)}
    coverage["policy_migration_changes"] = policy_changes
    result = {"schema_version": CATALOG_VERSION, "subject": "mathematics", "skills": skills,
              "placements": placements, "tasks": tasks, "materials": materials, "families": families,
              "units": compiled_units, "projects": compiled_projects, "source_index": source_index,
              "source_hashes": source_hashes, "coverage": coverage}
    result["content_sha256"] = content_hash(result)
    errors = validate_resource("catalog", result, schema_dir=schema_dir)
    if errors:
        raise CatalogError(errors)
    return result


def _check_cycle(start, rows, key, errors):
    visited, path = set(), set()
    def visit(identifier):
        if identifier in path:
            errors.append(f"{key}: 存在循环关系，涉及 {identifier}")
            return
        if identifier in visited or identifier not in rows:
            return
        path.add(identifier)
        for ref in rows[identifier].get(key, []):
            visit(ref)
        path.remove(identifier)
        visited.add(identifier)
    visit(start)


def public_task(task):
    """Shared public task shape, without answers, examples or author checks."""
    fields = ("id", "resource_id", "resource_version", "source_kind", "material_id",
              "type", "prompt", "title", "deliverable", "choices", "diagram", "phase",
              "objective_ids", "skill_ids", "topic_ids", "alignment", "purpose", "family_id",
              "variant_id", "family_status", "representation", "cognitive_demand", "availability")
    shown = {k: copy.deepcopy(task[k]) for k in fields if k in task}
    if "rubric" in task:
        shown["rubric"] = [{k: copy.deepcopy(row[k]) for k in ("criterion", "points", "objective_ids") if k in row}
                           for row in task["rubric"]]
    return shown


def coverage_report(skills, tasks, families, placements):
    active = [task for task in tasks.values() if task["availability"] == "active"]
    mapped = {sid for task in active for sid in task["skill_ids"]}
    by_granularity = {}
    for name in ("legacy_composite", "composite", "observable"):
        ids = {sid for sid, skill in skills.items() if skill["granularity"] == name}
        by_granularity[name] = {"registered": len(ids), "with_explicit_tasks": len(ids & mapped),
                                "without_explicit_tasks": len(ids - mapped)}
    return {"skill_granularity": by_granularity,
            "task_source_counts": dict(sorted(Counter(t["source_kind"] for t in tasks.values()).items())),
            "task_alignment_counts": dict(sorted(Counter(t["alignment"] for t in active).items())),
            "answer_kind_counts": dict(sorted(Counter(t["answer_spec"]["kind"] for t in tasks.values()).items())),
            "active_tasks": len(active), "quarantined_tasks": len(tasks) - len(active),
            "declared_families": sum(f["status"] == "declared" for f in families.values()),
            "unclassified_families": sum(f["status"] == "unclassified" for f in families.values()),
            "topic_placements": len(placements),
            "independent_mastery_evidence": False,
            "limitations": ["旧目录目标保留复合粒度；注册数量不是细技能课程覆盖率。",
                            "topic_only 任务没有自动绑定目标，不能据此计算技能覆盖。",
                            "未分类题族与不同任务 ID 不构成独立任务证据。",
                            "量规、自评与作答完成记录均不自动证明掌握。"]}
