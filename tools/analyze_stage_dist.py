#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识点阶段分布分析：小学 / 初中 / 高中，按年级、册次、领域、主题、题量覆盖。"""
import json
import os
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(ROOT, "data")

topics = json.load(open(os.path.join(D, "topics.json"), encoding="utf-8"))
questions = json.load(open(os.path.join(D, "questions.json"), encoding="utf-8"))
ext = json.load(open(os.path.join(D, "questions_ext.json"), encoding="utf-8"))

for q in ext:
    q["_ext"] = True
    q.setdefault("topicIds", [])
for q in questions:
    q.setdefault("topicIds", [])

allq = questions + ext


def stage(t):
    g = t.get("grade")
    if g is None:
        return "未标注"
    if g <= 6:
        return "小学"
    if g <= 9:
        return "初中"
    return "高中"


# ---------- 1. 阶段总览 ----------
by_stage = defaultdict(list)
for t in topics:
    by_stage[stage(t)].append(t)

print("=" * 72)
print("一、三学段知识点总览")
print("=" * 72)
print(f"{'学段':<8}{'知识点':>8}{'占比':>9}{'题量':>8}{'题/点':>9}{'≤2题的点':>11}")
total_t, total_q = len(topics), 0
stage_qcount = defaultdict(int)
for s in ["小学", "初中", "高中", "未标注"]:
    ts = by_stage.get(s, [])
    if not ts:
        continue
    ids = {t["id"] for t in ts}
    qs = [q for q in allq if ids & set(q.get("topicIds") or [q.get("topicId")])]
    low = sum(1 for t in ts if sum(1 for q in qs if t["id"] in q.get("topicIds") or [q.get("topicId")]) <= 2)
    total_q += len(qs)
    print(f"{s:<8}{len(ts):>8}{len(ts)/total_t*100:>8.1f}%{len(qs):>8}{len(qs)/max(len(ts),1):>9.2f}{low:>11}")

# ---------- 2. 年级分布 ----------
print()
print("=" * 72)
print("二、年级分布（知识点 / 题量 / 题点比）")
print("=" * 72)
by_grade = defaultdict(list)
for t in topics:
    by_grade[t.get("grade")].append(t)
print(f"{'年级':<8}{'知识点':>7}{'占比':>8}{'题量':>8}{'题/点':>9}   领域分布")
for g in sorted(by_grade):
    ts = by_grade[g]
    ids = {t["id"] for t in ts}
    qs = [q for q in allq if ids & set(q.get("topicIds") or [q.get("topicId")])]
    dom = Counter(t.get("domain", "?") for t in ts)
    domstr = " ".join(f"{k}:{v}" for k, v in dom.most_common())
    print(f"{g:<8}{len(ts):>7}{len(ts)/total_t*100:>7.1f}%{len(qs):>8}{len(qs)/max(len(ts),1):>9.2f}   {domstr}")

# ---------- 3. 册次分布 ----------
print()
print("=" * 72)
print("三、册次分布（23 册）")
print("=" * 72)
by_vol = defaultdict(list)
for t in topics:
    by_vol[t.get("volume", "?")].append(t)
VOL_ORDER = ["一上", "一下", "二上", "二下", "三上", "三下", "四上", "四下", "五上", "五下",
             "六上", "六下", "七上", "七下", "八上", "八下", "九上", "九下",
             "高一上", "高一下", "高二上", "高二下", "高三上"]
def vkey(v):
    try:
        return (VOL_ORDER.index(v), v)
    except ValueError:
        return (99, v)
print(f"{'册次':<10}{'学段':<6}{'知识点':>7}{'章':>5}{'节':>6}{'题量':>8}{'题/点':>8}")
for v in sorted(by_vol, key=vkey):
    ts = by_vol[v]
    ids = {t["id"] for t in ts}
    qs = [q for q in allq if ids & set(q.get("topicIds") or [q.get("topicId")])]
    ch = len({t.get("chapter") for t in ts})
    sec = len({t.get("section") for t in ts})
    print(f"{v:<10}{stage(ts[0]):<6}{len(ts):>7}{ch:>5}{sec:>6}{len(qs):>8}{len(qs)/max(len(ts),1):>8.2f}")

# ---------- 4. 领域分布 ----------
print()
print("=" * 72)
print("四、四大领域 × 学段交叉分布")
print("=" * 72)
domains = ["数与代数", "图形与几何", "统计与概率", "综合与实践"]
print(f"{'领域':<12}{'学段':<8}{'知识点':>7}{'题量':>8}{'题/点':>8}")
for d in domains:
    for s in ["小学", "初中", "高中"]:
        ts = [t for t in by_stage.get(s, []) if t.get("domain") == d]
        if not ts:
            continue
        ids = {t["id"] for t in ts}
        qs = [q for q in allq if ids & set(q.get("topicIds") or [q.get("topicId")])]
        print(f"{d:<12}{s:<8}{len(ts):>7}{len(qs):>8}{len(qs)/len(ts):>8.2f}")
    print("-" * 56)

