"""解析上海中考卷的 LibreOffice 文本，产出结构化题目。

卷子结构（实测 6 份，高度一致）：
  题目区：题号行「N．（X分）题干」，选项行「A．xxx B．yyy」，有时跨多行
  答案区：「参考答案与试题解析」之后重复同样的题号行，并带
          【考点】/【分析】/【解答】…「故选：X．」/【点评】

**关键：答案区重复题干，这给了两样东西**
1. 「故选：X」是可靠的答案信号（选择题）
2. 题干在两区各出现一次，可以互相校验——只有两区题干一致的题才可信

**致命限制：源 .doc 里的公式是位图**（MathType 转出来的 PNG，无 Equation OLE），
纯文本导出后公式变成空缺。所以每道题都要判定「公式是否丢失」，
丢失的一律拒收，不能让残缺的题干进库。
"""
from __future__ import annotations

import difflib
import json
import pathlib
import re
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
TXT = ROOT / "build" / "dl" / "smse_txt"
OUT = ROOT / "build" / "question-bank"
TAG_MAP = json.loads((ROOT / "data" / "kc_tag_map.json").read_text(encoding="utf-8"))

# 两种题号格式：
#   1．（4分）题干   —— 带分值（多数卷）
#   1．题干         —— 不带分值（部分卷）
#   1. 题干        —— 半角句点（2021 年后的卷）
QNUM = re.compile(r"^\s*(\d+)\s*[．.]\s*(?:[（(]\s*(\d+)\s*分\s*[）)]\s*)?(.*)$")
# 答案区题号行也要能匹配
QNUM_ANS = re.compile(r"^\s*\d+\s*[．.]\s*(?:[（(]\s*\d+\s*分\s*[）)]\s*)?(.*)$")
OPT = re.compile(r"[ABCD]\s*[．.]\s*")
SEC = re.compile(r"^[一二三四五六七八九十]+\s*[、．]\s*")
# 答案信号有两种写法：
#   「故选：C．」   —— 2012-2019 的卷（菁优网格式）
#   「【答案】C」  —— 2020 年后的卷（另一种排版）
PICK = re.compile(r"故选\s*[:：]\s*([ABCD])")
PICK2 = re.compile(r"【答案】\s*([ABCD])")
KAODIAN = re.compile(r"【考点】\s*([^【】]{1,80})")
ANALYSIS = re.compile(r"【分析】\s*(.*?)(?=【解答】|【点评】|【考点】|\n|$)", re.S)
SOLUTION = re.compile(r"【解答】\s*(.*?)(?=【点评】|\n\s*\d+\s*[．.]|$)", re.S)

# 公式丢失的特征：中文标点连着出现，或空括号。
# **不能**把「若，则」「计算：＝」当成缺失——源数据里本就有「若，则∠A…」这种
# 前半句是公式的写法（"若 cos30°＝√3/2，则∠A…"），公式被丢掉后会留下
# 「若，则」但前一句本身也有内容。真正可靠的信号是**连续标点**或**空括号**。
# 「（　　）」是选择题的答题括号，**不是**公式缺失（源数据里本来就有）。
# 真正要抓的是「（）」这种完全空的括号，和连续标点。
# 注意：Python 的 \s 会匹配全角空格 U+3000，所以「（　　）」也会命中。
# 这里只认 ASCII 空格（真正的公式丢失会留下半角空格或直接空括号）。
HOLLOW = re.compile(r"（[ \t]*）|\([ \t]*\)|[，：；、][ \t]*[，：；、]")

# 结构性缺口判据：连接词后面直接跟标点，说明中间的公式被丢成图片了。
# 例：源题「若 cosA=√3/2，则∠A…」丢成「若，则∠A…」。
# 这类判据比"找连续标点"准得多——连续标点只在少数形态出现，
# 而"若，"这种在丢公式的题里几乎是主流形态。
GAP_RULES = [
    (re.compile(r"若[，：]"), "若后无内容"),
    (re.compile(r"计算[：:][ \t]*[＝=]"), "计算式丢失"),
    (re.compile(r"已知[，：]"), "已知条件丢失"),
    (re.compile(r"对于[^，。；：]{0,10}[，：][ \t]*[，。]"), "对于…缺主语"),
    (re.compile(r"则[，：]"), "则后无内容"),
    (re.compile(r"=[^=＝\d\s]"), "等号后无值"),
    # 「对于函数，下列…」「那么等于（…）」「计算的结果是（…）」——
    # 主语或宾语被公式占掉了，剩下的谓语前面没有内容
    (re.compile(r"对于[^，。；：]{0,12}[，：][ \t]*下列"), "对于…缺主语"),
    (re.compile(r"那么[ \t]*等于"), "等于前缺宾语"),
    (re.compile(r"计算[ \t]*的[ \t]*结果[ \t]*是"), "计算式丢失"),
    (re.compile(r"[ \t]*的结果[ \t]*是"), "结果式丢失"),
    (re.compile(r"下列[ \t]*各?[ \t]*式[ \t]*中[，：][ \t]*正确"), "算式缺失"),
]


