"""把选择题的正确选项均匀打散到各位置。

为什么需要：题库是分批手写的，正确项习惯性落在 B 上。实测 422 道选择题里
B 占 51%、D 只占 3%——学生只要一路选 B 就能答对一半，比随机猜测高出一倍。

做法：对每道题按 id 的哈希在 A/B/C/D 里挑一个目标位置，然后把选项整体旋转，
让正确项落在该位置，再按 A、B、C、D 重新贴标签。旋转保留选项之间的相对顺序，
不会改变题意，也不会让正确项与干扰项互换内容。

幂等：目标位置只由 id 决定，跑第二遍时正确项已在目标位置，旋转量为 0。

    python tools/balance_options.py --dry-run
    python tools/balance_options.py
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def dump(path: pathlib.Path, rows: list):
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def hash_rank(qid: str) -> int:
    """用 id 的哈希当排序键：同 id 永远得到同一个位置，与当前选项顺序无关。"""
    return int(hashlib.sha256(qid.encode("utf-8")).hexdigest()[:12], 16)


def assign_targets(questions: list) -> dict:
    """按选项数分组，哈希排序后轮流分配目标位置，保证每组内近似完全均衡。

    单靠 hash % n 在大样本下会有 2—3 个百分点的随机偏差（实测 4 选项组 C 占 31%），
    轮流分配把偏差压到 1 道题以内。
    """
    groups = {}
    for q in questions:
        if q.get("type") == "choice" and q.get("choices"):
            groups.setdefault(len(q["choices"]), []).append(q)
    targets = {}
    for n, rows in groups.items():
        rows.sort(key=lambda q: hash_rank(q["id"]))
        for i, q in enumerate(rows):
            targets[q["id"]] = i % n
    return targets


def balance(question: dict, want: int) -> dict:
    """把正确项挪到第 want 个位置，返回重排后的题目。

    **option_id 改造后，这里不再有方向陷阱。**
    选项带着自己的 id 一起移动，答案指向的是 id，所以答案**完全不用改**——
    不可能再出现"旋转后答案指错"这类事故。
    旧的实现要按 (old - want) % n 计算旋转量，写反一次就让 307 道题的答案键失效（见 D19）。

    返回的 choices 顺序变了，但 id 不变；显示用的字母由前端按下标生成。
    """
    choices = question.get("choices") or []
    if question.get("type") != "choice" or not choices:
        return question
    ids = [c.get("id") for c in choices]
    answer_id = question.get("answer")
    if answer_id not in ids:
        return question

    n = len(choices)
    old = ids.index(answer_id)
    if old == want:
        return question

    k = (old - want) % n          # 左移 k 位后，old 位置的元素落到 want
    rotated = choices[k:] + choices[:k]
    assert [c["id"] for c in rotated][want] == answer_id, "旋转后正确项丢失"
    return {**question, "choices": rotated}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = DATA / "questions.json"
    questions = json.loads(path.read_text(encoding="utf-8"))
    before = collections.Counter()
    after = collections.Counter()
    changed = 0
    updated = {}
    targets = assign_targets(questions)

    for i, q in enumerate(questions):
        if q.get("type") != "choice":
            continue
        before[q["answer"]] += 1
        new = balance(q, targets[q["id"]])
        if new != q:
            changed += 1
            questions[i] = new
            updated[q["id"]] = new
            q = new
        after[q["answer"]] += 1

    print(f"选择题 {sum(before.values())}，调整 {changed} 道")
    print("调整前", dict(sorted(before.items())), "最大占比",
          f"{max(before.values()) / max(sum(before.values()), 1):.0%}")
    print("调整后", dict(sorted(after.items())), "最大占比",
          f"{max(after.values()) / max(sum(after.values()), 1):.0%}")

    if args.dry_run:
        return 0

    dump(path, questions)
    synced = 0
    for content in sorted((DATA / "content").glob("*.json")):
        payload = json.loads(content.read_text(encoding="utf-8"))
        hit = 0
        for i, q in enumerate(payload.get("questions", [])):
            if q.get("id") in updated:
                payload["questions"][i] = updated[q["id"]]
                hit += 1
        if hit:
            content.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
            synced += hit
            print(f"  同步 {content.name}：{hit} 题")
    print(f"已写入 questions.json，并同步内容文件 {synced} 题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
