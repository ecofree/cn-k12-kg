"""生成第四轮复核任务包。

第四轮要解决两件事：
1. 复检第三轮修订的 20 道题（verdict 仍是 fail，等复检才能改）
2. 复核剩余 617 道从未被第二人看过的题

抽样规则沿用 export_inbox.py 的 pick_review_sample：
短答优先、standard 优先、root 点优先、每册轮转补齐。
顺序用 id 哈希而非 random，保证同输入同输出。
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "build" / "to-astra-round4"

sys.path.insert(0, str(ROOT / "tools"))
from export_inbox import pick_review_sample  # noqa: E402


def sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_csv(path: pathlib.Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_review_rows(questions, topics, cap: int) -> list[list]:
    topic_map = {t["id"]: t for t in topics}
    rows = []
    for q in questions:
        t = topic_map.get(q["topicId"])
        if not t:
            continue
        choices = q.get("choices") or []
        opts = " | ".join(f"{chr(65 + i)}. {c['text']}" for i, c in enumerate(choices))
        rows.append([
            q["id"],
            q["topicId"],
            t["name"],
            t["volume"],
            q["type"],
            q.get("difficulty", ""),
            q["prompt"],
            opts,
            q["answer"],
            "|".join(q.get("accept") or []),
            q.get("explanation", ""),
            sha256(q),
            "",   # verdict
            "",   # note
        ])
    return rows[:cap]


def main() -> None:
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    reviews = json.loads((DATA / "reviews.json").read_text(encoding="utf-8"))

    pending = [
        qid for qid, rec in reviews.items()
        if (rec.get("resolution") or {}).get("status") == "fixed_pending_recheck"
    ]
    already = set(reviews)
    unseen = [q for q in questions if q["id"] not in already]

    # 第四轮复检表：只放这 20 条修订
    by_id = {q["id"]: q for q in questions}
    header = ["question_id", "topic_id", "type", "prompt", "options", "answer",
              "accept", "explanation", "full_question_sha256", "verdict", "note"]
    rows = []
    for qid in pending:
        q = by_id[qid]
        choices = q.get("choices") or []
        opts = " | ".join(f"{chr(65 + i)}. {c['text']}" for i, c in enumerate(choices))
        rows.append([qid, q["topicId"], q["type"], q["prompt"], opts, q["answer"],
                     "|".join(q.get("accept") or []), q.get("explanation", ""),
                     sha256(q), "", ""])
    write_csv(OUT / "inbox" / "fix_recheck.csv", header, rows)

    # 第四轮新题复核：从未被看过的题
    cap = 320
    picked = pick_review_sample(unseen, topics, cap)
    picked_ids = sorted(
        (q["id"] if isinstance(q, dict) else q for q in picked),
        key=lambda qid: hashlib.sha256(qid.encode()).hexdigest(),
    )
    sub = [by_id[qid] for qid in picked_ids]
    header2 = ["question_id", "topic_id", "topic_name", "volume", "type", "difficulty",
               "prompt", "options", "answer", "accept", "explanation",
               "full_question_sha256", "verdict", "note"]
    write_csv(OUT / "inbox" / "review.csv", header2, build_review_rows(sub, topics, cap))

    # 剩余未复核的题留给第五轮
    rest = [q["id"] for q in unseen if q["id"] not in set(picked_ids)]

    manifest = {
        "round": 4,
        "generated": "2026-10-24",
        "fix_recheck": len(rows),
        "review": len(sub),
        "remaining_unreviewed": len(rest),
        "review_coverage_before": round(len(already) / len(questions), 4),
        "review_coverage_after": round((len(already) + len(sub)) / len(questions), 4),
        "notes": [
            "fix_recheck 是第三轮修订的独立复检，verdict 写 pass/fail/skip",
            "review 的 note 若为 fail，必须以 correct_option_ids= 开头（选项已绑定稳定 id）",
            "sha256 是题目全字段指纹，回传时不要改动 prompt/options/answer/explanation",
        ],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "inbox" / "remaining_ids.txt").write_text(
        "\n".join(rest), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
