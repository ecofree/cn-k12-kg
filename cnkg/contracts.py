"""Versioned authoring contracts backed by the repository's JSON Schemas.

Schema files are the structural authority. This module does not implement a
second JSON Schema engine. The optional build dependency is loaded only when
validation is requested, so the local Web runtime remains dependency-free.

Every reference is resolved from the in-memory local registry. Validation never
fetches a schema over the network. Unknown resource versions fail closed; legacy
topic/question records without schema_version are interpreted as v1. Existing
learning resources continue to declare schema=1.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schema"
SCHEMA_BASE = "https://cn-k12-kg.local/schema/v1/"
RESOURCE_KINDS = frozenset({
    "topic", "dependency", "question", "teaching-card", "learning-task",
    "learning-unit", "project", "answer-spec",
    "skills-registry", "catalog-bindings", "catalog",
    "content-manifest", "question-source",
})


class ContractDependencyError(RuntimeError):
    """The build environment has not installed its declared dependencies."""


def _reject_constant(value):
    raise ValueError(f"JSON 不允许非有限数字 {value}")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=_reject_constant)


def load_schema(kind, schema_dir=None):
    """Return the canonical contract, never a generated duplicate definition."""
    if kind not in RESOURCE_KINDS and kind != "common":
        raise ValueError(f"未知结构契约 {kind!r}")
    return _read(Path(schema_dir or SCHEMA_DIR) / f"{kind}.schema.json")


def property_schema(kind, field, schema_dir=None):
    """Resolve a property definition for tools that need its enum or limits.

    This helper resolves only this project's named common definitions. Full
    validation, including arbitrary supported JSON Schema features, belongs to
    jsonschema, not to this accessor.
    """
    schema = load_schema(kind, schema_dir)
    value = schema["properties"][field]
    if "$ref" in value:
        prefix = SCHEMA_BASE + "common.schema.json#/$defs/"
        if not value["$ref"].startswith(prefix):
            raise ValueError("此字段不是共享枚举或简单定义")
        value = load_schema("common", schema_dir)["$defs"][value["$ref"][len(prefix):]]
    return value


@lru_cache(maxsize=8)
def _validators(schema_directory):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from referencing import Registry, Resource
        from referencing.exceptions import NoSuchResource
    except ImportError as exc:
        raise ContractDependencyError(
            "结构校验需要开发依赖。请在项目虚拟环境运行 "
            "python -m pip install -r requirements-dev.txt；"
            "安装后校验仅使用本地 Schema，无需联网。"
        ) from exc

    def local_only(uri):
        raise NoSuchResource(ref=uri)

    schemas = {}
    resources = []
    for path in sorted(Path(schema_directory).glob("*.schema.json")):
        schema = _read(path)
        expected_id = SCHEMA_BASE + path.name
        if schema.get("$id") != expected_id:
            raise ValueError(f"{path.name}: Schema ID 必须为 {expected_id}")
        Draft202012Validator.check_schema(schema)
        schemas[path.name.removesuffix(".schema.json")] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    missing = RESOURCE_KINDS - set(schemas)
    if missing:
        raise ValueError(f"缺少结构契约 {sorted(missing)}")
    registry = Registry(retrieve=local_only).with_resources(resources)
    return {kind: Draft202012Validator(schema, registry=registry,
                                      format_checker=FormatChecker())
            for kind, schema in schemas.items()}


def clear_schema_cache():
    """Reload contracts after an authoring tool deliberately changes a schema."""
    _validators.cache_clear()


def validate_resource(kind, value, *, schema_dir=None):
    """Return path-qualified structural errors for one authoring resource.

    Missing build dependencies and malformed Schema definitions are environment
    errors, not content errors, and therefore raise with an actionable message.
    """
    if kind not in RESOURCE_KINDS:
        raise ValueError(f"未知结构契约 {kind!r}")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        return [f"{kind} $: 资源不是有效的有限 JSON：{exc}"]
    validator = _validators(str(Path(schema_dir or SCHEMA_DIR).resolve()))[kind]
    problems = sorted(validator.iter_errors(value),
                      key=lambda error: (tuple(str(x) for x in error.absolute_path),
                                         error.message))
    errors = []
    for error in problems:
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}"
                             for part in error.absolute_path)
        errors.append(f"{kind} {path}: {error.message}")
    return errors


def validate_repository(data_dir=None, *, include_references=True):
    """Validate active resources and their links without changing any files.

    Archives and optional external corpora are not authored release resources.
    Existing subject-specific validators still own arithmetic, curriculum
    semantics, review hashes, diagram assets and instructional coverage.
    """
    data = Path(data_dir or ROOT / "data")
    errors, resources = [], {}
    for kind, filename in (("topic", "topics.json"),
                           ("dependency", "dependencies.json"),
                           ("question", "questions.json")):
        try:
            rows = _read(data / filename)
        except (OSError, ValueError) as exc:
            errors.append(f"{filename}: {exc}")
            continue
        if not isinstance(rows, list):
            errors.append(f"{filename}: 顶层必须为数组")
            continue
        resources[kind] = rows
        for i, row in enumerate(rows):
            identifier = row.get("id", i) if isinstance(row, dict) else i
            errors.extend(f"{filename}[{identifier}]: {e}"
                          for e in validate_resource(kind, row))
    for kind, folder in (("teaching-card", "teach"),
                         ("learning-unit", "learning_units"),
                         ("project", "projects")):
        resources[kind] = []
        for path in sorted((data / folder).glob("*.json")):
            try:
                row = _read(path)
            except (OSError, ValueError) as exc:
                errors.append(f"{path.relative_to(data)}: {exc}")
                continue
            resources[kind].append(row)
            errors.extend(f"{path.relative_to(data)}: {e}"
                          for e in validate_resource(kind, row))
    # Author manifests and declared question files are checked independently of
    # the compiled trace view; they do not become a second reference graph.
    manifest_path = data / "content_sources.json"
    if manifest_path.is_file():
        try:
            manifest = _read(manifest_path)
            problems = validate_resource("content-manifest", manifest)
            errors.extend(problems)
            if not problems:
                for source in manifest.get("question_sources", []):
                    path = data / source["path"]
                    if not path.resolve().is_relative_to(data.resolve()):
                        errors.append(f"作者题源越界：{source['path']}")
                        continue
                    payload = _read(path)
                    errors.extend(f"{source['path']}: {error}"
                                  for error in validate_resource("question-source", payload))
        except (OSError, ValueError) as exc:
            errors.append(f"content_sources.json: {exc}")
    # Avoid following malformed structures after the precise schema errors.
    if errors or not include_references:
        return errors
    return _reference_errors(resources, data)


def _reference_errors(resources, data):
    """Link checks are intentionally separate from field-shape contracts."""
    errors = []
    for kind, rows in resources.items():
        if kind == "dependency":
            identifiers = [(row["topicId"], row["prerequisiteId"]) for row in rows]
        else:
            key = "topicId" if kind == "teaching-card" else "id"
            identifiers = [row[key] for row in rows]
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"{kind}: 资源标识重复")
    topics = {row["id"]: row for row in resources.get("topic", [])}
    questions = {row["id"]: row for row in resources.get("question", [])}
    try:
        standards = {row["id"] for row in _read(data / "standards.json")}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [f"standards.json: 无法读取引用表：{exc}"]

    def references(label, ids, known):
        for identifier in ids:
            if identifier not in known:
                errors.append(f"{label}: 不存在引用 {identifier}")

    for topic in topics.values():
        references(topic["id"], [row["id"] for row in topic.get("standards", [])], standards)
    for edge in resources.get("dependency", []):
        references("dependency", [edge["topicId"], edge["prerequisiteId"]], topics)
    for question in questions.values():
        references(question["id"], [question["topicId"], *question.get("topicIds", [])], topics)
        if question.get("primaryTopicId"):
            references(question["id"], [question["primaryTopicId"]], topics)
            if question.get("topicIds") and question["primaryTopicId"] not in question["topicIds"]:
                errors.append(f"{question['id']}: primaryTopicId 不在 topicIds 中")
        topic = topics.get(question["topicId"])
        if topic:
            for index in question.get("tested_criterion_indices", []):
                if index >= len(topic.get("mastery_criteria", [])):
                    errors.append(f"{question['id']}: 目标下标 {index} 不存在")
        if question["type"] == "choice":
            choice_ids = [choice["id"] for choice in question["choices"]]
            if len(choice_ids) != len(set(choice_ids)):
                errors.append(f"{question['id']}: 稳定选项 ID 重复")
            references(question["id"] + ".answer", [question["answer"]], set(choice_ids))
    for card in resources.get("teaching-card", []):
        label = "teach/" + card["topicId"]
        references(label, [card["topicId"]], topics)
        references(label + ".practice", [row["id"] for row in card["practice"]], questions)
        for row in card["practice"]:
            question = questions.get(row["id"])
            if question and card["topicId"] not in question.get("topicIds", [question["topicId"]]):
                errors.append(f"{label}: 练习 {row['id']} 未关联该教材节点")
        topic = topics.get(card["topicId"])
        if topic:
            goals = topic.get("mastery_criteria", [])
            for row in card.get("objective_coverage", []):
                index = row["criterion_index"]
                if index >= len(goals) or row["title"] != goals[index]:
                    errors.append(f"{label}: 目标 {index} 已不存在或标题不匹配")
    for kind in ("learning-unit", "project"):
        for resource in resources.get(kind, []):
            label = resource["id"]
            references(label + ".standard_ids", resource["standard_ids"], standards)
            references(label + ".topic_ids", resource.get("topic_ids", []), topics)
            references(label + ".prerequisite_topic_ids", resource.get("prerequisite_topic_ids", []), topics)
            goals = [row["id"] for row in resource["objectives"]]
            if len(goals) != len(set(goals)):
                errors.append(f"{label}: 目标 ID 重复")
            for collection in ("tasks", "steps", "rubric"):
                for i, row in enumerate(resource.get(collection, [])):
                    references(f"{label}.{collection}[{i}].objective_ids",
                               row.get("objective_ids", []), set(goals))
    return errors