def formula_gaps(text: str) -> list[str]:
    """返回题干中公式丢失的位置描述列表；空列表表示公式完好。"""
    out = []
    for pat, label in GAP_RULES:
        if pat.search(text):
            out.append(label)
    return out
FIGURE = re.compile(r"如图|下图|图中|右图")


def is_blank_gap(text: str) -> bool:
    """公式被丢成空缺：连续标点或空括号。"""
    return bool(HOLLOW.search(text))


def split_options(block: str) -> list[str]:
    """把 'A．30°\tB．45°' 拆成 ['30°', '45°']。

    注意：源数据里选项行后面可能跟着章节标题（"二、填空题（本大题共12题…"），
    那不是选项内容，必须截掉。
    """
    block = SEC.split(block)[0] if SEC.match(block.strip()) else block
    # 遇到第一个非 A-D 开头的段落就停（后面可能是章节标题）
    m = re.search(r"[^\s]", block)
    parts = re.split(r"[ABCD]\s*[．.]\s*", block)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 去掉粘连的章节标题
        p = re.split(r"[一二三四五六七八九十]+\s*[、．]", p)[0].strip()
        if p:
            out.append(p)
    return out


YEAR_IN_NAME = re.compile(r"(19|20)\d{2}")
YEAR_IN_TEXT = re.compile(r"(19|20)\d{2}\s*年")


def extract_year(path: pathlib.Path, text: str) -> str:
    """从文件名或文件内容里抽年份。"""
    m = YEAR_IN_NAME.search(path.stem)
    if m:
        return m.group(0)
    m = YEAR_IN_TEXT.search(text[:200])
    if m:
        return m.group(0)[:4]
    return "unknown"


def parse_paper_v2(text: str) -> list[dict]:
    """2020+ 版式：题目与解析交错，【答案】X 紧跟在题目后面。

    与旧版式的区别：
      旧：题目区 …… 参考答案 …… 答案区（重复题干）
      新：1.题干 / A. / B. / 【答案】C / 【分析】… / 【详解】… / 2.题干 / …

    所以每道题是一个「题号行 + 若干行」的小块，
    答案从同一块里的【答案】标记取，考点标签从【考点】/【点睛】取。
    """
    lines = [l.rstrip() for l in text.split("\n")]
    blocks: list[dict] = []
    cur = None
    for l in lines:
        s = l.strip()
        m = QNUM.match(s)
        if m:
            cur = {"no": int(m.group(1)),
                   "pts": int(m.group(2)) if m.group(2) else 0,
                   "text": (m.group(3) or "").strip(),
                   "opts": [], "raw": [s], "in_opts": True}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        cur["raw"].append(s)
        if s.startswith("【答案】"):
            cur["answer_raw"] = s
            cur["in_opts"] = False
            continue
        if s.startswith("【"):
            cur["in_opts"] = False
            continue
        if not s:
            continue
        if cur["in_opts"] and (OPT.match(s) or (cur["opts"] and re.match(r"^[ABCD]\s*[．.]", s))):
            cur["opts"].extend(split_options(s))
        elif not cur["opts"] and cur["in_opts"]:
            cur["text"] += s
    return blocks


