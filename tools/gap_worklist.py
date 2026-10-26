"""补题工单：按当前证据口径，列出每个学段缺哪些点、缺几道、缺什么题型。

为什么单独做一个工具：
「缺 124 道」是一个没法执行的数字。要能执行，必须落到
「哪个知识点、现在几道、还差几道、现有题型是什么、smse 贡献了几道」。
否则补题的人只能凭感觉挑点，补完还是不知道补够没有。

用法：
    python tools/gap_worklist.py --stage junior
    python tools/gap_worklist.py --stage primary --json build/gap_primary.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cnkg.evidence import DIAGNOSABLE, is_own  # noqa: E402

DATA = ROOT / "data"
TARGET = {"primary": 4, "junior": 6, "senior": 8}


def stage_of(topic: dict) -> str:
    g = topic.get("grade")
    if g is None:
        return "?"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def idx(q: dict) -> list[str]:
    return q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="junior", choices=sorted(TARGET))
    ap.add_argument("--source", default="smse-shanghai-zhongkao",
                    help="要看贡献的来源；传空串表示不做来源对照")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))
    served = [q for q in ext if q.get("usable_for_diagnosis")
              and q.get("evidence_level") in DIAGNOSABLE]
    pool = own + served
    if args.source:
        without = [q for q in pool if q.get("source") != args.source]
    else:
        without = pool

    def count(items):
        c = collections.Counter()
        t = collections.defaultdict(collections.Counter)
        for q in items:
            for tid in idx(q):
                c[tid] += 1
                t[tid][q.get("type")] += 1
        return c, t

    full, _ = count(pool)
    base, types = count(without)

    rows = []
    for topic in topics:
        if stage_of(topic) != args.stage:
            continue
        tid = topic["id"]
        need = TARGET[args.stage] - base[tid]
        if need <= 0:
            continue
        rows.append({
            "topicId": tid,
            "name": topic["name"],
            "grade": topic.get("grade"),
            "have": base[tid],
            "need": need,
            "source_contribution": full[tid] - base[tid],
            "types": dict(types[tid]),
            "suggest": ("numeric" if types[tid].get("numeric", 0) <= 1 else
                        ("short" if types[tid].get("short", 0) == 0 else "choice")),
            "description": (topic.get("description") or "")[:60],
        })

    rows.sort(key=lambda r: (r["have"], r["topicId"]))
    total = sum(r["need"] for r in rows)
    print(f"学段 {args.stage}：不足 {len(rows)} 个点，合计缺 {total} 道")
    if args.source:
        print(f"其中 {args.source} 的贡献合计 "
              f"{sum(r['source_contribution'] for r in rows)} 道")
    for r in rows:
        print(f"  {r['topicId']:14}{r['name'][:18]:20} 现有 {r['have']:2} 缺 {r['need']:2}"
              f"  来源贡献 {r['source_contribution']:2}  题型 {r['types']}  建议 {r['suggest']}")

    if args.json:
        path = ROOT / args.json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stage": args.stage, "missing": total,
                                    "topics": rows}, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
