"""竞赛资料候选检索与目标关联准入。

板块、年级和设计难度只能产生待复核候选。课后推荐必须有绑定题目与
教材目标版本的独立学科审核；没有审核时返回空列表，不用散列匹配充数。
竞赛资料始终不进入诊断池，缺图题即使有答案也必须隔离。
"""
from __future__ import annotations

import hashlib
import json
import pathlib

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
CHALLENGE_FILE = DATA_DIR / "challenge" / "tal.json"

# 课程簇 → 竞赛板块。同一板块里的题共享，避免为每个知识点单独挑题。
CLUSTER_STRAND = {
    "p-add-sub": "calc",
    "p-mul-div": "calc",
    "p-ops": "calc",
    "p-frac-dec": "calc",
    "p-num-recog": "number",
    "p-pct-ratio": "word",
    "p-measure": "word",
    "p-word": "word",
    "p-perim-area": "geo",
    "p-volume": "geo",
    "p-shape-recog": "geo",
    "p-position": "geo",
    "p-stat": "logic",
    "p-reasoning": "logic",
    "j-number": "number",
    "j-algebra": "formal",
    "j-equation": "formal",
    "j-function": "formal",
    "j-triangle": "geo",
    "j-quad": "geo",
    "j-circle": "geo",
    "j-transform": "geo",
    "j-stat": "logic",
    "s-logic": "logic",
    "s-function": "formal",
    "s-trig": "formal",
    "s-sequence": "formal",
    "s-ineq": "formal",
    "s-vector": "formal",
    "s-solid": "geo",
    "s-analytic": "geo",
    "s-prob": "count",
    "s-count": "count",
}

# 每个知识点最多给几道挑战题
SAMPLE_SIZE = 3


def load(path: pathlib.Path | None = None) -> list[dict]:
    p = path or CHALLENGE_FILE
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("items", [])


def index_by_strand(items: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        out.setdefault((it["stage"], it["strand"]), []).append(it)
    for v in out.values():
        v.sort(key=lambda x: (x["level"], x["id"]))
    return out


def _grade_ok(item: dict, grade: int | None) -> bool:
    """年级差超过 2 的题不出。

    二年级学生看到六年级竞赛题，不是「见识」是「打击」；
    六年级学生看到二年级题，不是「挑战」是「浪费时间」。
    没有年级标注的题按学段放行。
    """
    gh = item.get("grade_hint")
    if gh is None or grade is None:
        return True
    return abs(gh - grade) <= 2


def _stable_offset(text: str, mod: int) -> int:
    if mod <= 0:
        return 0
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % mod


def candidates_for(topic: dict, cluster: str, index: dict, size: int = SAMPLE_SIZE) -> list[dict]:
    from .tiers import stage_of

    stage = stage_of(topic)
    strand = CLUSTER_STRAND.get(cluster, "formal" if stage != "primary" else "logic")
    quarantine = set(load_alignments().get("quarantine", {}))
    pool = [x for x in index.get((stage, strand), [])
            if _grade_ok(x, topic.get("grade")) and x["id"] not in quarantine]
    if not pool:
        return []

    # 难度梯度：入口一道、进阶一道、压轴一道。
    # 每个点用 topicId 的稳定散列做偏移——否则同簇的几十个点会拿到同一组题，
    # 「拓展」变成「同一道题看三十遍」。偏移是确定的，刷新不会换题。
    by_level: dict[int, list[dict]] = {}
    for it in pool:
        by_level.setdefault(it["level"], []).append(it)
    picked, used = [], set()
    for level in (1, 2, 3):
        bucket = by_level.get(level) or []
        if not bucket:
            continue
        start = _stable_offset(topic["id"] + str(level), len(bucket))
        for k in range(len(bucket)):
            it = bucket[(start + k) % len(bucket)]
            if it["id"] not in used:
                picked.append(it)
                used.add(it["id"])
                break
        if len(picked) >= size:
            break
    # 某档缺题时用同板块补满
    start = _stable_offset(topic["id"], len(pool))
    for k in range(len(pool)):
        if len(picked) >= size:
            break
        it = pool[(start + k) % len(pool)]
        if it["id"] not in used:
            picked.append(it)
            used.add(it["id"])
    return picked


def load_alignments(path=None) -> dict:
    path = pathlib.Path(path or DATA_DIR / "challenge" / "alignments.json")
    if not path.is_file():
        return {"schema": 1, "alignments": [], "quarantine": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def alignment_content_sha256(topic: dict, item: dict, alignment: dict) -> str:
    """绑定题目正文、目标原文与对齐理由；复核元数据不进入自己的哈希。"""
    body = {
        "topic": {k: topic.get(k) for k in ("id", "name", "description", "mastery_criteria")},
        "item": {k: v for k, v in item.items() if k not in ("review_status", "quality_review")},
        "alignment": {k: alignment.get(k) for k in
                      ("topic_id", "item_id", "target_objectives", "rationale", "prerequisites")},
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def sample_for(topic: dict, cluster: str, index: dict, size: int = SAMPLE_SIZE,
               *, alignment_data=None) -> list[dict]:
    """只返回通过内容版本绑定审核的目标关联；候选检索另用 candidates_for。"""
    data = alignment_data if alignment_data is not None else load_alignments()
    items = {item["id"]: item for values in index.values() for item in values}
    selected = []
    for row in data.get("alignments", []):
        if row.get("topic_id") != topic["id"]:
            continue
        item = items.get(row.get("item_id"))
        if not item or item["id"] in data.get("quarantine", {}):
            continue
        review = row.get("review", {})
        objectives = row.get("target_objectives", [])
        valid = (isinstance(objectives, list) and bool(objectives)
                 and all(x in topic.get("mastery_criteria", []) for x in objectives)
                 and bool(row.get("rationale")) and isinstance(row.get("prerequisites"), list)
                 and review.get("status") == "approved"
                 and review.get("reviewer_type") in ("teacher", "subject_expert")
                 and bool(review.get("reviewer_id")) and bool(review.get("record_id"))
                 and review.get("content_sha256") == alignment_content_sha256(topic, item, row))
        if valid and _grade_ok(item, topic.get("grade")) and item not in selected:
            selected.append(item)
    return sorted(selected, key=lambda x: (x["level"], x["id"]))[:size]
