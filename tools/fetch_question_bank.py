"""从公开数据集拉取题库，归一化成「候选池」，但不进诊断数据。

为什么单独建一层：
    项目里的 data/questions.json 每道题必须有 topicId 且经过 verify。
    外部题库没有 topicId，答案也未核验，直接合并会污染诊断。
    所以外部题先进 inbox/candidates/，标 needs_kc + review_status=unverified，
    等知识点映射（语义判定）和答案抽检都过了，再走 ingest 流程。

当前支持的数据源
    cmm       CMM-Math（华东师大，BSD-3-Clause，28069 条中文题，含选项/答案/解析）
    gaokao    GaoKaoMath（MIT，124 条高考题，含省份/年份/分值）

用法
    python tools/fetch_question_bank.py --stage junior
    python tools/fetch_question_bank.py --stage all --stats-only
    python tools/fetch_question_bank.py --source gaokao

输出
    inbox/candidates/<source>_<stage>.jsonl
    build/question-bank/stats.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "dl"
OUT_DIR = ROOT / "inbox" / "candidates"
STATS = ROOT / "build" / "question-bank" / "stats.json"

MIRROR = "https://hf-mirror.com/datasets"

SOURCES = {
    "cmm": {
        "repo": "ecnu-icalk/cmm-math",
        "file": "all_data.jsonl",
        "license": "BSD-3-Clause",
        "cache": "cmm_all.jsonl",
    },
    "gaokao": {
        "repo": "FrankieYao/GaoKaoMath",
        "file": "test.jsonl",
        "license": "MIT",
        "cache": "gaokao_frankie.jsonl",
    },
}

STAGE_GRADES = {
    "primary": {"一年级", "二年级", "三年级", "四年级", "五年级", "六年级"},
    "junior": {"七年级", "八年级", "九年级"},
    "senior": {"高一", "高二", "高三"},
}

OPT_LINE = re.compile(r"^\s*([A-H])\s*[.、．)）]\s*(.*)$", re.S)

# 行内选项分隔：CMM 有相当一部分选择题把 4 个选项挤在一行里，
# 例如 "开口向下B. 函数的最大值为 1C. 图象的对称轴为直线 x=-2D. ..."。
# 按行拆解只能得到 1 个选项，整题因此作废（实测初中池 63 道、
# 小学与高中更多）。这里用「后一个字母 + 分隔符」做切分点。
#
# 难点：字母 + 分隔符在正文里也常见（如 "x≥B."、"答案是A. 因为B. 所以"），
# 必须排除假切分点。判据是**切分点字母必须严格递增**：
# 真的一行四选项必然是 A→B→C→D；假切分点不满足递增，自动被排除。
#
# 但严格递增还不够："答案是A. 因为B. 所以" 的 A→B 也递增。
# 再加一条：**切分点之间必须存在实质内容**（非空且非纯标点）。
# "A. 因为B." 之间只有「因为」两个字，是解释性连接词而非选项内容。
OPT_INLINE = re.compile(
    r"([A-H])\s*[.、．)）]\s*"                # 字母 + 分隔符
    r"(?=[A-Za-z0-9$一-鿿\\])"               # 后面必须是内容
)

# 解释性连接词：夹在两个切分点之间时，说明这不是选项列表
CONNECTIVES = ("因为", "所以", "由于", "因此", "故", "则", "即", "那么",
               "而", "则", "由于", "根据", "所以", "and")


def txt(value) -> str:
    """CMM 的字段可能是 str / list / dict / None，统一成字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(txt(v) for v in value)
    if isinstance(value, dict):
        return " ".join(txt(v) for v in value.values())
    return str(value)


def download(source: str) -> pathlib.Path:
    spec = SOURCES[source]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / spec["cache"]
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    url = f"{MIRROR}/{spec['repo']}/resolve/main/{spec['file']}"
    print(f"下载 {url}")
    with urllib.request.urlopen(url, timeout=300) as resp, dest.open("wb") as fh:
        fh.write(resp.read())
    return dest


