"""生成第五轮复核任务包。

**这一轮的重点与前四轮不同**：前四轮复核的是手写题，这一轮要重点复核
**机器生成的题**（757 道）。

生成题的风险点：
1. 反向判断题的干扰项是否真的错（会不会有第二个正确项）
2. 条件错置的干扰项改得对不对（会不会把「不成立」改成「也成立」）
3. 计算题的答案与题目是否匹配（虽然有 verify 兜底，但 verify 只验算术，
   不验「这道题问的到底是不是这个知识点的内容」）

所以这一轮把生成题**单独抽样**，且要求逐条判定。
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "build" / "to-astra-round5"

HEADER = ["question_id", "topic_id", "topic_name", "volume", "type", "difficulty",
          "prompt", "options", "answer", "accept", "explanation",
          "full_question_sha256", "verdict", "note"]


def sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def row_for(q: dict, topics: dict) -> list:
    t = topics.get(q["topicId"], {})
    choices = q.get("choices") or []
    opts = " | ".join(f"{chr(65 + i)}. {c['text']}" for i, c in enumerate(choices))
    return [
        q["id"], q["topicId"], t.get("name", ""), t.get("volume", ""),
        q["type"], q.get("difficulty", ""), q["prompt"], opts, q["answer"],
        "|".join(q.get("accept") or []), q.get("explanation", ""),
        sha256(q), "", "",
    ]


def main() -> None:
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    topics = {x["id"]: x for x in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    reviews = json.loads((DATA / "reviews.json").read_text(encoding="utf-8")) if (DATA / "reviews.json").exists() else {}

    gen = [q for q in questions if q.get("generated")]
    reviewed = set(reviews)
    unseen = [q for q in questions if q["id"] not in reviewed and not q.get("generated")]

    OUT.joinpath("inbox").mkdir(parents=True, exist_ok=True)

    # 生成题：全量送审（757 道）
    gen_rows = [row_for(q, topics) for q in gen]

    # 未复核的手写题：抽 250 道
    rnd = random.Random(20261024)
    picked = rnd.sample(unseen, min(250, len(unseen)))
    picked.sort(key=lambda q: q["id"])
    hand_rows = [row_for(q, topics) for q in picked]

    with (OUT / "inbox" / "generated_review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(gen_rows)
    with (OUT / "inbox" / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(hand_rows)

    manifest = {
        "round": 5,
        "generated": "2026-10-24",
        "generated_review": len(gen_rows),
        "review": len(hand_rows),
        "remaining_unreviewed": len(unseen) - len(hand_rows),
        # 注意：分母含 757 道新生成的题。生成题必须有人看过才算数，
        # 所以覆盖率会从第四轮的 72.4%（对 1077 道手写题）"降"到
        # 全库口径的 42.5%——这不是退步，是口径变了。
        "coverage_before": round(len(reviewed) / len(questions), 4),
        "coverage_after": round((len(reviewed) + len(gen_rows) + len(hand_rows)) / len(questions), 4),
        "handwritten_coverage_before": round(len(reviewed) / (len(questions) - len(gen)), 4),
        "handwritten_coverage_after": round(
            (len(reviewed) + len(hand_rows)) / (len(questions) - len(gen)), 4),
        "focus": "本轮重点是机器生成的题：反向判断题的干扰项是否真的错、条件错置改得对不对",
        "notes": [
            "generated_review.csv 的题是机器生成的（source 字段可见），请重点看干扰项",
            "答案现在是 option_id（o1..o4）不是字母",
            "fail 的 note 必须以 correct_option_ids= 开头（选项已绑定稳定 id）",
            "sha256 是题目全字段指纹，回传时不要改动题目内容",
        ],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
