"""补齐跨学段先修承接链。

历史背景（2026-10-25 复核）：
    建链前 570 条先修边里跨学段的只有 11 条（小学→初中 2 条，初中→高中 9 条）。
    图本身连通（0 孤立点），但「小学数学没打好导致初中数学学不好」
    这个最高频的真实场景，在图上几乎没有路径可走——
    跨学段根因追溯基本失效。

    本工具执行后跨学段边 11 → 27 条（小学→初中 14、初中→高中 13），
    stage_policy.json 的 cross_stage.required_links 全部落地。

    承接链的来源是 data/stage_policy.json 的 cross_stage.required_links，
    那里记的是**教学事实**：哪块小学内容是某块初中内容的前提。
    这个工具只做一件事：把这些教学事实落成图上的边，并保持幂等。

    注意：required_links 里的 id 曾与 topics.json 对不上（m3b.2.1 实际是
    「口算除法」而非「分数的意义」），2026-10-25 已修正；匹配逻辑的
    守门测试在 tests/test_cross_stage_links.py。

强度标法：
    hard —— 不懂它就完全没法学后面的（数系扩充、运算基础）
    soft —— 有关联但不强制（立体经验、表示方法）

用法：
    python tools/add_cross_stage_links.py --dry-run
    python tools/add_cross_stage_links.py --apply
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

# 承接链定义。from/to 用知识点 id，强度与理由写在这里。
# 每一组都是「教学内容上的前提关系」，不是关键词猜出来的。
LINKS = [
    # ---------- 小学 → 初中 ----------
    # 数系第一次扩充：小数、分数 → 有理数
    {"prerequisiteId": "m5b.2.1", "topicId": "m7a.1.2.1", "strength": "hard",
     "reason": "小学已学分数的意义与分子分母，初中才能把它扩充成有理数并按正负分类。"},
    {"prerequisiteId": "m4b.4.1", "topicId": "m7a.1.2.1", "strength": "hard",
     "reason": "小数的认识是理解有理数小数部分的前提。"},
    # 运算律 → 有理数运算
    {"prerequisiteId": "m4b.2.1", "topicId": "m7a.2.1.1", "strength": "hard",
     "reason": "小学的运算律是初中有理数加减法简算的直接依据。"},
    {"prerequisiteId": "m4b.2.1", "topicId": "m7a.2.2.1", "strength": "hard",
     "reason": "小学的运算律是初中有理数乘法简算的直接依据。"},
    # 大数表示 → 科学记数法
    {"prerequisiteId": "m2b.3.1", "topicId": "m7a.2.3.2", "strength": "hard",
     "reason": "千以内数的读写与组成是科学记数法的前置。"},
    # 小数运算 → 实数运算
    {"prerequisiteId": "m3b.6.3", "topicId": "m7b.8.3", "strength": "hard",
     "reason": "小学的小数加减是初中实数运算规则的前身。"},
    # 因数倍数 → 整式与因式分解
    {"prerequisiteId": "m5b.1.1", "topicId": "m7a.4.1", "strength": "hard",
     "reason": "因数概念是理解多项式各项因子的基础。"},
    {"prerequisiteId": "m5b.1.1", "topicId": "m8a.16.2", "strength": "soft",
     "reason": "因数的分解思路会迁移到整式乘法的因式分解。"},
    # 立体经验 → 立体图形认识
    {"prerequisiteId": "m5b.4.1", "topicId": "m7a.6.1.1", "strength": "soft",
     "reason": "小学的长方体正方体经验帮助初中建立空间观念。"},
    # 数量关系 → 一元一次方程
    {"prerequisiteId": "m1b.6.3", "topicId": "m7a.5.1.2", "strength": "soft",
     "reason": "小学连续两问的问题意识，是初中列方程解应用题的基础。"},
    # 图形 → 平面直角坐标系
    {"prerequisiteId": "m3b.3.1", "topicId": "m7b.9.1.1", "strength": "soft",
     "reason": "小学的多边形与数对经验，帮助理解坐标系的点与位置。"},

    # ---------- 初中 → 高中 ----------
    # 比与比例 → 反比例函数
    {"prerequisiteId": "m6b.1.1", "topicId": "m9a.27.1", "strength": "hard",
     "reason": "小学已建立的比与比例关系，是初中反比例函数概念的来源。"},
    # 整式运算 → 整式与分式
    {"prerequisiteId": "m8a.16.2", "topicId": "m8a.18.1", "strength": "hard",
     "reason": "整式乘法是分式约分与通分运算的基础。"},
    # 一次函数 → 斜率与倾斜角
    {"prerequisiteId": "m8b.23.2", "topicId": "mh3.2.1.1", "strength": "hard",
     "reason": "一次函数的增减性认识，是理解斜率与倾斜角的前提。"},
    # 相似三角形 → 圆锥曲线与解三角形
    {"prerequisiteId": "m9b.31.2.3", "topicId": "mh2.6.4.3", "strength": "soft",
     "reason": "相似三角形性质的正弦定理、余弦定理可由初中相似推出。"},
    # 圆 → 圆锥曲线
    {"prerequisiteId": "m9a.29.2", "topicId": "mh3.3.1.1", "strength": "soft",
     "reason": "圆的性质与离心率概念相通，椭圆的定义建立在圆的基础上。"},
    # 数与式运算 → 指数对数运算
    {"prerequisiteId": "m7a.2.2.1", "topicId": "mh1.4.1.2", "strength": "hard",
     "reason": "有理数乘方运算是指数幂运算的前置。"},
    # 统计 → 统计案例
    {"prerequisiteId": "m8b.24.1", "topicId": "mh2.9.3", "strength": "soft",
     "reason": "初中平均数、中位数的经验，用于高中统计案例的分析。"},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只报告）")
    args = ap.parse_args()

    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    deps = json.loads((DATA / "dependencies.json").read_text(encoding="utf-8"))

    def stage(tid: str) -> str:
        g = topics[tid].get("grade")
        return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")

    existing = {(e["prerequisiteId"], e["topicId"]) for e in deps}
    added, skipped, invalid = [], [], []

    for link in LINKS:
        pre, tgt = link["prerequisiteId"], link["topicId"]
        if pre not in topics or tgt not in topics:
            invalid.append((pre, tgt, "知识点不存在"))
            continue
        if (pre, tgt) in existing:
            skipped.append((pre, tgt))
            continue
        # 同段内不建跨学段边（这不是本工具的职责）
        if stage(pre) == stage(tgt):
            invalid.append((pre, tgt, "不是跨学段"))
            continue
        added.append(link)

    print(f"待新增 {len(added)} 条跨学段边，已存在 {len(skipped)} 条，无效 {len(invalid)} 条")
    for link in added:
        p = topics[link["prerequisiteId"]]
        t = topics[link["topicId"]]
        print(f"  + {p['volume']}「{p['name']}」 --{link['strength']}--> "
              f"{t['volume']}「{t['name']}」")
    for pre, tgt, why in invalid:
        print(f"  ! {pre} → {tgt}：{why}")

    if not added:
        print("无需改动。")
        return

    if not args.apply:
        print("\n（试运行，未写入。加 --apply 生效）")
        return

    out = deps + [{k: v for k, v in link.items()} for link in added]
    (DATA / "dependencies.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 复核：跨学段边数与阶段分布
    cross = Counter()
    for e in out:
        a, b = e["prerequisiteId"], e["topicId"]
        if a in topics and b in topics and stage(a) != stage(b):
            cross[(stage(a), stage(b))] += 1
    print(f"\n已写入：边总数 {len(out)}")
    for (a, b), n in sorted(cross.items()):
        print(f"  {a} → {b}: {n} 条")


if __name__ == "__main__":
    main()
