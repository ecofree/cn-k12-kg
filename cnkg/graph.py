"""图结构层：加载数据、构造先修关系、祖先集合、拓扑排序。

只依赖标准库。设计目标与 cn-math-taxonomy 的 engine.js 对齐：
纯数据 + 纯函数，不碰 DOM、不碰数据库。
"""
from __future__ import annotations

import json
import pathlib
import re

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# 册次顺序，用于学习路径的稳定排序
VOLUME_ORDER = [
    "一上", "一下", "二上", "二下", "三上", "三下", "四上", "四下", "五上", "五下", "六上", "六下",
    "七上", "七下", "八上", "八下", "九上", "九下",
    "必修一", "必修二", "选必一", "选必二", "选必三",
]

_QNUM = re.compile(r"-q(\d+)$")


def load_json(name: str):
    """读取 data/ 下的 JSON（UTF-8，裸数组）。"""
    with open(DATA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def qnum(question_id: str) -> int:
    m = _QNUM.search(question_id or "")
    return int(m.group(1)) if m else 0


class Graph:
    """知识点图 + 题库索引。构造后只读。"""

    def __init__(self, topics, dependencies, questions):
        self.topics = topics
        self.dependencies = dependencies
        self.questions = questions
        self.by_id = {t["id"]: t for t in topics}
        self.index = {t["id"]: i for i, t in enumerate(topics)}

        self.prereqs = {t["id"]: [] for t in topics}
        self.dependents = {t["id"]: [] for t in topics}
        seen = set()
        for edge in dependencies:
            a, b = edge["topicId"], edge["prerequisiteId"]
            if a not in self.by_id or b not in self.by_id:
                continue
            key = (a, b)
            if key in seen:  # 同一对边只保留一次
                continue
            seen.add(key)
            self.prereqs[a].append(edge)
            self.dependents[b].append(edge)

        # 稳定顺序：hard 在前；同强度下“离目标更近”的先修优先（文件序倒序），
        # 因为诊断回溯时先查最近的一层更省题，也更符合教学直觉。
        for tid, edges in self.prereqs.items():
            edges.sort(key=lambda e: (0 if e["strength"] == "hard" else 1,
                                      -self.index[e["prerequisiteId"]]))
        for tid, edges in self.dependents.items():
            edges.sort(key=lambda e: self.index[e["topicId"]])

        # 题库索引：一道题可能属于多个知识点（复合题）。
        #
        # 复合题的主知识点（primaryTopicId）排在前面 —— 诊断某点时，
        # 应该优先问「主要考这个点」的题，而不是「顺带涉及这个点」的题。
        # 但两者都要能被抽到，否则复合题就被浪费了。
        self.bank = {t["id"]: [] for t in topics}
        for q in questions:
            # 挑战层（竞赛题）永远不进诊断池。它们的 for_diagnosis 已经是 False，
            # 这里再拦一道：万一有人把 tal.json 拼进 questions 列表，
            # 也不能让超纲题把「没学过奥数」判成「基础不牢」。
            if q.get("challenge") or q.get("for_diagnosis") is False:
                continue
            ids = q.get("topicIds") or ([q["topicId"]] if q.get("topicId") else [])
            primary = q.get("primaryTopicId") or (ids[0] if ids else None)
            for tid in ids:
                if tid in self.bank:
                    self.bank[tid].append(q)
        for tid in self.bank:
            # 稳定排序：主知识点优先，其次按题号。
            self.bank[tid].sort(key=lambda q: (
                0 if (q.get("primaryTopicId") or q.get("topicId")) == tid else 1,
                qnum(q["id"]),
            ))

        self._anc = {}

    # ---------------------------------------------------------------- 分层
    @property
    def tiers(self) -> dict:
        """知识点分层（core / tool / cluster）。缺文件时返回空表，调用方按全 core 处理。"""
        cached = getattr(self, "_tier_cache", None)
        if cached is not None:
            return cached
        path = DATA_DIR / "topic_tiers.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._tier_cache = data.get("topics", {})
        else:
            self._tier_cache = {}
        return self._tier_cache

    def tier_of(self, tid: str) -> str:
        """没有分层数据时按 core 处理——宁可多问一道，不可漏掉应用点。"""
        return (self.tiers.get(tid) or {}).get("tier", "core")

    # ---------------------------------------------------------------- 基本查询
    def name(self, tid: str) -> str:
        return self.by_id.get(tid, {}).get("name", tid)

    def ancestors(self, tid: str) -> set:
        """严格祖先集合（不含自身），带缓存，迭代实现以避免深图递归溢出。"""
        if tid in self._anc:
            return self._anc[tid]
        out = set()
        stack = [tid]
        seen = {tid}
        while stack:
            cur = stack.pop()
            for edge in self.prereqs.get(cur, ()):
                pid = edge["prerequisiteId"]
                if pid in seen:
                    continue
                seen.add(pid)
                out.add(pid)
                stack.append(pid)
        self._anc[tid] = out
        return out

    def descendants(self, tid: str) -> set:
        out, stack, seen = set(), [tid], {tid}
        while stack:
            cur = stack.pop()
            for edge in self.dependents.get(cur, ()):
                cid = edge["topicId"]
                if cid in seen:
                    continue
                seen.add(cid)
                out.add(cid)
                stack.append(cid)
        return out

    def scope(self, targets) -> set:
        """诊断范围 = 目标 ∪ 全部祖先。"""
        out = set(targets)
        for tid in targets:
            out |= self.ancestors(tid)
        return out

    # ---------------------------------------------------------------- 排序
    def topo_order(self, ids) -> list:
        """对子集做拓扑排序（先修在前）；同层按 topics 文件顺序。"""
        subset = [tid for tid in self.index if tid in set(ids)]
        inside = set(subset)
        indeg = {tid: sum(1 for e in self.prereqs[tid] if e["prerequisiteId"] in inside)
                 for tid in subset}
        ready = [tid for tid in subset if indeg[tid] == 0]
        ready.sort(key=lambda t: self.index[t])
        order = []
        while ready:
            cur = ready.pop(0)
            order.append(cur)
            for edge in self.dependents[cur]:
                nxt = edge["topicId"]
                if nxt not in indeg:
                    continue
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
                    ready.sort(key=lambda t: self.index[t])
        return order

    def curriculum_order(self, ids) -> list:
        """按册次→文件顺序排（用于展示，不保证先修在前）。"""
        def key(tid):
            t = self.by_id[tid]
            vol = VOLUME_ORDER.index(t["volume"]) if t.get("volume") in VOLUME_ORDER else 99
            return (vol, t.get("grade", 99), self.index[tid])
        return sorted(ids, key=key)


def load_graph(data_dir: pathlib.Path | None = None,
               include_external: bool = False,
               ext_evidence: tuple[str, ...] | None = None) -> Graph:
    """从 data/ 构造图。

    include_external 默认 False：外部题库（questions_ext.json）的知识点归属是
    规则召回 + 人工确认的，但答案没有独立复算过。让它们默认不参与诊断，
    避免"看起来题库变大了，实际把没验过的答案喂给判分"。

    ext_evidence 进一步把「能不能进」和「答案有多少证据」拆开：
    只有 `evidence_level` 落在给定集合里的外部题才加载。
    默认取 `DIAGNOSABLE`（verified / corroborated / explained），
    也就是排除只有答案、没有可用推导的 bare 题——
    那些题答错不足以判薄弱（见 cnkg/evidence.py）。
    传空元组表示不按证据过滤（退回旧行为，仅用于对照实验）。
    """
    global DATA_DIR
    if data_dir is not None:
        DATA_DIR = pathlib.Path(data_dir)
    questions = load_json("questions.json")
    if include_external:
        from .evidence import DIAGNOSABLE
        allowed = DIAGNOSABLE if ext_evidence is None else set(ext_evidence)
        external = load_json("questions_ext.json") if (DATA_DIR / "questions_ext.json").exists() else []
        kept = [q for q in external
                if q.get("usable_for_diagnosis")
                and (not allowed or q.get("evidence_level") in allowed)]
        questions = questions + kept
    return Graph(load_json("topics.json"), load_json("dependencies.json"), questions)
