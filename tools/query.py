"""命令行查询与诊断演示。

用法示例：
    python tools/query.py stats
    python tools/query.py ancestors m7a.5.2
    python tools/query.py descendants m7a.1.1
    python tools/query.py path m7a.5.2
    python tools/query.py chapter 七上
    python tools/query.py diagnose m7a.5.2 --answers w,w,c,c,c
    python tools/query.py screen 七上 --answers c,c,w,c,c,c,c,c,c,c,c,c,w,c,c,c,c,c,c,c,c,c,c,c,c,c,c,c
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import engine, store  # noqa: E402
from cnkg.graph import VOLUME_ORDER, load_graph  # noqa: E402
from cnkg.answers import display_answer, option_labels  # noqa: E402

VERDICTS = {"c": "correct", "w": "wrong", "s": "skip"}


def fmt_topic(graph, tid):
    t = graph.by_id[tid]
    return f"{tid:<12} {t['name']}（{t['chapter']} {t['section']}）"


def cmd_stats(graph, conn, args):
    topics = graph.topics
    filled = [t for t in topics if t["review_status"] != "skeleton"]
    hard = sum(1 for e in graph.dependencies if e["strength"] == "hard")
    roots = [t["id"] for t in filled if not graph.prereqs[t["id"]]]
    leaves = [t["id"] for t in filled if not graph.dependents[t["id"]]]
    verifiable = sum(1 for q in graph.questions if q.get("verify"))
    print(f"框架 {len(topics)} 点（已填内容 {len(filled)}，骨架 {len(topics) - len(filled)}）")
    print(f"已填部分：先修边 {len(graph.dependencies)}"
          f"（hard {hard} / soft {len(graph.dependencies) - hard}）"
          f"，平均每点 {len(graph.dependencies) / max(len(filled), 1):.2f} 条")
    print(f"题目 {len(graph.questions)}（可自动验算 {verifiable}）")
    print(f"根节点 {len(roots)}    末端节点 {len(leaves)}")
    print("根节点：" + "，".join(f"{t} {graph.name(t)}" for t in roots))

    print("\n== 按册（已填/总数）==")
    for volume in VOLUME_ORDER:
        group = [t for t in topics if t["volume"] == volume]
        if not group:
            continue
        done = sum(1 for t in group if t["review_status"] != "skeleton")
        print(f"  {volume:<5}{done:>3}/{len(group):<4}")

    print("\n== 按章（已填部分）==")
    print(f"{'册':<4}{'章':<22}{'点':>3}{'题':>5}{'边':>4}")
    for row in store.chapter_summary(conn):
        if row["questions"] or row["edges"]:
            print(f"{row['volume']:<4}{row['chapter']:<22}{row['topics']:>3}"
                  f"{row['questions']:>5}{row['edges']:>4}")


def cmd_ancestors(graph, conn, args):
    rows = store.ancestors(conn, args.topic_id)
    if not rows:
        print(f"{args.topic_id} 没有先修知识点（根节点）")
        return 0
    print(f"{fmt_topic(graph, args.topic_id)} 的全部先修（{len(rows)} 个）：")
    for row in rows:
        print(f"  {'  ' * (row['depth'] - 1)}L{row['depth']} {row['id']:<12} {row['name']}")
    return 0


def cmd_descendants(graph, conn, args):
    rows = store.descendants(conn, args.topic_id)
    if not rows:
        print(f"{args.topic_id} 没有后继知识点（末端节点）")
        return 0
    print(f"{fmt_topic(graph, args.topic_id)} 的后继（{len(rows)} 个）：")
    for row in rows:
        print(f"  L{row['depth']} {row['id']:<12} {row['name']}")
    return 0


def cmd_path(graph, conn, args):
    scope = graph.scope([args.topic_id])
    order = graph.topo_order(scope)
    print(f"从零补到「{graph.name(args.topic_id)}」的学习路径（{len(order)} 步）：")
    for i, tid in enumerate(order, 1):
        print(f"  {i:>2}. {fmt_topic(graph, tid)}")
    return 0


def cmd_chapter(graph, conn, args):
    volume = args.volume
    ids = [t["id"] for t in graph.topics if t["volume"] == volume]
    if not ids:
        print(f"{volume} 没有知识点")
        return 1
    order = graph.topo_order(ids)
    print(f"{volume} 共 {len(order)} 个知识点，按先修顺序：")
    current = None
    for tid in order:
        t = graph.by_id[tid]
        if t["chapter"] != current:
            current = t["chapter"]
            print(f"\n  [{current}]")
        prereqs = "、".join(graph.name(e["prerequisiteId"]) for e in graph.prereqs[tid]) or "—"
        print(f"    {tid:<12} {t['name']:<16} ← {prereqs}")
    return 0


def _parse_answers(raw):
    if not raw:
        return []
    out = []
    for token in raw.split(","):
        token = token.strip().lower()
        if token in VERDICTS:
            out.append(VERDICTS[token])
        elif token in VERDICTS.values():
            out.append(token)
    return out


def _run_session(graph, targets, mode, answers, limit=None, show_answer=True):
    session = engine.create_session(graph, targets, mode=mode, limit=limit)
    idx = 0
    while True:
        item = engine.next_item(graph, session)
        if item is None:
            break
        tid, question = item["topicId"], item["question"]
        verdict = answers[idx] if idx < len(answers) else "correct"
        idx += 1
        print(f"\n[{idx:>2}] {graph.name(tid)}（{tid}，第 {item['attempt']} 题，"
              f"{question.get('difficulty')}）")
        if session["current"] and session["current"].get("escalated"):
            print("     注意：题库里没有同级或更易的题，本题被迫升难度，结论存疑。")
        print(f"     {question['prompt']}")
        if question.get("choices"):
            for label, _identity, text in option_labels(question):
                print(f"       {label}. {text}")
        print(f"     模拟作答：{verdict}")
        if show_answer:
            print(f"     参考答案：{display_answer(question)}"
                  f"    解析：{question.get('explanation', '')}")
        engine.apply_answer(graph, session, verdict)

    summary = engine.summarize(graph, session)
    print("\n== 诊断结果 ==")
    print(engine.verdict_label(summary))
    if summary.get("escalated"):
        print(f"注：其中 {summary['escalated']} 道题因题库缺同级题被迫升难度，相关判定只能当线索。")
    if summary["weak"]:
        print("薄弱：" + "、".join(graph.name(t) for t in summary["weak"]))
        print("根因：" + "、".join(graph.name(t) for t in summary["roots"]))
    if summary["shaky"]:
        print("不稳：" + "、".join(graph.name(t) for t in summary["shaky"]))
    print("建议路径：" + " → ".join(graph.name(t) for t in summary["path"]))
    return summary


def cmd_diagnose(graph, conn, args):
    print(f"== 诊断：{fmt_topic(graph, args.topic_id)} ==")
    _run_session(graph, [args.topic_id], "diagnose",
                 _parse_answers(args.answers), limit=args.limit)
    return 0


def cmd_screen(graph, conn, args):
    ids = [t["id"] for t in graph.topics if t["volume"] == args.volume]
    if not ids:
        print(f"{args.volume} 没有知识点")
        return 1
    print(f"== 整册筛查：{args.volume}（{len(ids)} 个知识点）==")
    summary = _run_session(graph, ids, "screen",
                           _parse_answers(args.answers), limit=args.limit,
                           show_answer=False)
    print("\n== 按章分布 ==")
    for chapter, bucket in summary["by_chapter"].items():
        print(f"  {chapter:<22} 掌握 {bucket['mastered']:>2}  不稳 {bucket['shaky']:>2}"
              f"  薄弱 {bucket['weak']:>2}  未测 {bucket['untested']:>2}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="cn-k12-kg 命令行工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="总览统计")

    p = sub.add_parser("ancestors", help="查全部先修")
    p.add_argument("topic_id")
    p.set_defaults(func=cmd_ancestors)

    p = sub.add_parser("descendants", help="查全部后继")
    p.add_argument("topic_id")
    p.set_defaults(func=cmd_descendants)

    p = sub.add_parser("path", help="从零补到目标点的学习路径")
    p.add_argument("topic_id")
    p.set_defaults(func=cmd_path)

    p = sub.add_parser("chapter", help="按册列出先修顺序")
    p.add_argument("volume", help="七上 / 七下 / …")
    p.set_defaults(func=cmd_chapter)

    p = sub.add_parser("diagnose", help="单个知识点的自适应诊断")
    p.add_argument("topic_id")
    p.add_argument("--answers", help="作答序列，c=对 w=错 s=跳过，如 w,w,c")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("screen", help="整册筛查（每点 1 题）")
    p.add_argument("volume")
    p.add_argument("--answers")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_screen)

    args = parser.parse_args()
    graph = load_graph()
    conn = store.connect()
    try:
        if args.command == "stats":
            return cmd_stats(graph, conn, args)
        return args.func(graph, conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
