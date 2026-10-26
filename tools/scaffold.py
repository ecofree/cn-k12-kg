"""按 outline.json 补齐知识点骨架（幂等），并支持把目录修正同步到骨架。

框架先行：目录是唯一真源。脚本做两件事：
  1. 目录里有、topics.json 里没有的叶子 -> 追加 skeleton 知识点；
  2. --sync：目录改了章节名/单元名 -> 覆盖已有 skeleton 点的结构字段（不动 draft 内容）。

    python tools/scaffold.py --dry-run
    python tools/scaffold.py
    python tools/scaffold.py --sync
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
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

VOLUME_SUFFIX = {
    "一上": "1a", "一下": "1b", "二上": "2a", "二下": "2b", "三上": "3a", "三下": "3b",
    "四上": "4a", "四下": "4b", "五上": "5a", "五下": "5b", "六上": "6a", "六下": "6b",
    "七上": "7a", "七下": "7b", "八上": "8a", "八下": "8b", "九上": "9a", "九下": "9b",
    "必修一": "h1", "必修二": "h2", "选必一": "h3", "选必二": "h4", "选必三": "h5",
}

# 章名关键词 -> 课标主题（按顺序匹配，具体词在前，避免"函数"吃掉"二次函数"）
THEME_RULES = [
    # 小学
    ("分类与整理", "抽样与数据分析"),
    ("统计", "抽样与数据分析"),
    ("可能性", "随机事件的概率"),
    ("确定位置", "图形与坐标"),
    ("方向", "图形与坐标"),
    ("位置", "图形与坐标"),
    ("图形的运动", "图形的变化"),
    ("对称", "图形的变化"),
    ("平移", "图形的变化"),
    ("观察物体", "图形的性质"),
    ("观察简单组合体", "图形的性质"),
    ("面积", "图形的性质"),
    ("周长", "图形的性质"),
    ("厘米", "图形的性质"),
    ("毫米", "图形的性质"),
    ("分米", "图形的性质"),
    ("千米", "图形的性质"),
    ("图形", "图形的性质"),
    ("角", "图形的性质"),
    # 初中
    ("一元二次方程", "方程与不等式"),
    ("二元一次方程组", "方程与不等式"),
    ("一元一次方程", "方程与不等式"),
    ("不等式与不等式组", "方程与不等式"),
    ("二次函数", "函数"),
    ("反比例函数", "函数"),
    ("一次函数", "函数"),
    ("平面直角坐标系", "图形与坐标"),
    ("数据的收集", "抽样与数据分析"),
    ("数据的分析", "抽样与数据分析"),
    ("随机事件的概率", "随机事件的概率"),
    ("全等三角形", "图形的性质"),
    ("锐角三角函数", "图形的变化"),
    ("投影与视图", "图形的变化"),
    ("直线与圆", "图形的性质"),
    ("相交线与平行线", "图形的性质"),
    ("几何图形初步", "图形的性质"),
    ("勾股定理", "图形的性质"),
    ("整式的乘法", "数与式"),
    ("整式的加减", "数与式"),
    ("因式分解", "数与式"),
    ("二次根式", "数与式"),
    ("轴对称", "图形的变化"),
    ("四边形", "图形的性质"),
    ("三角形", "图形的性质"),
    ("有理数", "数与式"),
    ("代数式", "数与式"),
    ("分式", "数与式"),
    ("实数", "数与式"),
    ("旋转", "图形的变化"),
    ("相似", "图形的变化"),
    ("圆", "图形的性质"),
    ("函数", "函数"),
    # 高中
    ("集合与常用逻辑用语", "数与式"),
    ("一元二次函数", "方程与不等式"),
    ("指数函数", "函数"),
    ("对数函数", "函数"),
    ("三角函数", "函数"),
    ("平面向量", "图形的性质"),
    ("复数", "数与式"),
    ("立体几何", "图形的性质"),
    ("空间向量", "图形的性质"),
    ("直线和圆", "图形的性质"),
    ("圆锥曲线", "图形的性质"),
    ("数列", "函数"),
    ("导数", "函数"),
    ("计数原理", "数与式"),
    ("随机变量", "随机事件的概率"),
    ("成对数据", "抽样与数据分析"),
]

THEME_TO_DOMAIN = {
    "数与式": "数与代数", "方程与不等式": "数与代数", "函数": "数与代数",
    "图形的性质": "图形与几何", "图形的变化": "图形与几何", "图形与坐标": "图形与几何",
    "抽样与数据分析": "统计与概率", "随机事件的概率": "统计与概率",
}

# 小节名 -> 知识点类型（先匹配先赢）
TYPE_RULES = [
    (("实际问题",), "application"),
    (("判定", "证明", "逆定理", "命题", "推理"), "reasoning"),
    (("定理",), "reasoning"),
    (("应用",), "application"),
    (("运算", "计算", "化简", "乘法", "除法", "加法", "减法", "乘方", "加减", "乘除",
      "分解因式", "消元", "配方", "作图", "画"), "procedural"),
    (("概念", "性质", "认识", "意义", "表示", "定义", "根"), "conceptual"),
]

SYNC_FIELDS = ("name", "volume", "chapter", "section", "grade", "semester",
               "domain", "theme", "curriculum_version")


def theme_of(chapter: str) -> str:
    for keyword, theme in THEME_RULES:
        if keyword in chapter:
            return theme
    return "数与式"


def type_of(name: str) -> str:
    for keywords, value in TYPE_RULES:
        if any(k in name for k in keywords):
            return value
    return "conceptual"


def desired_topics(outline: dict) -> list:
    """按目录展开全部叶子，返回期望的知识点结构。"""
    out = []
    for vol in outline["volumes"]:
        if not any(ch["leaves"] for ch in vol["chapters"]):
            continue
        for chapter in vol["chapters"]:
            theme = theme_of(chapter["title"])
            for leaf in chapter["leaves"]:
                out.append({
                    "id": f"m{VOLUME_SUFFIX[vol['volume']]}.{leaf['no']}",
                    "name": leaf["title"],
                    "volume": vol["volume"],
                    "chapter": chapter["title"],
                    "section": leaf["section"],
                    "grade": vol["grade"],
                    "semester": vol["semester"],
                    "domain": THEME_TO_DOMAIN[theme],
                    "theme": theme,
                    "type": type_of(leaf["title"]),
                    "description": "",
                    "mastery_criteria": [],
                    "standards": [],
                    "curriculum_version": vol.get("curriculum_version", "2024"),
                    "edition": "人教版",
                    "review_status": "skeleton",
                    "external": False,
                })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sync", action="store_true",
                        help="用目录覆盖骨架点的名称/章节/领域等结构字段")
    args = parser.parse_args()

    outline = json.loads((DATA / "outline.json").read_text(encoding="utf-8"))
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    by_id = {t["id"]: t for t in topics}
    desired = desired_topics(outline)

    added = [d for d in desired if d["id"] not in by_id]
    updated = 0
    if args.sync:
        for want in desired:
            current = by_id.get(want["id"])
            if not current or current.get("review_status") != "skeleton":
                continue
            for field in SYNC_FIELDS:
                if current.get(field) != want[field]:
                    current[field] = want[field]
                    updated += 1

    print(f"目录叶子 {len(desired)}，现有知识点 {len(topics)}")
    print(f"待新增 {len(added)}，待同步字段 {updated}")
    if added:
        print("新增分布：" + "，".join(
            f"{k} {v}" for k, v in collections.Counter(d["volume"] for d in added).items()))

    if args.dry_run or (not added and not updated):
        return 0

    merged = topics + added
    lines = ",\n".join(json.dumps(t, ensure_ascii=False, separators=(",", ":")) for t in merged)
    (DATA / "topics.json").write_text("[\n" + lines + "\n]\n", encoding="utf-8")
    print(f"已写入 data/topics.json（{len(merged)} 个知识点）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
