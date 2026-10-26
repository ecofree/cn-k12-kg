"""把高考 tex 真题池转换成 map_by_lexicon 可用的候选池，并做答案自洽检查。

为什么单独一个工具：
    build/question-bank/gaokao_tex.jsonl 是 fetch_gaokao_tex.py 的直接产物，
    字段与 map_by_lexicon 期望的候选池差三处：
      1. 没有 source_id（只有 paper_id + index）
      2. 没有 license / stage 字段
      3. choices 用 key 而非 id

    转换时顺带做**答案自洽检查**——这是高考卷相对 CMM 的独特优势：
    同一份 tex 里答案和解析是分开的两个区块，可以互证。
    这比 CMM 的「从解析里正则抓『故选X』」证据强得多，
    因此高考题可以直接进入诊断集。

用法：
    python tools/prep_gaokao_pool.py --out inbox/candidates/gaokao_all.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "build" / "question-bank" / "gaokao_tex.jsonl"

# 答案里出现「故选X」时，与答案区块互相印证
PICK = re.compile(r"故选\s*\(?\s*([A-H])\s*\)?")
# 填空题答案里出现具体数值时，从解析里找同样的值
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def answer_selfcheck(row: dict) -> tuple[str, str]:
    """检查答案区块与解析区块是否互证。

    返回 (状态, 说明)：
        consistent —— 解析明确复述了同一个答案
        conflict   —— 解析说的和答案区块不一样 → 必须拒收
        no_signal  —— 解析里找不到可比的信号
    """
    ans = (row.get("answer") or "").strip()
    exp = (row.get("explanation") or "").strip()
    src = row.get("answer_source") or ""
    if not ans or not exp:
        return "no_signal", ""

    choices = row.get("choices") or []

    # 选择题：解析里「故选X」的字母必须与答案一致
    if choices:
        ans_letters = set(re.findall(r"[A-H]", ans))
        m = PICK.search(exp)
        if m:
            pick = m.group(1)
            if pick in ans_letters:
                # answer_block 是原卷答案，与解析互证 → 强证据
                # derived_letter 是从解析推导出来的，解析不能自证自己 → 弱证据
                if src == "derived_letter":
                    return "derived", f"由解析推导故{pick}"
                return "consistent", f"解析故{pick}"
            return "conflict", f"解析故{pick}，答案区块为{''.join(sorted(ans_letters)) or ans[:12]}"
        # 没有「故选」但解析里含选项原文，也算互证
        for c in choices:
            t = c["text"].strip()
            if len(t) >= 4 and t in exp and c["key"] in ans_letters:
                return "consistent", f"解析含选项{c['key']}原文"
        return "no_signal", ""

    # 填空题：解析里应复述答案的数值
    ans_nums = set(NUM.findall(ans))
    if not ans_nums:
        return "no_signal", ""
    exp_nums = set(NUM.findall(exp))
    # 解析里出现答案里的任一数值即视为复述
    if ans_nums & exp_nums:
        return "consistent", "解析复述了答案数值"
    return "no_signal", ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="inbox/candidates/gaokao_all.jsonl")
    ap.add_argument("--min-year", type=int, default=0,
                    help="只保留该年份及以后（0 = 全部）")
    ap.add_argument("--stats-only", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(ln) for ln in SRC.open(encoding="utf-8") if ln.strip()]
    if args.min_year:
        rows = [r for r in rows if int(r.get("year") or 0) >= args.min_year]

    out = []
    stats = collections.Counter()
    years = collections.Counter()
    for r in rows:
        sid = f"{r['paper_id']}-q{r['index']}"
        status, detail = answer_selfcheck(r)
        stats[status] += 1
        years[r.get("year", "?")] += 1
        choices = []
        for i, c in enumerate(r.get("choices") or []):
            choices.append({"id": f"o{i + 1}", "key": c.get("key", ""),
                            "text": c.get("text", "")})
        rec = {
            "source": "gaokao-tex",
            "source_id": sid,
            "stage": "senior",
            "grade_label": "高三",
            "type": "choice" if choices else ("numeric" if _is_numeric(r.get("answer", "")) else "short"),
            "prompt": r.get("prompt", ""),
            "choices": choices,
            "answer": r.get("answer", ""),
            "answer_source": r.get("answer_source", ""),
            "explanation": r.get("explanation", ""),
            "license": "CC-BY-SA-4.0",
            "meta": {
                "paper": r.get("paper_name", ""),
                "paper_id": r.get("paper_id", ""),
                "year": r.get("year", ""),
                "section": r.get("section", ""),
                "index": r.get("index", 0),
                "answer_check": status,
                "answer_check_detail": detail,
            },
        }
        out.append(rec)

    print(json.dumps({
        "total": len(out),
        "answer_selfcheck": dict(stats),
        "by_year": dict(sorted(years.items())),
        "out": args.out,
    }, ensure_ascii=False, indent=1))

    if not args.stats_only:
        p = ROOT / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as fh:
            for r in out:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"已写出 {args.out}：{len(out)} 条")


def _is_numeric(ans: str) -> bool:
    a = (ans or "").strip()
    return bool(a) and bool(re.fullmatch(r"-?\d+(?:\.\d+)?", a))


if __name__ == "__main__":
    main()
