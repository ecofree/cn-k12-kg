"""近似重复题检测：同一道题换个说法出现两次，会让学生在同一处被重复扣分。

两条通道，互补：

1. **题干+选项**：选择题把选项一起算指纹。只看题干的话，「下列说法正确的是哪一个」
   这种通用题干会把几十道完全不同的题算成一模一样。
2. **仅题干**：用于抓「选择题和短答题问同一件事」——这种重复会被选项稀释，
   通道 1 抓不到。仅题干通道会跳过出现次数超过 2 次的通用题干。

只做字面层面的字符二元组 Jaccard，不做语义判断。它的作用是把 1000+ 道题缩到几十对
候选，由人判断是不是真重复；同义改写识别不了，误报也很正常。

    python tools/find_duplicates.py --threshold 0.8
    python tools/find_duplicates.py --threshold 0.9 --out build/dup_report.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PUNCT = re.compile(r"[\s，。、；：？！“”‘’（）()【】\[\]《》<>·—…\-_/|,.!?;:\"'`~@#$%^&*+=]")


def normalize(text: str) -> str:
    return PUNCT.sub("", (text or "").lower())


def bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def collect(items, threshold):
    """items: [(question, bigram_set)]，返回相似对。按二元组数量排序剪枝。"""
    items = sorted(items, key=lambda x: len(x[1]))
    out = []
    for i, (qa, sa) in enumerate(items):
        if not sa:
            continue
        for j in range(i + 1, len(items)):
            qb, sb = items[j]
            if len(sb) > len(sa) * 4 + 8:
                break
            score = jaccard(sa, sb)
            if score >= threshold:
                out.append((score, qa, qb))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--out", default="build/dup_report.csv")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}

    # 出现超过 2 个知识点的题干视为通用题干，不参与"仅题干"通道
    stem_topics = collections.defaultdict(set)
    for q in questions:
        stem_topics[normalize(q["prompt"])].add(q["topicId"])
    generic = {s for s, tids in stem_topics.items() if len(tids) > 2}

    items_content, items_stem = [], []
    for q in questions:
        stem = normalize(q["prompt"])
        full = normalize("".join([q["prompt"]] + [c.get("text", "") for c in (q.get("choices") or [])]))
        items_content.append((q, bigrams(full)))
        if stem not in generic:
            items_stem.append((q, bigrams(stem)))

    found = {}
    for score, qa, qb in collect(items_content, args.threshold):
        found[frozenset((qa["id"], qb["id"]))] = (score, qa, qb, "题干+选项")
    for score, qa, qb in collect(items_stem, args.threshold):
        key = frozenset((qa["id"], qb["id"]))
        if key in found:
            continue
        found[key] = (score, qa, qb, "仅题干")

    pairs = []
    for score, qa, qb, basis in found.values():
        pairs.append({
            "score": round(score, 3), "basis": basis,
            "same_topic": qa["topicId"] == qb["topicId"],
            "same_answer": str(qa.get("answer")) == str(qb.get("answer")),
            "a_id": qa["id"], "a_topic": qa["topicId"], "a_prompt": qa["prompt"],
            "b_id": qb["id"], "b_topic": qb["topicId"], "b_prompt": qb["prompt"],
        })
    pairs.sort(key=lambda p: (not p["same_answer"], not p["same_topic"], -p["score"]))

    same_topic = sum(1 for p in pairs if p["same_topic"])
    print(f"题目 {len(questions)}，相似度 ≥ {args.threshold} 的候选 {len(pairs)} 对"
          f"（同知识点 {same_topic}，跨知识点 {len(pairs) - same_topic}）")

    for p in pairs[:args.top]:
        ta = topics[p["a_topic"]]["name"]
        tb = topics[p["b_topic"]]["name"]
        tag = "同点" if p["same_topic"] else f"{ta} / {tb}"
        print(f"\n  {p['score']:.2f} [{p['basis']}][{tag}]"
              f"{' 答案相同' if p['same_answer'] else ''}")
        print(f"    {p['a_id']}: {p['a_prompt'][:56]}")
        print(f"    {p['b_id']}: {p['b_prompt'][:56]}")

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["score", "basis", "same_topic", "same_answer", "a_id", "a_topic",
              "a_prompt", "b_id", "b_topic", "b_prompt"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pairs)
    print(f"\n报告已写入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
