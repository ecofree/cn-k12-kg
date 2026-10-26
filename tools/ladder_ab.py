"""升档实验：配对比较 + 分支变体。

`ladder_experiment_real.py` 只跑 A/B 两组均值，看不出「同一批学生」的差，
也看不出根因命中率下降是哪个分支造成的。这个工具补三件事：

1. **配对**：同一 seed 下 A/B 面对同一批学生（同样的 theta、同样的目标点），
   逐 seed 求差再取均值与 95% 置信区间，避免把种子噪声当成效应。
2. **分支变体**：升档题答错后是否回溯先修（DESCEND_ON_HARDER_FAILURE），
   这是 B 组根因命中率下降最可疑的来源，单独拆出来测。
3. **严重误判单列**：漏诊（真实不会 → 判掌握）是产品上最不能接受的错，
   不能和「把 weak 判成 shaky」这种温和错误混在一个准确率里平均掉。

    python tools/ladder_ab.py --seeds 20 --students 300 --external
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import simulate  # noqa: E402
from cnkg import engine  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


CONFIGS = (
    ("A 现状", False, False),
    ("B 升档", True, False),
    ("C 升档+错后回溯", True, True),
)


def one_run(graph, targets, args, seed, step_up, descend_harder):
    engine.STEP_UP_ON_CORRECT = step_up
    engine.DESCEND_ON_HARDER_FAILURE = descend_harder
    r = simulate.evaluate(graph, targets, args.students, args.slip,
                          0.25, 0.03, seed, limit=args.limit)
    return {
        "band_accuracy": r["band_accuracy"] or 0.0,
        "false_mastered": r["false_mastered_rate"] or 0.0,
        "false_weak": r["false_weak_rate"] or 0.0,
        "root_hit": r["root_hit_when_weak"] or 0.0,
        "questions": r["avg_questions"],
        "binary_accuracy": r["binary_accuracy"] or 0.0,
    }


def stage_of(tid: str) -> str | None:
    m = re.match(r"^(m(?:[1-9][ab]|h[1-5]))\.", tid or "")
    if not m:
        return None
    key = m.group(1)
    if key.startswith("mh"):
        return "senior"
    return "primary" if int(key[1]) <= 6 else "junior"


def mean_ci(xs):
    if not xs:
        return 0.0, 0.0
    m = statistics.mean(xs)
    if len(xs) < 2:
        return m, 0.0
    se = statistics.stdev(xs) / (len(xs) ** 0.5)
    # 20 个 seed 时 t≈2.09；用正态近似 1.96 足够，报告里注明。
    return m, 1.96 * se


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--slip", type=float, default=0.08)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--external", action="store_true")
    ap.add_argument("--stage", choices=("primary", "junior", "senior"),
                    help="只诊断该学段的目标点（用于分学段评估升档）")
    ap.add_argument("--out", default="build/ladder_ab.json")
    args = ap.parse_args()

    simulate.IRT = True
    graph = load_graph(include_external=args.external)
    targets = [t["id"] for t in graph.topics if graph.bank.get(t["id"])]
    if args.stage:
        targets = [t for t in targets if stage_of(t) == args.stage]

    raw = {name: [] for name, _, _ in CONFIGS}
    for seed in range(1, args.seeds + 1):
        for name, step_up, descend in CONFIGS:
            raw[name].append(one_run(graph, targets, args, seed, step_up, descend))

    summary = {}
    for name, _, _ in CONFIGS:
        rows = raw[name]
        summary[name] = {
            k: round(statistics.mean(r[k] for r in rows), 4)
            for k in ("band_accuracy", "binary_accuracy", "false_mastered",
                      "false_weak", "root_hit", "questions")
        }

    base = raw["A 现状"]
    comparisons = {}
    print(f"知识库：{'自有 + 外部' if args.external else '仅自有'}"
          f"　学段：{args.stage or '全部'}　种子 {args.seeds}　每种子 {args.students} 名学生\n")
    header = f"{'配置':<16}{'三档准确':>9}{'二档准确':>9}{'漏诊':>8}{'误判薄弱':>9}{'根因命中':>9}{'题量':>7}"
    print(header)
    print("-" * len(header.encode("gbk", errors="ignore")))
    for name, _, _ in CONFIGS:
        s = summary[name]
        print(f"{name:<16}{s['band_accuracy']:>9.1%}{s['binary_accuracy']:>9.1%}"
              f"{s['false_mastered']:>8.1%}{s['false_weak']:>9.1%}"
              f"{s['root_hit']:>9.1%}{s['questions']:>7.2f}")

    print("\n相对 A 的配对差（同一 seed 同一批学生，均值 [95% CI]）：")
    for name, _, _ in CONFIGS[1:]:
        rows = raw[name]
        comp = {}
        for metric in ("band_accuracy", "false_mastered", "root_hit", "questions"):
            diffs = [r[metric] - b[metric] for r, b in zip(rows, base)]
            m, ci = mean_ci(diffs)
            comp[metric] = {"mean": round(m, 4), "ci95": round(ci, 4)}
            unit = "题" if metric == "questions" else "pp"
            scale = 1.0 if metric == "questions" else 100.0
            print(f"  {name:<14}{metric:<14}{m * scale:+.2f} {unit}"
                  f"   [{m * scale - ci * scale:+.2f}, {m * scale + ci * scale:+.2f}]")
        comparisons[name] = comp

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "seeds": args.seeds,
        "students_per_seed": args.students,
        "include_external": args.external,
        "stage": args.stage,
        "limit": args.limit,
        "summary": summary,
        "paired_vs_A": comparisons,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
