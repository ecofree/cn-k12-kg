"""修复外部选择题里的空选项（默认只读，--apply 才写库）。

体检（tools/audit_ext_quality.py）发现 23313 道外部题里只有 17 道有空选项文本，
但**其中 6 道的正确答案正指着那个空选项**——2 道还是 cross-checked、当前就
被算作可诊断题。也就是说这两道题在诊断里会被问出来，学生看到的正确项是一片空白。

**为什么不直接删掉空选项了事**：
  · 答案指向空选项的 6 道，正确内容在源数据里就丢了，删了之后 answer 指向
    的是一个**不存在的选项**，比留着更糟；
  · 空选项只出现在干扰项上的 11 道，理论上一删就干净，但 3 选项的删完只剩
    2 项——猜测率 0.5，比四选一还差，不该留在诊断里。

处置（两件事一起做，都是「封存」而不是「瞎补」）：
  1. 17 道全部退出诊断：`usable_for_diagnosis=False` + `broken_reason` 写明原因。
     题目本身保留（题干/解析仍有参考价值），只是不再被抽中。
  2. 空选项在干扰项上、且删掉后仍剩 ≥3 项的，把这个空项删掉，
     让题目自身自洽（答案 id 不受影响）。

用法：
    python tools/repair_empty_options.py
    python tools/repair_empty_options.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "questions_ext.json"


def empty_opts(q: dict) -> list[str]:
    return [c.get("id") for c in (q.get("choices") or [])
            if not str(c.get("text", "")).strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ext = json.loads(EXT.read_text(encoding="utf-8"))
    # 第二轮起要能收敛：空干扰项被删掉后，这些题已经「没有空选项」，
    # 但它们仍然是封存状态——若 admission_level 还停在 cross-checked，
    # 「cross-checked ⟹ 可诊断」这条不变式就一直是破的。
    # 所以目标 = 有空选项的题 ∪ 已封存但等级没降到位的题。
    targets = [q for q in ext
               if q.get("type") == "choice"
               and (empty_opts(q)
                    or (q.get("broken_reason")
                        and q.get("admission_level") != "broken"))]

    print(f"外部选择题里有空选项的：{len(targets)} 道")
    stat = collections.Counter()
    dropped = 0
    for q in targets:
        empt = empty_opts(q)
        answer_in_empty = q.get("answer") in empt
        stat["答案指向空项" if answer_in_empty else "空项是干扰项"] += 1
        if q.get("usable_for_diagnosis"):
            stat["原本可诊断（将被封存）"] += 1

        if not args.apply:
            continue

        # 封存要连准入等级一起改：项目不变式是
        # 「admission_level ∈ {verified, cross-checked} ⟺ usable_for_diagnosis=True」
        # （tests/test_admission.py + tests/test_import_question_bank.py 双重看守）。
        # 只把 usable 置 False 而留着 cross-checked，会让这条不变式破掉。
        q["usable_for_diagnosis"] = False
        q["admission_level"] = "broken"
        q["usable_reason"] = "题目残缺，退出诊断（证据仍在，但不可用）"
        if empt:      # 已经删过空项的题沿用原封存理由，不覆盖
            q["broken_reason"] = ("正确答案指向空选项，答案内容在源数据里已丢失"
                                  if answer_in_empty else "存在空选项，题目残缺")
        # 只删干扰项上的空项，且删完仍要 ≥3 项
        if not answer_in_empty and len(q["choices"]) - len(empt) >= 3:
            q["choices"] = [c for c in q["choices"] if c.get("id") not in empt]
            dropped += 1

    for k, v in stat.items():
        print(f"  {k}: {v}")
    if not args.apply:
        print("\n（dry-run，未写入。加 --apply 落地）")
        return 0

    EXT.write_text(json.dumps(ext, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {EXT.relative_to(ROOT)}：封存 {len(targets)} 道，"
          f"删除空干扰项 {dropped} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
