"""给「讲完了却没有应用题」的核心点补应用题。

## 为什么单独一套工具

讲解卡解决「讲」，这道工序解决「用」。实测（2026-10-26）核心点里有一批
**只有 basic 题、没有一道 standard/advanced**：学生把基础题连对两题，
系统就记「掌握」，但他从没被要求把知识用到情境里。
用户的原话是「再做题过程掌握如何运用这些知识点」——缺的正是这一环。

## 出题标准（每条都在 `main()` 里被机器检查，不靠自觉）

1. **必须真的是一道应用题**：有情境、有已知量与待求量，不是把算式换成文字。
2. **答案可复算**：`verify` 表达式求值必须等于 `answer`（`cnkg.verify.compare`）。
3. **解析必须能自证**：`cnkg.evidence.grade` 要判成 `verified`
   （解析末步算式可复算且落在答案上）。短答题允许 `explained`，
   但答案文本必须真的出现在解析里。
4. **难度标 standard / advanced**：advanced 用于「需要换一步想」的题
   （单位换算、逆向求、两步以上、易错点埋在里面）。
5. **题干不与既有题重复**：归一化后去重，避免同一道题出现两次。

## 批次组织

题面数据按册次放在 `tools/app_items/`，一册一个模块，模块里只有数据；
本文件只管校验与写库。加一册就在 `app_items/__init__.py` 的 `BATCHES`
里登记，不要动这里的逻辑。

id 规则 `{topicId}-q{101 起}`：应用题专用号段，从 101 起、每点内部顺序编号，
所以**同一道题重跑仍是同一个 id**（可复跑、可 diff）。
`source=self-generated-application`，整批回滚就删这个 source。

用法：
    python tools/write_application_items.py --dry-run
    python tools/write_application_items.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from cnkg import evidence  # noqa: E402
from cnkg.verify import VerifyError, compare, evaluate  # noqa: E402
import app_items  # noqa: E402

DATA = ROOT / "data"
SOURCE = "self-generated-application"
ID_BASE = 100          # 应用题从 q101 起算


def build(items: list[dict], questions: list[dict]) -> tuple[list[dict], list[str]]:
    """把题面数据变成题目对象，同时做全部机器检查。返回 (新题, 错误)。"""
    errors: list[str] = []
    existing = {re.sub(r"\s+", "", q.get("prompt") or "") for q in questions}
    used_ids = {str(q.get("id")) for q in questions}
    # id 必须沿用 `<topicId>-q<n>`：校验器按这个格式核对前缀，
    # 自定义后缀（如 -app1）会被判 id 非法。序号按「每点内顺序」排，
    # 不依赖既有题的最大号，重跑才不会整体平移。
    next_idx: dict[str, int] = {}
    out: list[dict] = []
    for i, it in enumerate(items, 1):
        tid = it["topic"]
        n = next_idx.get(tid, ID_BASE) + 1
        while f"{tid}-q{n}" in used_ids:
            n += 1
        next_idx[tid] = n
        qtype = it.get("type", "numeric")
        q = {
            "id": f"{tid}-q{n}",
            "topicId": tid,
            "difficulty": it.get("difficulty", "standard"),
            "type": qtype,
            "prompt": it["prompt"],
            "choices": [],
            "answer": it["answer"],
            "explanation": it["explanation"],
            "difficulty_basis": (
                "自产应用题（" + it.get("difficulty", "standard") + "）："
                + (it.get("why") or "把知识用到具体情境里，一步或两步完成")),
            "drill_kind": "application",
            "generated": True,
            "review_status": "ai-generated",
            "source": SOURCE,
            "topicIds": [tid],
            "primaryTopicId": tid,
        }
        if qtype == "numeric":
            q["verify"] = it["verify"]
            try:
                val = evaluate(it["verify"])
            except VerifyError as exc:
                errors.append(f"#{i} {tid} verify 非法：{exc}")
                continue
            if not compare(it["answer"], val):
                errors.append(f"#{i} {tid} verify={val} 与答案 {it['answer']} 不一致")
                continue
        else:
            q["accept"] = it.get("accept") or []

        norm = re.sub(r"\s+", "", q["prompt"])
        if norm in existing:
            errors.append(f"#{i} {tid} 题干与既有题重复")
            continue
        existing.add(norm)

        g = evidence.grade(q)
        want = "verified" if qtype == "numeric" else "explained"
        if evidence.rank(g["level"]) < evidence.rank(want):
            errors.append(f"#{i} {tid} 证据等级 {g['level']} 低于 {want}：{g['notes']}")
            continue
        q["evidence_level"] = g["level"]
        q["evidence_signals"] = g["signals"]
        q["evidence_notes"] = g["notes"]
        used_ids.add(q["id"])
        out.append(q)
    return out, errors


def main() -> int:
    dry = "--dry-run" in sys.argv
    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    tiers = json.loads((DATA / "topic_tiers.json").read_text(encoding="utf-8"))["topics"]
    path = DATA / "questions.json"
    questions = json.loads(path.read_text(encoding="utf-8"))
    items = app_items.all_items()

    unknown = sorted({it["topic"] for it in items} - set(topics))
    if unknown:
        print(f"知识点不存在：{unknown}")
        return 1
    non_core = [t for t in {it["topic"] for it in items} if tiers.get(t, {}).get("tier") != "core"]
    if non_core:
        print(f"这些点不是核心点，不该配应用题：{sorted(non_core)}")
        return 1

    # 可复跑：先摘掉上一轮的产出，避免重复堆积
    kept = [q for q in questions if q.get("source") != SOURCE]
    new, errors = build(items, kept)
    if errors:
        print(f"校验失败 {len(errors)} 条：")
        for e in errors:
            print("  -", e)
        return 1

    by_topic: collections.Counter = collections.Counter(q["topicId"] for q in new)
    print(f"准备写入 {len(new)} 道应用题，覆盖 {len(by_topic)} 个核心点")
    levels = collections.Counter(q["evidence_level"] for q in new)
    print(f"证据等级：{dict(levels)}")
    print(f"难度：{dict(collections.Counter(q['difficulty'] for q in new))}")
    print(f"题型：{dict(collections.Counter(q['type'] for q in new))}")
    books = collections.Counter(
        re.match(r"m(\d[ab])", t).group(1) if re.match(r"m\d[ab]", t) else "h"
        for t in by_topic)
    print(f"按册次：{dict(sorted(books.items()))}")
    thin = [t for t, n in by_topic.items() if n < 2]
    if thin:
        print(f"警告：这些点只有 1 道应用题：{thin}")

    if dry:
        print("（dry-run，未写文件）")
        return 0

    path.write_text(
        json.dumps(kept + new, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(f"已写入 {path}（{len(kept)} → {len(kept) + len(new)} 道）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
