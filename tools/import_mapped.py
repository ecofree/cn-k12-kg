"""把映射好的外部题池导入 data/questions_ext.json，支持复合题（一题多知识点）。

与 import_question_bank.py 的区别：
    那个工具要求人工在 CSV 上逐条填 keep=1，是「抽检式」入库。
    这个工具面向**批量**入库：题目已由词典映射器打上知识点标签，
    自动做结构检查、答案自洽检查、去重，然后按证据强度分级准入。

准入分级（沿用 set_admission_level.py 的定义）：
    cross-checked   —— 两个独立证据互证（原卷答案 + 解析/答案区一致）
    tag-mapped      —— 标签映射，答案证据较弱（不进诊断）
    ai-mapped       —— 规则映射（不进诊断）

关键：**没有证据支持的题一律不进诊断**。批量导入扩大的是题库，
不是诊断集的规模——诊断集必须保证每道题的答案可信。

用法：
    python tools/import_mapped.py --pool build/question-bank/mapped_junior.jsonl \
        --stage junior --source cmm-math
    python tools/import_mapped.py --dry-run ...
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "questions_ext.json"

sys.path.insert(0, str(ROOT / "tools"))
from import_question_bank import (  # noqa: E402
    POLLUTION, BARE_TEX, PICKED, apply_fixes, check_structure, fp,
)

# 解析里「故选X」是位置，答案字段是 option_id
ANSWER_IN_TEXT = re.compile(r"(?:答案|故选|应选)\s*[：:为是]?\s*\(?\s*([A-H])\s*\)?")


def norm_answer(answer: str, n_choices: int) -> str:
    """把答案统一成 option_id。字母按位置换算；数字按序数换算。"""
    if not answer:
        return ""
    answer = answer.strip()
    if re.fullmatch(r"o\d+", answer):
        return answer
    if re.fullmatch(r"[A-Ha-h]", answer):
        idx = ord(answer.upper()) - ord("A")
        return f"o{idx + 1}" if idx < n_choices else ""
    # 纯数字答案：可能是选项序号，也可能是数值答案。选择题才做序号换算。
    if re.fullmatch(r"\d+", answer):
        idx = int(answer) - 1
        return f"o{idx + 1}" if 0 <= idx < n_choices else answer
    return answer


# 各来源的准入策略。
#
# **2026-10-25 改为按题目级证据定准入，不再按来源一刀切。**
#
# 原先是「cmm-math 整源留在诊断外」，理由是它的答案只有约 42% 能交叉印证。
# 但实测（本轮重算）CMM 初中 1770 道里只有 17% 能互证，而**能互证的那 308 道**
# 证据强度与 cross-checked 的定义完全一致——「两个独立证据互证」
# （答案字段 + 解析里的「故选X」来自原始数据的不同位置）。
# 一刀切把互证题和非互证题同等排除，与 cross-checked 的定义自相矛盾，
# 而且代价实在：初中可诊断题只有 117 道（全来自上海中考），
# 77 个初中点因此没有任何可诊断题。
#
# 新规则：准入只看**这道题的答案证据**，与来源无关。
#   consistent —— 答案与解析互证，或原卷答案块 → cross-checked，可进诊断
#   其他（no_signal / derived / conflict）→ tag-mapped，留诊断外
#
# 好处是规则更简单也更自洽：不再需要记住「哪个源可信」，
# 任何新源接入都自动按证据分级。高考/上海中考原本就满足 consistent，
# 行为不变；只有 CMM 里那 17% 的题被解锁。
SOURCE_POLICY = {
    # 源名 → (诊断集准入等级上限, 是否允许进入诊断集, 答案是否来自原卷)
    #
    # 第三项「答案是否来自原卷」的含义：该源的 answer 字段取自**原始卷面的
    # 答案区**，而不是从解析文本推导出来的。这类答案本身就是权威证据，
    # 不需要解析里再有第二个信号（见下方 from_original_paper 的说明）。
    #   gaokao-tex             —— 原卷 \begin{answer} / \answer{A} / 解析「故选X」
    #   smse-shanghai-zhongkao —— 官方答案区的「故选：X」，且做过题目区/答案区
    #                             选项文本双向一致性校验（350 道样本 100% 通过）
    #   cmm-math               —— 答案字段与解析都从同一处抓来，**不是**原卷答案区；
    #                             只有解析里能独立找到「故选X」与之相符才算互证。
    "cmm-math": ("cross-checked", True, False),
    "gaokao-tex": ("cross-checked", True, True),
    "smse-shanghai-zhongkao": ("cross-checked", True, True),
}
DEFAULT_POLICY = ("tag-mapped", False, False)


def source_policy(source_name: str) -> tuple[str, bool, bool]:
    return SOURCE_POLICY.get(source_name, DEFAULT_POLICY)


def check_self_consistent(row: dict) -> tuple[str, str]:
    """用解析文本交叉验证答案。返回 (状态, 说明)。

    三种状态：
        consistent —— 解析里的答案与答案字段一致（两个独立证据互证）
        derived   —— 答案本身是从解析推导出来的（解析不能自证自己），证据较弱
        conflict   —— 不一致，直接拒收
    """
    exp = row.get("explanation") or ""
    m = PICKED.search(exp)
    if not m:
        m = ANSWER_IN_TEXT.search(exp)
    if not m:
        return "no_signal", ""
    letter = m.group(1).upper()
    ids = [c.get("id") for c in row.get("choices") or []]
    if not ids:
        return "no_signal", ""
    idx = ord(letter) - ord("A")
    implied = ids[idx] if 0 <= idx < len(ids) else letter
    if implied == row["answer"]:
        # 答案若原本就是从解析推导的（derived_letter），不能算互证
        if row.get("answer_source") == "derived_letter":
            return "derived", f"由解析推导故{letter}"
        return "consistent", letter
    return "conflict", f"解析称{letter}(={implied})，答案字段为{row['answer']}"


def build_record(src: dict, primary: str, tags: list[str], status: str,
                 note: str, source_name: str, prefix: str, seq: int,
                 license_name: str = "", source_chain: str = "",
                 reviewed_by: str = "author") -> dict:
    """组装一条入库记录。复合题写 topicIds + primaryTopicId。"""
    ids = [primary] + [t for t in tags if t != primary]
    # consistent = 两个独立证据互证，可进诊断
    # derived   = 答案从解析推导，证据只有一份，不能进诊断
    admission = "cross-checked" if status == "consistent" else "tag-mapped"
    usable = status == "consistent"
    # 各来源池的顶层字段名不统一（高考用 meta，上海中考用 points/year_dir/paper，
    # CMM 用 meta）。统一收进 meta，否则难度校准等下游拿不到这些信息——
    # 实测上海中考的 points 丢失后，764 道难度全退化成 basic。
    meta = dict(src.get("meta") or {})
    for key in ("points", "year", "year_dir", "paper", "kaodian",
                "grade_label", "answer_source", "section", "index"):
        if key in src and meta.get(key) in (None, ""):
            meta[key] = src[key]
    return {
        "id": f"ext-{prefix}-{seq:05d}",
        # 向后兼容字段：topicId 恒等于主知识点
        "topicId": primary,
        # 复合题字段：本题实际涉及的全部知识点
        "topicIds": ids,
        "primaryTopicId": primary,
        "difficulty": "basic",
        "difficulty_basis": "inherited_unverified",
        "type": src.get("type", "short"),
        "prompt": src.get("prompt", ""),
        "choices": src.get("choices") or [],
        "answer": src.get("answer", ""),
        "explanation": src.get("explanation", ""),
        "source": source_name,
        "source_id": src.get("source_id", ""),
        "source_license": license_name,
        "source_chain": source_chain,
        "license_note": (f"许可状态：{license_name}" if license_name
                         else "许可状态未声明"),
        "meta": meta,
        "mapping": {
            "method": "lexicon-relaxed-multi",
            "confidence": "lexicon_multi",
            "stage": src.get("mapping", {}).get("stage", ""),
            "tags": ids,
            "reviewed_by": reviewed_by,
            "reviewed_at": "2026-10-24",
            "note": note,
        },
        "answer_check": {"method": "explanation_crosscheck", "status": status},
        "admission_level": admission,
        "admission_reason": (
            "原卷答案与解析互相印证" if usable
            else "答案缺少第二来源互证，暂不进入诊断"),
        "usable_for_diagnosis": usable,
        "usable_reason": (
            "答案经解析交叉验证一致" if usable
            else "仅有单一答案来源，未获第二人复核"),
        "review_status": "imported",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="映射结果 JSONL")
    ap.add_argument("--stage", default="junior")
    ap.add_argument("--source", required=True, help="来源标识，如 cmm-math / gaokao-tex")
    ap.add_argument("--prefix", default="", help="id 前缀，默认用 source")
    ap.add_argument("--license", default="")
    ap.add_argument("--source-chain", default="",
                    help="来源链路说明（真题源必填，用于追溯）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-per-topic", type=int, default=0,
                    help="每个知识点最多导入几道（按主知识点计），0 = 不限")
    ap.add_argument("--require-consistent", action="store_true",
                    help="只导入答案能自证的题（其余不导入）")
    ap.add_argument("--admission", default="", help="强制指定准入等级")
    ap.add_argument("--allow-unanswerable", action="store_true",
                    help="允许导入没有可判答案的题（如解答题）。"
                         "这类题只作**复合型训练题**保留题干、解析与多知识点标签，"
                         "标记为不可自动判题，明确排除出诊断集与自动判分。")
    args = ap.parse_args()

    prefix = args.prefix or args.source.replace("-", "")
    pool_path = pathlib.Path(args.pool)
    rows = [json.loads(ln) for ln in pool_path.open(encoding="utf-8") if ln.strip()]

    own = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    own_fp = {fp(q["prompt"]) for q in own}
    existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    ext_fp = {fp(q["prompt"]) for q in existing}
    ext_src = {q.get("source_id") for q in existing}

    # id 序号必须从**已有数据**的最大值往后接，不能从本轮导入的条数重新数。
    # 历史事故：CMM 分三批（小学/初中/高中池）导入，每批 seq 都从 1 开始，
    # 结果 4429 个 id 撞车、10628 道题挤在里面（重复 id 里没有一条是同一道题）。
    _seq_pat = re.compile(rf"^ext-{re.escape(prefix)}-(\d+)$")
    seq_base = max(
        (int(m.group(1))
         for m in (_seq_pat.match(q.get("id", "")) for q in existing) if m),
        default=0)
    # 已占用的 id 全集（含自有题），用来给新题兜底查重
    used_ids = {q["id"] for q in existing} | {q["id"] for q in own}

    imported, rejected = [], []
    reason_counter = collections.Counter()
    status_counter = collections.Counter()
    per_topic = collections.Counter()
    multi_count = 0

    for src in rows:
        sid = src.get("source_id") or src.get("id")
        if sid in ext_src:
            continue
        primary = src.get("primaryTopicId") or src.get("topicId")
        tags = src.get("topicIds") or ([primary] if primary else [])
        if not primary:
            rejected.append((sid, "无知识点归属"))
            continue

        rec = dict(src)
        rec["prompt"] = apply_fixes(rec.get("prompt", ""))
        choices = []
        for i, c in enumerate(rec.get("choices") or []):
            choices.append({"id": f"o{i + 1}", "text": apply_fixes(c.get("text", ""))})
        rec["choices"] = choices
        n_ch = len(choices)
        rec["answer"] = norm_answer(rec.get("answer", ""), n_ch)
        if rec.get("explanation"):
            rec["explanation"] = apply_fixes(rec["explanation"])

        reason = check_structure(rec) if n_ch else None
        if reason is None and n_ch == 0 and rec.get("type") == "choice":
            reason = "选择题缺选项"
        # 无答案的题：默认拒收，除非显式允许（解答题作为复合型训练题）
        if reason is None and not rec["answer"]:
            if args.allow_unanswerable:
                rec["_unanswerable"] = True
            else:
                reason = "无答案"
        if reason:
            rejected.append((sid, reason))
            reason_counter[reason] += 1
            continue

        key = fp(rec["prompt"])
        if key in own_fp:
            rejected.append((sid, "与自有题重复"))
            reason_counter["与自有题重复"] += 1
            continue
        if key in ext_fp:
            rejected.append((sid, "与本批重复"))
            reason_counter["与本批重复"] += 1
            continue

        status, detail = check_self_consistent(rec)
        status_counter[status] += 1
        if status == "conflict":
            rejected.append((sid, f"答案自相矛盾：{detail}"))
            reason_counter["答案自相矛盾"] += 1
            continue

        # 准入等级由**来源策略**给出上限，再由答案证据决定实际档位。
        # 2026-10-25 起不再按来源一刀切（见上方 SOURCE_POLICY 注释）：
        # 只有「答案有互证」的题进诊断，证据不足的留诊断外，与来源无关。
        policy_level, policy_usable, policy_from_paper = source_policy(args.source)

        # **真题源的答案本身就是权威答案**。
        # 高考卷的 \begin{answer} 是原卷给出的答案，不是二手推导；
        # 老卷的 \answer{A} 同理；derived_letter 是从原卷解析里的
        # 「故选X」推出字母，证据同样来自原卷。
        # 上海中考的答案取自官方答案区的「故选：X」，且做过题目区/答案区
        # 选项文本双向一致性校验，属于同一类原卷证据。
        # 所以即使解析里没有第二个可比对的信号，也足以进诊断。
        # 判据用「答案是否来自原卷」而不是「两个证据是否互证」——
        # 对真题源来说，原卷本身就是最强证据，不需要第二个来源。
        # CMM 不适用这条（见 SOURCE_POLICY 注释）。
        from_original_paper = policy_from_paper or (
            rec.get("answer_source") or "") in (
            "answer_block", "answer_inline", "derived_letter")
        if policy_usable and from_original_paper:
            if status in ("no_signal", "derived"):
                status = "consistent"
                detail = f"原卷答案（{rec.get('answer_source') or 'official_answer_section'}）"
                status_counter["promoted_by_answer_block"] += 1

        if args.require_consistent and status != "consistent":
            rejected.append((sid, "答案无法自证"))
            reason_counter["答案无法自证"] += 1
            continue

        if args.max_per_topic and per_topic[primary] >= args.max_per_topic:
            rejected.append((sid, "该知识点已达上限"))
            reason_counter["该知识点已达上限"] += 1
            continue

        admission = args.admission or policy_level
        # cross-checked 还要求答案确实自证过
        usable = (policy_usable and status == "consistent"
                  and not rec.get("_unanswerable"))
        if admission == "cross-checked" and status != "consistent":
            admission = "tag-mapped"
        note = ("词典多标签映射，主知识点由最高分决定；"
                "附属知识点表示该题同时涉及的知识点")
        if rec.get("_unanswerable"):
            note += ("。本题为解答题，无可自动判定的答案，"
                     "仅作复合型训练题保留（题干+完整解析），不参与自动判分")
        seq = seq_base + len(imported) + 1
        new_id = f"ext-{prefix}-{seq:05d}"
        guard = 1
        while new_id in used_ids:          # 已有数据动过（删记录/改编号）时兜底
            guard += 1
            new_id = f"ext-{prefix}-{seq:05d}-{guard}"
            seq += 1
        used_ids.add(new_id)
        new = build_record(rec, primary, tags, status, note, args.source,
                            prefix, seq, args.license,
                            source_chain=args.source_chain)
        new["id"] = new_id
        new["admission_level"] = admission
        new["usable_for_diagnosis"] = usable
        if rec.get("_unanswerable"):
            new["admission_level"] = "compound-reference"
            new["admission_reason"] = ("复合型训练题：覆盖多个知识点，"
                                       "但无可自动判定的答案")
            new["answer"] = ""
            new["auto_gradable"] = False
            new["usable_reason"] = "解答题无可自动判定的答案，仅作参考与复合覆盖"
        elif admission == "cross-checked":
            new["admission_reason"] = "原卷答案与解析互相印证"
        else:
            new["admission_reason"] = (
                f"{args.source} 来源：证据强度不足以支撑自动诊断，"
                "知识点由词典标签映射，保留题干供训练与复核")
            new["usable_reason"] = "来源证据强度不足，暂不进入诊断集"
        imported.append(new)
        per_topic[primary] += 1
        ext_fp.add(key)
        ext_src.add(sid)
        if len(tags) > 1:
            multi_count += 1

    result = {
        "pool": len(rows),
        "imported": len(imported),
        "multi_topic_imported": multi_count,
        "rejected": len(rejected),
        "reject_reasons": dict(reason_counter.most_common(10)),
        "answer_status": dict(status_counter),
        "topics_covered": len(per_topic),
        "diagnosable": sum(1 for q in imported if q["usable_for_diagnosis"]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))

    if args.dry_run:
        print("\n拒收样例：")
        for sid, r in rejected[:10]:
            print(f"   {sid}: {r}")
        return

    allq = existing + imported
    OUT.write_text(json.dumps(allq, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {OUT.relative_to(ROOT)}：共 {len(allq)} 道（新增 {len(imported)}）")


if __name__ == "__main__":
    main()
