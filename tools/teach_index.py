"""把讲解卡导成一张可抽查的清单（CSV + Markdown 摘要）。

写了几十张卡之后，最需要的不是再写，而是**能快速抽查**：
哪张卡没写原理、哪张只挂了两道题、哪张的过关标准是空话。
这个脚本不做判断，只把关键字段摊平，方便人一眼扫过去。
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"


def main() -> int:
    topics = {t["id"]: t for t in json.loads(
        (DATA / "topics.json").read_text(encoding="utf-8"))}
    tiers = json.loads((DATA / "topic_tiers.json").read_text(encoding="utf-8"))["topics"]
    rows = []

    for path in sorted((DATA / "teach").glob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        tid = card["topicId"]
        t = topics[tid]
        rows.append({
            "kind": "完整卡",
            "key": tid,
            "stage": "primary" if t["grade"] <= 6 else ("junior" if t["grade"] <= 9 else "senior"),
            "volume": t.get("volume"),
            "name": t["name"],
            "tier": tiers[tid]["tier"],
            "cluster": tiers[tid]["cluster"],
            "covers": 1,
            "why_chars": len(card.get("why") or ""),
            "signals": len(card.get("when_to_use") or []),
            "steps": len(card.get("steps") or []),
            "pitfalls": len(card.get("pitfalls") or []),
            "practice": "、".join(p["id"] for p in card.get("practice") or []),
            "check": card.get("check") or "",
        })

    for path in sorted((DATA / "teach" / "clusters").glob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "kind": "速查卡",
            "key": f"{card['cluster']}@{card['volume']}",
            "stage": "primary" if topics[card["covers"][0]]["grade"] <= 6 else (
                "junior" if topics[card["covers"][0]]["grade"] <= 9 else "senior"),
            "volume": card["volume"],
            "name": card["name"],
            "tier": "tool",
            "cluster": card["cluster"],
            "covers": len(card["covers"]),
            "why_chars": 0,
            "signals": 0,
            "steps": len(card.get("key_points") or []),
            "pitfalls": len(card.get("pitfalls") or []),
            "practice": "、".join(p["id"] for p in card.get("practice") or []),
            "check": card.get("check") or "",
        })

    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / "teach_index.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    full = [r for r in rows if r["kind"] == "完整卡"]
    brief = [r for r in rows if r["kind"] == "速查卡"]
    print(f"完整卡 {len(full)} 张，速查卡 {len(brief)} 张（覆盖 {sum(r['covers'] for r in brief)} 个工具点）")
    print(f"完整卡练习数：最少 {min(len(r['practice'].split('、')) for r in full)}，"
          f"平均 {sum(len(r['practice'].split('、')) for r in full) / len(full):.2f}")
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
