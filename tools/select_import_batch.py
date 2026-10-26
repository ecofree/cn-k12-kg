"""为「只有 2 道题」的知识点挑候选题，生成人工判定表。

规则映射的精度天花板经实测约 83%（两轮抽样），达不到自动入库的标准。
但缺口只需要 364 道，量小到可以人工逐条判。所以分工是：
    规则负责召回与排序，人负责最后一道判断。

本工具只出表，不写库。判完的表由 import_question_bank.py 消费。

用法：
    python tools/select_import_batch.py --stage senior --per-topic 1
    python tools/select_import_batch.py --stage junior --per-topic 2
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAND = ROOT / "inbox" / "candidates"
sys.path.insert(0, str(ROOT / "tools"))

from map_by_lexicon import classify, normalize  # noqa: E402

OUT = ROOT / "build" / "question-bank"


def fingerprint(text: str) -> str:
    return hashlib.sha1(re.sub(r"[\s$\\{}]", "", text or "").encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="senior", choices=["primary", "junior", "senior"])
    ap.add_argument("--per-topic", type=int, default=1)
    ap.add_argument("--target", type=int, default=3, help="每个知识点补到几题")
    ap.add_argument("--margin", type=float, default=1.0)
    ap.add_argument("--pool", default="", help="候选池 JSONL，默认 cmm_<stage>.jsonl")
    ap.add_argument("--only-uncovered", action="store_true",
                    help="只挑外部题尚未覆盖的缺口点")
    args = ap.parse_args()

    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))

    own_fp = {fingerprint(q["prompt"]) for q in own}
    own_count: dict[str, int] = {}
    for q in own:
        own_count[q["topicId"]] = own_count.get(q["topicId"], 0) + 1

    stage_of = {"primary": lambda g: g <= 6, "junior": lambda g: 7 <= g <= 9,
                "senior": lambda g: g >= 10}
    need = [t for t in topics.values()
            if stage_of[args.stage](t["grade"]) and own_count.get(t["id"], 0) < args.target]
    need.sort(key=lambda t: t["id"])

    # 只挑「外部题还没覆盖」的缺口点。
    # 否则已补过的点会占掉 per-topic 名额，候选全落在用不上的知识点上。
    if args.only_uncovered:
        ext_path = DATA / "questions_ext.json"
        if ext_path.exists():
            covered = {q["topicId"] for q in json.loads(ext_path.read_text(encoding="utf-8"))}
            need = [t for t in need if t["id"] not in covered]
        print(f"仅未覆盖点：{len(need)} 个")

    lexicon = json.loads((DATA / "kc_lexicon.json").read_text(encoding="utf-8"))
    entry_map = {k: v for k, v in lexicon.items() if v.get("stage") == args.stage}

    pool_path = pathlib.Path(args.pool) if args.pool else (CAND / f"cmm_{args.stage}.jsonl")
    pool = [json.loads(ln) for ln in pool_path.open(encoding="utf-8") if ln.strip()]
    # 解答题跨多个知识点，单选题是唯一能可靠落到单点的形态
    pool = [r for r in pool if r["type"] == "choice"
            and not any(m in r["prompt"] for m in ("（1）", "(1)", "（Ⅰ）", "(I)", "①"))]

    buckets: dict[str, list[tuple[float, dict]]] = {}
    for row in pool:
        if fingerprint(row["prompt"]) in own_fp:
            continue
        text = normalize(row["prompt"] + " " + " ".join(c["text"] for c in row.get("choices", [])))
        tid, scored = classify(entry_map, text, args.margin)
        if tid is None or tid not in {t["id"] for t in need}:
            continue
        buckets.setdefault(tid, []).append((scored[0][0], row))

    OUT.mkdir(parents=True, exist_ok=True)
    tag = pathlib.Path(args.pool).stem if args.pool else args.stage
    out_path = OUT / f"import_review_{args.stage}_{tag}.csv"
    rows_out = []
    for tid in sorted(buckets):
        picks = sorted(buckets[tid], key=lambda x: -x[0])[: args.per_topic]
        for score, row in picks:
            rows_out.append([
                tid, topics[tid]["name"], topics[tid]["volume"],
                row["source_id"], round(score, 1),
                row["prompt"].replace("\n", " "),
                # M3.1 之后选项身份是 id，字母是渲染时生成的
                " | ".join(f"{chr(65 + i)}. {c['text']}"
                           for i, c in enumerate(row["choices"])).replace("\n", " "),
                row["answer"], row.get("explanation", "").replace("\n", " ")[:120],
                "", "",
            ])

    with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["topic_id", "topic_name", "volume", "source_id", "score",
                    "prompt", "options", "answer", "explanation_short", "keep", "reason"])
        w.writerows(rows_out)

    covered = len({r[0] for r in rows_out})
    print(json.dumps({
        "stage": args.stage,
        "need_topics": len(need),
        "candidates": len(rows_out),
        "topics_with_candidate": covered,
        "topics_without_candidate": len(need) - covered,
        "out": str(out_path.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
