"""按「必须/任一/禁止」三组正则，把外部题判到具体知识点。纯标准库。

为什么不用关键词出现即命中：
    「一元二次方程」会在二次函数题里出现，「绝对值」会在解不等式里出现。
    命中一个词就落标，等于把噪声当信号。所以每个知识点要同时满足：
      must   —— 全都要出现（限定题材）
      any    —— 至少出现一个（限定考法）
      not    —— 一个都不能出现（排除同名不同义的题）
    并且只接受「唯一最高分且领先第二名」，否则一律不落标。

用法：
    python tools/map_by_lexicon.py --lexicon data/kc_lexicon.json --stage senior --report
    python tools/map_by_lexicon.py --lexicon data/kc_lexicon.json --stage senior --emit
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAND = ROOT / "inbox" / "candidates"


def normalize(text: str) -> str:
    """压掉空白，让正则不必关心 LaTeX 里的空格。"""
    return re.sub(r"\s+", " ", text or "")


def score(entry: dict, text: str) -> tuple[int, bool]:
    """返回 (命中权重, 是否满足硬条件)。"""
    must = entry.get("must") or []
    anyp = entry.get("any") or []
    notp = entry.get("not") or []

    for pat in notp:
        if re.search(pat, text):
            return 0, False
    for pat in must:
        if not re.search(pat, text):
            return 0, False
    hits = sum(1 for pat in anyp if re.search(pat, text))
    if anyp and hits == 0:
        return 0, False
    # 权重：must 全中记 1 分基础，any 每中一项加分，must 项数也算特异性
    return 1 + hits + 0.5 * len(must), True


def score_relaxed(entry: dict, text: str, min_tier: str = "B") -> tuple[float, bool]:
    """并集评分：must 或 any 命中其一即可，不再要求两者同时成立。

    为什么需要：
        原来的 score 要求 must **和** any 同时命中（交集条件）。
        实测初中池 2766 道里有 1561 道是「有 any 关键词但被 must 卡死」——
        例如「2022 的绝对值是」这道题命中了『绝对值』，但该知识点
        must=['绝对值']、any=['非负','距离']，any 一个都没中，于是被拒。
        但它显然就是一道绝对值题。

    三档权重（刻度刻意拉开，档位之间不会被 margin 误伤）：
        A  must 全中 + any 至少中一项   —— 题材与考法都命中，最强
        B  must 全中 + any 一项没中     —— 题材对，考法未命中
        C  any 命中若干 + must 没全中    —— 只有弱信号

    `not` 仍然是硬否决：它专门用来排除「同名不同义」，放松 must/any
    不能连带放松 not，否则会把噪声当信号。

    两处容易写错的地方，这里明确处理：
      1. must 为空且 any 没命中 → **零信号**，直接拒。
         （否则每个「any 较宽」的知识点都会白拿一个 B 档分。）
      2. must 为空但 any 命中 → 算 A 档。must 为空表示这个知识点
         本来就靠 any 界定，不是弱化。
      3. **C 档默认拒收**（min_tier="B"）。抽样核查发现 C 档几乎全是噪声：
         「篝火晚会时人们围成一圈」被 any 里的『计算』命中而落到
         「含有小括号的两级运算」。弱信号不该进题库。
    """
    must = entry.get("must") or []
    anyp = entry.get("any") or []
    notp = entry.get("not") or []

    for pat in notp:
        if re.search(pat, text):
            return 0.0, False

    must_hits = sum(1 for pat in must if re.search(pat, text))
    any_hits = sum(1 for pat in anyp if re.search(pat, text))
    must_ok = (not must) or (must_hits == len(must))

    if must_ok and any_hits:
        # A 档：题材与考法都命中
        return 10.0 + any_hits + 0.5 * len(must), True
    if must_ok and must:
        # B 档：题材命中但考法未命中（must 非空才有题材信号）
        return 5.0 + 0.5 * len(must), True
    if any_hits:
        # C 档：只有弱信号，按命中数给分并封顶，保证低于 B 档
        if min_tier == "C":
            return min(0.5 * any_hits, 3.0), True
        return 0.0, False
    return 0.0, False


def load_lexicon(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def classify(entry_map: dict, text: str, min_margin: float = 1.0):
    text = normalize(text)
    scored = []
    for tid, entry in entry_map.items():
        s, ok = score(entry, text)
        if ok:
            scored.append((s, tid))
    if not scored:
        return None, []
    scored.sort(reverse=True)
    if len(scored) == 1:
        return scored[0][1], scored
    if scored[0][0] - scored[1][0] < min_margin:
        return None, scored  # 并列，宁可不标
    return scored[0][1], scored


def classify_relaxed(entry_map: dict, text: str, min_margin: float = 1.0, min_tier: str = "B"):
    """单标签版并集评分。返回 (主知识点, 全部得分列表)。"""
    text = normalize(text)
    scored = []
    for tid, entry in entry_map.items():
        s, ok = score_relaxed(entry, text, min_tier)
        if ok:
            scored.append((s, tid))
    if not scored:
        return None, []
    scored.sort(key=lambda x: (-x[0], x[1]))
    if len(scored) == 1:
        return scored[0][1], scored
    if scored[0][0] - scored[1][0] < min_margin:
        return None, scored
    return scored[0][1], scored


def classify_multi(entry_map: dict, text: str, margin: float = 1.0,
                   max_tags: int = 3, min_ratio: float = 0.55,
                   relaxed: bool = False, min_tier: str = "B"):
    """多标签分类：返回 (主知识点, [全部知识点], 全部得分)。

    为什么改成多标签：
        中高考真题多为复合题——一道压轴题同时考函数、导数、方程。
        原来的单点分类只能二选一：要么错标到某一个点，要么整题丢弃
        （`--only-choice` 就是用 MULTI 标记直接过滤掉的）。

    规则：
      1. 先算出所有满足硬条件的知识点及其得分。
      2. 主知识点取最高分——它代表「这题主要考什么」。
      3. 附属知识点取分数 ≥ 主分 × min_ratio 的其余点——代表「这题还顺带考了什么」。
      4. 附属点最多 max_tags 个，避免一道题挂十几个点、稀释掉所有点的题量。

    并列时（top1 与 top2 差 < margin）**不再整题丢弃**：
        两个知识点得分接近，恰恰说明这是一道**复合题**（同时考两个点）。
        原来判为无归属，结果是两个点都少题——实测初中 6 个缺口点因此
        丢掉了 52% 本来映射得上的题（m8a.13.1 三角形的概念 121 道里丢 62 道）。
        改为：取 top1 作主点，把分差 < margin 的其余点收为附属。
        主点仍按分数定，所以「主要考什么」的判断没有变松。
    """
    text = normalize(text)
    scored = []
    for tid, entry in entry_map.items():
        if relaxed:
            s, ok = score_relaxed(entry, text, min_tier)
        else:
            s, ok = score(entry, text)
        if ok:
            scored.append((s, tid))
    if not scored:
        return None, [], []
    scored.sort(key=lambda x: (-x[0], x[1]))
    top_score = scored[0][0]
    primary = scored[0][1]
    # 并列：top1 与 top2 分差不足 margin，说明两者是并列考点
    tied = len(scored) > 1 and (top_score - scored[1][0]) < margin
    extra = []
    for s, tid in scored[1:]:
        if len(extra) >= max_tags:
            break
        if tied and not extra and (top_score - s) < margin:
            # 并列时**只收 top2**：两个得分接近的点就是「并列为考点」的那两个。
            # 不收 top3 及以后——B 档分数密集（5.0~6.0），差 <1.0 的点很多，
            # 全收会让一道题挂满 3 个点，题点对虚高（实测高考从 7310 涨到 12790）。
            extra.append(tid)
        elif s >= top_score * min_ratio:
            extra.append(tid)
    return primary, [primary] + extra, [t for _s, t in scored]


MULTI = ("（1）", "(1)", "（Ⅰ）", "(I)", "（I）", "①", "（i）")


def run(lexicon: dict, stage: str, margin: float, only_choice: bool = False,
         pool_path: pathlib.Path | None = None, multi: bool = False,
         max_tags: int = 3, min_ratio: float = 0.55, relaxed: bool = False) -> dict:
    path = pool_path or (CAND / f"cmm_{stage}.jsonl")
    rows = [json.loads(ln) for ln in path.open(encoding="utf-8") if ln.strip()]
    pool_before = len(rows)
    # 解答题常常一题考三个知识点。**多标签模式下不丢弃它们**——
    # 复合题是真题的主体，丢掉就等于把最有价值的题目扔了。
    # 单标签模式下才用 MULTI 标记过滤，因为那时标任何一个点都是错的。
    if only_choice and not multi:
        rows = [r for r in rows if r["type"] == "choice"
                and not any(m in r["prompt"] for m in MULTI)]
    multi_dropped = pool_before - len(rows) if (only_choice and not multi) else 0
    entry_map = {k: v for k, v in lexicon.items() if v.get("stage") == stage}

    mapped, ambiguous, nomatch = [], 0, 0
    per_topic = collections.Counter()
    tag_hist = collections.Counter()
    for row in rows:
        text = row["prompt"] + " " + " ".join(c["text"] for c in row.get("choices", []))
        if multi:
            primary, tags, _all = classify_multi(entry_map, text, margin,
                                                 max_tags, min_ratio, relaxed)
            if primary is None:
                if _all:
                    ambiguous += 1
                else:
                    nomatch += 1
                continue
            for t in tags:
                per_topic[t] += 1
            tag_hist[len(tags)] += 1
            mapped.append((row, primary, tags))
        else:
            if relaxed:
                tid, scored = classify_relaxed(entry_map, text, margin)
            else:
                tid, scored = classify(entry_map, text, margin)
            if tid is None:
                if scored:
                    ambiguous += 1
                else:
                    nomatch += 1
                continue
            per_topic[tid] += 1
            mapped.append((row, tid, [tid]))

    return {
        "stage": stage,
        "mode": ("multi" if multi else "single") + ("+relaxed" if relaxed else ""),
        "pool": len(rows),
        "dropped_multipart": multi_dropped,
        "lexicon_topics": len(entry_map),
        "mapped": len(mapped),
        "ambiguous": ambiguous,
        "nomatch": nomatch,
        "covered_topics": len(per_topic),
        "tag_count_hist": dict(sorted(tag_hist.items())),
        "top": per_topic.most_common(10),
        "items": mapped,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lexicon", default="data/kc_lexicon.json")
    ap.add_argument("--stage", default="senior", choices=["junior", "senior", "primary"])
    ap.add_argument("--margin", type=float, default=1.0)
    ap.add_argument("--pool", default="", help="候选池 JSONL，默认 cmm_<stage>.jsonl")
    ap.add_argument("--only-choice", action="store_true",
                    help="只收选择题且不含多小问标记（仅单标签模式有效）")
    ap.add_argument("--multi", action="store_true",
                    help="多标签模式：一题可挂多个知识点，复合题不再丢弃")
    ap.add_argument("--relaxed", action="store_true",
                    help="并集评分：must 或 any 命中其一即可（not 仍是硬否决）")
    ap.add_argument("--max-tags", type=int, default=3, help="每题最多挂几个知识点")
    ap.add_argument("--min-ratio", type=float, default=0.55,
                    help="附属知识点的最低得分占比（相对主知识点）")
    ap.add_argument("--emit", action="store_true", help="写出映射结果 JSONL")
    ap.add_argument("--show", type=int, default=0, help="抽样打印 N 条已映射的题")
    args = ap.parse_args()

    lexicon = load_lexicon(ROOT / args.lexicon)
    pool = pathlib.Path(args.pool) if args.pool else None
    result = run(lexicon, args.stage, args.margin, args.only_choice, pool,
                 multi=args.multi, max_tags=args.max_tags, min_ratio=args.min_ratio,
                 relaxed=args.relaxed)
    items = result.pop("items")

    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.show:
        import random
        random.seed(11)
        for item in random.sample(items, min(args.show, len(items))):
            row, primary, tags = item
            names = " + ".join(topics.get(t, {}).get("name", t) for t in tags)
            print(f"\n[{names}] {row['prompt'][:70]}".replace("\n", " "))

    if args.emit:
        out = ROOT / "build" / "question-bank" / f"mapped_{args.stage}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for row, primary, tags in items:
                rec = dict(row)
                rec["topicId"] = primary
                rec["primaryTopicId"] = primary
                rec["topicIds"] = tags
                rec["needs_kc"] = False
                rec["mapping"] = {"method": "lexicon-multi" if args.multi else "lexicon",
                                  "stage": args.stage, "tags": tags}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\n已写出 {out.relative_to(ROOT)}：{len(items)} 条")


if __name__ == "__main__":
    main()
