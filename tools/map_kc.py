"""外部知识点体系映射工具（用于与 XES3G5M、其他版本教材对齐）。

XES3G5M 有 865 个 KC、树形结构，与本项目 id 体系不同，必须先做名称映射再谈校准。
本工具只做候选召回（字面相似度），不代替人工确认：输出里保留 score 与 rank，
按 score 排序后人工抽检，抽检结果回填到 review 列。

输入 CSV 需含表头：id,name（可选 parent_id）
输出 CSV：external_id,external_name,rank,topic_id,topic_name,score,decision

用法：
    python tools/map_kc.py --kc external_kc.csv --out build/kc_mapping.csv
    python tools/map_kc.py --kc external_kc.csv --top 5 --threshold 0.5
"""
from __future__ import annotations

import argparse
import csv
import difflib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.graph import load_graph  # noqa: E402


def normalize(text: str) -> str:
    """去掉括号注释与空白，降低表述差异带来的噪声。"""
    text = (text or "").strip()
    for left, right in (("（", "）"), ("(", ")")):
        while left in text and right in text:
            start = text.index(left)
            end = text.index(right, start) + 1
            text = text[:start] + text[end:]
    return "".join(text.split())


def score(a: str, b: str) -> float:
    """字面相似度：整体相似度与"短串被长串包含"取较大值。

    包含关系按长度比折算，不做人为加分——否则"有理数"会被"有理数的除法"骗到高分。
    这类泛称本来就应该落到 low，交给人判断。
    """
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        ratio = max(ratio, len(shorter) / len(longer))
    return round(min(ratio, 1.0), 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kc", required=True, help="外部知识点 CSV（表头 id,name）")
    parser.add_argument("--out", default=str(ROOT / "build" / "kc_mapping.csv"))
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="达到此分数视为高置信候选；0.6 到该值之间标 review，需人工看")
    args = parser.parse_args()

    graph = load_graph()
    rows = list(csv.DictReader(open(args.kc, encoding="utf-8-sig")))
    if not rows or "name" not in rows[0]:
        print("输入 CSV 需要 id,name 两列")
        return 2

    review_floor = 0.6
    out_rows = []
    stats = {"high": 0, "review": 0, "low": 0}
    for row in rows:
        name = row.get("name", "")
        ranked = sorted(((score(name, t["name"]), t["id"], t["name"]) for t in graph.topics),
                        reverse=True)[:args.top]
        best = ranked[0][0] if ranked else 0.0
        if best >= args.threshold:
            decision = "high"
        elif best >= review_floor:
            decision = "review"
        else:
            decision = "low"
        stats[decision] += 1
        for rank, (s, tid, tname) in enumerate(ranked, 1):
            out_rows.append({
                "external_id": row.get("id", ""),
                "external_name": name,
                "rank": rank,
                "topic_id": tid,
                "topic_name": tname,
                "score": s,
                "decision": decision if rank == 1 else "",
            })

    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"外部知识点 {len(rows)} 条，候选 {len(out_rows)} 行 -> {args.out}")
    print(f"高置信 {stats['high']} 条，待人工确认 {stats['review']} 条，疑似无对应 {stats['low']} 条")
    print("提醒：字面相似不等于教学等价，high 也要抽检；泛称（如“有理数”）会自动落到 review。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
