"""生成交接模板：把需要外部补齐的字段导出成 CSV，对方填完放回 inbox/ 即可。

设计原则：**给出去的每一行都带 id**，因为 id 是唯一可靠的连接键，
名称会重名（如「平均数」在小学和初中各有一个），靠名字回填必错。

    python tools/export_inbox.py            # 生成 inbox/*.csv
    python tools/export_inbox.py --review-cap 120
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INBOX = ROOT / "inbox"  # main() 里会被 --out 覆盖

# 需要拆到课时/节级的册次：单元级的 3 册 + 节级的 4 册
OUTLINE_VOLUMES = ["四下", "五下", "六下", "二下", "四上", "五上", "六上"]


def write_csv(path: pathlib.Path, header: list, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {path.name:<24} {len(rows):>4} 行")


def export_topics(topics):
    """给 KC 映射用：对方拿这份清单把外部知识点对到我们的 id 上。"""
    write_csv(INBOX / "topics.csv",
              ["id", "name", "volume", "chapter", "section", "type"],
              [[t["id"], t["name"], t["volume"], t["chapter"], t["section"], t["type"]]
               for t in topics])


def export_kc_template():
    """空模板：一行示例，其余由对方填写。"""
    write_csv(INBOX / "kc_map.csv",
              ["kc_id", "kc_name", "topic_id", "confidence", "note"],
              [["<示例> 100001", "有理数的概念", "m7a.1.2.1", "high", "删除本行后再填"]])


def export_question_stats_template():
    write_csv(INBOX / "question_stats.csv",
              ["question_id", "attempts", "correct_rate"],
              [["<示例> m7a.1.1-q1", "1200", "0.63"]])


def export_standards(standards):
    """78 条课标：原文、出处、现有摘要一起给出，对方做核对与改写。

    踩过的坑：早先这里把 verbatim_text 和 verbatim 硬写成空串（那时课标还没有原文），
    课标补齐原文后没跟着改，导致第三轮回传收到 78 行空原文——
    Astra 从对比里发现了这个不一致。**导出字段必须跟着数据状态走，不能写死。**
    """
    rows = []
    for s in standards:
        rows.append([s["id"], s.get("domain", ""), s.get("theme", ""), s.get("title", ""),
                     s.get("summary", ""), s.get("verbatim_text", ""), s.get("page_or_ref", ""),
                     "true" if s.get("verbatim") else "false"])
    write_csv(INBOX / "standards.csv",
              ["id", "domain", "theme", "title", "current_summary",
               "verbatim_text", "page_or_ref", "verbatim"], rows)


def export_outline(outline):
    """把需要细化的册次按现有叶子导出一份，对方改/补即可。"""
    rows = []
    for vol in outline["volumes"]:
        if vol["volume"] not in OUTLINE_VOLUMES:
            continue
        for ch in vol["chapters"]:
            leaves = ch.get("leaves") or []
            if not leaves:
                rows.append([vol["volume"], ch["no"], ch["title"], "", "", "keep", ""])
                continue
            for leaf in leaves:
                rows.append([vol["volume"], ch["no"], ch["title"],
                             leaf["no"], leaf["title"], "keep", ""])
    write_csv(INBOX / "outline.csv",
              ["volume", "chapter_no", "chapter_title", "leaf_no", "leaf_title",
               "action", "source"], rows)


def pick_review_sample(questions, topics, cap=160, exclude=None):
    """抽样规则：所有短答 + 所有 standard + 每个 root 点 1 题 + 每册 3 题。

    root 点优先，因为它们的判断错了会污染整条链；短答优先，因为答案唯一性最脆。
    exclude 用于排除已复核过的题——否则每轮都会重复抽到同一批，
    917 道从未被看过的题永远轮不上。
    """
    exclude = exclude or set()
    questions = [q for q in questions if q["id"] not in exclude]
    by_topic = {}
    for q in questions:
        by_topic.setdefault(q["topicId"], []).append(q)

    picked, seen = [], set()

    def add(qid):
        if qid in seen:
            return
        seen.add(qid)
        picked.append(qid)

    for q in questions:
        if q["type"] == "short":
            add(q["id"])
    for q in questions:
        if q.get("difficulty") == "standard":
            add(q["id"])

    # 每册 3 题（取每册前三个点各 1 题），保证覆盖
    per_volume = {}
    for t in topics:
        qs = by_topic.get(t["id"]) or []
        if not qs:
            continue
        bucket = per_volume.setdefault(t["volume"], [])
        if len(bucket) < 3:
            bucket.append(qs[0]["id"])
    for qids in per_volume.values():
        for qid in qids:
            add(qid)

    # 规则抽样只覆盖"高危"题（短答/standard/root），排除已复核后往往凑不满 cap。
    # 剩下按册轮转补齐：既保证册际均衡，又不让某一册的长尾题永远轮不上。
    # 排序用 id 的稳定哈希，不用 random，保证同输入同输出、可复现。
    if len(picked) < cap:
        vol_of = {q["id"]: t["volume"] for t in topics for q in by_topic.get(t["id"], [])}
        queues = {}
        for q in questions:
            if q["id"] in seen:
                continue
            queues.setdefault(vol_of.get(q["id"], "?"), []).append(q["id"])
        for qids in queues.values():
            qids.sort(key=lambda x: hashlib.sha1(x.encode()).hexdigest())
        order = sorted(queues)
        while len(picked) < cap and any(queues.values()):
            for vol in order:
                if queues[vol] and len(picked) < cap:
                    add(queues[vol].pop(0))

    return [q for q in questions if q["id"] in seen][:cap]


def question_sha256(q: dict) -> str:
    """完整题目内容哈希，序列化规则与 docs/handoff.md 一致。

    覆盖题干、全部选项、答案、accept、解析、难度与题型；改任何一项都会变。
    """
    payload = json.dumps(q, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def option_signature(text: str) -> str:
    """选项内容指纹：让复核方能按内容绑定正确项，而不是按 A/B/C/D。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]


