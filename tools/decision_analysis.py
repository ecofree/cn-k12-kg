"""四项待决事项的数据底稿：把「开/不开」的后果算成同一张表。

四个问题：
  1. 外部题默认开不开
  2. 升档开关要不要开（见 tools/ladder_ab.py，本工具只算题库前提）
  3. smse 764 道（许可未声明）怎么处置
  4. 小学题型配比目标按哪个口径定

口径统一说明：
  * **题点对** = 引擎 bank 索引口径（core 题按 topicIds 给每个关联点各记一次）
  * **主知识点** = 只认 primaryTopicId，一道题只算一个点
  * 两个都报，因为「够不够诊断」看前者，「题库资产」看后者，混用会得出相反结论。
"""
from __future__ import annotations

import argparse
import collections
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

DATA = ROOT / "data"
PRIMARY, JUNIOR, SENIOR = "primary", "junior", "senior"
TARGETS = {PRIMARY: 4, JUNIOR: 6, SENIOR: 8}


def stage_of(tid: str) -> str | None:
    m = re.match(r"^(m(?:[1-9][ab]|h[1-5]))\.", tid or "")
    if not m:
        return None
    key = m.group(1)
    if key.startswith("mh"):
        return SENIOR
    return PRIMARY if int(key[1]) <= 6 else JUNIOR


def load():
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))
    deps = json.loads((DATA / "dependencies.json").read_text(encoding="utf-8"))
    policy = json.loads((DATA / "stage_policy.json").read_text(encoding="utf-8"))
    return topics, own, ext, deps, policy


def topic_ids(q):
    return q.get("topicIds") or ([q.get("topicId")] if q.get("topicId") else [])


def primary_topic(q):
    return q.get("primaryTopicId") or q.get("topicId")


def pool_stats(topics, pool, include_primary=False):
    """题库在引擎口径下的覆盖统计，按学段。"""
    idx = topic_ids if not include_primary else \
        lambda q: [primary_topic(q)] if primary_topic(q) else []
    per_topic = collections.Counter()
    diffs = collections.defaultdict(set)
    types = collections.defaultdict(set)
    for q in pool:
        for tid in idx(q):
            per_topic[tid] += 1
            diffs[tid].add(q.get("difficulty", "?"))
            types[tid].add(q.get("type", "?"))

    out = {}
    for stage in (PRIMARY, JUNIOR, SENIOR):
        tids = [t["id"] for t in topics if stage_of(t["id"]) == stage]
        need = TARGETS[stage]
        pairs = sum(per_topic[t] for t in tids)
        out[stage] = {
            "topics": len(tids),
            "pairs": pairs,
            "avg_per_topic": round(pairs / max(len(tids), 1), 2),
            "below_min": sum(1 for t in tids if per_topic[t] < need),
            "zero": sum(1 for t in tids if per_topic[t] == 0),
            "single_type": sum(1 for t in tids if len(types[t]) < 2),
            "single_difficulty": sum(1 for t in tids if len(diffs[t]) < 2),
            "has_standard": sum(1 for t in tids if "standard" in diffs[t]),
            "has_advanced": sum(1 for t in tids if "advanced" in diffs[t]),
            "min_count": min((per_topic[t] for t in tids), default=0),
        }
    return out


def guess_exposure(pool):
    """不会的学生在这套题上的平均蒙对率。选择题按选项数，其余按 3%。"""
    total = 0.0
    for q in pool:
        if q.get("type") == "choice":
            total += 1.0 / max(2, len(q.get("choices") or []))
        else:
            total += 0.03
    return round(total / max(len(pool), 1), 4)