def split_inline_options(line: str) -> list[str]:
    """把挤在一行的多个选项切开。

    「A. 第一项B. 第二项C. 第三项」→ ['A. 第一项', 'B. 第二项', 'C. 第三项']。
    没有切分点时原样返回单元素列表。

    只有在**切分点字母严格递增**时才切分。这样：
      - 真的一行多选项（A/B/C/D）→ 递增，成立，切开
      - 假切分点（"x≥B." 之类单个）→ 不递增，不切
      - "已知A、B两点" → 「、」不是分隔符，本就不匹配
    """
    marks = list(OPT_INLINE.finditer(line))
    if not marks:
        return [line]
    letters = [m.group(1) for m in marks]
    if not all(letters[i] < letters[i + 1] for i in range(len(letters) - 1)):
        return [line]

    # 相邻切分点之间必须有实质内容，且不能是纯解释性连接词。
    # 「答案是A. 因为B. 所以」会在这里被拦掉。
    for i in range(len(marks) - 1):
        gap = line[marks[i].end():marks[i + 1].start()].strip()
        if not gap:
            return [line]
        if gap in CONNECTIVES:
            return [line]

    parts = []
    if marks[0].group(1) != "A":
        # 第一段没有字母前缀，单独成段
        head = line[:marks[0].start()].strip()
        if head:
            parts.append(head)
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        seg = line[m.start():end].strip()
        if seg:
            parts.append(seg)
    return parts if len(parts) >= 2 else [line]


def parse_choices(raw: str) -> tuple[list[dict], bool]:
    """把选项字符串拆成 [{key,text}]。

    CMM 有 14% 的选择题丢了首项的 "A." 前缀（原始 HTML 解析残留），
    这里按行序补位；补位后仍凑不齐 4 个的，返回 ok=False 交给人工。

    另外要处理**选项挤在一行**的情况——按行拆会得到 1 个选项，
    整题作废。先按行拆，再对每一行做行内切分。
    """
    raw = (raw or "").strip()
    if not raw:
        return [], True
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    items: list[dict] = []
    for ln in lines:
        m = OPT_LINE.match(ln)
        if m:
            items.append({"key": m.group(1), "text": m.group(2).strip()})
        elif items and not re.match(r"^\s*[A-H]\s*[.、．)）]", ln):
            # 续行：属于上一个选项（LaTeX 换行导致）
            items[-1]["text"] += " " + ln.strip()
        else:
            items.append({"key": "", "text": ln.strip()})

    # 行内切分：把 "A. xxxB. yyyC. zzz" 这种挤在一行的选项拆开
    expanded: list[dict] = []
    for it in items:
        segs = split_inline_options(it["text"])
        if len(segs) <= 1:
            expanded.append(it)
            continue
        # 段内重新按字母前缀解析 key
        for j, seg in enumerate(segs):
            m2 = OPT_LINE.match(seg)
            if m2:
                expanded.append({"key": m2.group(1), "text": m2.group(2).strip()})
            elif j == 0 and it.get("key"):
                # 第一段继承了原 key，剩余段靠顺序补位
                expanded.append({"key": it["key"], "text": seg.strip()})
            else:
                expanded.append({"key": "", "text": seg.strip()})
    items = expanded

    letters = "ABCDEFGH"
    for idx, item in enumerate(items):
        if not item["key"]:
            item["key"] = letters[idx] if idx < len(letters) else "?"
    keys = [it["key"] for it in items]
    ok = len(items) >= 2 and len(set(keys)) == len(keys) and all(k in letters for k in keys)
    return items, ok


