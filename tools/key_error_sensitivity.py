"""外部题答案键噪声的敏感性分析：错多少会把「开外部」的收益吃光。

外部题（cmm / gaokao / smse）没有 verify 表达式，准入依据是
「原卷答案与解析互相印证」——解析写错了，答案和解析会一起错，互证发现不了。
所以「开不开外部」不能只比题量，必须回答：

    答案键错误率到多少，外部题的诊断收益会跌到不如只用自有题？

做法：给 simulate 加一个 KEY_ERROR_RATE，只对带 source 的外部题按概率
反转判定，然后扫一遍错误率，和自有题基线对比。这是敏感性分析，不是测量——
真实错误率要靠抽样人工复核，本工具只给出「可容忍上限」。

    python tools/key_error_sensitivity.py --seeds 8 --students 250
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


def run(graph, targets, args, rate):
    simulate.KEY_ERROR_RATE = rate
    engine.STEP_UP_ON_CORRECT = False
    rows = []
    for seed in range(1, args.seeds + 1):
        r = simulate.evaluate(graph, targets, args.students, args.slip,
                              0.25, 0.03, seed, limit=args.limit)
        rows.append(r)
    return {
        "band_accuracy": statistics.mean(r["band_accuracy"] or 0 for r in rows),
        "false_mastered": statistics.mean(r["false_mastered_rate"] or 0 for r in rows),
        "root_hit": statistics.mean(r["root_hit_when_weak"] or 0 for r in rows),
        "questions": statistics.mean(r["avg_questions"] for r in rows),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=250)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--slip", type=float, default=0.08)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--rates", default="0,0.005,0.01,0.02,0.03,0.05,0.08")
    ap.add_argument("--out", default="build/key_error_sensitivity.json")
    args = ap.parse_args()

    simulate.IRT = True
    rates = [float(x) for x in args.rates.split(",")]

    own_graph = load_graph(include_external=False)
    ext_graph = load_graph(include_external=True)
    own_targets = [t["id"] for t in own_graph.topics if own_graph.bank.get(t["id"])]
    ext_targets = [t["id"] for t in ext_graph.topics if ext_graph.bank.get(t["id"])]

    baseline = run(own_graph, own_targets, args, 0.0)
    print(f"自有题基线（答案视为全对）：三档准确 {baseline['band_accuracy']:.1%}"
          f"　漏诊 {baseline['false_mastered']:.1%}　根因 {baseline['root_hit']:.1%}\n")
    print(f"{'答案键错误率':>10}{'三档准确':>10}{'漏诊':>9}{'根因命中':>10}"
          f"{'题量':>8}{'相对基线':>10}")
    print("-" * 60)

    results = {"own_baseline": baseline, "rates": []}
    for rate in rates:
        x = run(ext_graph, ext_targets, args, rate)
        delta = x["band_accuracy"] - baseline["band_accuracy"]
        results["rates"].append({"rate": rate, **x, "delta_vs_own": round(delta, 4)})
        print(f"{rate:>10.1%}{x['band_accuracy']:>10.1%}{x['false_mastered']:>9.1%}"
              f"{x['root_hit']:>10.1%}{x['questions']:>8.2f}{delta:>+10.1%}")

    # 线性插值找 break-even：外部收益归零的错误率
    break_even = None
    for prev, cur in zip(results["rates"], results["rates"][1:]):
        if prev["delta_vs_own"] >= 0 > cur["delta_vs_own"]:
            span = prev["delta_vs_own"] - cur["delta_vs_own"]
            frac = prev["delta_vs_own"] / span if span else 0
            break_even = prev["rate"] + frac * (cur["rate"] - prev["rate"])
    results["break_even_rate"] = round(break_even, 4) if break_even is not None else None
    if break_even is not None:
        print(f"\n收益归零点（线性插值）：答案键错误率约 {break_even:.1%}")
    else:
        print("\n在测试范围内未出现收益归零：外部题在 {:.0%} 错误率下仍优于自有基线"
              .format(max(rates)))

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"报告已写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
