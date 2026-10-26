#!/usr/bin/env python3
"""Register new catalog identities without reallocating any existing identity.

New question/unit tasks require an authored answer_spec, family_id and
variant_id. Existing legacy contracts stay in the fixed migration registry.
Self-checks and project steps use the explicit process-rubric adapter; neither
is converted into an automatic correctness or mastery judgment.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cnkg.catalog import CatalogError, _read, _stage  # noqa: E402
from cnkg.contracts import ContractDependencyError, validate_resource  # noqa: E402
from tools.compile_catalog import _write  # noqa: E402


def register_new_sources(data_dir):
    """Return proposed registries and a change list; do not write anything."""
    data = Path(data_dir)
    registry = _read(data / "skills/registry.json")
    bindings = _read(data / "skills/catalog-bindings.json")
    errors, changes = [], {"skills": [], "tasks": [], "examples": []}

    def new_skill(statement, granularity, stage, domain):
        sid = "sk_" + uuid.uuid4().hex[:20]
        registry["skills"][sid] = {"id": sid, "statement": statement, "granularity": granularity,
                                   "stage": stage, "domain": domain,
                                   "prerequisite_skill_ids": [], "refines_skill_ids": []}
        changes["skills"].append(sid)
        return sid

    def check_existing_skill(sid, statement, granularity, label):
        skill = registry["skills"].get(sid)
        if skill is None or skill["statement"] != statement or skill["granularity"] != granularity:
            errors.append(f"{label}: 既有目标被改写或重排，不能自动重编号；请显式核对技能映射")

    def task_binding(key, raw, identifier=None, *, rubric_adapter=False):
        if key in bindings["tasks"]:
            return
        if rubric_adapter:
            spec = {"version": 1, "kind": "rubric"}
            identifier = ("sc_" if key.startswith("self_check:") else "ps_") + uuid.uuid4().hex[:20]
        else:
            for field in ("answer_spec", "family_id", "variant_id"):
                if field not in raw:
                    errors.append(f"{key}: 新任务须显式提供 {field}，不从题干猜测")
            if any(field not in raw for field in ("answer_spec", "family_id", "variant_id")):
                return
            spec = raw["answer_spec"]
        fid = raw.get("family_id", "unclassified:" + identifier)
        bindings["tasks"][key] = {"id": identifier, "answer_spec_source": "binding" if rubric_adapter else "author",
                                  "family_id": fid, "variant_id": raw.get("variant_id", identifier),
                                  "family_status": "declared" if "family_id" in raw else "unclassified",
                                  "representation": raw.get("representation", "unknown"),
                                  "cognitive_demand": raw.get("cognitive_demand", "unknown")}
        if rubric_adapter:
            bindings["tasks"][key]["answer_spec"] = copy.deepcopy(spec)
        if key.startswith("project_step:"):
            bindings["tasks"][key]["source_anchor"] = raw["title"]
        changes["tasks"].append(identifier)

    def example_binding(key, raw):
        if key in bindings["examples"]:
            return
        if not raw.get("family_id") or not raw.get("variant_id"):
            errors.append(f"{key}: 新例题须显式提供 family_id 和 variant_id，用于材料曝光")
            return
        bindings["examples"][key] = {"family_ids": [raw["family_id"]], "family_status": "declared"}
        changes["examples"].append(key)

    for topic in _read(data / "topics.json"):
        errors.extend(validate_resource("topic", topic))
        tid = topic["id"]
        placement = registry["placements"].setdefault(tid, {"topic_id": tid, "criterion_skill_ids": []})
        ids = placement["criterion_skill_ids"]
        for index, statement in enumerate(topic["mastery_criteria"]):
            if index < len(ids):
                check_existing_skill(ids[index], statement, "legacy_composite", f"{tid}/{index}")
            else:
                ids.append(new_skill(statement, "legacy_composite", _stage(topic), topic.get("domain", "数学")))
        if len(ids) != len(topic["mastery_criteria"]):
            errors.append(f"{tid}: 不自动删除既有技能或位置映射")
    for question in _read(data / "questions.json"):
        errors.extend(validate_resource("question", question))
        task_binding("bank:" + question["id"], question, question["id"])
    for path in sorted((data / "teach").glob("*.json")):
        card = _read(path)
        errors.extend(validate_resource("teaching-card", card))
        tid = card["topicId"]
        for row in card.get("objective_coverage", []):
            task_binding(f"self_check:{tid}:{row['criterion_index']}", row["self_check"], rubric_adapter=True)
        example_binding("example:topic:" + tid, card["worked_example"])
    for folder, kind in (("learning_units", "learning-unit"), ("projects", "project")):
        for path in sorted((data / folder).glob("*.json")):
            resource = _read(path)
            errors.extend(validate_resource(kind, resource))
            rid = resource["id"]
            for objective in resource["objectives"]:
                oid, granularity = objective["id"], objective.get("granularity", "composite")
                sid = registry["objective_skill_ids"].get(oid)
                if sid:
                    check_existing_skill(sid, objective["statement"], granularity, oid)
                else:
                    registry["objective_skill_ids"][oid] = new_skill(objective["statement"], granularity,
                                                                     resource["stage"], "数学" if kind == "learning-unit" else "综合实践")
            if kind == "learning-unit":
                for task in resource["tasks"]:
                    task_binding(f"unit:{rid}:{task['id']}", task, task["id"])
                for index, example in enumerate(resource["worked_examples"]):
                    example_binding(f"example:unit:{rid}:{index}", example)
            else:
                for index, step in enumerate(resource["steps"]):
                    task_binding(f"project_step:{rid}:{index}", step, rubric_adapter=True)
    errors.extend(validate_resource("skills-registry", registry))
    errors.extend(validate_resource("catalog-bindings", bindings))
    if errors:
        raise CatalogError(errors)
    return registry, bindings, changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        registry, bindings, changes = register_new_sources(args.data_dir)
        counts = {key: len(value) for key, value in changes.items()}
        if args.check and any(counts.values()):
            print(f"尚待显式登记：{counts}；审查来源后运行 --write。", file=sys.stderr)
            return 1
        if args.write and any(counts.values()):
            _write(args.data_dir / "skills/registry.json", registry)
            _write(args.data_dir / "skills/catalog-bindings.json", bindings)
        print(f"登记{'检查通过' if args.check else '完成'}：新增 {counts}；既有 ID 保持不变。")
        return 0
    except (CatalogError, ContractDependencyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
