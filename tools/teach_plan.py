"""教学计划：按分层算「还差多少张卡、多少道题」，而不是按 477 个点平摊。

输出三张清单：
  1. 核心点（core）—— 每个点一张完整讲解卡 + 4/6/8 道题
  2. 工具簇（tool）—— 同簇同册合并一张速查卡 + 每点 1–2 道验证题
  3. 补题工单 —— 核心点里题量不够的，按缺口排序

为什么不再用「477 个点 × 4 题」当目标：小学 216 个点里 157 个是工具点，
给它们各配 4 道题等于把 628 道题花在「熟练」上，而真正需要练的
46 个小学核心点反而摊薄。分层不是降低标准，是把题放到会迁移的地方。

用法：
    python tools/teach_plan.py
    python tools/teach_plan.py --md build/teach_plan.md
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cnkg import tiers  # noqa: E402
from cnkg.evidence import is_own  # noqa: E402

DATA = ROOT / "data"
STAGES = ("primary", "junior", "senior")
DIAGNOSABLE = {"verified", "corroborated", "explained"}

VOLUME_ORDER = [
    "一上", "一下", "二上", "二下", "三上", "三下", "四上", "四下", "五上", "五下",
    "六上", "六下", "七上", "七下", "八上", "八下", "九上", "九下",
    "必修一", "必修二", "选必一", "选必二", "选必三",
]


def idx(q: dict) -> list[str]:
    return q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])


def usable(q: dict) -> bool:
    """这道题能不能进练习/诊断。与 cnkg.graph.load_graph 同口径。"""
    if q.get("challenge"):
        return False
    if is_own(q):
        return True
    return bool(q.get("usable_for_diagnosis")) and q.get("evidence_level") in DIAGNOSABLE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="build/teach_plan.json")
    ap.add_argument("--md", default="build/teach_plan.md")
    ap.add_argument("--cards", default="build/teach_coverage.json")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))
    tier_data = tiers.load()
    if not tier_data["topics"]:
        print("缺 data/topic_tiers.json，先跑 tools/classify_tiers.py")
        return 1
    info = tier_data["topics"]
    clusters = tier_data["clusters"]

    # 现有讲解卡：完整卡按 topicId，速查卡按 brief_group
    full_cards = {p.stem for p in (DATA / "teach").glob("*.json")}
    brief_dir = DATA / "teach" / "clusters"
    brief_cards = {p.stem for p in brief_dir.glob("*.json")} if brief_dir.exists() else set()

    pool = own + [q for q in ext if usable(q)]
    per = collections.Counter()
    types = collections.defaultdict(set)
    diffs = collections.defaultdict(set)
    judge = collections.Counter()
    for q in pool:
        lv = q.get("evidence_level")
        can_judge = bool(q.get("verify")) if is_own(q) else lv in ("verified", "corroborated")
        for tid in idx(q):
            per[tid] += 1
            types[tid].add(q.get("type"))
            diffs[tid].add(q.get("difficulty"))
            if can_judge:
                judge[tid] += 1

    core_rows, tool_rows = [], []
    for t in topics:
        i = info[t["id"]]
        row = {
            "topicId": t["id"],
            "name": t["name"],
            "grade": t.get("grade"),
            "volume": t.get("volume"),
            "stage": tiers.stage_of(t),
            "cluster": i["cluster"],
            "questions": per[t["id"]],
            "target": i["practice_target"],
            "has_non_choice": bool(types[t["id"]] - {"choice"}),
            "judgeable": judge[t["id"]],
            "levels": sorted(diffs[t["id"]]),
        }
        if i["tier"] == "core":
            row["card"] = "full"
            row["has_card"] = t["id"] in full_cards
            row["gap"] = max(0, i["practice_target"] - per[t["id"]])
            # 「有题」不等于「有应用题」。核心点最该缺的是迁移题，
            # 8 道 basic 也能把题量缺口填成 0，但学生仍然只练了执行。
            row["has_application"] = bool({"standard", "advanced"} & set(row["levels"]))
            row["ready"] = (row["has_card"] and row["gap"] == 0
                            and row["has_non_choice"] and row["judgeable"] > 0
                            and row["has_application"])
            core_rows.append(row)
        else:
            row["card"] = "brief"
            row["brief_group"] = i["brief_group"]
            row["has_card"] = i["brief_group"] in brief_cards
            row["gap"] = max(0, 1 - per[t["id"]])
            tool_rows.append(row)

    def order(rows):
        return sorted(rows, key=lambda r: (
            VOLUME_ORDER.index(r["volume"]) if r.get("volume") in VOLUME_ORDER else 99,
            r["grade"] or 99, r["topicId"]))

    summary = {}
    for st in STAGES:
        cs = [r for r in core_rows if r["stage"] == st]
        ts = [r for r in tool_rows if r["stage"] == st]
        groups = {r["brief_group"] for r in ts}
        summary[st] = {
            "core": len(cs),
            "core_ready": sum(1 for r in cs if r["ready"]),
            "core_cards": sum(1 for r in cs if r["has_card"]),
            "core_missing_cards": sum(1 for r in cs if not r["has_card"]),
            "core_question_gap": sum(r["gap"] for r in cs),
            "core_without_application": sum(1 for r in cs if not r["has_application"]),
            "tool": len(ts),
            "tool_groups": len(groups),
            "tool_groups_with_card": len(groups & brief_cards),
            "tool_question_gap": sum(r["gap"] for r in ts),
        }

    print(f"{'学段':<8}{'核心点':>6}{'有卡':>5}{'待写卡':>7}{'题缺口':>7}"
          f"{'无应用题':>8}{'工具点':>7}{'速查组':>7}{'已写':>5}")
    for st in STAGES:
        x = summary[st]
        print(f"{st:<8}{x['core']:>6}{x['core_cards']:>5}{x['core_missing_cards']:>7}"
              f"{x['core_question_gap']:>7}{x['core_without_application']:>8}"
              f"{x['tool']:>7}{x['tool_groups']:>7}"
              f"{x['tool_groups_with_card']:>5}")
    total_cards = sum(summary[s]["core_missing_cards"] for s in STAGES)
    total_brief = sum(summary[s]["tool_groups"] - summary[s]["tool_groups_with_card"]
                      for s in STAGES)
    print(f"\n还差：完整卡 {total_cards} 张 + 速查卡 {total_brief} 张"
          f"（合计 {total_cards + total_brief}，逐点平摊要 477 张）")

    out = {
        "generated_by": "tools/teach_plan.py",
        "summary": summary,
        "core": order(core_rows),
        "tool": order(tool_rows),
    }
    p = ROOT / args.json
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 人类可读版：先写核心点，按册次；工具点只列组
    lines = ["# 教学计划（按分层）", ""]
    lines.append(f"完整卡还差 {total_cards} 张，速查卡还差 {total_brief} 张。")
    lines.append("")
    for st in STAGES:
        x = summary[st]
        lines.append(f"## {st}：核心点 {x['core']}，待写卡 {x['core_missing_cards']}，"
                     f"题缺口 {x['core_question_gap']}")
        lines.append("")
        lines.append("| 册次 | 知识点 | 现有题 | 目标 | 缺口 | 有应用题 | 有卡 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in order([r for r in core_rows if r["stage"] == st])[:12]:
            lines.append(f"| {r['volume']} | {r['name']} | {r['questions']} | "
                         f"{r['target']} | {r['gap']} | "
                         f"{'有' if r['has_application'] else '**没有**'} | "
                         f"{'是' if r['has_card'] else '否'} |")
        lines.append("")
    md = ROOT / args.md
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写入 {p}\n写入 {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
