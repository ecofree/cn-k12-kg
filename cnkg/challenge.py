"""挑战层：竞赛题的读取、筛选与抽样。

## 定位

挑战层独立于诊断池：

- `for_diagnosis: false` —— 不进 `Graph.bank`，答错不影响判薄弱。
- 只在讲完一个核心点后作为「看看竞赛怎么考」出现，最多 3–4 道。
- 挂靠单位是**板块**（calc/word/number/count/geo/logic/formal），
  不是课时级知识点。竞赛体系与本项目课标是两套分类，硬映射会挂错。

抽样按难度做梯度（入口 / 进阶 / 压轴各一道），而不是随机三连——
随机抽容易连出三道压轴，把"见识"变成"劝退"。
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


def sample_for(topic: dict, cluster: str, index: dict, size: int = SAMPLE_SIZE) -> list[dict]:
    from .tiers import stage_of

    stage = stage_of(topic)
    strand = CLUSTER_STRAND.get(cluster, "formal" if stage != "primary" else "logic")
    pool = [x for x in index.get((stage, strand), []) if _grade_ok(x, topic.get("grade"))]
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
