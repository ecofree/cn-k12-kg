"""把解析好的上海中考题转成标准候选池格式。

字段对齐 inbox/candidates/cmm_*.jsonl，让既有导入闸门
（tools/import_question_bank.py）能直接吃。

**与 CMM 的关键差异**：
- answer 对选择题是字母（菁优网「故选：X」），对填空题是答案文本
- topicId 来自菁优网考点标签 → 人工映射表，**不是猜的**
- license 是「公开试卷整理，未声明许可」——比 CMM 的 BSD-3 宽松，
  所以入库门槛要更严（见 D29）
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from fix_superscript import restore  # noqa: E402
PARSED = ROOT / "build" / "question-bank" / "smse_parsed.jsonl"
OUT = ROOT / "inbox" / "candidates" / "smse_junior.jsonl"

# 许可状态（实测）：
#   仓库 README 写「该仓库仅作个人备份，资源来自网络」
#   index.md 写「仅作个人备份 资源来自网络」
#   仓库无 LICENSE 文件
#   解析块里每道题都带「菁优网版权所有」
# → **未声明许可**，比 CMM-Math 的 BSD-3 宽松得多。
#   处置：全部 usable_for_diagnosis=false，且不做任何对外分发。
LICENSE = "未声明许可：仓库自述「仅作个人备份，资源来自网络」，原始来源为菁优网（题内含「菁优网版权所有」），仓库无 LICENSE"
SOURCE_CHAIN = "Xnye/Smse（GitHub 个人备份） ← 菁优网（原始来源） ← 各地教研/出版社试卷"


SUP_FIXED = 0


def clean(text: str) -> str:
    """去掉题号前缀残留与多余空白，并还原丢失的上标。"""
    global SUP_FIXED
    text = re.sub(r"^\s*\d+\s*[．.]\s*[（(]\s*\d+\s*分\s*[）)]\s*", "", text)
    text = text.replace("　", " ").strip()
    text = re.sub(r"\s{2,}", " ", text)
    text, n = restore(text)
    SUP_FIXED += n
    return text


def main() -> None:
    rows = [json.loads(l) for l in PARSED.open(encoding="utf-8") if l.strip()]
    out = []
    for i, r in enumerate(rows):
        paper = r["paper"]
        # 卷名必须带年份目录：不同年份下可能有同名卷（"2013年…一模" 在多个年份目录里都有）
        qid = f"smse:{paper}:q{r['no']}"
        if r["option_count"] >= 3:
            choices = [{"id": f"o{j + 1}", "text": clean(o)} for j, o in enumerate(r["options"])]
            ans_idx = "ABCD".index(r["answer_letter"]) if r["answer_letter"] in "ABCD" else -1
            answer = f"o{ans_idx + 1}" if ans_idx >= 0 else ""
            qtype = "choice"
        else:
            choices = []
            answer = clean(r["fill_answer"])
            qtype = "numeric" if re.fullmatch(r"[﹣\-]?[\d.]+", answer) else "short"
        out.append({
            "source": "smse-shanghai-zhongkao",
            "source_id": qid,
            "stage": "junior",
            "grade_label": "九年级",
            "type": qtype,
            "prompt": clean(r["prompt"]),
            "choices": choices,
            "answer": answer,
            "explanation": (r.get("analysis") or "").strip(),
            "kaodian": r.get("kaodian", ""),
            "paper": paper,
            "year_dir": paper.split("/")[0],
            "points": r.get("points"),
            "license": LICENSE,
            "source_chain": SOURCE_CHAIN,
            "topicId": r["topicId"],
            "needs_kc": False,   # 已有标签映射，不需要再靠规则猜
            "review_status": "tag-mapped",
        })

    with OUT.open("w", encoding="utf-8") as f:
        for x in out:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    import collections
    print(json.dumps({
        "out": str(OUT.relative_to(ROOT)),
        "total": len(out),
        "superscript_restored": SUP_FIXED,
        "by_type": dict(collections.Counter(x["type"] for x in out)),
        "topics_covered": len({x["topicId"] for x in out}),
        "no_answer": sum(1 for x in out if not x["answer"]),
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
