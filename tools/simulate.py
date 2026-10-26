"""诊断引擎在假定学生模型下的蒙特卡洛敏感性分析。

不依赖任何外部答题数据：先构造一批"知道真实掌握状态"的虚拟学生，
按知识点层级生成他们的答题结果，再让引擎去判定，最后统计模型内指标。
生成学生和评估引擎共用同一假定先修图，不模拟反馈后学习或长期保持。
本工具不能提供真实学生效度证据，也不能独立决定课程是否需要应用题。

要回答的问题很具体：
  1. "第 1 题错、第 2 题对 → 不稳" 这条规则，误判率有多高？
  2. 真实薄弱的学生，会不会被判成"掌握"（最严重的错误）？
  3. 引擎报的根因，命中真实根因的比例是多少？
  4. 学生作答带噪声（失误、猜对）时，结论还稳不稳？

    python tools/simulate.py --students 300
    python tools/simulate.py --students 300 --slip 0.15 --guess 0.35   # 噪声更大
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import engine, evidence  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


def mastery_of(graph, rng, base=0.85, gap=0.15, flip=0.05):
    """按拓扑序生成掌握状态：先修都会，当前点大概率会；先修缺一块，当前点大概率不会。

    再以 flip 的概率随机翻转，模拟"跳级学会"和"意外遗忘"。
    """
    state = {}
    for tid in graph.topo_order([t["id"] for t in graph.topics], hard_only=True):
        prereqs = graph.prereqs.get(tid, [])
        hard_ok = all(state.get(e["prerequisiteId"], True) for e in prereqs
                      if e["strength"] == "hard")
        p = base if hard_ok else gap
        state[tid] = rng.random() < p
    for tid in list(state):
        if rng.random() < flip:
            state[tid] = not state[tid]
    return state


# 敏感性分析用：把所有题都当成四选一，量"题型结构本身"值多少
ALL_CHOICE_GUESS = False

# 难度感知模型（IRT-lite）：
#   会做某点的学生，也不是任何难度都做得对——基础题几乎必对，标准题要费劲，难题可能做不出。
#   这是旧模型完全没有的部分：它把"掌握"当成二元开关，导致"难度"在模拟里毫无作用。
#   P(对|会) = sigmoid(slope * (theta - b + margin))，b 是题目难度，theta 是学生能力。
#   参数是假设，不是测量；只在比较规则/题库结构时使用。
IRT = False
# 答案键错误率（敏感性分析用）：0 表示假设题库答案全对。
# 非 0 时，对外部题按该概率把判定反转；自有题暂不注入这种噪声，
# 这只是实验设定，不表示所有自有题的题义、答案已经正确。
KEY_ERROR_RATE = 0.0
DIFF_B = {"basic": 0.0, "standard": 1.0, "advanced": 2.0}
SLOPE = 1.6
MARGIN = 1.2


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + 2.718281828459045 ** (-x))


def answer_probability(mastered: bool, difficulty: str, qtype: str,
                       slip: float, guess_choice: float, guess_numeric: float,
                       theta: float = 0.0) -> float:
    """答对的概率：掌握了也可能失误、也可能做不出更难的题；没掌握也可能猜对。

    猜测率按题型区分——四选一约 1/4，数值填空写错就是错（只留极小的"蒙对"）。
    IRT 模式下，"会"不再是必对：难度越高，会做也可能失败。
    """
    # 这里假定均匀猜选项、数值题低概率猜中；不模拟排除法或选项线索。
    g = guess_choice if qtype == "choice" else guess_numeric
    if ALL_CHOICE_GUESS:
        g = 0.25
    if mastered:
        if not IRT:
            return max(0.0, min(1.0, 1.0 - slip))
        p = _sigmoid(SLOPE * (theta - DIFF_B.get(difficulty, 0.0) + MARGIN))
        return max(0.0, min(1.0, p - slip * 0.5))
    return max(0.0, min(1.0, g))


def effective_state(state: dict, theta: float) -> dict:
    """把"学过"折算成"能演示"。

    IRT 模式下，能力过低的学生即使被标成"已学过"，也做不对基础题；
    对诊断而言他与没学过的人不可区分，真值也应按"不会"算，否则会冤枉引擎。
    阈值 theta > -1.2 与 truth_band 的 weak 分界一致。
    """
    if not IRT:
        return state
    return {k: (v and theta > -1.2) for k, v in state.items()}


def truth_band(state: dict, theta: float, target: str) -> str:
    """IRT 下的三档真值：能把哪一档题做出来，就归到哪一档。

    阈值来自同一套 sigmoid：基础题 b=0、标准题 b=1，margin=1.2，
    解 sigmoid(slope*(theta-b+margin))>0.5 即 theta > b-margin。
    """
    if not state.get(target):
        return "weak"                      # 没学会这个点，基础题也做不对
    if theta <= -1.2:
        return "weak"                      # 学过但连基础题都做不对，实际等同薄弱
    if theta <= -0.2:
        return "shaky"                     # 会基础、不会标准
    return "mastered"                      # 基础与标准都做得出来


def true_roots(graph, state):
    """全局真实根因：未掌握、且所有 hard 先修都已掌握的点（可能分布在多条分支上）。"""
    roots = set()
    for tid, ok in state.items():
        if ok:
            continue
        if all(state.get(e["prerequisiteId"], True)
               for e in graph.prereqs.get(tid, []) if e["strength"] == "hard"):
            roots.add(tid)
    return roots


def roots_for(graph, state, target):
    """针对某个目标点的根因：只沿该点的 hard 先修链向上找。

    学生可能在完全无关的分支上也有漏洞，那不是"为什么这个点不会"的答案，
    评估时不该算成引擎找错。目标点本身会做，则不存在根因。
    """
    if state.get(target, True):
        return set()
    # 沿 hard 边往上，只要父节点也没掌握就继续；遇到已掌握的父节点就停。
    # 这样得到的才是"导致这个点不会"的那条连续缺口链的顶端。
    #
    # 不能跨越已掌握节点把更远的旧缺口归到当前原因上。
    roots, stack, seen = set(), [target], {target}
    while stack:
        cur = stack.pop()
        ups = [e["prerequisiteId"] for e in graph.prereqs.get(cur, [])
               if e["strength"] == "hard" and not state.get(e["prerequisiteId"], True)]
        if not ups:
            roots.add(cur)
        for pid in ups:
            if pid not in seen:
                seen.add(pid)
                stack.append(pid)
    return roots


def run_one(graph, rng, state, target, slip, guess_choice, guess_numeric, limit, theta=0.0):
    session = engine.create_session(graph, [target], limit=limit)
    asked = 0
    exposure = 0.0           # 累计"这一题如果不会，蒙对的概率"
    types = collections.Counter()
    while True:
        item = engine.next_item(graph, session)
        if item is None:
            break
        asked += 1
        q = item["question"]
        tid = item["topicId"]
        qtype = q.get("type", "choice")
        types[qtype] += 1
        base = (0.25 if ALL_CHOICE_GUESS else
                (1.0 / max(2, len(q.get("choices") or [])) if qtype == "choice" else guess_numeric))
        exposure += base
        p = answer_probability(state.get(tid, False), q.get("difficulty", "basic"),
                               qtype, slip, base, guess_numeric, theta)
        verdict = "correct" if rng.random() < p else "wrong"
        # 答案键噪声：外部题没有 verify 表达式，只能靠来源互证。
        # 键错了意味着「学生答对但系统记错」，反向也成立（错误键可能被蒙中）。
        if KEY_ERROR_RATE and not evidence.is_own(q) and rng.random() < KEY_ERROR_RATE:
            verdict = "wrong" if verdict == "correct" else "correct"
        engine.apply_answer(graph, session, verdict)
        if asked > 60:
            break
    summary = engine.summarize(graph, session)
    return summary, asked, exposure, types


def root_set_counts(real_roots, reported):
    """逐个根因计 TP/FP/FN；多报和完全漏报都不能被“命中一个”掩盖。"""
    real, predicted = set(real_roots), set(reported)
    return {"tp": len(real & predicted), "fp": len(predicted - real),
            "fn": len(real - predicted)}


def evaluate(graph, targets, n_students, slip, guess_choice, guess_numeric, seed,
             theta_sd=1.0, limit=24):
    rng = random.Random(seed)
    # 混淆矩阵：真实 × 判定
    matrix = collections.Counter()
    root_hit = root_total = 0
    root_counts = collections.Counter()
    root_exact = root_relevant = candidate_hit = incomplete_count = 0
    # 分层统计：只有"目标点确实没掌握"时，谈根因命中才有意义；
    # 目标点掌握时，引擎不报根因才是对的（假阳性另计）
    weak_hit = weak_total = 0
    clean_false_alarm = clean_total = 0
    band_matrix = collections.Counter()
    asked_total = 0
    exposure_total = 0.0
    type_total = collections.Counter()
    per_target = collections.defaultdict(lambda: collections.Counter())

    for _ in range(n_students):
        state = mastery_of(graph, rng)
        theta = rng.gauss(0, theta_sd) if IRT else 0.0
        target = rng.choice(targets)
        summary, asked, exposure, types = run_one(graph, rng, state, target, slip,
                                                  guess_choice, guess_numeric, limit=limit,
                                                  theta=theta)
        asked_total += asked
        exposure_total += exposure
        type_total.update(types)

        eff = effective_state(state, theta)
        truth = eff.get(target, False)
        verdict = "untested"
        for bucket in ("mastered", "shaky", "weak", "assumed", "insufficient_evidence"):
            if target in summary.get(bucket, []):
                verdict = bucket
                break
        matrix[(truth, verdict)] += 1
        per_target[target][(truth, verdict)] += 1
        if IRT and verdict in ("mastered", "shaky", "weak"):
            band_matrix[(truth_band(eff, theta, target), verdict)] += 1

        real_roots = roots_for(graph, eff, target)
        reported = [r["topicId"] if isinstance(r, dict) else r for r in summary.get("roots", [])]
        # 分母包括有真根因但系统完全未报的会话。
        if real_roots:
            root_total += 1
            if set(reported) & real_roots:
                root_hit += 1
            if (set(reported) | set(summary.get("root_candidates", []))) & real_roots:
                candidate_hit += 1
        root_counts.update(root_set_counts(real_roots, reported))
        if real_roots or reported:
            root_relevant += 1
            root_exact += set(reported) == real_roots
        incomplete_count += bool(summary.get("incomplete"))
        if not truth:
            weak_total += 1
            if set(reported) & real_roots:
                weak_hit += 1
        else:
            clean_total += 1
            if reported:
                clean_false_alarm += 1

    total = sum(matrix.values())
    # 严重误判：真实没掌握，却被判成掌握
    false_mastered = matrix[(False, "mastered")]
    false_weak = matrix[(True, "weak")]
    true_pos = matrix[(False, "weak")] + matrix[(False, "shaky")]
    true_neg = matrix[(True, "mastered")] + matrix[(True, "shaky")]

    def rate(num, den):
        return round(num / den, 4) if den else None

    return {
        "params": {"students": n_students, "slip": slip,
                   "guess_choice": guess_choice, "guess_numeric": guess_numeric,
                   "seed": seed},
        "total": total,
        "confusion": {f"{'会' if k[0] else '不会'}|{k[1]}": v for k, v in matrix.items()},
        "false_mastered_rate": rate(false_mastered, sum(c for k, c in matrix.items() if not k[0])),
        "false_weak_rate": rate(false_weak, sum(c for k, c in matrix.items() if k[0])),
        "detect_rate": rate(true_pos, sum(c for k, c in matrix.items() if not k[0])),
        "confirm_rate": rate(true_neg, sum(c for k, c in matrix.items() if k[0])),
        "root_hit_rate": rate(root_hit, root_total),
        "root_precision": rate(root_counts["tp"], root_counts["tp"] + root_counts["fp"]),
        "root_recall": rate(root_counts["tp"], root_counts["tp"] + root_counts["fn"]),
        "root_exact_match_rate": rate(root_exact, root_relevant),
        "root_counts": dict(root_counts),
        "candidate_or_supported_hit_rate": rate(candidate_hit, root_total),
        "incomplete_rate": rate(incomplete_count, total),
        "assessed_rate": rate(sum(c for (truth, status), c in matrix.items()
                                  if status in ("mastered", "shaky", "weak")), total),
        "interpretation": {
            "empirical_student_validation": False,
            "assumptions": "同一假定先修图生成虚拟掌握状态；不模拟反馈后学习与保持",
            "root_metrics": "仅对模型内证据充分的 roots 计 precision/recall；候选另列",
            "accuracy_scope": "band/binary accuracy 仅在已形成判定的样本中计算，须同时看 assessed_rate",
        },
        "root_hit_when_weak": rate(weak_hit, weak_total),
        "root_false_alarm_when_ok": rate(clean_false_alarm, clean_total),
        "avg_questions": round(asked_total / max(total, 1), 2),
        # 实际暴露给学生的平均猜测率：题型构成不同，同一个引擎的漏诊风险也不同
        "guess_exposure": round(exposure_total / max(asked_total, 1), 4),
        "type_mix": dict(type_total),
        "band_matrix": {f"{k[0]}|{k[1]}": v for k, v in band_matrix.items()},
        "band_accuracy": rate(sum(v for k, v in band_matrix.items() if k[0] == k[1]),
                              sum(band_matrix.values())),
        # 二分类口径：把"掌握"和"不稳"都算"会"，只跟"薄弱"区分
        "binary_accuracy": rate(
            sum(v for k, v in band_matrix.items()
                if (k[0] == "weak") == (k[1] == "weak")),
            sum(band_matrix.values())),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--students", type=int, default=300)
    ap.add_argument("--slip", type=float, default=0.08)
    ap.add_argument("--guess", type=float, default=0.25,
                    help="四选一的猜测命中率")
    ap.add_argument("--guess-numeric", type=float, default=0.03,
                    help="数值填空的“蒙对”率，写错就是错，只剩极小的边界值命中")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-shaky-descend", action="store_true",
                    help="不稳时不继续回溯，用于对比旧行为")
    ap.add_argument("--descend-soft", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-confirm", action="store_true",
                    help="关闭“答对后再确认一题”，用于对比旧规则")
    ap.add_argument("--irt", action="store_true",
                    help="难度感知模型：会做的学生也可能做不出更难的题")
    ap.add_argument("--theta-sd", type=float, default=1.0,
                    help="IRT 模式下学生能力的标准差")
    ap.add_argument("--assume-standard", action="store_true",
                    help="假设每个知识点都有一道 standard 题（用于评估补题价值，不改数据）")
    ap.add_argument("--no-step-up", action="store_true",
                    help="关闭“答对后升档确认”，用于对比旧行为")
    ap.add_argument("--step-up", action="store_true",
                    help="仅用于敏感性实验：显式开启诊断升档，默认与产品保持关闭")
    ap.add_argument("--all-choice-guess", action="store_true",
                    help="敏感性分析：把所有题都当成四选一，用于量化题型结构的作用")
    ap.add_argument("--out", default="build/calibration_report.json")
    args = ap.parse_args()

    engine.CONFIRM_ON_CORRECT = not args.no_confirm
    if args.descend_soft:
        ap.error("soft 是相关关系，不能作为因果诊断先修；该实验开关已移除")
    engine.DESCEND_SHAKY = not args.no_shaky_descend
    globals()["ALL_CHOICE_GUESS"] = args.all_choice_guess
    globals()["IRT"] = args.irt
    engine.STEP_UP_ON_CORRECT = args.step_up and not args.no_step_up

    graph = load_graph()
    if args.assume_standard:
        added = 0
        for topic in graph.topics:
            bank = graph.bank.setdefault(topic["id"], [])
            if any(q.get("difficulty") == "standard" for q in bank):
                continue
            bank.append({"id": f"{topic['id']}-std", "topicId": topic["id"],
                         "difficulty": "standard", "type": "numeric",
                         "prompt": "(hypothetical)", "answer": "0", "verify": "0"})
            added += 1
        print(f"假设补题：为 {added} 个知识点注入 standard 题（仅内存，不写数据）")
    ready = [t["id"] for t in graph.topics if graph.bank.get(t["id"])]
    targets = ready
    print(f"知识点 {len(graph.topics)}，可诊断 {len(ready)}，模拟学生 {args.students}")

    scenarios = [
        ("低噪声", 0.05, 0.10, args.guess_numeric),
        ("基准", args.slip, args.guess, args.guess_numeric),
        ("高噪声", 0.15, 0.35, args.guess_numeric),
    ]
    results = []
    def pct(value):
        return "无适用样本" if value is None else f"{value:.1%}"
    for name, slip, guess, guess_num in scenarios:
        r = evaluate(graph, targets, args.students, slip, guess, guess_num, args.seed,
                     theta_sd=args.theta_sd)
        r["scenario"] = name
        results.append(r)
        mix = r["type_mix"]
        total_asked = sum(mix.values()) or 1
        print(f"\n[{name}] slip={slip} 四选一 guess={guess} 填空 guess={guess_num}")
        print(f"  真实不会 → 判掌握（严重误判）  {pct(r['false_mastered_rate'])}")
        print(f"  真实不会 → 判薄弱/不稳（检出） {pct(r['detect_rate'])}")
        print(f"  真实会   → 判薄弱（过度保守）  {pct(r['false_weak_rate'])}")
        print(f"  根因精确率 / 召回率              {pct(r['root_precision'])} / {pct(r['root_recall'])}")
        print(f"  根因命中（目标点未掌握时）      {pct(r['root_hit_when_weak'])}")
        print(f"  目标点掌握却报根因（假警报）    {pct(r['root_false_alarm_when_ok'])}")
        print(f"  形成判定 / 未完成确认            {pct(r['assessed_rate'])} / {pct(r['incomplete_rate'])}")
        print(f"  平均出题                        {r['avg_questions']} 题")
        if r.get("band_accuracy") is not None:
            print(f"  三档判定准确率                  {r['band_accuracy']:.1%}")
            print(f"  二档判定准确率（只分会/不会）   {r['binary_accuracy']:.1%}")
        print(f"  题型构成                        填空 {mix.get('numeric',0)/total_asked:.0%}"
              f" / 选择 {mix.get('choice',0)/total_asked:.0%}"
              f"   → 平均猜测率 {r['guess_exposure']:.1%}")

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    print(f"\n报告已写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
