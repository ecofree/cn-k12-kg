"""生成项目现状快照：只报能算出来的数，不写形容词。

    python tools/status_report.py            # 打印摘要
    python tools/status_report.py --json     # 输出 build/status.json

设计原则：每个数字都要能追到一个文件或一条规则，避免"我记得是……"。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT / "tools"))

from validate import VOLUMES, ID_RE  # noqa: E402


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def pct(a, b):
    return round(a / b, 4) if b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    topics = load("topics.json", [])
    deps = load("dependencies.json", [])
    questions = load("questions.json", [])
    standards = load("standards.json", [])
    outline = load("outline.json", {})
    reviews = load("reviews.json", {})
    kc = load("external/astra_2026-10-24/kc_candidates.json", {}) or {}
    std_src = load("external/astra_2026-10-24/standards_sources.json", {}) or {}

    # 外部候选池：已下载、已归一化，但还没有知识点映射，不能算进题目总数
    pool = []
    for p in sorted((ROOT / "inbox" / "candidates").glob("*.jsonl")):
        rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        pool.append(
            {
                "file": str(p.relative_to(ROOT)),
                "n": len(rows),
                "mapped": sum(1 for r in rows if r.get("topicId")),
                "source": rows[0].get("source") if rows else None,
            }
        )

    ext_path = DATA / "questions_ext.json"
    ext = json.loads(ext_path.read_text(encoding="utf-8")) if ext_path.exists() else []

    # 中考池：上海中考卷（Xnye/Smse），知识点来自菁优网考点标签映射
    zh_path = ROOT / "inbox" / "candidates" / "smse_junior.jsonl"
    zh = ([json.loads(ln) for ln in zh_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
          if zh_path.exists() else [])

    # --- 覆盖 ---
    filled = [t for t in topics if t.get("review_status") != "skeleton"]
    by_stage = collections.Counter()
    for t in topics:
        v = t["volume"]
        stage = VOLUMES[[k for k, val in VOLUMES.items() if val[0] == v][0]][3] if v else "?"
        by_stage[stage] += 1

    granularity = {}
    for vol in outline.get("volumes", []):
        granularity[vol["volume"]] = {
            "stage": vol.get("stage"),
            "granularity": vol.get("granularity"),
            "chapters": len(vol.get("chapters", [])),
            "leaves": sum(len(c.get("leaves", [])) for c in vol.get("chapters", [])),
            "status": vol.get("status"),
            "activities": len(vol.get("activities", [])),
        }

    # --- 题库 ---
    by_type = collections.Counter(q["type"] for q in questions)
    by_diff = collections.Counter(q.get("difficulty") for q in questions)
    verifiable = sum(1 for q in questions if q.get("verify"))
    per_topic = collections.Counter(q["topicId"] for q in questions)
    reviewed = {qid: r for qid, r in reviews.items() if r.get("verdict") in ("pass", "fail", "skip")}
    review_pass = sum(1 for r in reviewed.values() if r["verdict"] == "pass")
    review_fail = sum(1 for r in reviewed.values() if r["verdict"] == "fail")
    review_skip = sum(1 for r in reviewed.values() if r["verdict"] == "skip")
    never_reviewed = len(questions) - len(reviewed)

    # --- 图 ---
    hard = sum(1 for e in deps if e["strength"] == "hard")
    soft = len(deps) - hard
    has_hard = {e["topicId"] for e in deps if e["strength"] == "hard"}
    has_any = {e["topicId"] for e in deps}
    only_soft = [t["id"] for t in topics if t["id"] in has_any and t["id"] not in has_hard]
    roots = [t["id"] for t in topics if t["id"] not in has_any]
    dependents = {e["prerequisiteId"] for e in deps}
    leaves = [t["id"] for t in topics if t["id"] not in dependents]

    # --- 课标 ---
    std_verbatim = sum(1 for s in standards if s.get("verbatim"))
    std_by_band = collections.Counter(s.get("band") for s in standards)

    snapshot = {
        "topics": {
            "total": len(topics),
            "filled": len(filled),
            "by_stage": dict(by_stage),
            "volumes": len({t["volume"] for t in topics}),
        },
        "outline": granularity,
        "questions": {
            "total": len(questions),
            "by_type": dict(by_type),
            "by_difficulty": dict(by_diff),
            "verifiable": verifiable,
            "verifiable_rate": pct(verifiable, len(questions)),
            "min_per_topic": min(per_topic.values()) if per_topic else 0,
            "max_per_topic": max(per_topic.values()) if per_topic else 0,
            "avg_per_topic": round(len(questions) / max(len(topics), 1), 2),
            "topics_without_question": sum(1 for t in topics if t["id"] not in per_topic),
        },
        "external_questions": {
            "total": len(ext),
            "topics_covered": len({q["topicId"] for q in ext}),
            "usable": sum(1 for q in ext if q.get("usable_for_diagnosis")),
            "answer_check": dict(collections.Counter(
                (q.get("answer_check") or {}).get("status") for q in ext)),
            "source": dict(collections.Counter(q.get("source") for q in ext)),
            "by_admission_level": dict(collections.Counter(
                q.get("admission_level") for q in ext)),
        },
        "external_pool": {
            "files": pool,
            "total": sum(p["n"] for p in pool),
            "mapped": sum(p["mapped"] for p in pool),
            "note": "候选池只做参考，未映射知识点前不进诊断（D23）",
        },
        "zhongkao_pool": {
            "papers": len({r["paper"] for r in zh}) if zh else 0,
            "total": len(zh),
            "by_type": dict(collections.Counter(r["type"] for r in zh)),
            "topics_covered": len({r["topicId"] for r in zh if r.get("topicId")}),
            "with_answer": sum(1 for r in zh if r.get("answer")),
            "note": "上海中考（Xnye/Smse），知识点来自菁优网考点标签映射（D29）",
        },
        "review": {
            "reviewed": len(reviewed),
            "pass": review_pass,
            "fail": review_fail,
            "skip": review_skip,
            "never_reviewed": never_reviewed,
            "coverage": pct(len(reviewed), len(questions)),
            "teacher_reviewed": sum(1 for r in reviewed.values() if r.get("teacher_reviewed")),
            "reviewer_types": dict(collections.Counter(r.get("reviewer_type") for r in reviewed.values())),
            "fixed_pending_recheck": sum(
                1 for r in reviewed.values()
                if (r.get("resolution") or {}).get("status") == "fixed_pending_recheck"
            ),
        },
        "graph": {
            "edges": len(deps),
            "hard": hard,
            "soft": soft,
            "roots": len(roots),
            "leaves": len(leaves),
            "only_soft_topics": len(only_soft),
            "avg_prereq": round(len(deps) / max(len(topics), 1), 2),
        },
        "standards": {
            "total": len(standards),
            "verbatim": std_verbatim,
            "by_band": dict(sorted(std_by_band.items(), key=lambda x: (x[0] is None, x[0]))),
            "with_source_index": sum(1 for v in (std_src.get("items") or {}).values() if v.get("index")),
        },
        "external_kc": {
            "total": kc.get("total_kc"),
            "mapped": kc.get("mapped"),
            "covered_topics": kc.get("covered_topics"),
            "mapping_rate": pct(kc.get("mapped") or 0, kc.get("total_kc") or 0),
            "topic_cover_rate": pct(kc.get("covered_topics") or 0, len(topics)),
        },
        "response_data": {"rows": 0, "note": "data/external/.../response_stats.json 为空表"},
    }

    if args.json:
        BUILD.mkdir(exist_ok=True)
        (BUILD / "status.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"已写出 build/status.json")
        return 0

    t, q, r, g, s, k = (snapshot["topics"], snapshot["questions"], snapshot["review"],
                        snapshot["graph"], snapshot["standards"], snapshot["external_kc"])
    print(f"知识点 {t['total']}（{t['volumes']} 册，{dict(t['by_stage'])}）")
    print(f"题目 {q['total']}：{q['by_type']}；难度 {q['by_difficulty']}")
    print(f"  可机器验算 {q['verifiable']}（{q['verifiable_rate']:.0%}），"
          f"每点 {q['min_per_topic']}–{q['max_per_topic']} 道，均 {q['avg_per_topic']}")
    print(f"复核：已审 {r['reviewed']}（pass {r['pass']} / fail {r['fail']} / skip {r['skip']}），"
          f"覆盖 {r['coverage']:.0%}，从未审过 {r['never_reviewed']}，教师审核 {r['teacher_reviewed']}")
    if r.get("fixed_pending_recheck"):
        print(f"  其中 {r['fixed_pending_recheck']} 条 fail 已修订，等待复检（verdict 未改写）")
    print(f"先修：{g['edges']} 边（hard {g['hard']} / soft {g['soft']}），根 {g['roots']}，末端 {g['leaves']}，"
          f"只有 soft 的点 {g['only_soft_topics']}")
    print(f"课标：{s['total']} 条，verbatim={s['verbatim']}，有出处索引 {s['with_source_index']}")
    print(f"外部 KC：{k['mapped']}/{k['total']} 有候选（{k['mapping_rate']:.1%}），覆盖 {k['covered_topics']} 个点"
          f"（{k['topic_cover_rate']:.1%}）")
    print(f"真实作答：{snapshot['response_data']['rows']} 条")
    e = snapshot["external_questions"]
    if e["total"]:
        print(f"外部题：{e['total']} 道，覆盖 {e['topics_covered']} 个点，"
              f"可进诊断 {e['usable']}（准入分级 {e.get('by_admission_level')}）")
    z = snapshot.get("zhongkao_pool") or {}
    if z.get("total"):
        print(f"中考池：{z['total']} 道（{z['papers']} 份卷），覆盖 {z['topics_covered']} 个点，"
              f"类型 {z['by_type']}，未进诊断")
    p = snapshot["external_pool"]
    if p["total"]:
        print(f"候选池：{p['total']} 道待映射（已映射 {p['mapped']}），"
              f"{len(p['files'])} 个文件，未进诊断")
    return 0


if __name__ == "__main__":
    sys.exit(main())
