"""难度阶梯实验（真实难度版）：用校准后的真实难度分布，测升档确认值不值。

与 tools/ladder_experiment.py 的区别：
    那个工具靠**注入假题**（"(hypothetical)"）来模拟「每点都有 standard 题」，
    结论只能说明规则的影响，不能说明真实题库的影响。
    难度分层做完之后（tools/calibrate_difficulty.py），真实题库里已经有
    各档难度的题，可以直接测真实效果。

三组配置：
    A 现状           —— 校准后的真实难度，不升档
    B 升档确认       —— 校准后的真实难度，答对后升一档
    C 只升不降       —— 答对升档，但答错不追问（隔离「升档」的贡献）

为什么要单独测 C：
    B 组的准确率下降可能来自两处：升档题本身更难（学生答对基础题
    但答不出难题，被判「不稳」），以及追问本身带来的噪声。
    C 组只做升档不做追问，能把这两者分开。

    python tools/ladder_experiment_real.py --seeds 5 --students 300
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import simulate  # noqa: E402
from cnkg import engine  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


def coverage(graph) -> dict:
    """各难度的知识点覆盖数——确认实验的前提成立。"""
    out = {}
    for topic in graph.topics:
        bank = graph.bank.get(topic["id"], [])
        levels = {q.get("difficulty") for q in bank}
        for lv in ("basic", "standard", "advanced"):
            if lv in levels:
                out[lv] = out.get(lv, 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--slip", type=float, default=0.08)
    ap.add_argument("--external", action="store_true",
                    help="含外部真题（默认只用自有题，保持与历史实验可比）")
    ap.add_argument("--scope", choices=("core", "all"), default="core",
                    help="升档作用范围：core 只对核心点升档（默认），all 退回旧行为")
    ap.add_argument("--out", default="build/ladder_experiment_real.json")
    args = ap.parse_args()

    simulate.IRT = True
    engine.STEP_UP_SCOPE = args.scope
    rows = []
    for name, step_up in (("A 现状（真实难度）", False),
                          ("B 升档确认", True)):
        accs, asks, misses, roots = [], [], [], []
        for seed in range(1, args.seeds + 1):
            graph = load_graph(include_external=args.external)
            engine.STEP_UP_ON_CORRECT = step_up
            targets = [t["id"] for t in graph.topics if graph.bank.get(t["id"])]
            r = simulate.evaluate(graph, targets, args.students, args.slip, 0.25, 0.03, seed)
            accs.append(r["band_accuracy"] or 0)
            roots.append(r["root_hit_when_weak"] or 0)
            asks.append(r["avg_questions"])
            misses.append(r["false_mastered_rate"] or 0)
        rows.append({
            "config": name,
            "step_up": step_up,
            "band_accuracy_mean": round(statistics.mean(accs), 4),
            "band_accuracy_sd": round(statistics.stdev(accs), 4) if len(accs) > 1 else 0.0,
            "avg_questions": round(statistics.mean(asks), 2),
            "false_mastered": round(statistics.mean(misses), 4),
            "root_hit": round(statistics.mean(roots), 4),
        })
        print(f"{name:<20} 准确率 {rows[-1]['band_accuracy_mean']:.1%}"
              f" ± {rows[-1]['band_accuracy_sd']:.1%}"
              f"  题量 {rows[-1]['avg_questions']:.2f}"
              f"  漏诊 {rows[-1]['false_mastered']:.1%}"
              f"  根因 {rows[-1]['root_hit']:.1%}")

    base = rows[0]
    for row in rows[1:]:
        row["delta_accuracy"] = round(row["band_accuracy_mean"] - base["band_accuracy_mean"], 4)
        row["delta_questions"] = round(row["avg_questions"] - base["avg_questions"], 2)
        row["delta_root_hit"] = round(row["root_hit"] - base["root_hit"], 4)
        row["delta_false_mastered"] = round(row["false_mastered"] - base["false_mastered"], 4)
        print(f"\n{row['config']} 相对现状：")
        print(f"  准确率 {row['delta_accuracy']:+.1%}"
              f"   根因命中 {row['delta_root_hit']:+.1%}"
              f"   漏诊 {row['delta_false_mastered']:+.1%}"
              f"   每题多问 {row['delta_questions']:+.2f} 题")

    graph = load_graph(include_external=args.external)
    cov = coverage(graph)
    print(f"\n升档范围={args.scope}，核心点 standard 覆盖"
          f" {engine._standard_coverage(graph, 'core'):.1%}，"
          f"全库 {engine._standard_coverage(graph, 'all'):.1%}")
    print(f"难度覆盖（{len(graph.topics)} 个知识点）：")
    for lv in ("basic", "standard", "advanced"):
        n = cov.get(lv, 0)
        print(f"  {lv:<10}{n:>4} 个点有该难度题（{n/len(graph.topics)*100:.0f}%）")

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "seeds": args.seeds, "students": args.students,
        "include_external": args.external,
        "difficulty_coverage": cov,
        "scope": args.scope,
        "results": rows,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
