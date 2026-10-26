"""为每个核心点挂挑战题（只存 id，题目正文在 data/challenge/tal.json）。

为什么要单独一层索引：挑战题正文 9 MB，一次请求全塞给前端不现实；
索引只有 id，接口按需取。同时把「哪个点配哪几道」固化成数据，
方便人工复核——竞赛题配错，比不给更打击人。

覆盖规则：
- 只给 **core** 点配挑战题。工具点的目标是熟练，不是拓展。
- 同板块共享题池，按年级差 ≤2 过滤，按难度梯度抽 3 道。
- 抽样是确定性的（按 level、id 排序），同一知识点每次刷新都一样。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cnkg import challenge, tiers  # noqa: E402

DATA = ROOT / "data"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="build/challenge_coverage.json")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    tier_data = tiers.load()
    if not tier_data["topics"]:
        print("缺 data/topic_tiers.json，先跑 tools/classify_tiers.py")
        return 1
    items = challenge.load()
    index = challenge.index_by_strand(items)

    samples, missing = {}, []
    per_strand = collections.Counter()
    for t in topics:
        info = tier_data["topics"].get(t["id"])
        if not info or info["tier"] != "core":
            continue
        picked = challenge.sample_for(t, info["cluster"], index)
        if picked:
            samples[t["id"]] = [x["id"] for x in picked]
            per_strand[info["cluster"]] += len(picked)
        else:
            missing.append({"topicId": t["id"], "name": t["name"],
                            "cluster": info["cluster"]})

    out = {
        "schema": 1,
        "note": "挑战题只挂板块，不挂课时级知识点；不进诊断池",
        "count": len(samples),
        "samples": samples,
    }
    path = DATA / "challenge" / "links.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    core = [t for t in topics
            if tier_data["topics"].get(t["id"], {}).get("tier") == "core"]
    print(f"核心点 {len(core)}，配上挑战题 {len(samples)}"
          f"（{len(samples) / max(len(core), 1):.1%}）")
    print("按课程簇：")
    for cid, n in per_strand.most_common():
        print(f"  {cid:<14}{n:4}  {tiers.CLUSTER_NAMES.get(cid, '')}")
    if missing:
        print(f"没配上的核心点 {len(missing)}：")
        for m in missing[:10]:
            print(f"  {m['topicId']} {m['name']} ({m['cluster']})")

    report = {
        "core_topics": len(core),
        "linked_topics": len(samples),
        "coverage": len(samples) / max(len(core), 1),
        "by_cluster": dict(per_strand),
        "missing": missing,
        "strand_pool": {f"{st}/{sd}": len(v) for (st, sd), v in index.items()},
    }
    p = ROOT / args.json
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"写入 {path}\n写入 {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
