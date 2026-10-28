"""Immutable, local learning-session content; unrelated releases do not reset work.

Snapshots contain only the target scope and its hard prerequisites. The engine
and grader version is checked separately; recorded content is never silently
reinterpreted under changed rules. SVG bytes are captured along with questions.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re

from . import engine, learning
from .graph import Graph

_ASSET = re.compile(r"/static/diagrams/([A-Za-z0-9][A-Za-z0-9_.-]*\.svg)\Z")


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def capture_assets(resources):
    assets = {}
    for resource in resources:
        diagram = resource.get("diagram")
        if not isinstance(diagram, dict):
            continue
        src = diagram.get("src")
        if src in assets:
            continue
        path = learning.diagram_path(src)
        raw = path.read_bytes()
        if len(raw) > 512_000:
            raise ValueError("单个教学图示超过快照大小限制")
        assets[src] = {"sha256": hashlib.sha256(raw).hexdigest(),
                       "body_base64": base64.b64encode(raw).decode("ascii")}
    return assets


def graph_snapshot(graph, targets, *, assets=None):
    scope = graph.scope(targets, hard_only=True)
    topics = [copy.deepcopy(t) for t in graph.topics if t["id"] in scope]
    questions = [copy.deepcopy(q) for q in graph.questions
                 if scope.intersection(q.get("topicIds") or [q.get("topicId")])]
    return {
        "schema": 1, "topics": topics,
        "dependencies": [copy.deepcopy(e) for e in graph.dependencies
                         if e["topicId"] in scope and e["prerequisiteId"] in scope],
        "questions": questions,
        "tiers": {tid: copy.deepcopy(graph.tiers[tid]) for tid in scope if tid in graph.tiers},
        "standard_coverage": {kind: engine._standard_coverage(graph, kind)
                              for kind in ("all", "core", "tool")},
        "assets": ({src: copy.deepcopy(assets[src]) for src in
                    {q.get("diagram", {}).get("src") for q in questions} if src in assets}
                   if assets is not None else capture_assets(questions)),
    }


def restore_graph(snapshot):
    if snapshot.get("schema") != 1:
        raise ValueError("不支持的会话内容快照版本")
    graph = Graph(snapshot["topics"], snapshot["dependencies"], snapshot["questions"])
    graph._tier_cache = copy.deepcopy(snapshot["tiers"])
    for kind, value in snapshot["standard_coverage"].items():
        setattr(graph, "_std_cov" if kind == "all" else "_std_cov_" + kind, value)
    return graph


def unit_assets(unit):
    return capture_assets([unit, *unit.get("tasks", []), *unit.get("worked_examples", [])])


def unit_hash(unit, assets):
    # Same contract as learning.unit_content_sha256, using frozen asset bytes.
    content = {k: v for k, v in unit.items()
               if k not in {"teacher_review", "review_status", "content_sha256"}}
    diagrams = [unit.get("diagram"), *(task.get("diagram") for task in unit.get("tasks", []))]
    hashes = {d["src"]: assets[d["src"]]["sha256"] for d in diagrams if isinstance(d, dict)}
    return digest({"content": content, "diagram_assets": hashes})


def session_assets(record):
    return record.get("asset_snapshots", record.get("graph_snapshot", {}).get("assets", {}))


def public_diagram(record, diagram):
    if not diagram:
        return None
    shown = copy.deepcopy(diagram)
    match = _ASSET.fullmatch(shown.get("src", ""))
    if match and shown["src"] in session_assets(record):
        shown["src"] = f"/api/session/{record['id']}/assets/{match.group(1)}"
    return shown


def asset_bytes(record, filename):
    """Only a presented task's own image is available through the session API."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.svg", filename):
        return None
    src = "/static/diagrams/" + filename
    tasks = record.get("unit_snapshot", {}).get("tasks", []) if record["kind"] == "unit" else list(
        record.get("question_snapshots", {}).values())
    visible = {t.get("diagram", {}).get("src") for t in tasks
               if t["id"] in record.get("presentations", {})}
    if src not in visible:
        return None
    asset = session_assets(record).get(src)
    if not asset:
        return None
    raw = base64.b64decode(asset["body_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != asset["sha256"]:
        raise ValueError("保存的图示内容校验失败")
    return raw
