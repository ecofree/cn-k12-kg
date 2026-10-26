"""给知识点标注 exam_scope（是否被中高考单独考查），并留下可复核的证据。

**为什么要这个字段**：项目的知识点体系覆盖全部课标内容，但中高考并不是每个
知识点都单独命题。此前「自动 exam_scope」因判据不可靠被回退（用拆词检索，
「平面」会命中 1546 道无关题），只人工标了 5 个。这次用**完整术语**检索重建。

**判据（与 tools/check_lexicon_effectiveness.py 同一套，避免两处口径不一）**：
    用知识点名里最长的名词短语（不拆词）在对应学段的真题池里检索。
    0 命中 → textbook_only（只在课本出现，真题不单独考）
    >0 命中 → examined（真题考到）

**为什么要留证据**：这个结论直接影响「缺口」的口径——标错会把真缺口藏起来，
或者把不考的點报成缺口。所以每个标记都带检索词、池名、池大小和命中数，
任何时候都能重跑复核。

**已发现并修正的错误**：mh2.10.1.3 古典概型、mh5.7.4.2 超几何分布曾被标为
textbook_only，但按题型特征重新映射后分别有 389 道和 28 道高考题——
原来的判断是「术语名 0 命中」误当成「真题不考」，本工具会按实际有题状态修正。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 学段 → 真题池。池的大小一起记下来，让「0 命中」有意义（在 10 道题里 0 命中
# 和在 16000 道题里 0 命中，证据强度完全不同）。
POOLS = {
    "primary": [("CMM-Math 小学", "inbox/candidates/cmm_primary.jsonl")],
    "junior": [("CMM-Math 初中", "inbox/candidates/cmm_junior.jsonl"),
               ("上海中考", "inbox/candidates/smse_junior.jsonl")],
    "senior": [("高考真题全量", "inbox/candidates/gaokao_all.jsonl"),
               ("CMM-Math 高中", "inbox/candidates/cmm_senior.jsonl")],
}

# 精确复核模式：对「术语命中 > 0 但没映射入库」的点，用**题型特征**再查一次。
#
# 为什么需要：术语命中会骗人。例如「有理数的乘法」用「乘法」一词在初中池命中
# 75 道，但精确模式（有理数 + 乘号/积）只有 6 道且全是「差倒数」「相反数」这类
# 假阳性——CMM 初中池实际上没有有理数乘法题。宽泛词命中的是别的内容。
#
# 只有精确模式也命中，才算「真题考到但题源没供上」（source_gap）；
# 精确模式 0 命中 → 与 textbook_only 同论（当前题源内无供给）。
PRECISE = {
    "m7a.2.2.1": (["有理数", r"\(-\d", r"负数.{0,6}正数"], [r"×", r"\\times", r"积的"]),
    "m8a.14.2": ([r"全等|≌|cong"], [r"SSS|SAS|ASA|AAS|HL|判定.{0,6}全等"]),
    "m9b.31.2.2": ([r"相似|∽|sim"], [r"判定.{0,8}相似|两角.{0,10}对应相等|两边.{0,10}成比例"]),
    "m9a.27.3": ([r"反比例"], [r"实际|应用|压强|电流|路程"]),
    # 向量减法：必须出现「减法」这个**主题词**。
    # 曾用 `\vec{..}-\vec{..}` 做模式，结果命中 8 道全是假阳性——
    # 「2a-b」「(a+c)∥b」是数乘/加法/平行的混合运算，不是减法知识点。
    "mh2.6.2.2": ([r"vec|overrightarrow|向量"],
                  [r"向量的减法", r"向量.{0,4}减法", r"减去.{0,8}向量",
                   r"相反向量"]),
}


def stage_of(topic: dict) -> str:
    g = topic.get("grade", 0)
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def longest_terms(name: str) -> list[str]:
    """取知识点名里最长的名词短语作为检索词。

    不能拆词：「平面与平面平行」拆成「平面」「平行」后，「平面」会命中
    1546 道完全无关的题，把「确实不考」误判成「有题」。
    """
    parts = [p for p in re.split(r"[、（）()，,的与及\s]+", name) if len(p) >= 2]
    ranked = sorted(parts, key=len, reverse=True)
    return list(dict.fromkeys(ranked[:1] + [name]))


def load_pool_texts(rel: str) -> list[str]:
    path = ROOT / rel
    if not path.exists():
        return []
    texts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        blob = row.get("prompt", "") + " " + " ".join(
            c.get("text", "") for c in (row.get("choices") or []))
        texts.append(re.sub(r"\s+", " ", blob))
    return texts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 topics.json")
    ap.add_argument("--show", type=int, default=0, help="抽样打印 N 条判定")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))

    # 已有外部真题的知识点 —— 这就是 examined 最硬的证据
    has_ext: dict[str, set[str]] = collections.defaultdict(set)
    for q in ext:
        for t in (q.get("topicIds") or [q.get("topicId")]):
            if t:
                has_ext[t].add(q.get("source", "?"))

    # 预加载各学段题池文本
    pool_cache: dict[str, list[tuple[str, list[str]]]] = {}
    for stage, pools in POOLS.items():
        pool_cache[stage] = [(name, load_pool_texts(rel)) for name, rel in pools]

    changed = 0
    dist = collections.Counter()
    details = []
    for t in topics:
        tid = t["id"]
        stage = stage_of(t)
        sources = has_ext.get(tid, set())
        if sources:
            scope, why = "examined", f"已有外部真题：{', '.join(sorted(sources))}"
            evidence = {"sources": sorted(sources)}
        else:
            # 无外部题：用完整术语在各真题池里检索
            terms = longest_terms(t["name"])
            per_pool = {}
            total_hits = 0
            for pool_name, texts in pool_cache[stage]:
                if not texts:
                    per_pool[pool_name] = {"size": 0, "hits": None}
                    continue
                n = sum(1 for txt in texts if any(term in txt for term in terms))
                per_pool[pool_name] = {"size": len(texts), "hits": n}
                total_hits += n
            if total_hits > 0:
                # 术语命中了，但题没映射入库。用题型特征再精确查一次：
                # 宽泛词（「乘法」「向量」）会命中大量无关题，不能据此判定「真题考到」。
                if tid in PRECISE:
                    must, anyp = PRECISE[tid]
                    precise = 0
                    for _pool_name, texts in pool_cache[stage]:
                        precise += sum(
                            1 for txt in texts
                            if all(re.search(p, txt) for p in must)
                            and any(re.search(p, txt) for p in anyp))
                    evidence["precise_hits"] = precise
                    if precise > 0:
                        scope = "source_gap"
                        why = (f"精确题型特征命中 {precise} 道但未映射入库"
                               f"（宽泛术语命中 {total_hits} 道，多为假阳性）")
                    else:
                        scope = "textbook_only"
                        why = (f"精确题型特征 0 命中——宽泛术语那 {total_hits} 道"
                               f"是「乘法/向量」等通用词造成的假阳性，当前题源无供给")
                else:
                    scope = "source_gap"
                    why = f"真题池命中 {total_hits} 道但未映射入库"
            else:
                scope = "textbook_only"
                why = "完整术语在所有真题池 0 命中"
            evidence = {"method": "full_term_search", "terms": terms,
                        "pools": per_pool, "total_hits": total_hits,
                        "checked_at": date.today().isoformat()}

        old = t.get("exam_scope")
        if old != scope:
            changed += 1
        t["exam_scope"] = scope
        t["exam_scope_evidence"] = {"reason": why, **evidence}
        dist[scope] += 1
        details.append((tid, t["name"], stage, scope, why))

    print(f"exam_scope 分布：{dict(dist)}")
    print(f"与旧值不同：{changed} 个")

    if args.show:
        import random
        random.seed(1)
        for tid, name, stage, scope, why in random.sample(details, min(args.show, len(details))):
            print(f"  {tid:<14}{name[:20]:<22}[{stage}] {scope:<14}{why}")

    # 重点：被本工具改掉的点（原标记与实测矛盾）
    fixed = [(tid, name, why) for tid, name, _s, scope, why in details
             if tid in {"mh2.10.1.3", "mh5.7.4.2"}]
    if fixed:
        print("\n修正原先标错的点：")
        for tid, name, why in fixed:
            print(f"  {tid} {name} → {why}")

    if args.apply:
        (DATA / "topics.json").write_text(
            json.dumps(topics, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n已写回 topics.json（{len(topics)} 个知识点）")
    else:
        print("\n（dry-run，加 --apply 写回）")


if __name__ == "__main__":
    main()
