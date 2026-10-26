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
    DIAGNOSABLE, LEVEL_RANK, grade, stem_hash)

DATA = ROOT / "data"


def load() -> list[dict]:
    return json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))


def cross_source_hashes(questions: list[dict]) -> set[str]:
    """同一归一化题干出现在 ≥2 个来源 → 可做跨源互证。

    只算"不同 source"，同源重复（同一份数据被导入两次）不算独立证据。
    """
    seen: dict[str, set[str]] = collections.defaultdict(set)
    for q in questions:
        seen[stem_hash(q.get("prompt") or "")].add(q.get("source") or "")
    return {h for h, srcs in seen.items() if len(srcs) > 1}


def run(questions: list[dict]) -> tuple[list[dict], dict]:
    cross = cross_source_hashes(questions)
    levels = collections.Counter()
    by_source = collections.defaultdict(collections.Counter)
    flagged = []
    for q in questions:
        # 已封存的题也要重判一次：第一次封存时可能只写了等级没写原因，
        # 跳过重判会让原因永远补不上（实测就漏了 6 道）。
        # 判定是幂等的：结构残缺的题无论判几次都是 broken。
        h = stem_hash(q.get("prompt") or "")
        g = grade(q, cross_source=h in cross)
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
                     and q.get("evidence_level") in DIAGNOSABLE)
            for src in by_source
        },
        "flagged": flagged,
        "cross_source_groups": len(cross),
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
        lv = q.get("evidence_level")
        if lv not in DIAGNOSABLE:
            continue
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