def dup_prompts(pool):
    seen = collections.Counter()
    for q in pool:
        p = re.sub(r"\s+", "", q.get("prompt") or "")
        if p:
            seen[p] += 1
    groups = [v for v in seen.values() if v > 1]
    return {"groups": len(groups), "extra": sum(v - 1 for v in groups)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    topics, own, ext, deps, policy = load()
    usable = [q for q in ext if q.get("usable_for_diagnosis")]
    smse = [q for q in usable if q.get("source") == "smse-shanghai-zhongkao"]
    cmm = [q for q in usable if q.get("source") == "cmm-math"]
    gaokao = [q for q in usable if q.get("source") == "gaokao-tex"]

    report = {}

    # ------------------------------------------------------------------ 1
    pools = {
        "自有": own,
        "自有+外部可诊断": own + usable,
        "自有+外部可诊断（去 smse）": own + cmm + gaokao,
    }
    report["pools"] = {
        "engine_index": {k: pool_stats(topics, v) for k, v in pools.items()},
        "primary_topic": {k: pool_stats(topics, v, True) for k, v in pools.items()},
        "sizes": {k: len(v) for k, v in pools.items()},
        "guess_exposure": {k: guess_exposure(v) for k, v in pools.items()},
        "duplicates": {k: dup_prompts(v) for k, v in pools.items()},
    }

    # 外部题在各学段的「增量」：多少点从不足变成达标
    report["delta"] = {}
    for stage in (PRIMARY, JUNIOR, SENIOR):
        a = report["pools"]["engine_index"]["自有"][stage]
        b = report["pools"]["engine_index"]["自有+外部可诊断"][stage]
        report["delta"][stage] = {
            "below_min": f"{a['below_min']} → {b['below_min']}",
            "single_difficulty": f"{a['single_difficulty']} → {b['single_difficulty']}",
            "has_standard": f"{a['has_standard']} → {b['has_standard']}",
            "pairs": f"{a['pairs']} → {b['pairs']}",
            "avg": f"{a['avg_per_topic']} → {b['avg_per_topic']}",
        }

    # ------------------------------------------------------------------ 2
    report["step_up"] = {
        s: round(
            report["pools"]["engine_index"]["自有"][s]["has_standard"]
            / report["pools"]["engine_index"]["自有"][s]["topics"], 4)
        for s in (PRIMARY, JUNIOR, SENIOR)
    }
    report["step_up"]["threshold"] = 0.6
    report["step_up"]["own_total_coverage"] = round(
        sum(report["pools"]["engine_index"]["自有"][s]["has_standard"]
            for s in (PRIMARY, JUNIOR, SENIOR)) / len(topics), 4)
    report["step_up"]["ext_total_coverage"] = round(
        sum(report["pools"]["engine_index"]["自有+外部可诊断"][s]["has_standard"]
            for s in (PRIMARY, JUNIOR, SENIOR)) / len(topics), 4)

    # ------------------------------------------------------------------ 3
    jt = [t["id"] for t in topics if stage_of(t["id"]) == JUNIOR]
    def junior_cov(pool):
        have_std, have_adv, covered = set(), set(), set()
        for q in pool:
            for tid in topic_ids(q):
                covered.add(tid)
                if q.get("difficulty") == "standard":
                    have_std.add(tid)
                if q.get("difficulty") == "advanced":
                    have_adv.add(tid)
        return {
            "pairs": sum(1 for q in pool for t in topic_ids(q) if t in jt),
            "covered": len(covered & set(jt)),
            "standard": len(have_std & set(jt)),
            "advanced": len(have_adv & set(jt)),
        }
    report["smse"] = {
        "count": len(smse),
        "usable": sum(1 for q in smse if q.get("usable_for_diagnosis")),
        "with_explanation": sum(1 for q in smse if (q.get("explanation") or "").strip()),
        "junior": {
            "topics": len(jt),
            "own": junior_cov(own),
            "own+cmm": junior_cov(own + cmm),
            "own+cmm+smse": junior_cov(own + cmm + smse),
        },
        "junior_share_of_diagnostic": round(
            len(smse) / max(len([q for q in own + usable if stage_of(primary_topic(q)) == JUNIOR]), 1), 4),
    }
    # 去 smse 后，初中各点题量是否掉到下限以下
    def below(pool):
        per = collections.Counter()
        for q in pool:
            for tid in topic_ids(q):
                per[tid] += 1
        return sum(1 for t in jt if per[t] < TARGETS[JUNIOR])
    report["smse"]["junior"]["below_min"] = {
        "own": below(own),
        "own+cmm": below(own + cmm),
        "own+cmm+smse": below(own + cmm + smse),
    }

    # ------------------------------------------------------------------ 4
    pt = [t["id"] for t in topics if stage_of(t["id"]) == PRIMARY]
    def mix(pool):
        c = collections.Counter(q.get("type") for q in pool
                                for tid in topic_ids(q) if tid in pt)
        n = sum(c.values()) or 1
        return {k: round(v / n, 3) for k, v in c.items()}, n
    report["primary_mix"] = {}
    for label, pool in pools.items():
        m, n = mix(pool)
        report["primary_mix"][label] = {"mix": m, "pairs": n,
                                        "guess_exposure": guess_exposure(
                                            [q for q in pool
                                             if any(t in pt for t in topic_ids(q))])}
    # 小学题型多样性（引擎口径，按点）
    def diversity(pool):
        types = collections.defaultdict(set)
        for q in pool:
            for tid in topic_ids(q):
                if tid in pt:
                    types[tid].add(q.get("type"))
        only_choice = sum(1 for t in pt if types[t] == {"choice"})
        no_numeric = sum(1 for t in pt if "numeric" not in types[t])
        no_short = sum(1 for t in pt if "short" not in types[t])
        return {"only_choice": only_choice, "no_numeric": no_numeric,
                "no_short": no_short, "single_type": sum(1 for t in pt if len(types[t]) < 2)}
    report["primary_mix"]["diversity"] = {k: diversity(v) for k, v in pools.items()}

    # 实际抽到的题型（模拟器口径）：题库配比 ≠ 学生看到的配比
    simulate.IRT = True
    seen = {}
    for label, pool in (("自有", own), ("自有+外部可诊断", own + usable)):
        graph = load_graph(include_external=(pool is not own))
        targets = [t for t in pt if graph.bank.get(t)]
        engine.STEP_UP_ON_CORRECT = False
        acc = collections.Counter()
        asked = 0
        for seed in range(1, 6):
            r = simulate.evaluate(graph, targets, 200, 0.08, 0.25, 0.03, seed, limit=24)
            acc.update(r["type_mix"])
            asked += sum(r["type_mix"].values())
        seen[label] = {k: round(v / max(asked, 1), 3) for k, v in acc.items()}
        seen[label]["guess_exposure"] = round(
            sum(k * (1 / 4 if q == "choice" else 0.03) for q, k in
                ((q, v / max(asked, 1)) for q, v in acc.items())), 4)
    report["primary_mix"]["sampled"] = seen

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    print("=" * 78)
    print("一、题库构成（引擎口径：题点对）")
    print("=" * 78)
    print(f"{'池子':<26}{'题量':>8}{'小学不足':>9}{'初中不足':>9}{'高中不足':>9}")
    for label, v in report["pools"]["engine_index"].items():
        n = report["pools"]["sizes"][label]
        print(f"{label:<26}{n:>8}{v['primary']['below_min']:>9}"
              f"{v['junior']['below_min']:>9}{v['senior']['below_min']:>9}")
    print("\n开外部后的增量：")
    for stage, d in report["delta"].items():
        print(f"  {stage:<8} 题量 {d['pairs']:<18} 不足点 {d['below_min']:<10}"
              f"单一难度 {d['single_difficulty']:<10} 有 standard {d['has_standard']}")

    print("\n" + "=" * 78)
    print("二、升档的题库前提")
    print("=" * 78)
    print(f"  自有 standard 覆盖率　　{report['step_up']['own_total_coverage']:.1%}"
          f"（门槛 {report['step_up']['threshold']:.0%}）")
    print(f"  自有+外部覆盖率　　　　{report['step_up']['ext_total_coverage']:.1%}")
    for s in (PRIMARY, JUNIOR, SENIOR):
        print(f"    {s:<8}{report['step_up'][s]:.1%}")

    print("\n" + "=" * 78)
    print("三、smse 依赖程度")
    print("=" * 78)
    sj = report["smse"]["junior"]
    print(f"  smse 可诊断 {report['smse']['usable']} 道，占初中诊断池 "
          f"{report['smse']['junior_share_of_diagnostic']:.1%}；"
          f"带解析 {report['smse']['with_explanation']} 道")
    print(f"  {'池子':<16}{'题点对':>8}{'覆盖点':>8}{'有standard':>11}{'有advanced':>11}{'不足点':>8}")
    for label in ("own", "own+cmm", "own+cmm+smse"):
        x = sj[label]
        print(f"  {label:<16}{x['pairs']:>8}{x['covered']:>8}{x['standard']:>11}"
              f"{x['advanced']:>11}{sj['below_min'][label]:>8}")

    print("\n" + "=" * 78)
    print("四、小学题型配比（四种口径）")
    print("=" * 78)
    for label, x in report["primary_mix"].items():
        if label == "diversity" or label == "sampled":
            continue
        print(f"  {label:<26}{x['mix']}  题点对 {x['pairs']}  蒙对率 {x['guess_exposure']:.3f}")
    print("  实际抽到：")
    for label, x in report["primary_mix"]["sampled"].items():
        print(f"    {label:<24}{ {k: v for k, v in x.items() if k != 'guess_exposure'} }"
              f"  蒙对率 {x['guess_exposure']:.3f}")
    print("  题型多样性：")
    for label, x in report["primary_mix"]["diversity"].items():
        print(f"    {label:<24}单一题型 {x['single_type']:>3} 点　"
              f"只有选择 {x['only_choice']:>3}　无 numeric {x['no_numeric']:>3}　"
              f"无 short {x['no_short']:>3}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
