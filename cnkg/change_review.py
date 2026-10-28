"""Content-bound semantic change inventory from real Git objects and sources.

The inventory proposes review and preview work. It never approves mathematical
semantics, independent teacher review or student outcomes automatically.
"""
from __future__ import annotations

import ast
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

PREFIXES = ("data", "schema", "cnkg", "web", "tools")
DERIVED = {"data/catalog.json", "data/release_manifest.json"}
PRESERVATION_POLICY = {
    "sessions": "保留原会话ID及实际已保存的冻结内容、原始作答、评分版本和原始评分；缺失原快照或汇总时明确显示缺失，不用当前题目或引擎补算，新内容不追溯重判旧作答。",
    "works": "保留实际已保存的作品版本、修订和帮助记录；只显示原记录中存在的题面、量规、提示和参考，缺失字段不从新版补入，新量规另建版本。",
    "unresolved_needs": "新会话、阶段或版本不得清除旧事项；只允许有具体后续证据的解决或带理由的显式暂缓。",
    "identity": "不得按标题相似度重编号或猜测目标等价；删除、拆分、合并需显式映射及旧资源查看路径。",
    "review_evidence": "旧的内容审核与预览只绑定旧内容指纹，不能自动转移到修改后的资源。",
}


def _git(root, *args, missing_ok=False):
    result = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode and not missing_ok:
        raise ValueError(result.stderr.decode("utf-8", "replace").strip() or "Git读取失败")
    return result.stdout if result.returncode == 0 else None


