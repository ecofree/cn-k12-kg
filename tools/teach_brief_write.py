"""速查参考卡的统一落盘器。

参考范围由 cnkg.brief_groups 明确登记，不因 core/tool 变化而扩大或丢失。
这些卡可以辅助核心点，但不计作完整讲解或技能达标。自动选择的少量练习
用于复习抽查，不能代表已测量 covers 中所有目标。
"""
from __future__ import annotations

# Historical CLI is fenced before imports or authoring side effects.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))


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
           if (info.get("reference_group") or info.get("brief_group")) == key]
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
        "reference_only": True,
        "coverage_scope": "辅助速查；不替代核心点的完整讲解，不证明本组全部技能达标",
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
    raise RuntimeError('历史速查写入器已封存；编辑 data/teach/clusters JSON 后运行 tools/build_project.py --release')
