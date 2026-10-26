"""迁移题目 schema：单值 topicId → topicIds 数组 + primaryTopicId。

为什么改：
    中高考真题多为复合题，一道题同时考多个知识点（例如压轴题同时涉及
    函数、导数、方程）。原来的单值 `topicId` 模型只能把这种题"错标"到
    任意一个知识点上，或者干脆丢弃——`map_by_lexicon.py --only-choice`
    就是用 MULTI 标记把多小问题直接过滤掉的。

改成：
    topicIds        —— 该题实际涉及的**全部**知识点（有序，第一个是主知识点）
    primaryTopicId  —— 主知识点，等于 topicIds[0]
    topicId         —— 保留，恒等于 primaryTopicId，向后兼容旧代码

这样复合题不用再被丢弃，一道题可以被多个知识点共享，且在诊断时
仍能判断「这题主要是考哪个点」。

幂等：重复运行不会改变已迁移的题目。

用法：
    python tools/migrate_multi_topic.py            # 检查并报告
    python tools/migrate_multi_topic.py --apply    # 实际写入
"""
from __future__ import annotations

# Historical CLI is fenced before imports or authoring side effects.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))


import argparse
import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def migrate(q: dict) -> tuple[dict, bool]:
    """返回 (迁移后的题目, 是否发生变更)。"""
    tid = q.get("topicId")
    ids = q.get("topicIds")
    primary = q.get("primaryTopicId")

    # 已是新格式且一致 → 不动
    if isinstance(ids, list) and ids:
        norm = []
        for x in ids:
            x = (x or "").strip()
            if x and x not in norm:
                norm.append(x)
        if not norm:
            norm = [tid] if tid else []
        new_primary = primary if primary in norm else norm[0]
        changed = (norm != ids) or (primary != new_primary) or (q.get("topicId") != new_primary)
        q["topicIds"] = norm
        q["primaryTopicId"] = new_primary
        q["topicId"] = new_primary
        return q, changed

    # 旧格式 → 新格式
    q["topicIds"] = [tid] if tid else []
    q["primaryTopicId"] = tid
    # topicId 保持不变
    return q, True


def process(path: pathlib.Path, apply: bool) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    multi = Counter()
    for q in data:
        q, ch = migrate(q)
        if ch:
            changed += 1
        if len(q.get("topicIds", [])) > 1:
            multi[len(q["topicIds"])] += 1
    if apply and changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "file": path.name,
        "total": len(data),
        "changed": changed,
        "multi_topics": dict(sorted(multi.items())),
        "applied": apply and changed > 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只报告）")
    args = ap.parse_args()

    for name in ("questions.json", "questions_ext.json"):
        p = DATA / name
        if not p.exists():
            print(f"{name}: 不存在，跳过")
            continue
        r = process(p, args.apply)
        print(json.dumps(r, ensure_ascii=False))

    # 迁移后自检：topicId == primaryTopicId == topicIds[0]
    for name in ("questions.json", "questions_ext.json"):
        p = DATA / name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        bad = [q["id"] for q in data
               if q.get("topicIds") and q.get("primaryTopicId") != q["topicIds"][0]]
        bad2 = [q["id"] for q in data if q.get("topicId") != q.get("primaryTopicId")]
        print(f"{name}: 主键不一致 {len(bad)} 条, topicId!=primary {len(bad2)} 条")
        if bad[:5]:
            print("  样例:", bad[:5])
        if bad2[:5]:
            print("  样例:", bad2[:5])


if __name__ == "__main__":
    main()