# ---------- 5. 主题分布 ----------
print()
print("=" * 72)
print("五、主题（theme）分布")
print("=" * 72)
by_theme = defaultdict(list)
for t in topics:
    by_theme[t.get("theme", "?")].append(t)
print(f"{'主题':<14}{'知识点':>7}{'占比':>8}{'题量':>8}{'题/点':>8}   各学段")
for th, ts in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
    ids = {t["id"] for t in ts}
    qs = [q for q in allq if ids & set(q.get("topicIds") or [q.get("topicId")])]
    sd = Counter(stage(t) for t in ts)
    sdstr = " ".join(f"{k}{v}" for k, v in sd.most_common())
    print(f"{th:<14}{len(ts):>7}{len(ts)/total_t*100:>7.1f}%{len(qs):>8}{len(qs)/len(ts):>8.2f}   {sdstr}")

# ---------- 6. 题型分布（按学段） ----------
print()
print("=" * 72)
print("六、题型分布（按学段）")
print("=" * 72)
qtype_of = {}
for s in ["小学", "初中", "高中"]:
    ids = {t["id"] for t in by_stage.get(s, [])}
    qs = [q for q in allq if ids & set(q.get("topicIds") or [q.get("topicId")])]
    c = Counter(q.get("type", q.get("questionType", "?")) for q in qs)
    tot = sum(c.values())
    print(f"{s}: " + "  ".join(f"{k}={v}({v/max(tot,1)*100:.0f}%)" for k, v in c.most_common()))

# ---------- 7. 外部真题按学段 ----------
print()
print("=" * 72)
print("七、外部真题来源分布（按学段）")
print("=" * 72)
src = defaultdict(Counter)
for q in ext:
    ids = set(q.get("topicIds") or [q.get("topicId")])
    s = "未映射"
    for t in topics:
        if t["id"] in ids:
            s = stage(t)
            break
    src[q.get("source", "?")][s] += 1
for k, v in src.items():
    print(f"{k:<28} 合计 {sum(v.values()):>4}   " + " ".join(f"{a}:{b}" for a, b in v.most_common()))

# ---------- 8. 课标覆盖 ----------
print()
print("=" * 72)
print("八、课标条目分布")
print("=" * 72)
std = json.load(open(os.path.join(D, "standards.json"), encoding="utf-8"))
if isinstance(std, dict):
    std = std.get("standards", std.get("items", list(std.values())[0]))
slist = std if isinstance(std, list) else list(std.values())
sc = Counter()
for t in topics:
    for s in t.get("standards", []):
        sc[s.get("id", "?").split(".")[-1] if "." in s.get("id", "") else s.get("id", "?")] += 1
print(f"课标条目总数: {len(slist)}")
print("被引用最多的课标编号 (前15):")
for k, v in sc.most_common(15):
    print(f"  {k:<16}{v:>5}")
print()
print("知识点挂课标覆盖率:")
covered = sum(1 for t in topics if t.get("standards"))
print(f"  已挂课标: {covered}/{len(topics)} ({covered/len(topics)*100:.1f}%)")
nocov = [t for t in topics if not t.get("standards")]
if nocov:
    print("  未挂课标示例:")
    for t in nocov[:10]:
        print(f"    {t['id']} {t['name']}")

# ---------- 9. 依赖边跨阶段 ----------
print()
print("=" * 72)
print("九、先修依赖边的跨阶段分布")
print("=" * 72)
deps = json.load(open(os.path.join(D, "dependencies.json"), encoding="utf-8"))
if isinstance(deps, dict):
    deps = deps.get("edges", deps.get("dependencies", list(deps.values())[0]))
dlist = deps if isinstance(deps, list) else list(deps.values())
tmap = {t["id"]: t for t in topics}
cross = Counter()
for e in dlist:
    a = e.get("prerequisiteId") or e.get("from") or e.get("source")
    b = e.get("topicId") or e.get("to") or e.get("target")
    ta, tb = tmap.get(a), tmap.get(b)
    if not ta or not tb:
        cross[("未知", "?")] += 1
        continue
    cross[(stage(ta), stage(tb))] += 1
for (a, b), v in sorted(cross.items(), key=lambda kv: -kv[1]):
    print(f"  {a:<6} -> {b:<6} {v:>5} 条")
