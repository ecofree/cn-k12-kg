"""按 data/stage_policy.json 检查三学段策略的达成情况。

为什么需要：
    三学段各自独立，不强行一致。但「独立」不等于「不管」——
    每段的目标（题型配比、难度分层、题量下限、真源准入）要能被检查，
    否则策略文件就只是文档，落地情况无人知晓。

本工具输出每段的达成率与缺口，用于决定下一轮补什么。

用法：
    python tools/check_stage_policy.py
    python tools/check_stage_policy.py --json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 外部真题来源（自有题不含这些；自产题的 source 是 self-generated-*）
EXTERNAL_SOURCES = {"cmm-math", "gaokao-tex", "smse-shanghai-zhongkao"}
sys.path.insert(0, str(ROOT / "cnkg"))


def stage_of(t: dict) -> str:
    g = t.get("grade")
    if g is None:
        return "unknown"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def link_ids(s: str, tmap: dict) -> list[str]:
    """从 'm5b.2.1 分数的认识 / m4b.4.1 小数的认识' 中提取知识点 id。

    只保留 tmap 里真实存在的 id——名称里的数字（如「100以内」）不会误判。
    历史教训：早期版本拿 id 去和知识点**名字**做前缀匹配，永远匹配不上，
    7 条承接链被全量误报为缺失（2026-10-25 修复）。
    """
    ids: list[str] = []
    for tok in s.replace("，", " ").replace("/", " ").split():
        if tok in tmap and tok not in ids:
            ids.append(tok)
    return ids


def link_satisfied(link: dict, deps: list, tmap: dict) -> bool:
    """一条承接链是否已落成图上的边。

    from/to 可以是多个 id（「A / B」形式），任一端点间有边即算落地。
    """
    from_ids, to_ids = link_ids(link["from"], tmap), link_ids(link["to"], tmap)
    if not (from_ids and to_ids):
        return False
    existing = {(e["prerequisiteId"], e.get("topicId")) for e in deps}
    return any((f, t) in existing for f in from_ids for t in to_ids)


def build_report(topics, own, ext, policy, deps) -> dict:
    """构造分学段对账报告。

    纯函数：入参给定，输出确定。测试直接调它断言口径——
    2026-10-26 就是在这里踩过「诊断池漏算了自有题」的错
    （`usable` 从 allq 过滤，而自有题没有 usable_for_diagnosis 字段，
    结果 2235 道自有题整段消失，小学诊断池不足被虚报成 169 点）。
    """
    allq = own + ext

    by_stage = collections.defaultdict(list)
    for t in topics:
        by_stage[stage_of(t)].append(t)

    # 注意：自有题没有 usable_for_diagnosis 字段（它们本来就是诊断主库），
    # 不能直接 allq 过滤，否则会把 2235 道自有题整段漏掉——一度算出
    # 「小学诊断池不足 169 点」的假数。诊断池 = 自有全部 + 外部可诊断。
    usable = list(own) + [q for q in ext if q.get("usable_for_diagnosis")]

    # 两套账，必须都报：
    #   all    —— 库里所有题，含 tag-mapped / compound-reference / broken
    #   usable —— 诊断真正抽得到的题（自有 + usable_for_diagnosis）
    # 只报 all 会系统性地高估就绪度：不可诊断的题照样把「题量下限」「难度分层」
    # 填满，但诊断器一道也抽不到。历史教训：「三学段题量下限 0 缺口」是 all
    # 口径的产物，换到 usable 口径是 24 / 41 / 19 个点不足（2026-10-26 实测，
    # 见 docs/audit-2026-10-26.md）。两个数都是真的，混着报就是假的。
    def stats(pool):
        per = collections.Counter()
        types = collections.defaultdict(collections.Counter)
        diffs = collections.defaultdict(set)
        for q in pool:
            tids = q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])
            for t in tids:
                per[t] += 1
                types[t][q.get("type", "?")] += 1
                diffs[t].add(q.get("difficulty", "?"))
        return per, types, diffs

    per_topic, type_per_topic, diff_levels = stats(allq)
    per_usable, type_per_usable, diff_usable = stats(usable)

    source_per_topic = collections.defaultdict(collections.Counter)
    diag_per_topic = collections.Counter()
    composite_per_topic = collections.Counter()
    for q in allq:
        tids = q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])
        for t in tids:
            source_per_topic[t][q.get("source", "own")] += 1
            if q.get("usable_for_diagnosis"):
                diag_per_topic[t] += 1
            if len(tids) > 1:
                composite_per_topic[t] += 1

    report = {}
    for stage in ("primary", "junior", "senior"):
        cfg = policy[stage]
        ts = by_stage[stage]
        ids = {t["id"] for t in ts}

        # 题量下限（两个口径各算一次）
        need = cfg["targets"]["min_questions_per_topic"]
        short = [t["id"] for t in ts if per_topic[t["id"]] < need]
        short_usable = [t["id"] for t in ts if per_usable[t["id"]] < need]
        # 题型多样性：至少 2 种题型
        one_type = [t["id"] for t in ts if len(type_per_topic[t["id"]]) < 2]
        one_type_usable = [t["id"] for t in ts if len(type_per_usable[t["id"]]) < 2]
        # 难度分层：至少 basic + standard 两档
        one_diff = [t["id"] for t in ts if len(diff_levels[t["id"]]) < 2]
        one_diff_usable = [t["id"] for t in ts if len(diff_usable[t["id"]]) < 2]

        # 题型配比（按该段题量加权）
        def mix_of(pool):
            mix = collections.Counter()
            for q in pool:
                tids = q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])
                if any(t in ids for t in tids):
                    mix[q.get("type", "?")] += 1
            total = sum(mix.values()) or 1
            return {k: round(v / total, 3) for k, v in mix.items()}

        actual_mix = mix_of(allq)
        actual_mix_usable = mix_of(usable)

        # 真源诊断准入
        src_diag = collections.Counter()
        for q in allq:
            tids = q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])
            if any(t in ids for t in tids) and q.get("usable_for_diagnosis"):
                src_diag[q.get("source", "own")] += 1

        report[stage] = {
            "name": cfg["display_name"],
            "topics": len(ts),
            "questions_pairs": sum(per_topic[t["id"]] for t in ts),
            "questions_pairs_usable": sum(per_usable[t["id"]] for t in ts),
            "avg_per_topic": round(
                sum(per_topic[t["id"]] for t in ts) / max(len(ts), 1), 2),
            "median_per_topic": sorted(
                (per_topic[t["id"]] for t in ts), reverse=True)[len(ts) // 2] if ts else 0,
            "target_min_per_topic": need,
            "below_target": len(short),
            "below_target_sample": short[:8],
            # 诊断口径：这才是诊断器真正能抽到的题
            "below_target_usable": len(short_usable),
            "below_target_usable_sample": short_usable[:8],
            "single_type_topics": len(one_type),
            "single_type_topics_usable": len(one_type_usable),
            "single_difficulty_topics": len(one_diff),
            "single_difficulty_topics_usable": len(one_diff_usable),
            "type_mix_target": cfg["question_mix"],
            "type_mix_actual": actual_mix,
            "type_mix_actual_usable": actual_mix_usable,
            "difficulty_mix_target": cfg["difficulty_mix"],
            "difficulty_actual": dict(
                collections.Counter(
                    q.get("difficulty", "?") for q in allq
                    if any(t in ids for t in (q.get("topicIds") or [q.get("topicId")])))
            ),
            "difficulty_actual_usable": dict(
                collections.Counter(
                    q.get("difficulty", "?") for q in usable
                    if any(t in ids for t in (q.get("topicIds") or [q.get("topicId")])))
            ),
            "diagnosable_by_source": dict(src_diag),
            # 诊断池口径：自有题 + 外部可诊断题
            "topics_with_diagnosable_questions": sum(
                1 for t in ts if per_usable[t["id"]] > 0),
            # 外部可诊断题覆盖（不含自有题）：衡量「有多少点是真题撑着的」
            "topics_with_external_diagnosable": sum(
                1 for t in ts if diag_per_topic[t["id"]] > 0),
            "topics_without_external_diagnosable": sum(
                1 for t in ts if diag_per_topic[t["id"]] == 0),
            # exam_scope：textbook_only 的点是「真题不单独考」，不是数据缺口，
            # 单独统计出来，避免把它们和「有题没映射上」的真缺口混在一起。
            "exam_scope": dict(
                collections.Counter(t.get("exam_scope", "?") for t in ts)),
            "textbook_only_topics": sum(
                1 for t in ts if t.get("exam_scope") == "textbook_only"),
            # 外部题真缺口：该点没有任何外部真题，且 exam_scope 判定真题确实考到。
            # textbook_only 的点不计入——那是「真题不单独考」或「当前题源无供给」，
            # 不是数据缺口；source_gap 是「真题考到但题源没供上」，单独统计。
            # 注意不能用 source=='own' 判断：自产题的 source 是 self-generated-*，
            # 只有手写题没有 source 字段。这里按「有无外部来源」判断。
            "external_gap_real": sum(
                1 for t in ts
                if not (source_per_topic[t["id"]].keys() & EXTERNAL_SOURCES)
                and t.get("exam_scope") == "source_gap"),
            "source_gap_topics": [t["id"] for t in ts
                                  if t.get("exam_scope") == "source_gap"],
            "composite_topics": sum(1 for t in ts if composite_per_topic[t["id"]] > 0),
        }

    # 跨学段承接链检查
    tmap = {t["id"]: t for t in topics}
    cross = []
    for e in deps:
        a, b = tmap.get(e["prerequisiteId"]), tmap.get(e.get("topicId"))
        if a and b and stage_of(a) != stage_of(b):
            cross.append((stage_of(a), stage_of(b)))
    cross_pairs = collections.Counter(cross)
    required = policy["cross_stage"]["required_links"]
    missing = [
        {**link,
         "parsed_from_ids": link_ids(link["from"], tmap),
         "parsed_to_ids": link_ids(link["to"], tmap)}
        for link in required
        if not link_satisfied(link, deps, tmap)
    ]
    report["cross_stage"] = {
        "cross_edges": len(cross),
        "by_pair": {f"{a}->{b}": n for (a, b), n in sorted(cross_pairs.items())},
        "required_links": len(required),
        "missing_links": missing,
    }

    return report


def render(report: dict) -> None:
    for stage in ("primary", "junior", "senior"):
        r = report[stage]
        print("=" * 72)
        print(f"{r['name']}（{stage}）")
        print("=" * 72)
        print(f"知识点 {r['topics']}  题点对 {r['questions_pairs']}（可诊断 "
              f"{r['questions_pairs_usable']}）  平均 {r['avg_per_topic']} 题/点  "
              f"中位 {r['median_per_topic']}")
        print(f"题量下限 {r['target_min_per_topic']}："
              f"诊断池不足 {r['below_target_usable']} 个点 "
              f"{r['below_target_usable_sample']}")
        print(f"             全库口径（含不可诊断）不足 {r['below_target']} 个点 "
              f"{r['below_target_sample']}")
        print(f"只有单一题型的点 {r['single_type_topics']}（诊断池 "
              f"{r['single_type_topics_usable']}）   "
              f"只有单一难度的点 {r['single_difficulty_topics']}（诊断池 "
              f"{r['single_difficulty_topics_usable']}）")
        print(f"题型配比 目标 {r['type_mix_target']}")
        print(f"         实际 全库 {r['type_mix_actual']}")
        print(f"         实际 诊断池 {r['type_mix_actual_usable']}")
        print(f"难度分布 目标 {r['difficulty_mix_target']}")
        print(f"         实际 全库 {r['difficulty_actual']}")
        print(f"         实际 诊断池 {r['difficulty_actual_usable']}")
        print(f"可诊断题来源 {r['diagnosable_by_source']}")
        print(f"诊断池覆盖 {r['topics_with_diagnosable_questions']}/{r['topics']} 点"
              f"（其中外部真题撑着 {r['topics_with_external_diagnosable']} 点，"
              f"纯自有题 {r['topics_without_external_diagnosable']} 点）"
              f"   含复合题的点 {r['composite_topics']}")
        print(f"exam_scope {r['exam_scope']}   "
              f"其中真题不单独考/题源无供给 {r['textbook_only_topics']} 点")
        print(f"外部题真缺口（真题考到但题源没供上）{r['external_gap_real']} 点"
              + (f"  {r['source_gap_topics']}" if r["source_gap_topics"] else ""))
        print()

    cs = report["cross_stage"]
    print("=" * 72)
    print("跨学段承接链")
    print("=" * 72)
    print(f"现有跨学段边 {cs['cross_edges']} 条 {cs['by_pair']}")
    print(f"策略要求的承接链 {cs['required_links']} 条，缺失 {len(cs['missing_links'])} 条：")
    for m in cs["missing_links"]:
        print(f"   ✗ {m['from']}  →  {m['to']}")
        print(f"     理由：{m['reason']}")
        if m.get("parsed_from_ids") or m.get("parsed_to_ids"):
            print(f"     解析出的 id：from={m.get('parsed_from_ids')} to={m.get('parsed_to_ids')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))
    policy = json.loads((DATA / "stage_policy.json").read_text(encoding="utf-8"))
    deps = json.loads((DATA / "dependencies.json").read_text(encoding="utf-8"))

    report = build_report(topics, own, ext, policy, deps)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    render(report)


if __name__ == "__main__":
    main()
