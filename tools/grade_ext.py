"""给外部题做证据分级，并把结论写回数据。

## 它解决什么问题

`admission_level = cross-checked` 描述的是导入时的动作（"原卷答案与解析互相印证"），
不是题目现在的证据强度。实测：smse 763 道里，答案文本真的出现在解析里的只有 57 道。

这个工具用 `cnkg/evidence.py` 把每道题重新判一遍，写入 `evidence_level`，
并给出两条互不干扰的门槛：

    usable_for_diagnosis  能不能进诊断（导入时定的，看归属与许可）
    evidence_level        答案有多少证据（本次算的，看解析与复算）

两者同时满足才进诊断。这样"来源可信"和"这道题可信"不再混为一谈。

## 用法

    python tools/grade_ext.py                 # 只报告，不写盘
    python tools/grade_ext.py --apply         # 写回 data/questions_ext.json
    python tools/grade_ext.py --json build/ext_evidence.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.evidence import (  # noqa: E402
    DIAGNOSABLE, LEVEL_RANK, answer_fingerprint, canonical_answer, grade,
    item_fingerprint, question_policy, source_lineage, structural_defects)

DATA = ROOT / "data"


def load() -> list[dict]:
    return json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))


def resolved_lineages(questions: list[dict]) -> list[set[str]]:
    """把已登记的转载链展开；链中共同祖先与循环均不能制造独立来源。"""
    direct = [source_lineage(q) for q in questions]
    by_origin: dict[str, set[str]] = collections.defaultdict(set)
    for q, roots in zip(questions, direct):
        if roots:
            by_origin[q["provenance"]["origin_id"].strip()].update(roots)
    resolved = []
    for roots in direct:
        seen, stack = set(), list(roots)
        while stack:
            origin = stack.pop()
            if origin in seen:
                continue
            seen.add(origin)
            stack.extend(by_origin.get(origin, set()) - seen)
        resolved.append(seen)
    return resolved


def corroboration_records(questions: list[dict]) -> list[dict]:
    """比对完整题目与答案，按明确来源根排除转载，返回逐题可追溯记录。

    source 仅是导入器/数据集名字。只有附带 origin_id、来源 URL 且谱系
    不相交的答案来源才算独立。同题任何答案冲突都进入隔离，不能多数表决。
    """
    groups: dict[str, list[int]] = collections.defaultdict(list)
    lineages = resolved_lineages(questions)
    for index, q in enumerate(questions):
        if not structural_defects(q):
            groups[item_fingerprint(q)].append(index)
    records = []
    for index, q in enumerate(questions):
        fingerprint = item_fingerprint(q)
        members = groups.get(fingerprint, [])
        answers: dict[str, list[str]] = collections.defaultdict(list)
        for other in members:
            answers[canonical_answer(questions[other])].append(str(questions[other].get("id", "")))
        lineage = lineages[index]
        peers = [questions[other] for other in members if other != index
                 and questions[other].get("id") != q.get("id")
                 and lineage and lineages[other]
                 and not lineage.intersection(lineages[other])]
        record = {
            "schema": 1, "status": "no_independent_match",
            "item_fingerprint": fingerprint, "answer_fingerprint": answer_fingerprint(q),
            "peer_question_ids": [], "origin_ids": [],
            "reason": "尚无题干、选项、答案一致且来源谱系独立的第二份记录",
        }
        if len(answers) > 1 and index in members:
            record.update(
                status="conflict",
                peer_question_ids=sorted({str(questions[other].get("id", "")) for other in members
                                          if other != index}),
                conflicting_answers=[{"answer": answer, "question_ids": sorted(set(ids))}
                                     for answer, ids in sorted(answers.items())],
                reason="相同题干、类型和选项存在不同答案，须核对原始来源后处理",
            )
        elif peers:
            record.update(
                status="corroborated",
                peer_question_ids=sorted({str(peer["id"]) for peer in peers}),
                origin_ids=sorted({item["provenance"]["origin_id"].strip() for item in [q, *peers]}),
                reason="题干、选项和答案一致，原始来源及转载谱系无交集",
            )
        elif not lineage or (len(members) > 1 and not any(
                lineages[other] for other in members if other != index)):
            record.update(
                status="insufficient_provenance",
                reason="缺少可追溯的原始来源ID或来源URL；不同数据集名称不代表独立来源",
            )
        records.append(record)
    return records


def cross_source_hashes(questions: list[dict]) -> set[str]:
    """兼容报告入口：返回已核对的完整题目指纹，不再只比较题干散列。"""
    return {row["item_fingerprint"] for row in corroboration_records(questions)
            if row["status"] == "corroborated"}


def run(questions: list[dict]) -> tuple[list[dict], dict]:
    corroborations = corroboration_records(questions)
    cross = {row["item_fingerprint"] for row in corroborations if row["status"] == "corroborated"}
    levels = collections.Counter()
    by_source = collections.defaultdict(collections.Counter)
    flagged = []
    for q, corroboration in zip(questions, corroborations):
        # 已封存的题也要重判一次：第一次封存时可能只写了等级没写原因，
        # 跳过重判会让原因永远补不上（实测就漏了 6 道）。
        # 判定是幂等的：结构残缺的题无论判几次都是 broken。
        g = grade(q, corroboration=corroboration)
        q["cross_source_evidence"] = corroboration
        if corroboration["status"] == "conflict":
            g = {"level": "defective", "signals": {**g["signals"], "cross_source": False},
                 "notes": [corroboration["reason"]]}
        elif corroboration["status"] == "insufficient_provenance":
            g["notes"].append(corroboration["reason"])
        q["evidence_level"] = g["level"]
        q["evidence_signals"] = g["signals"]
        q["evidence_notes"] = g["notes"]
        # 证据等级和准入是两条正交的线，但**结构残缺/确证矛盾**是例外：
        # 这种题不是"证据少"，是"这道题坏了"，不该继续标成可诊断。
        # 只降不升：工具不会把任何题改回可用。
        # 只降**本来可用**的题。储备题（tag-mapped / 无答案）的
        # admission_level 记录的是"它当初怎么进来的"，不该被内容判定改写；
        # 它们照旧标 evidence_level，由加载层按证据过滤。
        # 不加这一条，4045 道"无答案"的 gaokao 会被改成 broken，
        # 把"封存"这个动作稀释成噪音（tests/test_ext_content_gates.py 有守卫）。
        if g["level"] in ("broken", "defective") and q.get("usable_for_diagnosis"):
            q["usable_for_diagnosis"] = False
            q["admission_level"] = "broken"
            q["usable_reason"] = "证据分级发现结构残缺或解析与答案矛盾，退出诊断"
            # 项目既有约定：封存的题必须写明原因。
            # 只打标记不写原因，半年后没人知道它为什么被关掉。
            q["broken_reason"] = "；".join(g["notes"])
        levels[g["level"]] += 1
        by_source[q.get("source")][g["level"]] += 1
        if g["level"] in ("defective", "broken"):
            flagged.append({"id": q.get("id"), "source": q.get("source"),
                            "level": g["level"], "notes": g["notes"],
                            "prompt": (q.get("prompt") or "")[:120]})
    report = {
        "total": len(questions),
        "levels": dict(levels),
        "by_source": {k: dict(v) for k, v in sorted(by_source.items())},
        # 这个必须单独算：by_source 统计的是全部题，
        # 而"可进诊断"要求 准入 ∧ 证据 同时成立。
        # 两者混用会把 12079 道 cmm 读成 7705 道可诊断（实际 1820）。
        "servable_by_source": {
            src: sum(1 for q in questions
                     if q.get("source") == src
                     and q.get("usable_for_diagnosis")
                     and question_policy(q)["diagnosis_eligible"])
            for src in by_source
        },
        "flagged": flagged,
        "cross_source_groups": len(cross),
        "cross_source_statuses": dict(collections.Counter(row["status"] for row in corroborations)),
    }
    return questions, report


_STAGE_RE = None


def stage_of_topic(tid: str) -> str:
    """按知识点 id 判学段：m1a–m6b 小学，m7a–m9b 初中，mh* 高中。

    不用 `mapping.stage`——smse 那份映射的 stage 字段是空的，
    按它统计会让初中整段消失。
    """
    import re
    m = re.match(r"^(m(?:[1-9][ab]|h[1-5]))\.", tid or "")
    if not m:
        return "?"
    key = m.group(1)
    if key.startswith("mh"):
        return "senior"
    return "primary" if int(key[1]) <= 6 else "junior"


def serve_stats(questions: list[dict]) -> dict:
    """按新门槛算"真正进得了诊断"的题量。"""
    out = {}
    for q in questions:
        if not q.get("usable_for_diagnosis"):
            continue
        policy = question_policy(q)
        if not policy["diagnosis_eligible"]:
            continue
        lv = policy["evidence_level"]
        stage = stage_of_topic(q.get("primaryTopicId") or q.get("topicId"))
        out.setdefault(stage, collections.Counter())[lv] += 1
    return {k: dict(v) for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", default="build/ext_evidence.json")
    args = ap.parse_args()

    questions = load()
    questions, report = run(questions)
    report["servable_by_stage"] = serve_stats(questions)

    path = ROOT / args.json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")

    print(f"外部题 {report['total']} 道，跨源同题组 {report['cross_source_groups']}")
    for lv, n in sorted(report["levels"].items(), key=lambda kv: -kv[1]):
        print(f"  {lv:<13}{n:6}  {n / report['total']:.1%}")
    print("\n按来源：")
    for src, c in report["by_source"].items():
        tot = sum(c.values())
        diag = report["servable_by_source"].get(src, 0)
        print(f"  {src:<26} 总 {tot:5}  进诊断 {diag:5} ({diag / tot:.0%})")
        print(f"      证据分布 {c}")
    if report["flagged"]:
        print(f"\n确证问题 {len(report['flagged'])} 道（需人工确认）：")
        for f in report["flagged"][:10]:
            print(f"  {f['id']} {f['level']} {f['notes']} :: {f['prompt'][:60]}")
    print(f"\n报告写入 {path}")

    if args.apply:
        bak = DATA / "questions_ext.json.bak-evidence"
        if not bak.exists():
            shutil.copy2(DATA / "questions_ext.json", bak)
            print(f"已备份 {bak.name}")
        (DATA / "questions_ext.json").write_text(
            json.dumps(questions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("已写回 data/questions_ext.json")
    else:
        print("（未写盘；加 --apply 生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
