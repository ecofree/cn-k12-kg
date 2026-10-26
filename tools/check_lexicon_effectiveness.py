"""词典自检：抓「must 是 AND 语义」造成的隐性失效。

背景：
    词典的三组规则语义是
        must —— **全部**都要命中（AND）
        any  —— **至少**命中一项（OR）
        not  —— 一项都不能命中
    所以并列的候选词必须写在**单个正则的 alternation** 里：
        错：must=['导数', '单调区间']     ← 要求两者同时出现
        对：must=['导数|单调区间']

    写错的后果很隐蔽：规则看着合理，但几乎匹配不到任何题，
    该知识点就一道题也没有——而且不会报错。
    实测因此有 6 个高中点 + 3 个初中点被封死。

本工具做三件事：
    1. 找出 must 里含多个元素、且不是 alternation 的规则（AND 陷阱）
    2. 用真实题池测每个知识点实际能命中多少题
    3. 报告「有题但匹配不上」和「完全没题」两类缺口

用法：
    python tools/check_lexicon_effectiveness.py
    python tools/check_lexicon_effectiveness.py --pool senior
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "tools"))

from map_by_lexicon import normalize  # noqa: E402

POOL_PATHS = {
    "senior": "inbox/candidates/gaokao_all.jsonl",
    "junior": "inbox/candidates/cmm_junior.jsonl",
    "primary": "inbox/candidates/cmm_primary.jsonl",
}


def is_alternation(pat: str) -> bool:
    """这个正则本身是不是 alternation（含 |）——即已经表达了「任一」。"""
    return "|" in pat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="senior", choices=list(POOL_PATHS))
    ap.add_argument("--show", type=int, default=0, help="打印 N 个命中题的样例")
    args = ap.parse_args()

    lexicon = json.loads((DATA / "kc_lexicon.json").read_text(encoding="utf-8"))
    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    topic_list = list(topics.values())
    pool_path = ROOT / POOL_PATHS[args.pool]
    if not pool_path.exists():
        print(f"题池不存在：{pool_path}")
        return
    rows = [json.loads(ln) for ln in pool_path.open(encoding="utf-8") if ln.strip()]

    # 预编译，重复用
    comp = {}
    for tid, v in lexicon.items():
        comp[tid] = (
            [re.compile(p) for p in (v.get("must") or [])],
            [re.compile(p) for p in (v.get("any") or [])],
            [re.compile(p) for p in (v.get("not") or [])],
        )

    texts = []
    for r in rows:
        t = r.get("prompt", "") + " " + " ".join(
            c.get("text", "") for c in (r.get("choices") or []))
        texts.append((r, normalize(t)))

    print(f"题池 {args.pool}：{len(rows)} 道，词典 {len(lexicon)} 条规则\n")

    # ---- 1. AND 陷阱 ----
    traps = []
    for tid, v in lexicon.items():
        must = v.get("must") or []
        if len(must) >= 2 and not all(is_alternation(p) for p in must):
            traps.append((tid, must))
    print("=" * 72)
    print(f"一、must 的 AND 陷阱（并列词应写在单条正则的 alternation 里）：{len(traps)} 条")
    print("=" * 72)
    for tid, must in traps:
        print(f"  {tid:<14}{topics.get(tid,{}).get('name',''):<22}must={must}")

    # ---- 2. 实际命中数 ----
    hits = collections.Counter()
    sample = collections.defaultdict(list)
    for r, txt in texts:
        for tid, (must, anyp, notp) in comp.items():
            if any(p.search(txt) for p in notp):
                continue
            if must and not all(p.search(txt) for p in must):
                continue
            if anyp and not any(p.search(txt) for p in anyp):
                continue
            hits[tid] += 1
            if len(sample[tid]) < 3:
                sample[tid].append(r.get("prompt", "")[:56])

    # ---- 3. 缺口 ----
    zero = [tid for tid in lexicon if hits[tid] == 0]
    stage = args.pool
    stage_topics = [t for t in topic_list if
                    (stage == "senior" and t.get("grade", 0) >= 10) or
                    (stage == "junior" and 7 <= t.get("grade", 0) <= 9) or
                    (stage == "primary" and t.get("grade", 0) <= 6)]
    stage_ids = {t["id"] for t in stage_topics}
    zero_stage = [t for t in zero if t in stage_ids]

    # 区分「规则失配」与「考试确实不考」：
    #   只用知识点名里**最长那个连续名词短语**（拆词后的最长片段）检查，
    #   不用整名（整名带「的」字，真题里几乎不会原样出现）。
    #   也不能拆成多个词分别判断——「平面与平面平行」拆成「平面」「平行」后，
    #   「平面」会命中 1546 道完全无关的题（真实完整术语命中数是 0）。
    #   最长片段都不出现，说明这个知识点在整份卷里根本没被考到：
    #   「平面与平面平行」「点斜式」「正弦函数」这些，
    #   高考卷里确实不单独考（它们只作为其它题的条件出现）。
    name_terms = {}
    for tid in zero_stage:
        name = topics.get(tid, {}).get("name", "")
        parts = [p for p in re.split(r"[、（）()，,的与及\s]+", name) if len(p) >= 2]
        name_terms[tid] = sorted(parts, key=len, reverse=True)[:1]

    truly_absent = []
    rule_mismatch = []
    for tid in zero_stage:
        terms = name_terms[tid]
        n = sum(1 for _q, txt in texts if terms and any(t in txt for t in terms))
        if n == 0:
            truly_absent.append(tid)
        else:
            rule_mismatch.append((tid, n))

    print()
    print("=" * 72)
    print(f"二、{stage} 段实际命中情况")
    print("=" * 72)
    print(f"知识点 {len(stage_ids)} 个，命中 0 道的 {len(zero_stage)} 个")
    print(f"\n其中「考试确实不考」（知识点术语在整份卷里完全不出现）：{len(truly_absent)} 个")
    for tid in truly_absent:
        print(f"  {tid:<14}{topics.get(tid,{}).get('name','')}")
    if rule_mismatch:
        print(f"\n其中「规则失配」（题里有相关术语但规则匹配不上）：{len(rule_mismatch)} 个")
        for tid, n in rule_mismatch:
            print(f"  {tid:<14}{n:>5} 道含相关术语  {topics.get(tid,{}).get('name','')}")

    low = [(tid, hits[tid]) for tid in sorted(stage_ids)
           if 0 < hits[tid] <= 3]
    print(f"\n命中 1~3 道的（覆盖太薄）{len(low)} 个：")
    for tid, n in low[:15]:
        print(f"  {tid:<14}{n} 道  {topics.get(tid,{}).get('name','')}")

    print()
    print("=" * 72)
    print("三、命中量 TOP10（集中度检查）")
    print("=" * 72)
    total_hits = sum(hits[t] for t in stage_ids)
    for tid, n in hits.most_common(10):
        if tid not in stage_ids:
            continue
        print(f"  {n:>5} ({n/max(total_hits,1)*100:>4.1f}%)  {tid:<14}"
              f"{topics.get(tid,{}).get('name','')}")
    top10 = sum(n for tid, n in hits.most_common(10) if tid in stage_ids)
    print(f"\n前 10 个点占该段命中量的 {top10/max(total_hits,1)*100:.0f}%")

    if args.show:
        print("\n样例：")
        for tid, _ in hits.most_common(args.show):
            if tid not in stage_ids:
                continue
            print(f"  {topics.get(tid,{}).get('name','')}：")
            for s in sample[tid]:
                print(f"     {s}")

    if traps or zero_stage:
        print("\n提示：must 的 AND 陷阱是隐性失效——规则看着合理但匹配不到题，"
              "不会报错。修法是把并列词合并进单条正则的 alternation。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
