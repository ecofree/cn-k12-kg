"""生成竞赛资料的审核候选与已通过目标关联审核的推荐索引。

同板块、年级范围与设计难度只产生 candidates；samples 仅包含有内容哈希
绑定的教师/学科专家批准记录。不将候选数量作为教材目标覆盖率。
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

    samples, candidates, missing = {}, {}, []
    per_strand = collections.Counter()
    for t in topics:
        info = tier_data["topics"].get(t["id"])
        if not info or info["tier"] != "core":
            continue
        candidates[t["id"]] = [x["id"] for x in challenge.candidates_for(t, info["cluster"], index)]
        picked = challenge.sample_for(t, info["cluster"], index)
        if picked:
            samples[t["id"]] = [x["id"] for x in picked]
            per_strand[info["cluster"]] += len(picked)
        else:
            missing.append({"topicId": t["id"], "name": t["name"],
                            "cluster": info["cluster"]})

    out = {
        "schema": 2,
        "note": "samples 为已审核的目标关联；candidates 仅为板块检索候选，不计覆盖，不进诊断池",
        "alignment_status": "approved_only",
        "candidates": candidates,
        "count": len(samples),
        "samples": samples,
    }
    path = DATA / "challenge" / "links.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    core = [t for t in topics
            if tier_data["topics"].get(t["id"], {}).get("tier") == "core"]
    print(f"核心点 {len(core)}，获目标关联审核 {len(samples)}"
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
        "candidate_topics": sum(bool(v) for v in candidates.values()),
        "difficulty_status": "design_labels_uncalibrated",
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
