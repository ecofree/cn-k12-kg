"""把生成的记忆检验题写入 data/content/memory_drills.json 并应用。

**为什么要单独一个内容源**：项目约定所有题目改动都要落在 content/*.json 里，
这样任何时候重放都能得到一致结果（D24 修过「重放旧册文件会把修复冲掉」的坑）。

生成题的字段比普通题多几个：
- `drill_kind`: reverse（反向判断）/ fill（正向填空）
- `drill_basis`: 生成所依据的知识点条款原文
- `generated: true`：标记这是机器生成的题，复核时要优先看
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "tools"))
import gen_memory_questions as G  # noqa: E402
import gen_calc_questions as C  # noqa: E402

OUT = DATA / "content" / "memory_drills.json"


def build() -> list[dict]:
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    # 全部有条款的知识点（记忆型优先，理解型也生成「下列说法正确的是」）
    mem = [t for t in topics if G.has_clauses(t)]
    mem.sort(key=lambda t: (not G.is_memory_topic(t), t["id"]))
    out = []
    for t in mem:
        rev = G.build_reverse_question(t, mem)
        if rev:
            # id 必须符合 m7a.1.2.2-qN 形式（validate 的硬规则）
            out.append({
                "id": f"{t['id']}-q9",
                "topicId": t["id"],
                "type": rev["type"],
                "prompt": rev["prompt"],
                "choices": rev["choices"],
                "answer": rev["answer"],
                "explanation": rev["explanation"],
                "difficulty": "basic",
                "difficulty_basis": "generated_memory_drill",
                "drill_kind": "reverse",
                "drill_basis": G.clauses(t.get("description") or "")[0],
                "generated": True,
                "review_status": "ai-generated",
                "source": "self-generated-memory-drill",
            })
        # 只生成反向判断题（选择题）。
        # 正向填空题的答案是自然语言（「写出要点」），既无法机器验算，
        # accept 列表也穷举不了同义写法，判题必然脆——不入库。
    return out


def main() -> None:
    drills = build() + C.build()
    payload = {
        "volume": "专项自生成（记忆检验 + 计算练习）",
        "topics": {},
        "dependencies": [],
        "questions": drills,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    import collections
    print(json.dumps({
        "out": str(OUT.relative_to(ROOT)),
        "questions": len(drills),
        "by_kind": dict(collections.Counter(d["drill_kind"] for d in drills)),
        "topics": len({d["topicId"] for d in drills}),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
