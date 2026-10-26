"""速查卡（工具点合并卡）的统一落盘器。

## 一张卡 = 一个「簇 × 册次」组

组由 `cnkg/tiers.py` 切出来，`covers` 不再手写——脚本按组自动填满，
少一个点都会在校验时报错。理由很直接：漏掉的点既没有完整卡、
也不在任何速查卡里，等于这个知识点根本没讲。

## 练习题自动挑

工具点的验证题不需要精挑，只要求「当场能做、不是纯选择」：
按知识点顺序，每个点取第一道非选择题，最多 3 道。
这样既保证至少一道非选择，也不会因为手写题号而引用到不存在的题。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.teach_cards import check_brief  # noqa: E402

DATA = ROOT / "data"
TEACH = DATA / "teach" / "clusters"

_TOPICS = {t["id"]: t for t in json.loads(
    (DATA / "topics.json").read_text(encoding="utf-8"))}
_TIERS = json.loads((DATA / "topic_tiers.json").read_text(encoding="utf-8"))
_BANK: dict[str, list[dict]] = {}
for _q in json.loads((DATA / "questions.json").read_text(encoding="utf-8")):
    for _t in (_q.get("topicIds") or [_q["topicId"]]):
        _BANK.setdefault(_t, []).append(_q)


def group_members(key: str) -> list[str]:
    ids = [tid for tid, info in (_TIERS.get("topics") or {}).items()
           if info.get("brief_group") == key]
    return sorted(ids, key=lambda x: (_TOPICS[x].get("grade") or 0, x))


def pick_practice(covers: list[str], n: int = 3) -> list[dict]:
    """轮转各点取非选择题，最多 n 道。

    先取每个点的第一道非选择题，再回头取第二道——单点组（认识人民币、
    三视图这类只有一个知识点的组）也能凑到 2–3 道，不至于「一页只配一道」。
    全是选择题时才退而求其次。
    """
    pools = [[q for q in _BANK.get(t, []) if q.get("type") != "choice"]
             for t in covers]
    out, used = [], set()
    while len(out) < n:
        progressed = False
        for pool in pools:
            while pool and pool[0]["id"] in used:
                pool.pop(0)
            if not pool:
                continue
            q = pool.pop(0)
            out.append({"id": q["id"], "note": "当场验证"})
            used.add(q["id"])
            progressed = True
            if len(out) >= n:
                break
        if not progressed:
            break
    if not out:  # 全是选择题也得给一道，总比不给强
        for tid in covers:
            for q in _BANK.get(tid, []):
                out.append({"id": q["id"], "note": "当场验证"})
                break
            if out:
                break
    return out


def B(key: str, name: str, one_liner: str, kp: list[str],
      pitfalls: list[str], check: str, practice: list[dict] | None = None) -> dict:
    cid, _, vol = key.partition("@")
    covers = group_members(key)
    return {
        "schema": 1,
        "kind": "brief",
        "cluster": cid,
        "volume": vol,
        "name": name,
        "review_status": "draft",
        "author": "hand",
        "covers": covers,
        "one_liner": one_liner,
        "key_points": kp,
        "pitfalls": pitfalls,
        "check": check,
        "practice": practice or pick_practice(covers),
    }


def write_briefs(cards: list[dict], source: str) -> int:
    problems = []
    for card in cards:
        key = f"{card['cluster']}@{card['volume']}"
        if not group_members(key):
            problems.append(f"{key}：组不存在")
            continue
        for e in check_brief(card, _TOPICS, _TIERS, {q["id"]: q for t in _BANK.values()
                                                     for q in t}):
            problems.append(f"{key}：{e}")
    keys = [f"{c['cluster']}@{c['volume']}" for c in cards]
    dup = {k for k in keys if keys.count(k) > 1}
    if dup:
        problems.append(f"重复的组：{'、'.join(sorted(dup))}")
    if problems:
        print(f"{source}：{len(problems)} 处问题，未写盘")
        for p in problems:
            print("  " + p)
        return 1
    TEACH.mkdir(parents=True, exist_ok=True)
    for card in cards:
        path = TEACH / f"{card['cluster']}@{card['volume']}.json"
        path.write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print(f"{source}：写入 {len(cards)} 张 -> {TEACH}")
    return 0
