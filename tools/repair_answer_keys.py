"""重建选择题答案键（事故修复工具）。

背景：`tools/balance_options.py` 早期的旋转方向写反，把正确项转到了
`(2*old - want) mod n`，而答案键仍记在 `want`。凡是它旋转过的题，答案都是错的。
详见 docs/decisions.md D19。

为什么能修：单次旋转满足 `c ≡ ±a (mod 2)`，因此
- 4 选项：正确项只可能落在当前位置 `a` 或 `a+2`（二元判断）
- 3 选项：正确项是 `a / a+1 / a+2` 三选一

本工具按约束生成候选，用解析文本给候选打分，输出
`build/answer_key_repair.json` 供人工确认；**不自动改数据**，
因为"解析支持哪个选项"是语义判断，机器只能给线索。

    python tools/repair_answer_keys.py            # 生成待确认清单
    python tools/repair_answer_keys.py --stats    # 只看规模与自动判定率
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BUILD = ROOT / "build"

NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
PUNCT = str.maketrans("", "", " 　，。；：、（）()【】[]“”\"'‘’·")


def norm(text) -> str:
    return str(text or "").translate(PUNCT).lower()


def numbers(text) -> list:
    return NUM_RE.findall(str(text or "").replace("−", "-"))


def score_option(option_text: str, explanation: str) -> tuple:
    """给候选选项打分，返回 (分数, 依据列表)。

    只做字面证据：选项文字被解析引用、选项里的数值在解析里出现。
    不做同义推断——宁可交给人工，也不猜。
    """
    opt, exp = norm(option_text), norm(explanation)
    score, why = 0, []

    if len(opt) >= 2 and opt in exp:
        pos = exp.find(opt)
        window = exp[max(0, pos - 10): pos + len(opt) + 10]
        if re.search(r"(错|不对|不成立|不一定|不符合|不是|无法|不能)", window):
            score -= 8
            why.append("选项原文出现在解析中，但上下文是否定")
        else:
            score += 6
            why.append("选项原文出现在解析中")

    opt_nums = numbers(option_text)
    exp_nums = numbers(explanation)
    if opt_nums:
        hit = [n for n in opt_nums if n in exp_nums]
        if len(hit) == len(opt_nums):
            score += 6 if len(exp_nums) <= 2 else 3
            why.append(f"选项数值 {opt_nums} 全部出现在解析中")
        elif hit:
            score += 2
            why.append(f"选项数值部分命中 {hit}")
    return score, why


def candidates_for(n: int, a: int) -> list:
    if n == 4:
        return sorted({a, (a + 2) % 4})
    if n == 3:
        return sorted({a, (a + 1) % 3, (a + 2) % 3})
    return list(range(n))


def analyse(questions) -> dict:
    rows = []
    for q in questions:
        if q.get("type") != "choice" or not q.get("choices"):
            continue
        keys = [c["key"] for c in q["choices"]]
        if q.get("answer") not in keys:
            rows.append({"question_id": q["id"], "status": "answer_not_in_options"})
            continue
        a = keys.index(q["answer"])
        n = len(keys)
        cands = []
        for idx in candidates_for(n, a):
            text = q["choices"][idx]["text"]
            s, why = score_option(text, q.get("explanation") or "")
            cands.append({"index": idx, "key": keys[idx], "text": text,
                          "score": s, "why": why, "is_current": idx == a})
        ranked = sorted(cands, key=lambda c: (-c["score"], c["is_current"]))
        best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
        margin = best["score"] - (second["score"] if second else -99)
        rows.append({
            "question_id": q["id"],
            "topic_id": q["topicId"],
            "prompt": q["prompt"],
            "explanation": q.get("explanation", ""),
            "current_answer": q["answer"],
            "current_index": a,
            "options": [{"index": i, "key": c["key"], "text": c["text"]}
                        for i, c in enumerate(q["choices"])],
            "candidates": cands,
            "suggest_index": best["index"],
            "suggest_key": best["key"],
            "suggest_is_change": best["index"] != a,
            "margin": margin,
            "auto": margin >= 4 and best["score"] >= 4,
        })
    return {"total": sum(1 for q in questions if q.get("type") == "choice"),
            "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="只输出统计")
    ap.add_argument("--out", default="build/answer_key_repair.json")
    args = ap.parse_args()

    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    rep = analyse(questions)
    rows = rep["rows"]
    auto = [r for r in rows if r.get("auto")]
    change = [r for r in auto if r["suggest_is_change"]]
    manual = [r for r in rows if not r.get("auto")]

    print(f"选择题 {rep['total']}")
    print(f"  可自动判定（证据分差≥4）{len(auto)}，其中建议改答案 {len(change)}")
    print(f"  需人工确认                {len(manual)}")
    print(f"  建议改答案合计            {sum(1 for r in rows if r.get('suggest_is_change'))}")
    if args.stats:
        return 0

    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "note": "由 tools/repair_answer_keys.py 生成的答案键重建清单，人工确认后再写回",
        "total": rep["total"], "auto": len(auto),
        "need_manual": len(manual), "suggest_change": sum(1 for r in rows if r.get("suggest_is_change")),
        "rows": rows,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n已写出 {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
