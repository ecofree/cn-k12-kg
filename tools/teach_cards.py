"""讲解卡校验与覆盖率统计。

## 为什么需要它

项目 477 个知识点此前只有一句 `description`。题库能测「会不会」，
但测不出「为什么」——用户要的是「每个知识点讲清楚，再做够题」。
讲解卡是把这件事变成可检查的数据：定义、步骤、例题、易错点、过关标准。

## 三层检查

1. **引用完整**：练习题 id 必须真实存在，且确实挂在这个知识点上。
   否则卡片会把学生导向它没学过的题。
2. **例题可验算**：`worked_example.verify` 必须能算出 `answer`。
   讲解里的例题自己错，比题目错更糟——学生照着错的学。
3. **够不够教**：步骤 ≥3、易错点 ≥1、练习题 ≥2 且至少一道非选择题。
   选择题占比高时，孩子"会选"不等于"会做"。
4. **讲不讲原理、会不会认**：`why` 要说清"为什么这样做"（不是重复步骤），
   `when_to_use` 要给 2–4 条可观察的识别信号。
   只有步骤的卡，学生会算这道题、不会认下一道——这正是用户说的
   "在做题过程中掌握如何运用"缺的那一环。

## 用法

    python tools/teach_cards.py --check
    python tools/teach_cards.py --check --json build/teach_coverage.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cnkg.evidence import safe_eval  # noqa: E402
from cnkg.verify import compare, to_number  # noqa: E402

DATA = ROOT / "data"
TEACH = DATA / "teach"
STAGES = ("primary", "junior", "senior")


def check_objective_coverage(card: dict, topic: dict) -> list[str]:
    """核对本项目已登记的目标闭环，不把结构检查当教师审核。"""
    goals = topic.get("mastery_criteria") or []
    rows = card.get("objective_coverage")
    if not isinstance(rows, list) or not rows:
        return ["缺 objective_coverage：每个已登记目标需有专属讲解和自检"]
    errors, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("objective_coverage 每项必须为对象")
            continue
        idx = row.get("criterion_index")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(goals):
            errors.append("objective_coverage 引用无效目标索引")
            continue
        if idx in seen:
            errors.append(f"objective_coverage 目标 {idx} 重复")
        seen.add(idx)
        if row.get("title") != goals[idx]:
            errors.append(f"objective_coverage 目标 {idx} 标题与知识点不一致，需重新核对")
        if not isinstance(row.get("explanation"), str) or len(row["explanation"].strip()) < 10:
            errors.append(f"objective_coverage 目标 {idx} 缺少具体讲解")
        task = row.get("self_check")
        if not isinstance(task, dict):
            errors.append(f"objective_coverage 目标 {idx} 缺自检任务")
            continue
        for key in ("prompt", "reference_answer"):
            value = task.get(key)
            if not isinstance(value, str) or not value.strip() or value.strip() in ("略", "答案略", "待补充", "TODO"):
                errors.append(f"objective_coverage 目标 {idx} 缺 {key}")
        rubric = task.get("criteria")
        if not isinstance(rubric, list) or not rubric or any(not isinstance(v, str) or not v.strip() for v in rubric):
            errors.append(f"objective_coverage 目标 {idx} 缺可观察评价标准")
    if seen != set(range(len(goals))):
        errors.append(f"objective_coverage 未覆盖目标 {sorted(set(range(len(goals))) - seen)}")
    return errors


def stage_of(topic: dict) -> str:
    g = topic.get("grade")
    if g is None:
        return "?"
    return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")


def check_card(card: dict, topics: dict, bank: dict) -> list[str]:
    errs = []
    tid = card.get("topicId")
    if tid not in topics:
        return [f"知识点不存在：{tid}"]

    for key, minlen in (("hook", 10), ("definition", 20), ("check", 10)):
        v = (card.get(key) or "").strip()
        if len(v) < minlen:
            errs.append(f"{key} 太短（{len(v)} 字）")
    why = (card.get("why") or "").strip()
    if len(why) < 30:
        errs.append(f"why 太短（{len(why)} 字），要说清为什么这样做")
    wtu = card.get("when_to_use") or []
    if not 2 <= len(wtu) <= 4:
        errs.append(f"when_to_use 应 2–4 条，实际 {len(wtu)} 条")
    else:
        for i, x in enumerate(wtu):
            if len((x or "").strip()) < 8:
                errs.append(f"when_to_use 第 {i + 1} 条太短")
    if len(card.get("steps") or []) < 3:
        errs.append("讲解步骤少于 3 步")
    if not card.get("pitfalls"):
        errs.append("缺易错点")

    ex = card.get("worked_example") or {}
    if not (ex.get("prompt") or "").strip():
        errs.append("例题缺题干")
    if len(ex.get("steps") or []) < 2:
        errs.append("例题讲解少于 2 步")
    if not str(ex.get("answer") or "").strip():
        errs.append("例题缺参考结论")
    if to_number(ex.get("answer")) is not None and not ex.get("verify"):
        errs.append("数值例题缺 verify，无法复算")
    if ex.get("verify"):
        got = safe_eval(ex["verify"])
        want = to_number(ex.get("answer"))
        if got is None:
            errs.append(f"例题 verify 无法求值：{ex['verify']}")
        elif want is None:
            errs.append("例题 answer 不是数值，但给了 verify")
        elif not compare(ex.get("answer"), got):
            errs.append(f"例题 verify={ex['verify']} 得 {got}，与 answer={want} 不符")

    if card.get("tier") == "core" or "objective_coverage" in card:
        errs.extend(check_objective_coverage(card, topics[tid]))
    if card.get("diagram"):
        from cnkg.learning import _check_diagram
        errs.extend(_check_diagram(card["diagram"], "diagram"))

    practice = card.get("practice") or []
    if len(practice) < 2:
        errs.append("练习题少于 2 道")
    types = set()
    for p in practice:
        pid = p.get("id")
        q = bank.get(pid)
        if q is None:
            errs.append(f"练习题不存在：{pid}")
            continue
        if tid not in (q.get("topicIds") or [q.get("topicId")]):
            errs.append(f"练习题 {pid} 不属于 {tid}")
        types.add(q.get("type"))
    if practice and not (types - {"choice"}):
        errs.append("练习题全是选择题，缺少需要自己算的题型")
    return errs


def check_brief(card: dict, topics: dict, tier_data: dict,
                bank: dict | None = None) -> list[str]:
    """校验同簇同册的辅助参考卡；core 可以引用，但不能冒充完整讲解。

