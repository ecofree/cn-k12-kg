"""把抓到的目录草稿整理成正式的 data/outline.json（K12 全学段）。

三个学段的建点粒度不同，这是刻意的：
  小学  —— 按单元建点。教材本身以单元为教学单位，且单元下混着"综合与实践"活动，
           强行切条目会造出"欢乐购物街""小讲堂"这类假知识点；
  初中  —— 按小节建点（有子节取子节）；
  高中  —— 按小节建点（人教 A 版 2019 版，1.1 / 1.4.1 两级）。

课程版本也不一样：小学、初中是 2024 版新课标教材，高中仍是 2019 版（课标 2017 版 2020 修订）。

    python tools/build_outline.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SOURCE = ("人教版数学 K12 目录：小学/初中来自 2024 版新教材（电子课本网 2024—2026 版目录页），"
          "高中来自人教 A 版 2019 版；初中章节课时取自 2024 年人教版七—九年级教材解读")

# 册次 -> (学段, 年级, 学期, 教材版本)
VOLUME_META = {
    "一上": ("primary", 1, "上", "2024"), "一下": ("primary", 1, "下", "2024"),
    "二上": ("primary", 2, "上", "2024"), "二下": ("primary", 2, "下", "2024"),
    "三上": ("primary", 3, "上", "2024"), "三下": ("primary", 3, "下", "2024"),
    "四上": ("primary", 4, "上", "2024"), "四下": ("primary", 4, "下", "2024"),
    "五上": ("primary", 5, "上", "2024"), "五下": ("primary", 5, "下", "2024"),
    "六上": ("primary", 6, "上", "2024"), "六下": ("primary", 6, "下", "2024"),
    "七上": ("junior", 7, "上", "2024"), "七下": ("junior", 7, "下", "2024"),
    "八上": ("junior", 8, "上", "2024"), "八下": ("junior", 8, "下", "2024"),
    "九上": ("junior", 9, "上", "2024"), "九下": ("junior", 9, "下", "2024"),
    "必修一": ("senior", 10, "上", "2019"), "必修二": ("senior", 10, "下", "2019"),
    "选必一": ("senior", 11, "上", "2019"), "选必二": ("senior", 11, "下", "2019"),
    "选必三": ("senior", 12, "上", "2019"),
}

# 只有初中拿到了官方课时
CHAPTER_HOURS = {
    "七上": {1: 9, 2: 14, 3: 7, 4: 7, 5: 14, 6: 11},
    "七下": {7: 11, 8: 9, 9: 7, 10: 13, 11: 9, 12: 10},
    "八上": {13: 8, 14: 10, 15: 11, 16: 11, 17: 7, 18: 14},
    "八下": {19: 9, 20: 7, 21: 16, 22: 8, 23: 10, 24: 11},
    "九上": {25: 11, 26: 14, 27: 8, 28: 7, 29: 11, 30: 8},
    "九下": {31: 13, 32: 10, 33: 7, 34: 8},
}
TOTAL_HOURS = {"七上": 68, "七下": 65, "八上": 67, "八下": 67, "九上": 65, "九下": 44}
PROJECTS = {
    "七上": [("综合与实践 进位制的认识与探究", 3), ("综合与实践 设计学校田径运动会比赛场地", 3)],
    "七下": [("综合与实践 白昼时长规律的探究", 3)],
    "八上": [("综合与实践 确定匀质薄板的重心位置", 3), ("综合与实践 最短路径问题", 0),
             ("综合与实践 音乐与数学", 3)],
    "八下": [("综合与实践 学生体质健康调查与分析", 3)],
    "九上": [("综合与实践 高铁列车运行的数量关系", 3), ("综合与实践 生活中的优化问题举例", 3)],
    "九下": [("综合与实践 停车位设计问题", 3), ("综合与实践 建筑中的数学", 3)],
}

# 2027 春版教材尚未印发，但四册的章级（九下为节级）结构已由教育部审定目录与教辅平台确认，
# 因此不再是“未发布”：统一由 outline.override.json 提供结构，PENDING 只用于说明。
PENDING = {}
VOLUME_9B = {
    "volume": "九下", "grade": 9, "semester": "下", "curriculum_version": "2024",
    "stage": "junior", "status": "pending_textbook",
    "note": "2027 春版教材未发布，仅有章级结构；小节待教材面世后补齐。",
    "chapters": [{"no": 31, "title": "第三十一章 相似", "hours": 13, "leaves": []},
                 {"no": 32, "title": "第三十二章 锐角三角函数", "hours": 10, "leaves": []},
                 {"no": 33, "title": "第三十三章 投影与视图", "hours": 7, "leaves": []},
                 {"no": 34, "title": "第三十四章 随机事件的概率", "hours": 8, "leaves": []}],
}


CN_CHARS = "一二三四五六七八九十"


def clean_unit_title(title: str) -> str:
    return title.lstrip("一二三四五六七八九十、 　").strip()


def leaves_of(sections: list) -> list:
    """初中/高中：有子节取子节，否则本节；section 记父节标题。"""
    subs = {s["no"] for s in sections if s["no"].count(".") == 2}
    parent = {s["no"]: f"{s['no']} {s['title']}" for s in sections if s["no"].count(".") == 1}
    leaves = []
    for sec in sections:
        no = sec["no"]
        if no.count(".") == 2:
            leaves.append({"no": no, "title": sec["title"],
                           "section": parent.get(no.rsplit(".", 1)[0], no)})
            continue
        if any(s.startswith(no + ".") for s in subs):
            continue
        leaves.append({"no": no, "title": sec["title"], "section": f"{no} {sec['title']}"})
    return leaves


# 小学：能拆课时就拆课时，拆不动就退回单元。判定依据是抓到的条目本身。
REVIEW_WORDS = ("复习", "整理和复习", "整理复习")
# 综合与实践、数学游戏、主题活动：这些不是知识点，建点会污染图谱
ACTIVITY_WORDS = (
    "☆", "小讲堂", "讲堂", "故事", "游戏", "购物街", "导游", "画家", "故事会",
    "编码", "学号", "称重", "设计师", "密铺", "小书", "分享会", "我做主", "剪纸",
    "拼图", "身体", "年历", "秘密", "时间在哪里", "东、南、西、北",
)

# 数据源的 OCR/录入错误，按教材原名纠正
TITLE_FIXES = {"干米": "千米", "未尾": "末尾"}


def is_activity(title: str) -> bool:
    return any(word in title for word in ACTIVITY_WORDS)


def strip_lesson_no(title: str) -> str:
    """去掉条目自带的“1.”“2.”序号，编号由我们统一生成；顺带修 OCR 错字。"""
    import re
    title = re.sub(r"^[0-9]+[.．]\s*", "", title).strip()
    for wrong, right in TITLE_FIXES.items():
        title = title.replace(wrong, right)
    # "数数、数的组成-不超过100的数数、数的组成" 这类，"-" 后是说明，取前半段
    if "-" in title and len(title.split("-")[0]) >= 2:
        title = title.split("-")[0].strip()
    return title


def primary_leaves(chapter: dict) -> list:
    """小学：条目够细就按课时建叶，否则退回单元。

    四上、五上、六上、二下这几册，电子课本网当前只给出单元名（新教材刚启用，
    细目录还没上线），此时按单元建点，绝不凭想象补课时。
    """
    unit_title = clean_unit_title(chapter["title"])
    if any(word in unit_title for word in REVIEW_WORDS):
        return []

    seen, lessons = set(), []
    for sec in chapter.get("sections", []):
        title = strip_lesson_no(sec["title"])
        if not title or is_activity(title) or title in seen:
            continue
        seen.add(title)
        lessons.append(title)

    if len(lessons) >= 2:
        return [{"no": f"{chapter['no']}.{i}", "title": title,
                 "section": f"{chapter['no']}.{i} {title}"}
                for i, title in enumerate(lessons, 1)]
    return [{"no": f"{chapter['no']}.1", "title": unit_title, "section": unit_title}]


def primary_chapter_title(chapter: dict) -> str:
    """把"一  5以内数的认识"统一成"第一单元 5以内数的认识"。"""
    no = chapter["no"]
    numeral = CN_CHARS[no - 1] if 1 <= no <= 10 else str(no)
    return f"第{numeral}单元 {clean_unit_title(chapter['title'])}"


def load_override() -> dict:
    """人工核对的补充目录：抓取站只给单元名的册次，在这里补到课时/节级。"""
    path = DATA / "outline.override.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("volumes", {})


def main():
    draft = json.loads((DATA / "outline.draft.json").read_text(encoding="utf-8"))
    override = load_override()
    volumes = []
    skipped_review = 0
    overridden = []

    for vol in draft["volumes"]:
        name = vol["volume"]
        stage, grade, semester, version = VOLUME_META[name]
        chapters = []
        source_note = None
        volume_activities = []
        if name in override:
            ov = override[name]
            chapters = [{"no": c["no"], "title": c["title"], "hours": c.get("hours"),
                         "leaves": c["leaves"],
                         **({"kind": c["kind"]} if c.get("kind") else {}),
                         **({"official_page": c["official_page"]} if c.get("official_page") else {})
                         } for c in ov["chapters"]]
            source_note = ov.get("source")
            volume_activities = ov.get("activities", [])
            overridden.append(name)
        else:
            for chapter in vol["chapters"]:
                if stage == "primary":
                    leaves = primary_leaves(chapter)
                    skipped_review += 1 if not leaves else 0
                else:
                    leaves = leaves_of(chapter["sections"])
                chapters.append({
                    "no": chapter["no"],
                    "title": primary_chapter_title(chapter) if stage == "primary" else chapter["title"],
                    "hours": CHAPTER_HOURS.get(name, {}).get(chapter["no"]),
                    "leaves": leaves,
                })
        allocated = None
        if name in TOTAL_HOURS:
            allocated = (sum(c["hours"] or 0 for c in chapters)
                         + sum(h for _, h in PROJECTS.get(name, [])))
        volumes.append({
            "volume": name, "stage": stage, "grade": grade, "semester": semester,
            "curriculum_version": version,
            "granularity": ("lesson" if name in override else
                            "lesson" if stage == "primary" and all(
                len(c["leaves"]) != 1 or c["leaves"][0]["title"] != clean_unit_title(ch["title"])
                for c, ch in zip(chapters, vol["chapters"]) if c["leaves"])
                and any(c["leaves"] for c in chapters) else "unit"
                if stage == "primary" else "section"),
            "source_note": source_note,
            "activities": volume_activities,
            "total_hours": TOTAL_HOURS.get(name),
            "allocated_hours": allocated,
            "status": "ready",
            "projects": [{"title": t, "hours": h} for t, h in PROJECTS.get(name, [])],
            "chapters": chapters,
        })
        if name in TOTAL_HOURS and allocated != TOTAL_HOURS[name]:
            print(f"[注意] {name} 课时合计 {allocated}，官方 {TOTAL_HOURS[name]}，"
                  f"差 {TOTAL_HOURS[name] - allocated}")

    # override 里还可能有 draft 中没有的册次（2027 春版四册），补在后面
    draft_names = {v["volume"] for v in draft["volumes"]}
    for name, ov in override.items():
        if name in draft_names:
            continue
        stage, grade, semester, version = VOLUME_META[name]
        chapters = [{"no": c["no"], "title": c["title"], "hours": c.get("hours"),
                     "leaves": c["leaves"]} for c in ov["chapters"]]
        has_leaf = any(c["leaves"] for c in chapters)
        volumes.append({
            "volume": name, "stage": stage, "grade": grade, "semester": semester,
            "curriculum_version": version,
            "granularity": "section" if stage == "junior" else "unit",
            "total_hours": TOTAL_HOURS.get(name),
            "allocated_hours": (sum(c["hours"] or 0 for c in chapters)
                                + sum(h for _, h in PROJECTS.get(name, []))) if name in TOTAL_HOURS else None,
            "status": "ready" if has_leaf else "pending_textbook",
            "source_note": ov.get("source"),
            "note": ov.get("note"),
            "projects": [{"title": t, "hours": h} for t, h in PROJECTS.get(name, [])],
            "chapters": chapters,
        })
        overridden.append(name)

    if "九下" not in override:
        volumes.append({**VOLUME_9B,
                        "allocated_hours": sum(c["hours"] for c in VOLUME_9B["chapters"])
                        + sum(h for _, h in PROJECTS["九下"]),
                        "granularity": "section", "total_hours": TOTAL_HOURS["九下"],
                        "projects": [{"title": t, "hours": h} for t, h in PROJECTS["九下"]]})

    order = ["一上", "一下", "二上", "二下", "三上", "三下", "四上", "四下",
             "五上", "五下", "六上", "六下", "七上", "七下", "八上", "八下",
             "九上", "九下", "必修一", "必修二", "选必一", "选必二", "选必三"]
    volumes.sort(key=lambda v: order.index(v["volume"]) if v["volume"] in order else 99)

    outline = {
        "curriculum_version": "2024",
        "edition": "人教版",
        "subject": "数学",
        "source": SOURCE,
        "granularity_note": "小学按单元建点，初中/高中按小节建点；复习单元不建点。",
        "pending_volumes": PENDING,
        "volumes": volumes,
    }
    out = DATA / "outline.json"
    out.write_text(json.dumps(outline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    total = sum(len(c["leaves"]) for v in volumes for c in v["chapters"])
    print(f"\n已写入 {out}")
    print(f"册 {len(volumes)}  章/单元 {sum(len(v['chapters']) for v in volumes)}  点 {total}"
          f"（跳过复习单元 {skipped_review} 个；人工补充目录 {len(overridden)} 册："
          f"{'、'.join(overridden) or '无'}）")
    for vol in volumes:
        n = sum(len(c["leaves"]) for c in vol["chapters"])
        print(f"  {vol['volume']:<5}{vol['stage']:<8}单元/章 {len(vol['chapters']):>2}  点 {n:>3}"
              f"  {vol['curriculum_version']}  {vol.get('status')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