def parse_paper(text: str) -> list[dict]:
    lines = [l.rstrip() for l in text.split("\n")]
    # 两种版式：
    #   A. 「参考答案与试题解析」之后是答案区（2012-2019）
    #   B. 全文就是题目+解析交错，【答案】紧跟在每题后面（2020+）
    ans_start = next((i for i, l in enumerate(lines) if "参考答案" in l), None)
    if ans_start is None:
        if "【答案】" in text:
            # 2020+ 交错版式，走另一套解析
            blocks = parse_paper_v2(text)
            out = []
            for b in blocks:
                b = dict(b)
                b["answer_block"] = {"raw": b["raw"], "opts": [], "text": b["text"]}
                out.append(b)
            return out
        return []
    q_lines = lines[:ans_start]
    a_lines = lines[ans_start:]

    def collect(ls: list[str]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        cur = None
        for l in ls:
            m = QNUM.match(l.strip())
            if m:
                no = int(m.group(1))
                pts = int(m.group(2)) if m.group(2) else 0
                cur = {"no": no, "pts": pts, "text": (m.group(3) or "").strip(),
                       "opts": [], "raw": [l.strip()]}
                out[no] = cur
                continue
            if cur is None:
                continue
            cur["raw"].append(l.strip())
            s = l.strip()
            if not s:
                continue
            if SEC.match(s):
                # 章节标题：题目区结束，选项列表也要收尾
                cur = None
                continue
            if s.startswith("【答案】"):
                # 2020+ 版式：【答案】X 紧跟在题目后面。
                # 答案区的题号行就是题目行，答案要单独取。
                cur = None
                continue
            if s.startswith("【"):
                # 解析块开始（【考点】/【分析】/【解答】/【点评】）。
                # 注意：**不能**把 cur 置 None——「故选：X」就在【解答】块里，
                # 置 None 会让答案信号丢失。正确做法是标记"离开选项阶段"，
                # 但仍然归属当前题。
                cur["in_opts"] = False
                cur["raw"].append(s)
                continue
            if cur.get("in_opts", True) and (
                    OPT.match(s) or (cur["opts"] and re.match(r"^[ABCD]\s*[．.]", s))):
                cur["opts"].extend(split_options(s))
            elif not cur["opts"] and cur.get("in_opts", True):
                # 题干续行
                cur["text"] += s
            elif not cur.get("in_opts", True):
                pass  # 解析块里的普通行，不是选项
            elif re.match(r"^[（(]\s*[一二三四五六七八九十0-9]", s):
                # 选项续行（形如 "（1）…"）
                cur["opts"].extend(split_options(s))
            else:
                # 选项的尾巴，如 "C．都经过原点D．互相可以通过平移得到"
                cur["opts"].extend(split_options(s))
        return out

    qmap = collect(q_lines)
    amap = collect(a_lines)
    return [qmap[k] | {"answer_block": amap.get(k)} for k in sorted(qmap)]


BLANK = re.compile(r"[ \u3000]{2,}")


def extract_fill_answer(qtext: str, atext: str) -> str:
    """从答案区的题干行里抠出填空答案。

    菁优网的答案区会把题目区的空白**替换成**答案：
        题目区「…那么AP＝　   　cm．」
        答案区「…那么AP＝　3（﹣1）　cm．」
    所以答案区那一段**没有空白标记**，不能靠对齐空白位置。

    正确做法：把两段都按"空白"切段，比较对应段的内容差异。
        Q 段: [...][BLANK][...]      A 段: [...][3（﹣1）][...]
    差异段就是答案。
    """
    if not atext:
        return ""
    # 用空白作为分隔符切段（题目区有空白，答案区对应位置是答案）
    q_parts = BLANK.split(qtext)
    a_parts = BLANK.split(atext)
    # 两边段数应一致：Q = [前, 后]，A = [前, 答案+后] 或 [前+答案, 后]
    if len(q_parts) < 2:
        return ""
    # 逐个空白位置比对：用 SequenceMatcher 更稳
    sm = difflib.SequenceMatcher(None, qtext, atext)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace") and j2 > j1:
            # 插入/替换的内容就是答案，但要确认题目区对应位置确实是空白
            seg = qtext[i1:i2]
            if not seg.strip(" \u3000"):
                return atext[j1:j2].strip(" \u3000")
    return ""


# 填空答案里的公式丢失特征。
# 注意：不能把「（0，1）」「3（﹣1）」「（x2+2）（x+1）」当丢失——这些是完整答案。
# 真正的丢失形态只有：完全空的括号、等号后没有值、系数后接空括号。
BAD_ANSWER = [
    (re.compile(r"（\s*）"), "空括号"),
    (re.compile(r"[=＝]\s*$"), "等号后无值"),
    (re.compile(r"^[=＝]"), "答案以等号开头"),
    # 「2x（x+）（x﹣）」——括号里只剩符号，中间的内容是丢掉的公式
    (re.compile(r"（[^（）()]{0,6}[+＋\-－×÷*]\s*）"), "括号内只剩符号"),
    (re.compile(r"[a-zA-Z]\d*（\s*）"), "系数后空括号"),
]


def answer_is_broken(ans: str) -> bool:
    return any(p.search(ans) for p, _ in BAD_ANSWER)


def classify(rec: dict) -> dict:
    q = rec["text"]
    opts = rec["opts"]
    ab = rec.get("answer_block") or {}
    atext = ab.get("text", "")
    aopts = ab.get("opts", [])

    # 「故选：X」出现在【解答】块里，不在题干行上，必须在整段 raw 上搜
    araw = "\n".join(ab.get("raw", []))
    picked = ""
    m = PICK.search(araw) or PICK2.search(araw)
    if m:
        picked = m.group(1)

    # 考点标签：可能多个（用 ；分隔），逐个映射
    kaodian = ""
    kaodian_tags = []
    topic_id = None
    km = KAODIAN.search(araw)
    if km:
        raw_tag = km.group(1).replace("．菁优网版权所有", "").replace("．菁优网", "")
        kaodian = raw_tag.split("．")[0].strip()
        for part in re.split(r"[；;]", raw_tag):
            part = part.strip()
            if not part:
                continue
            # 去掉编码前缀 "S9："
            name = part.split("：", 1)[1].strip() if "：" in part and len(part.split("：", 1)[0]) <= 4 else part
            kaodian_tags.append(name)
            if topic_id is None:
                tid = TAG_MAP["map"].get(name)
                if tid:
                    topic_id = tid

    analysis = ""
    am = ANALYSIS.search("\n".join(ab.get("raw", [])))
    if am:
        analysis = am.group(1).strip()

    # 填空题：答案区题干行里已填好答案
    araw_lines = [l for l in ab.get("raw", []) if l.strip()]
    atext = ""
    if araw_lines:
        m2 = QNUM_ANS.match(araw_lines[0].strip())
        if m2:
            atext = m2.group(1)
    fill_answer = extract_fill_answer(q, atext) if not opts else ""

    # 交叉校验：题目区与答案区的选项文本必须一致。
    # 这是防串题的独立检查——如果解析时把两题的内容混在一起，这里会立刻暴露。
    aopts = ab.get("opts", [])
    opts_consistent = (opts == aopts) if (opts and aopts) else None

    gaps = formula_gaps(q) + [f"选项:{x}" for o in opts for x in formula_gaps(o)]
    # 选项自身的公式丢失：「直线x＝」「c＝」——等号后面没有内容
    for o in opts:
        if re.search(r"[=＝]\s*$", o) or not re.search(r"[=＝\dA-Za-z]", o):
            gaps.append(f"选项公式丢失:{o[:14]}")
    if is_blank_gap(q) or any(is_blank_gap(o) for o in opts):
        gaps.append("空括号")
    has_formula_gap = bool(gaps)
    has_fig = bool(FIGURE.search(q))

    return {
        "no": rec["no"], "points": rec["pts"], "prompt": q, "options": opts,
        "answer_letter": picked, "fill_answer": fill_answer,
        "kaodian": kaodian, "kaodian_tags": kaodian_tags,
        "topicId": topic_id, "analysis": analysis,
        "has_formula_gap": has_formula_gap, "gap_reasons": gaps,
        "opts_consistent": opts_consistent, "has_figure": has_fig,
        "option_count": len(opts),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    papers = []
    total = kept = 0
    for f in sorted(TXT.glob("*.txt")):
        if f.stem in ("test", "probe"):
            continue  # 调试残留
        text = f.read_text(encoding="utf-8-sig")
        recs = [classify(r) for r in parse_paper(text)]
        if not recs:
            continue
        # 只保留题干在答案区能对上的题（两个区都出现同一题号）
        usable = []
        for r in recs:
            total += 1
            if r["has_formula_gap"] or r["has_figure"]:
                continue
            if r["option_count"] >= 3:
                if not r["answer_letter"]:
                    continue
                if r["opts_consistent"] is False:
                    continue  # 题目区与答案区选项不一致 → 串题了
                # 答案指向不存在的选项 → 第 4 项是图片（被丢弃），
                # 这道题的选项不全，不能入库
                if "ABCD".index(r["answer_letter"]) >= r["option_count"]:
                    continue
            if not r["topicId"]:
                continue  # 考点标签没映射上，无法归属知识点
            if r["option_count"] < 3:
                if not r["fill_answer"]:
                    continue  # 填空题抽不到答案
                if answer_is_broken(r["fill_answer"]):
                    continue  # 答案里的公式被丢成空缺
            usable.append(r)
        # 年份从**文件内容**里抽，而不是从目录名：
        #   - 目录名是转换输出目录 smse_txt，没有年份
        #   - 文件名里的年份可能在中间（「上海市嘉定区2020届…」）
        #   - 文件名开头是年份的（「2012年上海市…」）可以直接用
        year_dir = extract_year(f, text)
        papers.append({
            "paper": f"{year_dir}/{f.stem}",
            "questions": usable,
            "all_count": len(recs),
        })
        kept += len(usable)

    with (OUT / "smse_parsed.jsonl").open("w", encoding="utf-8") as fh:
        for p in papers:
            for q in p["questions"]:
                fh.write(json.dumps({**q, "paper": p["paper"]}, ensure_ascii=False) + "\n")

    from collections import Counter
    c = Counter()
    for p in papers:
        for q in p["questions"]:
            c["choice" if q["option_count"] >= 3 else "fill"] += 1
    print(json.dumps({
        "papers": len(papers), "questions_seen": total, "questions_kept": kept,
        "kept_by_type": dict(c),
        "out": str((OUT / "smse_parsed.jsonl").relative_to(ROOT)),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
