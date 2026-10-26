"""接收外部填好的 CSV，先校验再应用。

    python tools/ingest.py --check      # 只校验，不改任何数据（默认）
    python tools/ingest.py --apply      # 校验通过后写回

各文件的作用与安全边界：

| 文件 | 内容 | --apply 会做什么 |
|---|---|---|
| standards.csv | 课标原文与页码 | 写入 standards.json 的 verbatim_text / page_or_ref / verbatim |
| review.csv | 人工复核结论 | 汇总到 data/reviews.json（不动题目本身） |
| kc_map.csv | 外部知识点 → 本项目 topic | 复制到 data/external/，只报覆盖率，不改数据 |
| question_stats.csv | 真实作答正确率 | 复制到 data/external/，需要先完成映射才能回填难度 |
| outline.csv | 教材目录细化 | 生成 data/external/outline_proposed.json，需人工确认后再合并 |

刻意不自动做的事：改题目难度、改先修边。这两件必须等映射抽检通过，
否则就是把"看起来有条理"的数字当成校准结果。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INBOX = ROOT / "inbox"
EXTERNAL = DATA / "external"


def read_csv(name):
    path = INBOX / name
    if not path.exists():
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def dump(path: pathlib.Path, rows):
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


class Report:
    def __init__(self):
        self.errors = []
        self.warns = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warns.append(msg)

    def show(self):
        for m in self.warns:
            print(f"  [WARN ] {m}")
        for m in self.errors:
            print(f"  [ERROR] {m}")
        if self.errors:
            print(f"\n未通过：{len(self.errors)} 个错误，{len(self.warns)} 个警告")
        else:
            print(f"\n通过：{len(self.warns)} 个警告")


def check_standards(rows, known, rpt):
    filled = 0
    for i, row in enumerate(rows, 2):
        sid = (row.get("id") or "").strip()
        if sid not in known:
            rpt.err(f"standards 第 {i} 行：课标 id 不存在 {sid}")
            continue
        text = (row.get("verbatim_text") or "").strip()
        flag = (row.get("verbatim") or "").strip().lower()
        if not text:
            if flag in ("true", "1", "是"):
                rpt.err(f"standards 第 {i} 行：verbatim=true 但没有原文")
            continue
        filled += 1
        if len(text) < 8:
            rpt.warn(f"standards {sid}：原文只有 {len(text)} 字，疑似没填完整")
        page = (row.get("page_or_ref") or "").strip()
        if not page or page == "待核":
            rpt.warn(f"standards {sid}：填了原文但没给页码/条款号")
    return filled


def check_reviews(rows, known, rpt):
    counts = {"pass": 0, "fail": 0, "skip": 0, "": 0}
    for i, row in enumerate(rows, 2):
        qid = (row.get("question_id") or "").strip()
        if qid not in known:
            rpt.err(f"review 第 {i} 行：题目不存在 {qid}")
            continue
        verdict = (row.get("verdict") or "").strip().lower()
        if verdict not in counts:
            rpt.err(f"review 第 {i} 行：verdict 只能是 pass/fail/skip，实际 {verdict!r}")
            continue
        counts[verdict] += 1
        if verdict == "fail" and not (row.get("note") or "").strip():
            rpt.warn(f"review {qid}：判为 fail 但没写原因")
    return counts


def check_kc_map(rows, topic_ids, rpt):
    seen_kc, mapped = set(), set()
    for i, row in enumerate(rows, 2):
        kc = (row.get("kc_id") or "").strip()
        tid = (row.get("topic_id") or "").strip()
        if not kc or kc.startswith("<示例>"):
            continue
        if kc in seen_kc:
            rpt.err(f"kc_map 第 {i} 行：kc_id 重复 {kc}")
        seen_kc.add(kc)
        if tid not in topic_ids:
            rpt.err(f"kc_map 第 {i} 行：topic_id 不存在 {tid}")
            continue
        mapped.add(tid)
        conf = (row.get("confidence") or "").strip().lower()
        if conf and conf not in ("high", "medium", "low"):
            rpt.warn(f"kc_map {kc}：confidence 建议用 high/medium/low，实际 {conf!r}")
    return len(seen_kc), len(mapped)


def check_stats(rows, qids, rpt):
    ok = 0
    for i, row in enumerate(rows, 2):
        qid = (row.get("question_id") or "").strip()
        if not qid or qid.startswith("<示例>"):
            continue
        if qid not in qids:
            rpt.err(f"question_stats 第 {i} 行：题目不存在 {qid}")
            continue
        try:
            attempts = int(float(row.get("attempts") or 0))
            rate = float(row.get("correct_rate") or -1)
        except ValueError:
            rpt.err(f"question_stats 第 {i} 行：attempts / correct_rate 不是数字")
            continue
        if attempts <= 0:
            rpt.err(f"question_stats {qid}：attempts 必须为正")
        if not 0 <= rate <= 1:
            rpt.err(f"question_stats {qid}：correct_rate 应在 0—1 之间，实际 {rate}")
        ok += 1
    return ok


def check_outline(rows, rpt):
    volumes = {"四下", "五下", "六下", "二下", "四上", "五上", "六上"}
    seen = set()
    new = 0
    for i, row in enumerate(rows, 2):
        vol = (row.get("volume") or "").strip()
        leaf = (row.get("leaf_no") or "").strip()
        action = (row.get("action") or "keep").strip().lower()
        if vol not in volumes:
            rpt.err(f"outline 第 {i} 行：册次不在待细化范围 {vol!r}")
            continue
        if action not in ("keep", "new", "rename", "drop"):
            rpt.err(f"outline 第 {i} 行：action 非法 {action!r}")
            continue
        if action in ("new", "rename"):
            if not leaf or not (row.get("leaf_title") or "").strip():
                rpt.err(f"outline 第 {i} 行：{action} 需要 leaf_no 与 leaf_title")
            if not (row.get("source") or "").strip():
                rpt.warn(f"outline 第 {i} 行：新增条目没写 source，无法追溯")
            new += 1
        if leaf:
            key = (vol, leaf)
            if key in seen:
                rpt.err(f"outline 第 {i} 行：同一册内 leaf_no 重复 {vol} {leaf}")
            seen.add(key)
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验（默认行为）")
    ap.add_argument("--apply", action="store_true", help="校验通过后写回")
    args = ap.parse_args()

    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    standards = json.loads((DATA / "standards.json").read_text(encoding="utf-8"))

    topic_ids = {t["id"] for t in topics}
    qids = {q["id"] for q in questions}
    sids = {s["id"] for s in standards}

    rpt = Report()
    present = 0
    summary = {}

    rows = read_csv("standards.csv")
    if rows is not None:
        present += 1
        filled = check_standards(rows, sids, rpt)
        summary["standards"] = (len(rows), filled)
        print(f"standards.csv  {len(rows)} 行，已填原文 {filled} 条")

    rows = read_csv("review.csv")
    if rows is not None:
        present += 1
        counts = check_reviews(rows, qids, rpt)
        summary["review"] = counts
        print(f"review.csv     {len(rows)} 行，"
              f"pass {counts['pass']} / fail {counts['fail']} / skip {counts['skip']}"
              f" / 未填 {counts['']}")

    rows = read_csv("kc_map.csv")
    if rows is not None:
        present += 1
        kc_n, mapped = check_kc_map(rows, topic_ids, rpt)
        summary["kc_map"] = (kc_n, mapped)
        cover = mapped / len(topic_ids) if topic_ids else 0
        print(f"kc_map.csv     {kc_n} 个 KC，覆盖 {mapped}/{len(topic_ids)} 个知识点"
              f"（{cover:.1%}）")
        if mapped and cover < 0.9:
            rpt.warn(f"映射覆盖率 {cover:.1%} 低于 90%，抽检前不要拿去回填难度")

    rows = read_csv("question_stats.csv")
    if rows is not None:
        present += 1
        ok = check_stats(rows, qids, rpt)
        summary["question_stats"] = ok
        print(f"question_stats.csv  {ok} 行可用")

    rows = read_csv("outline.csv")
    if rows is not None:
        present += 1
        new = check_outline(rows, rpt)
        summary["outline"] = (len(rows), new)
        print(f"outline.csv    {len(rows)} 行，新增/改名 {new} 条")

    if not present:
        print("inbox/ 里没有可读的 CSV。先运行：python tools/export_inbox.py")
        return 1

    print("\n== 校验 ==")
    rpt.show()
    if rpt.errors:
        return 1
    if not args.apply:
        print("\n（未写回；确认无误后加 --apply）")
        return 0

    EXTERNAL.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()

    # 1) 课标：只补原文与出处，不覆盖我们的摘要
    rows = read_csv("standards.csv")
    if rows:
        by_id = {s["id"]: s for s in standards}
        updated = 0
        for row in rows:
            sid = (row.get("id") or "").strip()
            text = (row.get("verbatim_text") or "").strip()
            if sid not in by_id or not text:
                continue
            item = by_id[sid]
            item["verbatim_text"] = text
            item["page_or_ref"] = (row.get("page_or_ref") or "").strip() or item["page_or_ref"]
            item["verbatim"] = (row.get("verbatim") or "true").strip().lower() in ("true", "1", "是")
            item["verified_at"] = today
            updated += 1
        dump(DATA / "standards.json", standards)
        print(f"\n课标：更新 {updated} 条原文")

    # 2) 复核结论
    rows = read_csv("review.csv")
    if rows:
        path = DATA / "reviews.json"
        existing = {}
        if path.exists():
            for r in json.loads(path.read_text(encoding="utf-8")):
                existing[r["questionId"]] = r
        for row in rows:
            qid = (row.get("question_id") or "").strip()
            verdict = (row.get("verdict") or "").strip().lower()
            if qid and verdict:
                existing[qid] = {"questionId": qid, "verdict": verdict,
                                 "note": (row.get("note") or "").strip(), "at": today}
        rows_out = [existing[k] for k in sorted(existing)]
        dump(path, rows_out)
        fails = [r["questionId"] for r in rows_out if r["verdict"] == "fail"]
        print(f"复核：累计 {len(rows_out)} 条，其中 fail {len(fails)} 条"
              f"{'：' + '、'.join(fails[:5]) if fails else ''}")

    # 3) 外部映射与统计：只落盘，不改数据
    for name in ("kc_map.csv", "question_stats.csv", "outline.csv"):
        src = INBOX / name
        if src.exists():
            target = EXTERNAL / name
            target.write_bytes(src.read_bytes())
            print(f"已复制 {name} → data/external/")

    print("\n下一步：")
    print("  1) 核对 data/external/kc_map.csv，抽检后用 tools/calibrate.py 回填难度（待实现）")
    print("  2) outline.csv 需要人工确认结构，再合并进 data/outline.override.json")
    print("  3) 跑 tools/validate.py 与 tools/build_db.py 刷新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
