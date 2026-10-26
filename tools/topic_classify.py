"""题 → 章节 的基线分类器（纯标准库），用来判断语义映射值不值得做。

背景
    docs/question-sources.md 实测：拿知识点名称去题里做字面检索，只命中 8.4%。
    所以「外部题接入」卡在一步上——判断这道题考的是哪一章/哪个知识点。

本工具回答一个更小的问题：
    只给题干文本，能不能判出它属于哪一章？

    能判出章节，就做「先章后点」两级映射；
    连章节都判不出，就别浪费时间，直接上人/模型逐题看。

做法
    字符 n-gram（2/3-gram）+ TF-IDF 权重 + 质心余弦相似度。
    训练数据就是项目自己的 1077 道题（已有 topicId → chapter 标注）。
    交叉验证按 topicId 分组，避免同一知识点的相似题同时进训练和验证。

用法
    python tools/topic_classify.py                      # 5 折交叉验证
    python tools/topic_classify.py --predict            # 给候选池打章节预标
    python tools/topic_classify.py --predict --stage senior
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOPICS = ROOT / "data" / "topics.json"
QUESTIONS = ROOT / "data" / "questions.json"
CAND_DIR = ROOT / "inbox" / "candidates"


def clean(text: str) -> str:
    """去掉 LaTeX 记号与标点，只留中文、字母、数字。"""
    text = re.sub(r"\$[^$]*\$", " ", text or "")
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", " ", text)


def ngrams(text: str, ns=(2, 3)) -> collections.Counter:
    text = clean(text)
    counter: collections.Counter = collections.Counter()
    for n in ns:
        for i in range(len(text) - n + 1):
            gram = text[i : i + n]
            if gram.strip() == gram:  # 跳过带空格的碎片
                counter[gram] += 1
    return counter


class CentroidModel:
    """每个类别一个 TF-IDF 质心，预测时取余弦相似度最高的类。"""

    def __init__(self) -> None:
        self.idf: dict[str, float] = {}
        self.centroids: dict[str, dict[str, float]] = {}
        self.labels: list[str] = []

    def fit(self, texts: list[str], labels: list[str]) -> None:
        docs = [ngrams(t) for t in texts]
        df: collections.Counter = collections.Counter()
        for doc in docs:
            df.update(doc.keys())
        total = len(docs)
        self.idf = {g: math.log((1 + total) / (1 + c)) + 1 for g, c in df.items()}
        self.labels = sorted(set(labels))

        acc: dict[str, collections.Counter] = {lab: collections.Counter() for lab in self.labels}
        counts = collections.Counter()
        for doc, lab in zip(docs, labels):
            counts[lab] += 1
            for gram, tf in doc.items():
                acc[lab][gram] += tf * self.idf.get(gram, 1.0)
        for lab, vec in acc.items():
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self.centroids[lab] = {g: v / norm for g, v in vec.items()}

    def scores(self, text: str) -> list[tuple[str, float]]:
        doc = ngrams(text)
        vec = {g: tf * self.idf.get(g, 1.0) for g, tf in doc.items() if g in self.idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        out = []
        for lab, cen in self.centroids.items():
            # 稀疏点积：遍历较短的一侧
            small, big = (vec, cen) if len(vec) < len(cen) else (cen, vec)
            dot = sum(v * big.get(g, 0.0) for g, v in small.items())
            out.append((lab, dot / norm))
        out.sort(key=lambda x: -x[1])
        return out

    def predict(self, text: str) -> str:
        return self.scores(text)[0][0]


def load_labeled() -> tuple[list[str], list[str], list[str], list[str]]:
    """返回 (题干, 册|章, 册, topicId)。"""
    topics = {t["id"]: t for t in json.load(open(TOPICS, encoding="utf-8"))}
    questions = json.load(open(QUESTIONS, encoding="utf-8"))
    texts, chapters, volumes, groups = [], [], [], []
    for q in questions:
        topic = topics.get(q.get("topicId"))
        if not topic or not topic.get("chapter"):
            continue
        texts.append(q["prompt"])
        chapters.append(f"{topic['volume']}|{topic['chapter']}")
        volumes.append(topic["volume"])
        groups.append(q["topicId"])
    return texts, chapters, volumes, groups


def _cv(texts: list[str], labels: list[str], groups: list[str], k: int, seed: int = 0) -> tuple[float, float]:
    """按 group 分组的 k 折，返回 (top1, top3)。"""
    by_group: dict[str, list[int]] = collections.defaultdict(list)
    for idx, g in enumerate(groups):
        by_group[g].append(idx)

    rng = random.Random(seed)
    keys = sorted(by_group)
    rng.shuffle(keys)
    folds: list[list[int]] = [[] for _ in range(k)]
    for i, key in enumerate(keys):
        folds[i % k].extend(by_group[key])

    correct = top3_hits = 0
    for f in range(k):
        test_idx = set(folds[f])
        train_idx = [i for i in range(len(texts)) if i not in test_idx]
        model = CentroidModel()
        model.fit([texts[i] for i in train_idx], [labels[i] for i in train_idx])
        for i in sorted(test_idx):
            ranked = [lab for lab, _ in model.scores(texts[i])[:3]]
            if ranked and ranked[0] == labels[i]:
                correct += 1
            if labels[i] in ranked:
                top3_hits += 1
    return correct / len(texts), top3_hits / len(texts)


def cross_validate(k: int = 5) -> dict:
    texts, chapters, volumes, groups = load_labeled()

    top1, top3 = _cv(texts, chapters, groups, k)
    vol1, vol3 = _cv(texts, volumes, groups, k)

    # 第二级：册内判章。只统计样本足够的册。
    per_volume = []
    for vol in sorted(set(volumes)):
        idx = [i for i, v in enumerate(volumes) if v == vol]
        if len(idx) < 20:
            continue
        sub_ch = [chapters[i] for i in idx]
        sub_g = [groups[i] for i in idx]
        sub_t = [texts[i] for i in idx]
        v1, _ = _cv(sub_t, sub_ch, sub_g, min(k, len(set(sub_g))))
        per_volume.append((vol, len(idx), len(set(sub_ch)), round(v1, 3)))

    weighted = sum(n * acc for _, n, _, acc in per_volume) / sum(n for _, n, _, _ in per_volume)
    return {
        "n_questions": len(texts),
        "n_chapters": len(set(chapters)),
        "n_volumes": len(set(volumes)),
        "A_全库判章_117类": {"top1": round(top1, 4), "top3": round(top3, 4)},
        "B_先判册_23类": {"top1": round(vol1, 4), "top3": round(vol3, 4)},
        "C_册内判章_加权": round(weighted, 4),
        "C_明细": per_volume,
    }


def predict_pool(stage: str, top: int = 1) -> dict:
    texts, labels, _, _ = load_labeled()
    model = CentroidModel()
    model.fit(texts, labels)

    path = CAND_DIR / f"cmm_{stage}.jsonl"
    if not path.exists():
        raise SystemExit(f"缺候选池 {path}，先跑 python tools/fetch_question_bank.py --stage {stage}")
    rows = [json.loads(ln) for ln in path.open(encoding="utf-8") if ln.strip()]

    dist: collections.Counter = collections.Counter()
    weak = 0
    for row in rows:
        scored = model.scores(row["prompt"])[:top]
        dist[scored[0][0]] += 1
        # 第一名与第二名差距太小，说明模型自己也拿不准
        if top >= 2 and scored[0][1] - scored[1][1] < 0.05:
            weak += 1
    return {
        "pool": str(path.relative_to(ROOT)),
        "n": len(rows),
        "chapters_predicted": len(dist),
        "top_chapters": dist.most_common(8),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--predict", action="store_true")
    ap.add_argument("--stage", default="junior", choices=["primary", "junior", "senior"])
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()

    if args.predict:
        print(json.dumps(predict_pool(args.stage), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(cross_validate(args.folds), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
