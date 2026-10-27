"""Rebuild the self-owned data from an explicit, ordered source registry.

The registry replaces glob-order precedence and historical patch replay.  It
does not certify mathematical or pedagogical correctness.  Archived patches
are retained for provenance and are never build inputs.

    python tools/rebuild_data.py --check
    python tools/rebuild_data.py --output-dir build/rebuilt
    python tools/rebuild_data.py --write

Edit the registered JSON authoring sources. The v2 manifest does not execute
question generators or copy edited build outputs back into authoring files.
Use tools/build_project.py --release for the complete publish workflow.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

REGISTRY = "content_sources.json"
OUTPUTS = ("topics.json", "dependencies.json", "questions.json")
APPLICATION_SOURCE = "self-generated-application"


class SourceError(ValueError):
    """An ambiguous or incomplete set of authoring sources."""


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8", newline="\n")


def sha256_lf(path: pathlib.Path) -> str:
    """Text content hash independent of checkout line endings (not a byte hash)."""
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def load_registry(data_dir: pathlib.Path = DATA) -> dict:
    registry = read_json(data_dir / REGISTRY)
    if registry.get("schema_version") not in (1, 2):
        raise SourceError("unsupported content source registry version")
    seen, volumes = set(), set()
    for source in registry.get("content_sources", []):
        rel = pathlib.PurePosixPath(source["path"])
        if rel.parent != pathlib.PurePosixPath("content") or rel.suffix != ".json":
            raise SourceError(f"not an active content source: {rel}")
        if source["path"] in seen or source["volume"] in volumes:
            raise SourceError(f"duplicate source path or volume: {source}")
        seen.add(source["path"])
        volumes.add(source["volume"])
        if not (data_dir / rel).is_file():
            raise SourceError(f"missing registered source: {rel}")
    if not seen:
        raise SourceError("no registered content sources")
    retired = {}
    for source in registry.get("retired_sources", []):
        rel = pathlib.PurePosixPath(source["path"])
        if rel.parent != pathlib.PurePosixPath("content") or rel.name in (".", ".."):
            raise SourceError(f"invalid retired source path: {rel}")
        if source["path"] in retired:
            raise SourceError(f"duplicate retired source: {rel}")
        retired[source["path"]] = source
    if seen & retired.keys():
        raise SourceError("a source cannot be both active and retired")
    # Old checkouts and legacy editors can leave these explicitly retired files
    # at the old path.  They have no precedence and never contribute a record.
    for rel, source in retired.items():
        path = data_dir / rel
        if path.exists() and sha256_lf(path) != source["sha256_lf"]:
            raise SourceError(f"retired source was edited: {rel}; edit its active owner instead")
    actual = {p.relative_to(data_dir).as_posix()
              for p in (data_dir / "content").glob("*.json")}
    if actual - seen - retired.keys():
        raise SourceError(f"unregistered active content files: {sorted(actual - seen - retired.keys())}")
    question_sources = registry.get("question_sources", [])
    question_paths, names = set(), set()
    for source in question_sources:
        rel = pathlib.PurePosixPath(source["path"])
        if rel.parent != pathlib.PurePosixPath("question_sources") or rel.suffix != ".json":
            raise SourceError(f"not a registered question source: {rel}")
        if source["path"] in question_paths or source["name"] in names:
            raise SourceError(f"duplicate declarative question source: {source}")
        if source.get("status") not in ("active", "retired"):
            raise SourceError(f"question source needs an explicit lifecycle: {rel}")
        path = data_dir / rel
        if not path.is_file() or not path.resolve().is_relative_to(data_dir.resolve()):
            raise SourceError(f"missing or out-of-root question source: {rel}")
        if source.get("status") == "retired" and source.get("sha256_lf") != sha256_lf(path):
            raise SourceError(f"retired question archive changed: {rel}")
        question_paths.add(source["path"])
        names.add(source["name"])
    actual_questions = {p.relative_to(data_dir).as_posix()
                        for p in (data_dir / "question_sources").glob("*.json")}
    if actual_questions - question_paths:
        raise SourceError(f"unregistered question sources: {sorted(actual_questions - question_paths)}")
    generators = registry.get("question_generators", [])
    if registry["schema_version"] == 2 and generators:
        raise SourceError("v2 builds only registered JSON; Python question generators are retired")
    if len(generators) > 1 or any(g.get("name") != "application-items" for g in generators):
        raise SourceError("unsupported or repeated question generator")
    for generator in generators:
        if (generator.get("source") != APPLICATION_SOURCE
                or generator.get("entrypoint") != "tools/write_application_items.py"):
            raise SourceError("application generator identity does not match its implementation")
        from tools import write_application_items as application
        expected = {"tools/app_items/__init__.py", "cnkg/evidence.py", "cnkg/verify.py"}
        expected.update(pathlib.Path(batch.__file__).resolve().relative_to(ROOT).as_posix()
                        for batch in application.app_items.BATCHES)
        if set(generator.get("inputs", [])) != expected:
            raise SourceError("register the exact app_items batches and evidence/verify inputs")
    from tools import balance_options
    from cnkg import choice_order
    transforms = registry.get("question_transforms", [])
    allowed = {balance_options.TRANSFORM_NAME: (balance_options.TRANSFORM_VERSION,
                balance_options.TRANSFORM_ENTRYPOINT, []),
               choice_order.NAME: (choice_order.VERSION, choice_order.ENTRYPOINT, choice_order.INPUTS)}
    if (not isinstance(transforms, list) or len(transforms) > 1
            or any(t.get("name") not in allowed for t in transforms)):
        raise SourceError("unsupported or repeated question transform")
    for transform in transforms:
        expected = allowed[transform["name"]]
        if (transform.get("version"), transform.get("entrypoint"), transform.get("inputs")) != expected:
            raise SourceError("choice-order transform identity or inputs do not match its implementation")
        if not (ROOT / transform["entrypoint"]).is_file():
            raise SourceError("missing registered choice-order transform")
        if transform["name"] == choice_order.NAME and not (data_dir / "choice_orders.json").is_file():
            raise SourceError("missing frozen legacy choice order snapshot")
    return registry


def row_key(name: str, row: dict):
    if name == "dependencies.json":
        return row["topicId"], row["prerequisiteId"]
    return row["id"]


def natural_id_key(identifier: str) -> tuple:
    """Numeric chapter/question indices: chapter 2 precedes chapter 10."""
    return tuple((1, int(part)) if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", identifier))


def index_rows(name: str, rows: list) -> dict:
    indexed = {}
    for row in rows:
        key = row_key(name, row)
        if key in indexed:
            raise SourceError(f"duplicate {name} record: {key}")
        indexed[key] = row
    return indexed


def application_questions(base_questions: list) -> list:
    from tools import write_application_items as application

    generated, errors = application.build(application.app_items.all_items(), base_questions)
    if errors:
        raise SourceError("application generator failed: " + "; ".join(errors[:8]))
    return generated


def apply_question_transforms(questions: list, registry: dict, data_dir=DATA) -> list:
    """Apply an explicitly versioned transform; never re-sign content reviews."""
    from tools import balance_options
    from cnkg import choice_order
    for transform in registry.get("question_transforms", []):
        try:
            if transform.get("name") == choice_order.NAME:
                snapshot = read_json(pathlib.Path(data_dir) / "choice_orders.json")
                questions = choice_order.stable_order(questions, snapshot)
            elif transform.get("name") == balance_options.TRANSFORM_NAME:
                # Historical fixtures/imports only; production uses stable-choice-order.
                questions = balance_options.balance_questions(questions)
            else:
                raise SourceError(f"unsupported question transform: {transform.get('name')}")
        except balance_options.ChoiceOrderError as exc:
            raise SourceError(str(exc)) from exc
    return questions


def build_data(data_dir: pathlib.Path = DATA, *, apply_transforms: bool = True) -> tuple[dict, dict]:
    """Return deterministic rows and provenance without reading outputs.

    apply_transforms=False exposes the merged authoring order for audit reports;
    published outputs always use the registered transforms after generation.
    """
    registry = load_registry(data_dir)
    rows = {name: [] for name in OUTPUTS}
    origins = {name: {} for name in OUTPUTS}

    def add(name, row, origin):
        key = row_key(name, row)
        if key in origins[name]:
            raise SourceError(f"{name} {key} redefined by {origin}; "
                              f"already owned by {origins[name][key]}")
        rows[name].append(copy.deepcopy(row))
        origins[name][key] = origin

    for source in registry["content_sources"]:
        payload = read_json(data_dir / source["path"])
        volume = source["volume"]
        if payload.get("volume") != volume:
            raise SourceError(f"volume mismatch: {source['path']}")
        for tid, topic in payload.get("topics", {}).items():
            if topic.get("id") != tid or topic.get("volume") != volume:
                raise SourceError(f"incomplete topic or wrong volume: {tid}")
            add("topics.json", topic, source["path"])
        for edge in payload.get("dependencies", []):
            add("dependencies.json", edge, source["path"])
        for question in payload.get("questions", []):
            if question.get("source") == APPLICATION_SOURCE:
                raise SourceError(f"generated question duplicated in content: {question['id']}")
            add("questions.json", question, source["path"])

    for generator in registry.get("question_generators", []):
        for question in application_questions(rows["questions.json"]):
            add("questions.json", question, generator["entrypoint"])

    for source in registry.get("question_sources", []):
        payload = read_json(data_dir / source["path"])
        if payload.get("schema_version") != 1 or not isinstance(payload.get("questions"), list):
            raise SourceError(f"invalid declarative question source: {source['path']}")
        for question in payload["questions"]:
            add("questions.json", question, source["path"])

    if apply_transforms:
        rows["questions.json"] = apply_question_transforms(rows["questions.json"], registry, data_dir)

    topics = index_rows("topics.json", rows["topics.json"])
    for edge in rows["dependencies.json"]:
        if edge["topicId"] not in topics or edge["prerequisiteId"] not in topics:
            raise SourceError(f"unknown dependency endpoint: {row_key('dependencies.json', edge)}")
    for question in rows["questions.json"]:
        tids = question.get("topicIds") or [question.get("topicId")]
        if question.get("topicId") not in topics or any(tid not in topics for tid in tids):
            raise SourceError(f"unknown question topic: {question['id']}")
    volume_order = {source["volume"]: i
                    for i, source in enumerate(registry["content_sources"])}
    rows["topics.json"].sort(key=lambda t: (volume_order[t["volume"]], natural_id_key(t["id"])))
    topic_order = {topic["id"]: i for i, topic in enumerate(rows["topics.json"])}
    rows["dependencies.json"].sort(key=lambda e: (topic_order[e["topicId"]],
                                                 topic_order[e["prerequisiteId"]]))
    rows["questions.json"].sort(key=lambda q: (topic_order[q["topicId"]], natural_id_key(q["id"])))
    return rows, origins


def differences(expected: dict, actual: dict) -> dict:
    """Compare every field, including choices, accept, verification and metadata."""
    result = {}
    for name in OUTPUTS:
        left = index_rows(name, expected.get(name, []))
        right = index_rows(name, actual.get(name, []))
        changed = []
        for key in sorted(left.keys() & right.keys()):
            if left[key] != right[key]:
                fields = sorted(k for k in left[key].keys() | right[key].keys()
                                if left[key].get(k) != right[key].get(k)
                                or (k in left[key]) != (k in right[key]))
                changed.append({"id": key, "fields": fields})
        if changed or left.keys() != right.keys():
            result[name] = {"missing_from_output": sorted(left.keys() - right.keys()),
                            "unregistered_in_output": sorted(right.keys() - left.keys()),
                            "changed": changed}
    return result


def synchronize_sources(data_dir: pathlib.Path, reason: str) -> dict:
    """Explicit migration for legacy editors; never runs during a check/build.

    Generated application records must already match the complete build,
    including its transforms.  This operation cannot launder a direct output
    edit into a generated source.  Display order is derived: preserve each
    source's stable option-ID order while retaining edited option payloads.
    """
    if not reason.strip():
        raise SourceError("source synchronization requires a reason")
    registry = load_registry(data_dir)
    if registry.get("schema_version") == 2:
        raise SourceError("v2 作者源禁止从派生文件反向同步；请编辑登记的 JSON 后运行 tools/build_project.py --release")
    current = {name: read_json(data_dir / name) for name in OUTPUTS}
    indexed = {name: index_rows(name, rows) for name, rows in current.items()}
    topics = indexed["topics.json"]
    volumes = {source["volume"] for source in registry["content_sources"]}
    if any(t["volume"] not in volumes for t in topics.values()):
        raise SourceError("register a source for each new volume before synchronization")
    original = {source["path"]: read_json(data_dir / source["path"])
                for source in registry["content_sources"]}
    canonical = index_rows("questions.json", [q for payload in original.values()
                                              for q in payload.get("questions", [])])
    base = copy.deepcopy([q for q in current["questions.json"] if q.get("source") != APPLICATION_SOURCE])
    if registry.get("question_transforms"):
        for question in base:
            previous = canonical.get(question["id"], {})
            if question.get("type") != "choice" or previous.get("type") != "choice":
                continue
            # IDs survive display rotation.  Keep the current objects, including
            # legitimate text/metadata edits, in their original source order.
            # Deleted IDs disappear; newly authored IDs are appended in their
            # current relative order.  Neither case silently revives an old option.
            choices = question.get("choices") or []
            ids = [option.get("id") for option in choices]
            if (any(not isinstance(oid, str) or not oid for oid in ids)
                    or len(set(ids)) != len(ids)):
                raise SourceError(f"{question['id']}: invalid choice IDs during source synchronization")
            by_id = {option["id"]: option for option in choices}
            old_ids = [option["id"] for option in previous.get("choices", [])]
            order = [oid for oid in old_ids if oid in by_id]
            order.extend(oid for oid in ids if oid not in old_ids)
            question["choices"] = [by_id[oid] for oid in order]
    generated = application_questions(base) if registry.get("question_generators") else []
    transformed = apply_question_transforms(base + generated, registry)
    if registry.get("question_generators"):
        expected = [q for q in transformed if q.get("source") == APPLICATION_SOURCE]
        actual = [q for q in current["questions.json"] if q.get("source") == APPLICATION_SOURCE]
        delta = differences({"questions.json": expected}, {"questions.json": actual})
        if delta:
            raise SourceError("generated output differs: "
                              "if ordinary choice questions were added/deleted or their option count changed "
                              "in generated outputs, move those edits to the registered content source and "
                              "run tools/rebuild_data.py --write (the full pool determines display order); "
                              "otherwise fix app_items first. Differences: "
                              + json.dumps(delta, ensure_ascii=False)[:1800])
    for q in base:
        if q.get("topicId") not in topics:
            raise SourceError(f"unknown topic for {q['id']}")
    for d in current["dependencies.json"]:
        if d["topicId"] not in topics or d["prerequisiteId"] not in topics:
            raise SourceError(f"unknown dependency endpoint: {d}")
    changed = []
    for source in registry["content_sources"]:
        volume = source["volume"]
        previous = original[source["path"]]
        old_questions = {q["id"]: i for i, q in enumerate(previous.get("questions", []))}
        old_edges = {row_key("dependencies.json", d): i
                     for i, d in enumerate(previous.get("dependencies", []))}
        payload = {**previous,
            "volume": volume,
            "topics": {tid: topics[tid] for tid in sorted(topics, key=natural_id_key)
                       if topics[tid]["volume"] == volume},
            "dependencies": sorted([d for d in current["dependencies.json"]
                                    if topics[d["topicId"]]["volume"] == volume],
                                   key=lambda d: (old_edges.get(row_key("dependencies.json", d), len(old_edges)),
                                                  row_key("dependencies.json", d))),
            "questions": sorted([q for q in base if topics[q["topicId"]]["volume"] == volume],
                                key=lambda q: (old_questions.get(q["id"], len(old_questions)),
                                               natural_id_key(q["id"]))),
        }
        path = data_dir / source["path"]
        if previous != payload:
            write_json(path, payload)
            changed.append(source["path"])
    return {"reason": reason, "changed_sources": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    action.add_argument("--output-dir", type=pathlib.Path)
    action.add_argument("--sync-sources", action="store_true")
    ap.add_argument("--reason", default="")
    ap.add_argument("--data-dir", type=pathlib.Path, default=DATA)
    ap.add_argument("--report", type=pathlib.Path,
                    help="write full field differences here; stdout shows a short sample")
    args = ap.parse_args(argv)
    try:
        if args.sync_sources:
            print(json.dumps(synchronize_sources(args.data_dir, args.reason),
                             ensure_ascii=False, indent=2))
        built, _ = build_data(args.data_dir)
        print("Source build counts: " + json.dumps({k: len(v) for k, v in built.items()}))
        print("Scope: source/structure/arithmetic consistency; independent subject review is separate.")
        destination = args.data_dir if args.write else args.output_dir
        if destination:
            for name, rows in built.items():
                write_json(destination / name, rows)
            print(f"Wrote reproducible data to {destination}")
            return 0
        actual = {name: read_json(args.data_dir / name) for name in OUTPUTS}
        delta = differences(built, actual)
        if args.report:
            write_json(args.report, delta)
        if delta:
            for name, changes in delta.items():
                print(f"{name}: " + ", ".join(f"{key}={len(values)}"
                                               for key, values in changes.items()))
                print(json.dumps({key: values[:8] for key, values in changes.items()},
                                 ensure_ascii=False, indent=2))
            return 1
        print("SOURCE CONSISTENCY PASSED (all record fields)")
        return 0
    except (SourceError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"SOURCE BUILD FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
