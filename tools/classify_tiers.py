"""生成知识点分层数据（data/topic_tiers.json）并输出对照报告。

用法：
    python tools/classify_tiers.py
    python tools/classify_tiers.py --csv build/tier_review.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cnkg import tiers  # noqa: E402

DATA = ROOT / "data"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/topic_tiers.json")
    ap.add_argument("--json", default="build/tier_report.json")
    ap.add_argument("--csv", default="build/tier_review.csv")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    bank = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))

    data = tiers.build(topics)
    out_path = ROOT / args.out
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")

    per = collections.Counter()
    qcnt = collections.Counter()
    for q in bank:
        for tid in (q.get("topicIds") or [q.get("topicId")]):
            qcnt[tid] += 1

    rows = []
    by_topic = data["topics"]
    for t in topics:
        info = by_topic[t["id"]]
        st = tiers.stage_of(t)
        per[(st, info["tier"])] += 1
        rows.append({
            "topicId": t["id"],
            "grade": t.get("grade"),
            "stage": st,
            "name": t.get("name"),
            "type": t.get("type"),
            "tier": info["tier"],
            "cluster": info["cluster"],
            "practice_target": info["practice_target"],
            "own_questions": qcnt[t["id"]],
            "why": info["why"],
        })

    print("按学段 × 层")
    for st in ("primary", "junior", "senior"):
        c, k = per[(st, "core")], per[(st, "tool")]
        print(f"  {st:<8} core {c:3}  tool {k:3}  core 占比 {c / max(c + k, 1):.1%}")

    print("\n教学工作量（核心点=完整卡，工具簇=速查卡）")
    cl = data["clusters"]
    for st in ("primary", "junior", "senior"):
        cs = [c for c in cl.values() if c["stage"] == st]
        print(f"  {st:<8} 簇 {len(cs):2}  完整卡 {sum(c['full_cards_needed'] for c in cs):3}"
              f"  速查卡 {sum(c['brief_cards_needed'] for c in cs):2}"
              f"  合计 {sum(c['full_cards_needed'] + c['brief_cards_needed'] for c in cs):3}")

    print("\n最大的 8 个簇")
    for c in sorted(cl.values(), key=lambda x: -(x["core"] + x["tool"]))[:8]:
        print(f"  {c['name'][:34]:<36} core {c['core']:2} tool {c['tool']:2}")

    csv_path = ROOT / args.csv
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    report = {
        "generated_by": "tools/classify_tiers.py",
        "by_stage": {f"{st}_{k}": v for (st, k), v in per.items()},
        "clusters": cl,
        "workload": {
            st: {
                "clusters": len([c for c in cl.values() if c["stage"] == st]),
                "full_cards": sum(c["full_cards_needed"] for c in cl.values()
                                  if c["stage"] == st),
                "brief_cards": sum(c["brief_cards_needed"] for c in cl.values()
                                   if c["stage"] == st),
            } for st in ("primary", "junior", "senior")
        },
    }
    p = ROOT / args.json
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n写入 {out_path}\n写入 {p}\n写入 {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
