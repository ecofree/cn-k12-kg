"""难度阶梯实验：补 standard 题到底值不值？

背景：477 个知识点里只有 28 个有 standard 题，第 2 题又被限制在"不高于第 1 题难度"，
所以难度阶梯在引擎里实际上不起作用。补 449 道题是大投入，先用模拟做决策。

三组配置：
  A 现状            —— 现有题库，第 2 题同档
  B 补标准题 + 升档  —— 假设每个点都有一道 standard 题，答对后升一档确认
  C 补标准题不升档   —— 只补题、不改规则（用来区分"题多"和"规则对"的贡献）

单次运行的随机数流会分叉，所以每组跑多个种子取均值与标准差，避免把噪声当效果。

    python tools/ladder_experiment.py --seeds 5 --students 300
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


def inject_standard(graph):
    """给缺少 standard 题的知识点在内存里补一道，不改数据文件。"""
    added = 0
    for topic in graph.topics:
        bank = graph.bank.setdefault(topic["id"], [])
        if any(q.get("difficulty") == "standard" for q in bank):
            continue
        bank.append({"id": f"{topic['id']}-std", "topicId": topic["id"],
                     "difficulty": "standard", "type": "numeric",
                     "prompt": "(hypothetical)", "answer": "0"})
        added += 1
    return added


CONFIGS = [
    ("A 现状", False, False),
    ("B 补标准题 + 升档确认", True, True),
    ("C 补标准题但不升档", True, False),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--slip", type=float, default=0.08)
    ap.add_argument("--out", default="build/ladder_experiment.json")
    args = ap.parse_args()

    simulate.IRT = True
    rows = []
    for name, assume_std, step_up in CONFIGS:
        accs, asks, misses, roots = [], [], [], []
        added = 0
        for seed in range(1, args.seeds + 1):
            graph = load_graph()
            if assume_std:
                added = inject_standard(graph)
            engine.STEP_UP_ON_CORRECT = step_up
            targets = [t["id"] for t in graph.topics if graph.bank.get(t["id"])]
            r = simulate.evaluate(graph, targets, args.students, args.slip, 0.25, 0.03, seed)
            accs.append(r["band_accuracy"] or 0)
            roots.append(r["root_hit_when_weak"] or 0)
            asks.append(r["avg_questions"])
            misses.append(r["false_mastered_rate"] or 0)
        rows.append({
            "config": name,
            "assume_standard": assume_std,
            "step_up": step_up,
            "injected": added,
            "band_accuracy_mean": round(statistics.mean(accs), 4),
            "band_accuracy_sd": round(statistics.stdev(accs), 4) if len(accs) > 1 else 0.0,
            "avg_questions": round(statistics.mean(asks), 2),
            "false_mastered": round(statistics.mean(misses), 4),
            "root_hit": round(statistics.mean(roots), 4),
        })
        print(f"{name:<22} 准确率 {rows[-1]['band_accuracy_mean']:.1%}"
              f" ± {rows[-1]['band_accuracy_sd']:.1%}"
              f"  题量 {rows[-1]['avg_questions']:.2f}"
              f"  漏诊 {rows[-1]['false_mastered']:.1%}"
              f"  根因 {rows[-1]['root_hit']:.1%}")

    base = rows[0]["band_accuracy_mean"]
    for row in rows[1:]:
        row["delta_accuracy"] = round(row["band_accuracy_mean"] - base, 4)
        row["delta_questions"] = round(row["avg_questions"] - rows[0]["avg_questions"], 2)
        print(f"{row['config']}：准确率 {row['delta_accuracy']:+.1%}，"
              f"每题多问 {row['delta_questions']:+.2f} 题")

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seeds": args.seeds, "students": args.students,
                                "results": rows}, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"\n报告已写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
