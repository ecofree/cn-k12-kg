"""从电子课本网抓取人教版数学 K12 全学段目录，生成 data/outline.draft.json。

三个学段的目录结构不同，解析规则也不同：
  小学  —— "一  5以内数的认识和加、减法" 是单元，单元下的条目没有编号，按出现顺序编号；
  初中  —— "第一章 有理数" + "1.1 / 1.2.1" 两级编号；
  高中  —— "第一章 集合与常用逻辑用语" + "1.1 / 1.4.1" 编号，另有阅读材料等噪声条目。

只用于采集，产物必须人工过一遍再进 outline.json。

    python tools/fetch_toc.py --cache /tmp/kgref
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "http://www.dzkbw.com/books/rjb/shuxue/"

# (册名, 书号, 学段, 年级, 学期)
BOOKS = [
    ("一上", "xs1s_2024", "primary", 1, "上"),
    ("一下", "xs1x_2025", "primary", 1, "下"),
    ("二上", "xs2s_2025", "primary", 2, "上"),
    ("二下", "xs2x_2026", "primary", 2, "下"),
    ("三上", "xs3s_2025", "primary", 3, "上"),
    ("三下", "xs3x_2026", "primary", 3, "下"),
    ("四上", "xs4s_2026", "primary", 4, "上"),
    ("五上", "xs5s_2026", "primary", 5, "上"),
    ("六上", "xs6s_2026", "primary", 6, "上"),
    ("七上", "xc7s_2024", "junior", 7, "上"),
    ("七下", "xc7x_2025", "junior", 7, "下"),
    ("八上", "xc8s_2025", "junior", 8, "上"),
    ("八下", "xc8x_2026", "junior", 8, "下"),
    ("九上", "xc9s_2026", "junior", 9, "上"),
    ("必修一", "gzabx1", "senior", 10, "上"),
    ("必修二", "gzabx2", "senior", 10, "下"),
    ("选必一", "gzaxzxbx1", "senior", 11, "上"),
    ("选必二", "gzaxzxbx2", "senior", 11, "下"),
    ("选必三", "gzaxzxbx3", "senior", 12, "上"),
]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}

CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十]+)章\s*(.+)$")
UNIT_RE = re.compile(r"^([一二三四五六七八九十]+)[、\s　]+(\S.*)$")
EXAMPLE_SUFFIX = re.compile(r"\s*例\s*\d+\s*$")
NUM_RE = re.compile(r"^(\d+(?:\.\d+){1,2})\s*(.+)$")

# 阅读材料、复习、活动类条目不是知识点
NOISE_PREFIX = ("阅读与思考", "探究与发现", "信息技术应用", "小结", "复习参考题",
                "数学活动", "图说数学史", "整理和复习", "总复习", "数学游戏",
                "学习准备", "练习", "习题", "单元小结")

CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
      "八": 8, "九": 9, "十": 10}


def cn_num(text: str) -> int:
    if "十" not in text:
        return CN[text]
    tens, _, ones = text.partition("十")
    value = (CN.get(tens, 1) if tens else 1) * 10
    if ones:
        value += CN[ones]
    return value


def is_noise(text: str) -> bool:
    return text.startswith(NOISE_PREFIX)


def clean_item(text: str) -> str:
    """去掉例题编号，保留可读的小节名。"""
    return EXAMPLE_SUFFIX.sub("", text).strip()


def fetch(code: str, cache: pathlib.Path | None) -> str:
    if cache:
        cached = cache / f"{code}.html"
        if cached.exists():
            return cached.read_bytes().decode("gbk", errors="replace")
    req = urllib.request.Request(f"{BASE}{code}/", headers=UA)
    raw = urllib.request.urlopen(req, timeout=30).read()
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{code}.html").write_bytes(raw)
    return raw.decode("gbk", errors="replace")


def split_merged(text: str) -> list:
    """把"2.3.2 科学记数法 2.3.3近似数"拆成两条；不切 "1.2.1" 内部。"""
    parts = re.split(r"(?<=\s)(?=\d+\.\d+(?:\.\d+)?\s*\D)", text)
    return [p.strip() for p in parts if p.strip()]


def extract_items(html: str, code: str) -> list:
    pattern = re.compile(rf'<a[^>]+href="[^"]*{code}/(\d+)\.htm"[^>]*>(.*?)</a>', re.S | re.I)
    items = []
    for _, raw in pattern.findall(html):
        text = re.sub(r"<[^>]+>", "", raw).replace("&nbsp;", " ").strip()
        if not text or text in ("封面", "目录", "上一页", "下一页"):
            continue
        for piece in split_merged(text):
            if not is_noise(piece):
                items.append(piece)
    return items


def parse_primary(items: list) -> list:
    """小学：单元 + 无编号条目。

    条目按出现顺序编号（1.1、1.2…）。没有条目的大单元（2026 秋的四上、五上、六上）
    把单元本身作为叶子，编号 1.1。
    """
    chapters = []
    current = None
    for text in items:
        m = UNIT_RE.match(text)
        if m and "章" not in text and not text.startswith("☆"):
            no = cn_num(m.group(1))
            current = {"no": no, "title": text, "sections": []}
            chapters.append(current)
            continue
        if current is None:
            continue
        title = clean_item(text)
        if not title:
            continue
        current["sections"].append({
            "no": f"{current['no']}.{len(current['sections']) + 1}",
            "title": title,
        })

    for chapter in chapters:
        if not chapter["sections"]:
            title = re.sub(r"^[一二三四五六七八九十]+[、\s　]+", "", chapter["title"]).strip()
            chapter["sections"].append({"no": f"{chapter['no']}.1", "title": title})
    return chapters


def parse_numbered(items: list) -> list:
    """初中 / 高中：章 + 编号小节（初中兼容无子节的情况）。"""
    chapters = []
    current = None
    for text in items:
        m = CHAPTER_RE.match(text)
        if m:
            no = cn_num(m.group(1))
            current = {"no": no, "title": text, "sections": []}
            chapters.append(current)
            continue
        if current is None:
            continue
        n = NUM_RE.match(text)
        if n:
            # 章号与节号不一致时不采信（页面偶有串章）
            if n.group(1).split(".")[0] != str(current["no"]):
                continue
            current["sections"].append({"no": n.group(1), "title": n.group(2).strip()})
    return chapters


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=None)
    args = parser.parse_args()
    cache = pathlib.Path(args.cache) if args.cache else None

    volumes = []
    for volume, code, stage, grade, semester in BOOKS:
        html = fetch(code, cache)
        items = extract_items(html, code)
        chapters = parse_primary(items) if stage == "primary" else parse_numbered(items)
        leaves = sum(len(c["sections"]) for c in chapters)
        volumes.append({"volume": volume, "grade": grade, "semester": semester,
                        "stage": stage, "source_code": code, "chapters": chapters})
        print(f"{volume:<5}{stage:<8}{code:<14} 章/单元 {len(chapters):>2}  条目 {leaves:>3}")

    out = ROOT / "data" / "outline.draft.json"
    out.write_text(json.dumps({"volumes": volumes}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"\n草稿已写入 {out}（需人工校对后再并入 outline.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