`covers` 只表示本卡的参考范围，不证明全部目标已经被讲清或测量。
"""
    errs = []
    if card.get("reference_only") is not True:
        errs.append("速查卡必须标记 reference_only=true，不能冒充完整讲解")
    cid = card.get("cluster")
    vol = card.get("volume")
    if cid not in (tier_data.get("clusters") or {}):
        errs.append(f"簇不存在：{cid}")
    covers = card.get("covers") or []
    if not covers:
        errs.append("没有覆盖任何知识点")
    for tid in covers:
        t = topics.get(tid)
        if t is None:
            errs.append(f"覆盖的知识点不存在：{tid}")
            continue
        info = (tier_data.get("topics") or {}).get(tid) or {}
        if info.get("cluster") != cid:
            errs.append(f"{tid} 属于 {info.get('cluster')}，与卡片的 {cid} 不符")
        if t.get("volume") != vol:
            errs.append(f"{tid} 是 {t.get('volume')}，与卡片的 {vol} 不符")

    oneliner = (card.get("one_liner") or "").strip()
    if not (10 <= len(oneliner) <= 120):
        errs.append(f"one_liner 长度 {len(oneliner)} 不在 10–120 字之间")
    kp = card.get("key_points") or []
    if not (3 <= len(kp) <= 6):
        errs.append(f"要点 {len(kp)} 条，应为 3–6 条")
    for i, x in enumerate(kp):
        if len((x or "").strip()) < 10:
            errs.append(f"第 {i + 1} 条要点太短")
    if not card.get("pitfalls"):
        errs.append("缺易错点")
    if len((card.get("check") or "").strip()) < 10:
        errs.append("缺过关标准")

    # 与显式登记的参考范围一致，不随核心/工具分类自动扩大。
    members = {tid for tid, info in (tier_data.get("topics") or {}).items()
               if (info.get("reference_group") or info.get("brief_group")) == f"{cid}@{vol}"}
    if covers and members:
        for tid in sorted(members - set(covers)):
            errs.append(f"漏了同组的 {tid}（{topics.get(tid, {}).get('name')}）")
        for tid in sorted(set(covers) - members):
            errs.append(f"{tid} 不属于 {cid}@{vol} 这一组")

    practice = card.get("practice") or []
    if not 1 <= len(practice) <= 4:
        errs.append(f"练习题应 1–4 道，实际 {len(practice)} 道")
    elif bank is not None:
        for ref in practice:
            qid = ref.get("id") if isinstance(ref, dict) else ref
            q = bank.get(qid)
            if q is None:
                errs.append(f"练习题不存在：{qid}")
                continue
            ids = set(q.get("topicIds") or [q.get("topicId")])
            if not ids & set(covers):
                errs.append(f"练习题 {qid} 不在本卡覆盖的知识点里")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", default="build/teach_coverage.json")
    args = ap.parse_args()

    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
    bank = {q["id"]: q for q in json.loads((DATA / "questions.json").read_text(encoding="utf-8"))}
    cards = {}
    for path in sorted(TEACH.glob("*.json")) if TEACH.exists() else []:
        card = json.loads(path.read_text(encoding="utf-8"))
        cards[card.get("topicId") or path.stem] = card

    problems = []
    for tid, card in cards.items():
        for e in check_card(card, topics, bank):
            problems.append({"topicId": tid, "error": e})

    # 速查卡（工具点合并卡）：data/teach/clusters/<cluster>@<volume>.json
    tier_data = {}
    tp = DATA / "topic_tiers.json"
    if tp.exists():
        tier_data = json.loads(tp.read_text(encoding="utf-8"))
    brief_cards = {}
    brief_dir = TEACH / "clusters"
    for path in sorted(brief_dir.glob("*.json")) if brief_dir.exists() else []:
        card = json.loads(path.read_text(encoding="utf-8"))
        brief_cards[path.stem] = card
    for key, card in brief_cards.items():
        for e in check_brief(card, topics, tier_data, bank):
            problems.append({"topicId": key, "error": e, "kind": "brief"})

    referenced = {tid for c in brief_cards.values() for tid in (c.get("covers") or [])}
    tool_ids = {tid for tid, info in tier_data.get("topics", {}).items() if info.get("tier") == "tool"}
    per_stage = {s: {"topics": 0, "cards": 0, "ready": 0, "brief": 0, "with_resource": 0}
                 for s in STAGES}
    for tid, t in topics.items():
        st = stage_of(t)
        if st not in per_stage:
            continue
        per_stage[st]["topics"] += 1
        if tid in cards:
            per_stage[st]["cards"] += 1
            if not any(p["topicId"] == tid for p in problems):
                per_stage[st]["ready"] += 1
        if tid in referenced:
            per_stage[st]["brief"] += 1
        if tid in cards or tid in referenced:
            per_stage[st]["with_resource"] += 1

    total = {"topics": len(topics), "cards": len(cards),
             "brief_cards": len(brief_cards), "brief_covers": len(referenced),
             "brief_tool_covers": len(referenced & tool_ids),
             "brief_core_references": len(referenced - tool_ids),
             "ready": sum(1 for tid in cards
                          if not any(p["topicId"] == tid for p in problems))}
    print(f"完整讲解卡 {len(cards)}/{len(topics)} 个知识点，通过校验 {total['ready']}")
    print(f"速查参考卡 {len(brief_cards)} 张，关联 {len(referenced)} 点"
          f"（工具点 {total['brief_tool_covers']}，核心点辅助参考 {total['brief_core_references']}）")
    for st in STAGES:
        x = per_stage[st]
        print(f"  {st:<8} 点 {x['topics']:3}  完整卡 {x['cards']:3}"
              f"  速查参考 {x['brief']:3}  有资源 {x['with_resource']:3}"
              f" ({x['with_resource'] / max(x['topics'], 1):.1%}；含速查，不代表完整讲解)")
    if problems:
        print(f"\n问题 {len(problems)} 条：")
        for p in problems[:20]:
            print(f"  {p['topicId']}: {p['error']}")
    else:
        print("\n全部通过：引用完整、例题可验算、题型不全是选择题。")

    out = {"total": total, "by_stage": per_stage, "problems": problems,
           "scope": "ready 仅表示结构校验通过；速查只作参考，独立学科审核与技能覆盖另计",
           "brief_cards": sorted(brief_cards)}
    path = ROOT / args.json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"写入 {path}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