def export_review(questions, topics, cap, exclude=None):
    """review.csv 保持 9 列协议不变；完整题目另存 sidecar。

    上一版只导出题干/答案/解析，导致 44 道选择题缺选项、57 道短答缺 accept，
    复核方无法判断唯一正确项，68 道只能标 skip。这个问题必须由导出方负责，
    不能要求复核方去猜。
    """
    sample = pick_review_sample(questions, topics, cap, exclude)
    rows = []
    sidecar = []
    for q in sample:
        answer = q.get("answer", "")
        if q["type"] == "choice":
            # answer 现在是 option_id；显示文字按当前顺序取
            ids = [c.get("id") for c in q.get("choices", [])]
            texts = {c.get("id"): c["text"] for c in q.get("choices", [])}
            answer = f"{answer}. {texts.get(answer, '')}"
        rows.append([q["id"], q["topicId"], q["type"], q.get("difficulty", ""),
                     q["prompt"], answer, q.get("explanation", ""), "", ""])

        item = {
            "question_id": q["id"],
            "topic_id": q["topicId"],
            "type": q["type"],
            "difficulty": q.get("difficulty", ""),
            "prompt": q["prompt"],
            "answer": q.get("answer", ""),
            "explanation": q.get("explanation", ""),
            "full_question_sha256": question_sha256(q),
        }
        if q["type"] == "choice":
            item["choices"] = [{"option_id": c["id"],
                                "text": c["text"], "option_sha256": option_signature(c["text"])}
                               for c in q.get("choices", [])]
            item["correct_option_ids"] = [q["answer"]]
            item["single_or_multiple"] = "single"
        elif q["type"] == "short":
            item["accept"] = q.get("accept", [])
        elif q["type"] == "numeric":
            item["verify"] = q.get("verify", "")
        sidecar.append(item)

    write_csv(INBOX / "review.csv",
              ["question_id", "topic_id", "type", "difficulty", "prompt",
               "answer", "explanation", "verdict", "note"], rows)
    path = INBOX / "review_sidecar.json"
    id_field = [{"question_id": s["question_id"], "topic_id": s["topic_id"],
                 "full_question_sha256": s["full_question_sha256"]} for s in sidecar]
    id_field.sort(key=lambda x: x["question_id"])
    side_topic = hashlib.sha256(json.dumps(id_field, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
    path.write_text(json.dumps({
        "purpose": "review.csv 的完整题目 sidecar：补齐选项、正确项 id、accept、verify 与内容哈希",
        "hash_rule": "sha256(utf-8(json.dumps(question, ensure_ascii=False, sort_keys=True, separators=(',',':'))))",
        "review_sidecar_sha256": side_topic,
        "questions": sorted(sidecar, key=lambda x: x["question_id"]),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  {'review_sidecar.json':<24} {len(sidecar):>4} 行（完整选项/accept/哈希）")
    return len(sample)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-cap", type=int, default=160)
    ap.add_argument("--exclude-reviewed", action="store_true",
                    help="排除 data/reviews.json 里已经复核过的题，用于下一批")
    ap.add_argument("--out", default="inbox", help="导出目录，默认 inbox/")
    args = ap.parse_args()

    global INBOX
    if args.out:
        INBOX = pathlib.Path(args.out)
        INBOX.mkdir(parents=True, exist_ok=True)

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    standards = json.loads((DATA / "standards.json").read_text(encoding="utf-8"))
    outline = json.loads((DATA / "outline.json").read_text(encoding="utf-8"))

    print("生成 inbox 模板：")
    export_topics(topics)
    export_kc_template()
    export_question_stats_template()
    export_standards(standards)
    export_outline(outline)
    exclude = set()
    if args.exclude_reviewed:
        rv = json.loads((DATA / "reviews.json").read_text(encoding="utf-8"))
        exclude = set(rv)
        print(f"排除已复核 {len(exclude)} 题")
    n = export_review(questions, topics, args.review_cap, exclude)
    print(f"\n复核样本 {n} 题（短答优先、每册 3 题）")
    print("填好后运行：python tools/ingest.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
