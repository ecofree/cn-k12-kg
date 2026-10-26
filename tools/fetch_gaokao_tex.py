"""解析 DxAThing 高考数学真题合集（LaTeX 源码）为结构化题目。

为什么换这个源：
    CMM-Math 是「中考水平的中文题」，没有年份、没有省份、没有难度。
    这个仓库是**真题**，每年每卷一个 .tex，题、答案、解析、年份、卷别全在，
    而且是干净的 LaTeX —— 不是扫描件，不需要 OCR。
    许可证 CC BY-SA 4.0，允许使用与改编（需署名并以相同方式共享）。

格式（实测 2024 新高考 I 卷）：
    \\begin{problem}
    题干，行内公式用 \\(...\\)，独立公式用 \\[...\\]
    \\choices
        {选项1}{选项2}{选项3}{选项4}
    \\end{problem}
    \\begin{answer}A\\end{answer}
    \\begin{solution}...故选 A.\\end{solution}

用法：
    python tools/fetch_gaokao_tex.py --limit 3        # 先跑 3 份看效果
    python tools/fetch_gaokao_tex.py --all            # 全量（777 份，约 16 MB）
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "dl" / "dxa"
OUT = ROOT / "build" / "question-bank" / "gaokao_tex.jsonl"
TREE = ROOT / "build" / "dl" / "dxa_tree.json"

RAW = "https://raw.githubusercontent.com/DxAThing/Gaokao-Math-Problems-Compilation/main/"
REPO = "DxAThing/Gaokao-Math-Problems-Compilation"
LICENSE = "CC-BY-SA-4.0"
FAILED: list[tuple[str, str]] = []

# 仓库自定义宏，定义在 styles.tex。只收数学相关的纯替换。
MACROS = {
    r"\bs": r"\boldsymbol",
    r"\R": r"\mathbf{R}",
    r"\N": r"\mathbf{N}",
    r"\i": r"\mathrm{i}",
    r"\dfrac": r"\frac",
    r"\dbinom": r"\binom",
    r"\leqslant": r"\le",
    r"\geqslant": r"\ge",
    r"\varnothing": r"\emptyset",
}

PROBLEM = re.compile(r"\\begin\{problem\}(.*?)\\end\{problem\}", re.S)
ANSWER = re.compile(r"\\begin\{answer\}(.*?)\\end\{answer\}", re.S)
SOLUTION = re.compile(r"\\begin\{solution\}(.*?)\\end\{solution\}", re.S)
# 老卷（1991 等）用内联命令写答案：\answer{A}、\answer{B}
# 而不是 \begin{answer}...\end{answer} 块。实测这类卷的选择题
# 全部因为只认块格式而丢失答案（334 道）。
ANSWER_INLINE = re.compile(r"\\answer\s*\{([^{}]*)\}")
# 解析里的「故选X」「应选X」——块格式缺失时的兜底证据来源。
# 字母后面可能是右括号、句号、空格或直接结束，所以**不能**强制要求右括号。
PICK_INLINE = re.compile(r"(?:故|应|因此|所以)?\s*选\s*[（(]?\s*([A-H])\s*[）)]?(?![A-Za-z])")
CHOICES = re.compile(r"\\choices((?:\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})+)", re.S)
GROUP = re.compile(r"\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", re.S)
CHAPTER = re.compile(r"\\chapter\{(.+?)\}")
SECTION = re.compile(r"\\section\{(.+?)\}")


def apply_macros(tex: str) -> str:
    """替换自定义宏。必须带词边界：\\i 如果是裸 replace，会把 \\item 改成 \\mathrm{i}tem。"""
    for bad, good in MACROS.items():
        tex = re.sub(re.escape(bad) + r"(?![A-Za-z])", lambda _m, g=good: g, tex)
    return tex


def normalize_math(tex: str) -> str:
    """统一成 $...$ / $$...$$，与项目里 CMM 题目的写法一致。

    高考卷用「（\quad）」表示填空位，它不是公式，只是排版空白。
    原样保留的话前端会显示成 \quad，要先换成下划线。
    """
    tex = re.sub(r"[（(]\\(?:qquad|quad|hspace\{[^}]*\})[）)]", "______", tex)
    tex = re.sub(r"\\(?:qquad|quad)(?![A-Za-z])", "____", tex)
    tex = re.sub(r"\\\[(.*?)\\\]", lambda m: "$$" + m.group(1).strip() + "$$", tex, flags=re.S)
    tex = re.sub(r"\\\((.*?)\\\)", lambda m: "$" + m.group(1).strip() + "$", tex, flags=re.S)
    tex = re.sub(r"[ \t]*\n[ \t]*", " ", tex)
    tex = re.sub(r"\s{2,}", " ", tex)
    return tex.strip()


def parse_file(text: str, paper: dict) -> list[dict]:
    text = apply_macros(text)
    chapter = CHAPTER.search(text)
    paper_name = chapter.group(1) if chapter else paper["stem"]

    records = []
    # 按 problem 块切，同时取之后紧跟的 answer/solution
    marks = [(m.start(), m.end(), m.group(1)) for m in PROBLEM.finditer(text)]
    for i, (start, end, body) in enumerate(marks):
        tail_end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        tail = text[end:tail_end]

        has_figure = bool(re.search(r"\\includegraphics|\\bitmapinclude|\\bitmapfigure|\\begin\{tikzpicture\}|\\begin\{center\}", body))

        choices = []
        m = CHOICES.search(body)
        if m:
            groups = GROUP.findall(m.group(1))
            letters = "ABCDEFGH"
            choices = [{"key": letters[j], "text": normalize_math(g)}
                       for j, g in enumerate(groups[:8])]
            body = body[:m.start()] + body[m.end():]

        # 老卷（如 1985）把 answer/solution 写在 problem 块内部，新卷写在外面。
        # 两种都要支持：先看外面，没有再往里找，找到就从题干里摘掉。
        ans = ANSWER.search(tail) or ANSWER.search(body)
        sol = SOLUTION.search(tail) or SOLUTION.search(body)
        for pat in (ANSWER, SOLUTION):
            m_in = pat.search(body)
            if m_in:
                body = body[:m_in.start()] + body[m_in.end():]

        answer = normalize_math(ans.group(1)) if ans else ""
        solution = normalize_math(sol.group(1)) if sol else ""

        # 答案的三级兜底（按证据强度递减）：
        #   1. \begin{answer} 块          —— 原卷答案，最强
        #   2. \answer{A} 内联命令       —— 老卷格式
        #   3. 解析里的「故选X」          —— 从 solution 反推
        # 3 只能用于选择题（答案就是选项字母），且要记录来源。
        answer_source = "answer_block" if ans else ""
        if not answer:
            m_inline = ANSWER_INLINE.search(body) or ANSWER_INLINE.search(tail)
            if m_inline:
                answer = normalize_math(m_inline.group(1)).strip()
                answer_source = "answer_inline"
                if m_inline.string is body:
                    body = body[:m_inline.start()] + body[m_inline.end():]
        if not answer and choices:
            # 从解析里找「故选X」
            m_pick = PICK_INLINE.search(solution)
            if m_pick:
                answer = m_pick.group(1)
                answer_source = "solution_pick"

        # **答案是数值但题目有选项**：部分老卷（如 1991）的 answer 块写的是
        # 选项的**值**而不是字母，而正确字母只出现在解析的「故选X」里。
        # 此时要用解析里的字母替换，并标成 derived_letter。
        # 判据：答案不是一个纯字母，且解析里能取到字母。
        if choices and answer and not re.fullmatch(r"[A-Ha-h]", answer.strip()):
            m_pick = PICK_INLINE.search(solution)
            if m_pick:
                answer = m_pick.group(1)
                answer_source = "derived_letter"

        body = body.replace("\\fillinblank{}", "______").replace("\\fillinblank", "______")
        body = ANSWER_INLINE.sub("", body)
        prompt = normalize_math(body)
        if len(prompt) < 8:
            continue
        section = SECTION.search(text[:start])
        records.append({
            "paper_id": paper["id"],
            "year": paper["year"],
            "paper_name": paper_name,
            "section": section.group(1) if section else "",
            "index": i + 1,
            "prompt": prompt,
            "choices": choices,
            "answer": answer,
            "answer_source": answer_source,
            "explanation": solution,
            "has_figure": has_figure,
            "source": "gaokao-tex",
            "source_license": LICENSE,
        })
    return records


LOCAL_REPO = ROOT / "build" / "dl" / "dxa_repo"


def fetch(paper: dict, retries: int = 4) -> list[dict]:
    """单个文件失败不能拖垮整批：重试 + 退避，最终失败就跳过并计数。

    优先读本地稀疏克隆（git clone --filter=blob:none --sparse + sparse-checkout content）。
    逐个 HTTP 拉 777 个文件会被代理限流，一个整包克隆反而更快更稳。
    """
    local_repo_file = LOCAL_REPO / paper["path"]
    if local_repo_file.is_file():
        try:
            return parse_file(local_repo_file.read_text(encoding="utf-8", errors="replace"), paper)
        except Exception as exc:
            FAILED.append((paper["path"], f"parse:{str(exc)[:50]}"))
            return []
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / paper["path"].replace("/", "__")
    if not local.exists():
        url = RAW + urllib.parse.quote(paper["path"])
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    local.write_bytes(resp.read())
                break
            except Exception as exc:  # 502/超时/连接重置都走这里
                if attempt == retries - 1:
                    FAILED.append((paper["path"], str(exc)[:60]))
                    return []
                time.sleep(1.5 * (attempt + 1))
    try:
        return parse_file(local.read_text(encoding="utf-8", errors="replace"), paper)
    except Exception as exc:
        FAILED.append((paper["path"], f"parse:{str(exc)[:50]}"))
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    tree = json.loads(TREE.read_text(encoding="utf-8"))["tree"]
    papers = []
    for node in tree:
        p = node["path"]
        if not (p.startswith("content/") and p.endswith(".tex")):
            continue
        parts = p.split("/")
        papers.append({"path": p, "year": parts[1],
                       "stem": parts[-1][:-4], "id": p[7:-4].replace("/", "-")})
    papers.sort(key=lambda x: (x["year"], x["stem"]))

    if args.limit and not args.all:
        # 跨年代抽样，确认老卷格式是否一致
        picks = [papers[0], papers[len(papers) // 2], papers[-1]]
        picks += [p for p in papers if p["year"] in ("1985", "2005", "2015")][:3]
        papers = picks
    elif not args.all:
        papers = papers[-8:]

    all_rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rows in pool.map(fetch, papers):
            all_rows.extend(rows)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_year = {}
    for r in all_rows:
        by_year[r["year"]] = by_year.get(r["year"], 0) + 1
    print(json.dumps({
        "papers": len(papers),
        "questions": len(all_rows),
        "with_answer": sum(1 for r in all_rows if r["answer"]),
        "with_solution": sum(1 for r in all_rows if r["explanation"]),
        "choice": sum(1 for r in all_rows if r["choices"]),
        "has_figure": sum(1 for r in all_rows if r["has_figure"]),
        "by_year": dict(sorted(by_year.items())),
        "failed_papers": len(FAILED),
        "failed_samples": FAILED[:5],
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
