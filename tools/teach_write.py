"""把讲解卡写进 data/teach/ 之前，先把能查的错挡住。

一次写几十张卡，最怕的是"看起来都对、其实指向不存在的题"。
所以落盘前逐张过三关：

1. 知识点存在、名字对得上、且它确实是**核心点**（工具点走速查卡）。
2. 每道练习题真实存在，且挂在同一个知识点上，至少一道非选择。
3. 例题的 `verify` 能求值，并且等于 `answer`。

第 3 关最要紧：讲解里的例题自己算错，学生会照着错的学会。
写完后再由 `tools/teach_cards.py --check` 复查一遍（它还会查文案长度、
易错点、识别信号），这里只挡"会把人带偏"的硬错。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.evidence import safe_eval  # noqa: E402
from tools.teach_cards import check_card  # noqa: E402

DATA = ROOT / "data"
TEACH = DATA / "teach"


def _load() -> tuple[dict, dict, dict]:
    topics = {t["id"]: t for t in json.loads(
        (DATA / "topics.json").read_text(encoding="utf-8"))}
    bank = {q["id"]: q for q in json.loads(
        (DATA / "questions.json").read_text(encoding="utf-8"))}
    tiers = json.loads((DATA / "topic_tiers.json").read_text(encoding="utf-8"))
    return topics, bank, tiers


def write_cards(cards: list[dict], source: str, *, expect_tier: str = "core") -> int:
    topics, bank, tiers = _load()
    problems: list[str] = []
    for card in cards:
        tid = card.get("topicId")
        label = tid or "<缺 topicId>"
        if tid not in topics:
            problems.append(f"{label}：知识点不存在")
            continue
        t = topics[tid]
        info = (tiers.get("topics") or {}).get(tid) or {}
        if info.get("tier") != expect_tier:
            problems.append(f"{label}：分层是 {info.get('tier')}，不该写进这批卡")
        if (card.get("name") or "") != t.get("name"):
            problems.append(f"{label}：name 与知识点不一致"
                            f"（卡={card.get('name')!r} 库={t.get('name')!r}）")
        for ref in card.get("practice") or []:
            q = bank.get(ref.get("id"))
            if q is None:
                problems.append(f"{label}：练习题不存在 {ref.get('id')}")
                continue
            if tid not in (q.get("topicIds") or [q.get("topicId")]):
                problems.append(f"{label}：练习题 {ref['id']} 不属于本知识点")
        ex = card.get("worked_example") or {}
        if ex.get("verify"):
            got = safe_eval(ex["verify"])
            try:
                want = float(str(ex.get("answer")).strip())
            except (TypeError, ValueError):
                want = None
            if got is None:
                problems.append(f"{label}：verify 无法求值 {ex['verify']!r}")
            elif want is None:
                problems.append(f"{label}：answer 不是数值，但给了 verify")
            elif abs(got - want) > 1e-6 * max(abs(want), 1.0):
                problems.append(f"{label}：verify={ex['verify']} 得 {got}，"
                                f"与 answer={want} 不符")
        for e in check_card(card, topics, bank):
            problems.append(f"{label}：{e}")

    if problems:
        print(f"{source}：{len(problems)} 处问题，未写盘")
        for p in problems:
            print("  " + p)
        return 1

    TEACH.mkdir(parents=True, exist_ok=True)
    for card in cards:
        card.setdefault("schema", 1)
        card.setdefault("review_status", "draft")
        card.setdefault("author", "hand")
        path = TEACH / f"{card['topicId']}.json"
        path.write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    print(f"{source}：写入 {len(cards)} 张 -> {TEACH}")
    return 0
