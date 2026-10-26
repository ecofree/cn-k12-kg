"""One coherent, immutable author-content generation for a local server.

Authors rebuild and restart explicitly. An edit on disk cannot mix a new card
with an old graph, task catalog or diagram in an already running process.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

from .catalog import CatalogError, load_catalog
from .graph import load_graph
from . import learning, projects


class ContentRepository:
    def __init__(self, data, *, include_external=False):
        self.data = Path(data)
        self.catalog = load_catalog(self.data)
        self.graph = load_graph(self.data, include_external=include_external)
        self.units = learning.load_units(self.data)
        self.projects = projects.load_projects(self.data)
        if set(self.units) != set(self.catalog.units) or set(self.projects) != set(self.catalog.projects):
            raise CatalogError(["作者资源与目录不一致，请先注册并编译，再重启服务"])
        self.cards, self.briefs, self.assets = {}, {}, {}
        for path in sorted((self.data / "teach").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.cards[value["topicId"]] = value
        for path in sorted((self.data / "teach/clusters").glob("*.json")):
            self.briefs[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        from .quality_scope import build_scope_report
        self.scope_report = build_scope_report(self.data)
        for relative, expected in self.catalog.data["source_hashes"].items():
            path = self.data.parent / relative
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise CatalogError([f"启动读取期间内容发生改变：{relative}；请完成构建后重启"])
            if relative.startswith("web/static/diagrams/"):
                self.assets["/static/diagrams/" + path.name] = {
                    "sha256": expected, "body_base64": base64.b64encode(raw).decode("ascii")}
        # Extra brief cards are auxiliary but frozen along with this process.
        self.version = self.catalog.version

    def topic_card(self, topic_id, group=None):
        if topic_id in self.cards:
            return "full", copy.deepcopy(self.cards[topic_id])
        if group in self.briefs:
            return "brief", copy.deepcopy(self.briefs[group])
        return "none", {}

    def assets_for(self, resources):
        sources = {row.get("diagram", {}).get("src") for row in resources}
        return {src: copy.deepcopy(self.assets[src]) for src in sources if src in self.assets}

    def asset_bytes(self, source):
        return base64.b64decode(self.assets[source]["body_base64"]) if source in self.assets else None