def normalize_cmm(row: dict) -> dict | None:
    answer = txt(row.get("answer")).strip()
    if answer in ("", "null", "None"):
        return None
    if row.get("image"):
        return None  # 题干依赖配图，纯文本管道解不了，先排除
    prompt = txt(row.get("question")).strip()
    if not prompt:
        return None
    raw_opts = txt(row.get("options"))
    choices, opt_ok = parse_choices(raw_opts)
    qtype = "choice" if choices else "short"
    return {
        "source": "cmm-math",
        "source_id": str(row.get("id")),
        "stage": "junior" if row.get("level") in STAGE_GRADES["junior"]
        else "senior" if row.get("level") in STAGE_GRADES["senior"]
        else "primary",
        "grade_label": row.get("level"),
        "type": qtype,
        "prompt": prompt,
        "choices": choices,
        "answer": answer,
        "explanation": txt(row.get("analysis")).strip() or txt(row.get("solution")).strip(),
        "raw_subject": row.get("subject"),
        "options_intact": opt_ok,
        "topicId": None,
        "needs_kc": True,
        "review_status": "unverified",
        "license": SOURCES["cmm"]["license"],
    }


def normalize_gaokao(row: dict) -> dict | None:
    prompt = txt(row.get("problem")).strip()
    answer = txt(row.get("answer")).strip()
    if not prompt or not answer:
        return None
    return {
        "source": "gaokao-math",
        "source_id": str(row.get("id")),
        "stage": "senior",
        "grade_label": "高三",
        "type": "short",
        "prompt": prompt,
        "choices": [],
        "answer": answer,
        "explanation": "",
        "meta": {
            "year": row.get("year"),
            "province": row.get("province"),
            "number": row.get("number"),
            "score": row.get("score"),
        },
        "options_intact": True,
        "topicId": None,
        "needs_kc": True,
        "review_status": "unverified",
        "license": SOURCES["gaokao"]["license"],
    }


NORMALIZERS = {"cmm": normalize_cmm, "gaokao": normalize_gaokao}


def fingerprint(prompt: str) -> str:
    """去空白与 LaTeX 分隔符后取哈希，用于跨源去重。"""
    cleaned = re.sub(r"[\s$\\{}]", "", prompt or "")
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def run(source: str, stage: str, stats_only: bool) -> dict:
    path = download(source)
    rows = [json.loads(ln) for ln in path.open(encoding="utf-8") if ln.strip()]
    normalize = NORMALIZERS[source]

    kept: list[dict] = []
    seen: set[str] = set()
    dropped = collections.Counter()
    for row in rows:
        item = normalize(row)
        if item is None:
            dropped["缺答案或依赖配图"] += 1
            continue
        if stage != "all" and item["stage"] != stage:
            dropped["学段不符"] += 1
            continue
        fp = fingerprint(item["prompt"])
        if fp in seen:
            dropped["重复题"] += 1
            continue
        seen.add(fp)
        kept.append(item)

    choice = [k for k in kept if k["type"] == "choice"]
    broken = [k for k in choice if not k["options_intact"]]
    letters = collections.Counter(k["answer"].strip().rstrip(".、")[:1] for k in choice)

    stats = {
        "source": source,
        "license": SOURCES[source]["license"],
        "raw_rows": len(rows),
        "kept": len(kept),
        "dropped": dict(dropped),
        "by_stage": dict(collections.Counter(k["stage"] for k in kept)),
        "by_grade": dict(collections.Counter(k["grade_label"] for k in kept)),
        "choice": len(choice),
        "short": len(kept) - len(choice),
        "options_broken": len(broken),
        "answer_letter_dist": dict(letters.most_common()),
        "with_explanation": sum(1 for k in kept if k["explanation"]),
    }

    if not stats_only:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"{source}_{stage}.jsonl"
        with out.open("w", encoding="utf-8") as fh:
            for item in kept:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        stats["out"] = str(out.relative_to(ROOT))
        STATS.parent.mkdir(parents=True, exist_ok=True)
        STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SOURCES), default="cmm")
    ap.add_argument("--stage", choices=["primary", "junior", "senior", "all"], default="junior")
    ap.add_argument("--stats-only", action="store_true")
    args = ap.parse_args()
    run(args.source, args.stage, args.stats_only)


if __name__ == "__main__":
    main()