def _digest(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _json(value):
    try:
        return json.loads(value) if value is not None else None
    except (ValueError, UnicodeDecodeError):
        return None


def _escape(part):
    return str(part).replace("~", "~0").replace("/", "~1")


def _changes(before, after, pointer="", *, before_exists=True, after_exists=True):
    if not before_exists or not after_exists:
        yield {"pointer": pointer or "/", "operation": "add" if after_exists else "remove",
               "before": before if before_exists else None, "after": after if after_exists else None}
    elif type(before) is type(after) and isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            yield from _changes(before.get(key), after.get(key), pointer + "/" + _escape(key),
                                before_exists=key in before, after_exists=key in after)
    elif type(before) is type(after) and isinstance(before, list):
        for index in range(max(len(before), len(after))):
            yield from _changes(before[index] if index < len(before) else None,
                                after[index] if index < len(after) else None, pointer + "/" + str(index),
                                before_exists=index < len(before), after_exists=index < len(after))
    elif before != after or type(before) is not type(after):
        yield {"pointer": pointer or "/", "operation": "replace", "before": before, "after": after}


def _categories(pointer, path, *values):
    tokens = set(pointer.replace("/", " ").split())
    def collect(value):
        if isinstance(value, dict):
            tokens.update(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    for value in values:
        collect(value)
    kinds = set()
    fields = {
        "objectives": {"objectives", "mastery_criteria", "objective_ids", "skill_ids", "criterion_index", "tested_criterion_indices", "placements", "refines_skill_ids"},
        "conditions": {"conditions", "prerequisites", "prerequisite_skill_ids", "prerequisite_objective_ids", "prerequisite_unit_ids", "prerequisite_topic_ids", "when_to_use", "counterexample"},
        "answers_and_criteria": {"answer", "reference_answer", "verify", "answer_spec", "criteria", "rubric", "normalized_answer"},
        "representations": {"representation", "representation_note", "diagram", "alt", "src"},
        "task_families": {"family_id", "family_ids", "family_status", "variant_id", "reference_family_ids", "reference_families_by_key"},
        "instructional_support": {"instructional_support", "difficulty_support", "diagnostic", "guided_task", "feedback", "hints", "followup_task_ids", "discriminating_prompt", "revision_prompt"},
        "teaching_process": {"explanation", "definition", "why", "reasoning", "intuition", "steps", "worked_example", "worked_examples", "self_check", "prompt", "pitfalls", "task_brief", "deliverable"},
        "scope_and_identity": {"role", "tier", "goals", "id", "topicId", "topic_ids", "source_tier", "completion_requirement"},
        "review_claims": {"review_status", "teacher_review", "content_revision", "reviewed_sha256", "assessment_note", "observation_note", "alignment_note", "target_alignment"},
    }
    for name, names in fields.items():
        if tokens & names:
            kinds.add(name)
    if path.startswith("schema/"):
        kinds.add("schema_contract")
    if path.endswith(".svg"):
        kinds.add("representations")
    if path.startswith(("cnkg/", "web/", "tools/")) and not path.endswith(".svg"):
        kinds.add("runtime_or_workflow")
    return sorted(kinds or {"metadata_or_other"})


def _format_only(before, after, path):
    if before is None or after is None:
        return False
    if path.endswith(".json"):
        a, b = _json(before), _json(after)
        # Python considers True == 1 == 1.0, including inside containers.
        # Their JSON types differ and must not skip semantic review.
        return a is not None and b is not None and not any(_changes(a, b))
    if path.endswith(".py"):
        try:
            return ast.dump(ast.parse(before), include_attributes=False) == ast.dump(ast.parse(after), include_attributes=False)
        except (SyntaxError, UnicodeDecodeError):
            return False
    if path.endswith(".svg"):
        def shape(node):
            text = node.text if node.text and node.text.strip() else ""
            tail = node.tail if node.tail and node.tail.strip() else ""
            return node.tag, sorted(node.attrib.items()), text, tail, [shape(child) for child in node]
        try:
            return shape(ET.fromstring(before)) == shape(ET.fromstring(after))
        except ET.ParseError:
            return False
    return before == after


def _index(catalog, registry):
    result = {"by_path": defaultdict(lambda: {"skills": set(), "tasks": set(), "materials": set()}),
              "by_id": defaultdict(lambda: {"skills": set(), "tasks": set(), "materials": set()}),
              "dependents": defaultdict(set), "skill_materials": defaultdict(set), "material_topics": defaultdict(set),
              "topics": {}, "objectives": registry.get("objective_skill_ids", {}), "skills": set(registry.get("skills", {}))}

    def add(bucket, skills=(), tasks=(), materials=()):
        for key, values in (("skills", skills), ("tasks", tasks), ("materials", materials)):
            bucket[key].update(value for value in values if value)
    for tid, placement in registry.get("placements", {}).items():
        skills = placement.get("criterion_skill_ids", [])
        mid = "topic:" + tid
        result["topics"][tid] = skills
        add(result["by_id"][tid], skills=skills, materials=[mid])
        add(result["by_path"][f"data/teach/{tid}.json"], skills=skills, materials=[mid])
        for sid in skills:
            result["skill_materials"][sid].add(mid)
        result["material_topics"][mid].add(tid)
    for oid, sid in result["objectives"].items():
        add(result["by_id"][oid], skills=[sid])
    for sid, skill in {**registry.get("skills", {}), **catalog.get("skills", {})}.items():
        result["skills"].add(sid)
        add(result["by_id"][sid], skills=[sid])
        for predecessor in skill.get("prerequisite_skill_ids", []) + skill.get("refines_skill_ids", []):
            result["dependents"][predecessor].add(sid)
    for tid, task in catalog.get("tasks", {}).items():
        skills, mid = task.get("skill_ids", []), task.get("material_id")
        add(result["by_id"][tid], skills=skills, tasks=[tid], materials=[mid])
        path = task.get("source_ref", {}).get("path")
        if path:
            add(result["by_path"][path], skills=skills, tasks=[tid], materials=[mid])
        for sid in skills:
            result["skill_materials"][sid].add(mid)
    for kind, folder in (("units", "learning_units"), ("projects", "projects")):
        prefix = "unit:" if kind == "units" else "project:"
        for rid, resource in catalog.get(kind, {}).items():
            mid, skills = prefix + rid, resource.get("skill_ids", [])
            tasks = resource.get("task_ids", [])
            add(result["by_id"][rid], skills=skills, tasks=tasks, materials=[mid])
            add(result["by_path"][f"data/{folder}/{rid}.json"], skills=skills, tasks=tasks, materials=[mid])
            result["material_topics"][mid].update(resource.get("topic_ids", []))
            for sid in skills:
                result["skill_materials"][sid].add(mid)
    return result


def _merge_impact(target, other):
    for field in ("skills", "tasks", "materials"):
        target[field].update(other.get(field, ()))


def _family_ids(value):
    if isinstance(value, dict):
        found = {value["family_id"]} if isinstance(value.get("family_id"), str) else set()
        for child in value.values():
            found.update(_family_ids(child))
        return found
    if isinstance(value, list):
        return set().union(*(_family_ids(child) for child in value)) if value else set()
    return set()


def _identified_nodes(value, pointer, index):
    """Use real enclosing IDs; never infer skills from a title or file order."""
    nodes = [value]
    current = value
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(token)] if isinstance(current, list) else current[token]
        except (ValueError, IndexError, KeyError, TypeError):
            break
        nodes.append(current)
    for node in reversed(nodes):
        if isinstance(node, dict):
            identities = [node.get("id"), node.get("topicId"), node.get("topic_id"), node.get("objective_id")]
            matched = [identity for identity in identities if isinstance(identity, str) and identity in index["by_id"]]
            if matched:
                return matched
    return [token for token in pointer.split("/") if token in index["by_id"]]


def review_changes(root, base, *, preview_base_url="http://127.0.0.1:8765"):
    root = Path(root).resolve()
    commit = _git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    names = _git(root, "diff", "--name-only", "-z", "--no-renames", commit, "--", *PREFIXES).split(b"\0")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *PREFIXES).split(b"\0")
    paths = sorted({path.decode("utf-8") for path in names + untracked if path})
    untracked_set = {path.decode("utf-8") for path in untracked if path}
    before_cache, after_cache = {}, {}

    def before(path):
        if path not in before_cache:
            before_cache[path] = _git(root, "show", f"{commit}:{path}", missing_ok=True)
        return before_cache[path]

    def after(path):
        if path not in after_cache:
            file = root / path
            after_cache[path] = file.read_bytes() if file.is_file() and not file.is_symlink() else None
        return after_cache[path]

    old_catalog, new_catalog = _json(before("data/catalog.json")) or {}, _json(after("data/catalog.json")) or {}
    old_registry, new_registry = _json(before("data/skills/registry.json")) or {}, _json(after("data/skills/registry.json")) or {}
    indexes = [_index(old_catalog, old_registry), _index(new_catalog, new_registry)]
    changed, overall = [], {field: set() for field in ("skills", "tasks", "materials")}
    unresolved_bindings = []
    for path in paths:
        old_raw, new_raw = before(path), after(path)
        old, new = _json(old_raw), _json(new_raw)
        kind = "added" if old_raw is None else "deleted" if new_raw is None else "modified"
        format_only = _format_only(old_raw, new_raw, path)
        derived = path in DERIVED
        if path.endswith(".json") and not derived:
            deltas = list(_changes(old, new, before_exists=old_raw is not None, after_exists=new_raw is not None))
        else:
            deltas = [] if format_only else [{"pointer": "/", "operation": kind,
                                              "before": None, "after": None, "detail": "请查看绑定的源文件差异"}]
        for delta in deltas:
            delta["categories"] = _categories(delta["pointer"], path, delta.get("before"), delta.get("after"))
        categories = sorted({category for delta in deltas for category in delta["categories"]})
        impact = {field: set() for field in ("skills", "tasks", "materials")}
        related_families = set()
        if not format_only and not derived:
            related_families = _family_ids(old) | _family_ids(new)
            for index, value in zip(indexes, (old, new)):
                found = False
                for delta in deltas:
                    for identity in _identified_nodes(value, delta["pointer"], index):
                        _merge_impact(impact, index["by_id"][identity])
                        found = True
                if not found or path.startswith(("data/teach/", "data/learning_units/", "data/projects/")):
                    _merge_impact(impact, index["by_path"].get(path, {}))
                # New author resources may precede catalog compilation. Retain
                # existing stable objective identities and report missing task
                # registrations rather than inventing IDs.
                if isinstance(value, dict) and value.get("id") and path.startswith(("data/learning_units/", "data/projects/")):
                    prefix = "unit:" if path.startswith("data/learning_units/") else "project:"
                    impact["materials"].add(prefix + value["id"])
                    for objective in value.get("objectives", []):
                        sid = index["objectives"].get(objective["id"])
                        if sid:
                            impact["skills"].add(sid)
                        else:
                            unresolved_bindings.append({"path": path, "objective_id": objective["id"], "reason": "缺稳定技能登记，不能猜测目标等价"})
                    impact["tasks"].update(task["id"] for task in value.get("tasks", []) if task.get("id"))
                if isinstance(value, dict) and value.get("topicId"):
                    tid = value["topicId"]
                    impact["materials"].add("topic:" + tid)
                    impact["skills"].update(index["topics"].get(tid, []))
                    for row in value.get("objective_coverage", []):
                        key = f"self_check:{tid}:{row.get('criterion_index')}"
                        catalog = old_catalog if index is indexes[0] else new_catalog
                        task_id = catalog.get("source_index", {}).get(key)
                        if task_id:
                            impact["tasks"].add(task_id)
                        else:
                            unresolved_bindings.append({"path": path, "source_ref": key, "reason": "自检尚未出现在此版本catalog；需登记并编译后确认稳定任务ID"})
            if path.endswith(".svg"):
                asset = "/static/" + path.removeprefix("web/static/")
                listed = _git(root, "grep", "-l", "-F", asset, commit, "--", "data/teach", "data/learning_units", "data/projects", missing_ok=True) or b""
                refs = {line.decode().split(":", 1)[1] for line in listed.splitlines() if b":" in line}
                for folder in ("data/teach", "data/learning_units", "data/projects"):
                    for source in (root / folder).glob("*.json"):
                        if asset in source.read_text(encoding="utf-8"):
                            refs.add(source.relative_to(root).as_posix())
                for source in refs:
                    for index in indexes:
                        _merge_impact(impact, index["by_path"].get(source, {}))
            if path.startswith(("cnkg/", "web/", "schema/", "tools/")) and not path.endswith(".svg"):
                categories = sorted(set(categories) | {"runtime_or_contract_preview"})
            # A reference/example can expose sibling tasks without changing
            # their text. Inspect those tasks' independent-evidence boundaries.
            for catalog, index in zip((old_catalog, new_catalog), indexes):
                for task_id, task in catalog.get("tasks", {}).items():
                    if task.get("family_status") == "declared" and task.get("family_id") in related_families:
                        _merge_impact(impact, index["by_id"][task_id])
        _merge_impact(overall, impact)
        change_id = "change_" + hashlib.sha256(json.dumps([path, _digest(old_raw), _digest(new_raw)], separators=(",", ":")).encode()).hexdigest()[:24]
        changed.append({"change_id": change_id, "path": path, "operation": kind, "untracked_new_source": path in untracked_set,
            "before_sha256": _digest(old_raw), "after_sha256": _digest(new_raw), "format_only": format_only,
            "derived_output": derived, "categories": categories, "field_changes": deltas,
            "related_family_ids": sorted(related_families),
            "impact": {key: sorted(values) for key, values in impact.items()},
            "semantic_review_required": not format_only and not derived,
            "automatic_semantic_approval": False})
    dependents = defaultdict(set)
    dependency_reasons = []
    for index in indexes:
        for sid, successors in index["dependents"].items():
            dependents[sid].update(successors)
            dependency_reasons.extend({"from_skill_id": sid, "to_skill_id": successor,
                                       "basis": "explicit_skill_prerequisite_or_refinement"} for successor in successors)
    for index, raw in zip(indexes, (before("data/dependencies.json"), after("data/dependencies.json"))):
        for edge in _json(raw) or []:
            for source in index["topics"].get(edge.get("prerequisiteId"), []):
                for target in index["topics"].get(edge.get("topicId"), []):
                    dependents[source].add(target)
                    dependency_reasons.append({"from_skill_id": source, "to_skill_id": target,
                        "basis": "topic_dependency_" + edge.get("strength", "unspecified"),
                        "source_ref": "data/dependencies.json", "reason": edge.get("reason")})
    downstream, queue = set(), deque(sorted(overall["skills"]))
    while queue:
        for successor in dependents[queue.popleft()]:
            if successor not in downstream and successor not in overall["skills"]:
                downstream.add(successor)
                queue.append(successor)
    downstream_materials = set()
    for sid in downstream:
        for index in indexes:
            downstream_materials.update(index["skill_materials"].get(sid, set()))
    preview_rows = []
    for material in sorted(value for value in overall["materials"] | downstream_materials if value):
        kind, _, identity = material.partition(":")
        query = {"topic": "teach", "unit": "unit", "project": "project"}.get(kind)
        if query is None:
            continue
        path = "/?" + urlencode({query: identity})
        preview_rows.append({"material_id": material, "url": preview_base_url.rstrip("/") + path, "path": path,
            "reason": "direct_change" if material in overall["materials"] else "prerequisite_or_refinement_dependency",
            "checks": ["打开实际学习页核对目标、条件、表征与公式", "实际展开讲解和参考并检查帮助记录",
                       "完成对应任务或自检，检查错误提示和修订入口", "刷新恢复并核对旧事项及内容版本"],
            "status": "pending", "evidence": []})
    runtime = any("runtime_or_contract_preview" in row["categories"] for row in changed if not row["format_only"])
    if runtime:
        for query in ({"scope": "1"}, {"needs": "1"}, {"history": "1"}, {"profile": "1"}):
            path = "/?" + urlencode(query)
            preview_rows.append({"material_id": None, "url": preview_base_url.rstrip("/") + path, "path": path,
                "reason": "runtime_or_contract_change", "checks": ["实际执行受影响流程并保存截图或浏览器断言日志"],
                "status": "pending", "evidence": []})
    bindings = [{"path": row["path"], "before_sha256": row["before_sha256"], "after_sha256": row["after_sha256"]} for row in changed]
    fingerprint = hashlib.sha256(json.dumps({"base_commit": commit, "files": bindings}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    checklist = [{"change_id": row["change_id"], "path": row["path"], "after_sha256": row["after_sha256"],
        "review_status": "pending", "reviewer_kind": None, "reviewer": None, "findings": [], "decision": None,
        "required_checks": row["categories"], "evidence_refs": []}
        for row in changed if row["semantic_review_required"]]
    unique_missing = {json.dumps(row, sort_keys=True, ensure_ascii=False): row for row in unresolved_bindings}
    changed_during_read = []
    for path, frozen in after_cache.items():
        current_path = root / path
        current = current_path.read_bytes() if current_path.is_file() and not current_path.is_symlink() else None
        if current != frozen:
            changed_during_read.append(path)
    return {"schema": 1, "base_commit": commit, "head_commit": _git(root, "rev-parse", "HEAD").decode().strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "content_fingerprint": fingerprint,
        "review_status": "invalidated" if changed_during_read else "pending", "automatic_semantic_approval": False,
        "source_changed_during_review": changed_during_read,
        "files": changed, "summary": {"changed_files": len(changed), "format_only": sum(row["format_only"] for row in changed),
            "new_untracked_sources": sum(row["untracked_new_source"] for row in changed), "review_items": len(checklist),
            "direct_skills": len(overall["skills"]), "direct_tasks": len(overall["tasks"]), "direct_materials": len(overall["materials"]),
            "downstream_skills": len(downstream), "preview_pages": len(preview_rows)},
        "impact": {**{key: sorted(values) for key, values in overall.items()}, "downstream_skill_ids": sorted(downstream),
                   "downstream_material_ids": sorted(value for value in downstream_materials if value),
                   "dependency_reasons": [row for key, row in {json.dumps(row, sort_keys=True): row for row in dependency_reasons
                        if row["from_skill_id"] in overall["skills"] | downstream and row["to_skill_id"] in downstream}.items()],
                   "runtime_scope": "存在运行时/Schema改动，需要所有学习流程的代表性回归；不能仅以列出的内容页面限定影响。" if runtime else None,
                   "basis": "旧与新显式稳定身份及先修/细化依赖的保守影响范围；不猜测内容等价。"},
        "unresolved_registrations": list(unique_missing.values()), "historical_record_policy": PRESERVATION_POLICY,
        "preview_tasks": preview_rows,
        "review_template": {"schema": 1, "base_commit": commit, "content_fingerprint": fingerprint,
                            "status": "pending", "items": checklist, "preview_results": [],
                            "teacher_review": "deferred_by_user", "real_student_effect_validation": "deferred_by_user"},
        "limits": ["字段分类与影响传播帮助安排审查，不自动判定数学、教学或题目语义正确。",
                   "预览清单不是已执行证据；实际截图、断言日志和语义核对记录必须绑定本内容指纹。",
                   "文件字节改变后本审查模板失效；需要重新计算差异和相应影响。"]}


def _evidence_problem(root, reference):
    if not isinstance(reference, dict):
        return "证据引用须为含path和sha256的对象"
    name, expected = reference.get("path"), reference.get("sha256")
    if (not isinstance(name, str) or not name or "\\" in name
            or any(ord(char) < 32 for char in name)
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        return "证据路径须为仓库内的规范相对路径，不得越界"
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        return "证据sha256须为64位小写十六进制"
    try:
        current = root
        for part in PurePosixPath(name).parts:
            current = current / part
            if current.is_symlink():
                return "证据文件及上级目录不得使用符号链接"
        if not current.resolve().is_relative_to(root):
            return "证据路径越出仓库"
        if not current.is_file():
            return "证据须为实际存在的普通文件"
        if hashlib.sha256(current.read_bytes()).hexdigest() != expected:
            return "证据文件SHA256不匹配，须重新核对内容"
    except (OSError, ValueError, RuntimeError) as exc:
        return f"证据文件无法读取：{type(exc).__name__}"
    return None


def validate_review_binding(report, review, *, root=None):
    """Check claims and, in real gates, repository evidence byte bindings.

    ``root=None`` retains pure in-memory compatibility for callers testing
    record structure. It does not verify evidence files and is not a release
    gate. Formal CLI callers must pass the actual repository root. File and
    hash checks establish provenance, not the truth of semantic judgements.
    """
    errors = []
    evidence_root = Path(root).resolve() if root is not None else None
    if report.get("source_changed_during_review"):
        errors.append("生成差异时源文件仍在变化；请固定内容后重新生成")
    if review.get("status") != "reviewed":
        errors.append("审查汇总仍未标记为已完成")
    if review.get("content_fingerprint") != report.get("content_fingerprint") or review.get("base_commit") != report.get("base_commit"):
        errors.append("审查记录与当前基线/内容指纹不一致")
    expected = {row["change_id"]: row for row in report["review_template"]["items"]}
    actual = {row.get("change_id"): row for row in review.get("items", [])}
    if set(actual) != set(expected) or len(actual) != len(review.get("items", [])):
        errors.append("审查项目缺失、重复或包含不属于当前差异的条目")
    for identity, row in expected.items():
        saved = actual.get(identity, {})
        if saved.get("after_sha256") != row["after_sha256"] or saved.get("path") != row["path"]:
            errors.append(f"{row['path']}: 文件指纹不匹配")
        if (saved.get("review_status") != "reviewed" or saved.get("reviewer_kind") not in {"ai", "human"}
                or not saved.get("reviewer") or not saved.get("findings") or not saved.get("evidence_refs")
                or saved.get("decision") not in {"accept", "accept_with_limits"}):
            errors.append(f"{row['path']}: 缺已完成的具体语义核对、身份、结论或证据引用")
        if evidence_root is not None:
            references = saved.get("evidence_refs")
            if not isinstance(references, list) or not references:
                errors.append(f"{row['path']}: 需要非空证据引用列表")
            else:
                for number, reference in enumerate(references, 1):
                    problem = _evidence_problem(evidence_root, reference)
                    if problem:
                        errors.append(f"{row['path']}: 证据{number}：{problem}")
    return errors
