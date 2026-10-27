"""可回放的初步诊断与练习状态机。

诊断结论来自本次有限题目和假定的 hard 先修图，不代表教师审核或教育因果。
证据不足、题量截断和难度被迫提升必须显式保留，不能补成“已掌握/已确诊”。
练习使用独立反馈状态，不生成薄弱或根因标签。
"""
from __future__ import annotations

from . import evidence

DIFF_RANK = {"basic": 0, "standard": 1, "advanced": 2}
MODES = {
    "diagnose": {"max_attempts": 2, "descend": True},
    "screen": {"max_attempts": 1, "descend": False},
    "practice": {"max_attempts": 2, "descend": False, "step_up_always": True},
}
CONFIRM_ON_CORRECT = True
DESCEND_SHAKY = True
# 诊断升档保持关闭；这是一项保守策略，不是虚拟学生已证明的普遍教学结论。
STEP_UP_ON_CORRECT = False
STEP_UP_SCOPE = "core"
STEP_UP_FAIL_IS_BASIC_ONLY = True
STEP_UP_MIN_STANDARD_COVERAGE = 0.6
VARY_TYPE_ON_CONFIRM = True
DESCEND_ON_HARDER_FAILURE = False
TYPE_GUESS = {"numeric": 0.02, "short": 0.05, "choice": 0.25, "self": 0.3}
TERMINAL = {"mastered", "assumed", "shaky", "weak", "untested", "insufficient_evidence",
            "practice_passed", "practice_review", "practice_basic"}


def create_session(graph, targets, mode="diagnose", limit=None, rotate=0) -> dict:
    """建立纯 dict 会话；rotate 使选题可轮转且可复现。"""
    if mode not in MODES:
        raise ValueError(f"未知模式：{mode}")
    targets = [t for t in dict.fromkeys(targets) if t in graph.by_id]
    if not targets:
        raise ValueError("没有有效的目标知识点")
    queue = graph.topo_order(targets, hard_only=True) if mode == "screen" else list(targets)
    default_limit = (len(queue) if mode == "screen" else
                     min(24, max(12, len(graph.scope(targets, hard_only=True)) * 3)))
    chosen_limit = default_limit if limit is None else int(limit)
    if chosen_limit < 1:
        raise ValueError("limit 必须为正整数")
    return {
        "mode": mode, "targets": targets, "limit": chosen_limit, "queue": queue,
        "state": {}, "asked": {}, "seen": [], "log": [], "current": None,
        "escalated": 0, "rotate": int(rotate), "stop_reason": None,
        "evidence_gaps": {}, "assessment_kind": "practice" if mode == "practice" else "screening",
    }


def _by_guess(pool):
    """按假定猜测率稳定排序；这些常数不是实测的题目参数。"""
    order = sorted(enumerate(pool), key=lambda pair: (
        TYPE_GUESS.get(pair[1].get("type"), 0.25), pair[0]))
    return [q for _, q in order]


def _interleave_types(pool):
    """在已经通过质量闸门的题目中交错题型；先按假定猜测率排序。"""
    buckets: dict[str, list] = {}
    for q in pool:
        buckets.setdefault(q.get("type", ""), []).append(q)
    if len(buckets) <= 1:
        return pool
    out: list = []
    depth = max(len(b) for b in buckets.values())
    for i in range(depth):
        for bucket in buckets.values():
            if i < len(bucket):
                out.append(bucket[i])
    return out


def _rotate_pool(pool, rot: int):
    """按偏移轮转题序。

    同一知识点被反复诊断时，每次抽到不同的题。
    必须在最终排序（题型交错 + 换问法）之后做，
    否则轮转会被重排冲掉。
    """
    if not rot or len(pool) <= 1:
        return pool
    k = rot % len(pool)
    return pool[k:] + pool[:k]


def _prefer_other_type(pool, used_types):
    """同等证据条件下优先不同题型；题型本身不能保证考查了另一种能力。"""
    if not used_types:
        return pool
    other = [q for q in pool if q.get("type") not in used_types]
    same = [q for q in pool if q.get("type") in used_types]
    return other + same


def _standard_coverage(graph, scope: str = "all") -> float:
    """诊断池中有 standard 题的点占比；不是内容教学质量分。"""
    key = "_std_cov" if scope == "all" else f"_std_cov_{scope}"
    cached = getattr(graph, key, None)
    if cached is not None:
        return cached
    topics = graph.topics
    if scope in ("core", "tool"):
        topics = [t for t in topics if graph.tier_of(t["id"]) == scope]
    cov = (sum(any(q.get("difficulty") == "standard" for q in graph.bank.get(t["id"], ()))
               for t in topics) / len(topics)) if topics else 0.0
    setattr(graph, key, cov)
    return cov


def _step_up_enabled(graph, tid: str, session=None) -> bool:
    if session is not None and MODES[session["mode"]].get("step_up_always"):
        return STEP_UP_SCOPE != "core" or graph.tier_of(tid) == "core"
    return (STEP_UP_ON_CORRECT
            and (STEP_UP_SCOPE not in ("core", "tool") or graph.tier_of(tid) == STEP_UP_SCOPE)
            and _standard_coverage(graph, STEP_UP_SCOPE) >= STEP_UP_MIN_STANDARD_COVERAGE)


def _policy(graph, question):
    return graph.question_policies.get(question["id"]) or evidence.question_policy(question)


def _pick_question(graph, session, tid, attempt):
    bank = graph.practice_bank if session["mode"] == "practice" else graph.bank
    pool = [q for q in bank.get(tid, []) if q["id"] not in set(session["seen"])]
    if not pool:
        return None, False
    if attempt == 1:
        for level in ("basic", "standard", "advanced"):
            same = [q for q in pool if q.get("difficulty") == level]
            if same:
                return _rotate_pool(_interleave_types(_by_guess(same)),
                                    session.get("rotate", 0))[0], False
        return _rotate_pool(_by_guess(pool), session.get("rotate", 0))[0], False

    first = next((e for e in session["log"] if e["topicId"] == tid and e["attempt"] == 1), {})
    base = DIFF_RANK.get(first.get("difficulty") or "basic", 0)
    if first.get("verdict") == "correct" and _step_up_enabled(graph, tid, session):
        harder = [q for q in pool if DIFF_RANK.get(q.get("difficulty"), 1) == base + 1]
        if harder:
            return _rotate_pool(_by_guess(harder), session.get("rotate", 0))[0], False
    allowed = [q for q in pool if DIFF_RANK.get(q.get("difficulty"), 1) <= base]
    escalated = not allowed
    pool = allowed or pool
    level = max(DIFF_RANK.get(q.get("difficulty"), 1) for q in pool)
    ordered = _by_guess([q for q in pool if DIFF_RANK.get(q.get("difficulty"), 1) == level])
    if VARY_TYPE_ON_CONFIRM and first.get("questionType"):
        ordered = _prefer_other_type(ordered, {first["questionType"]})
    # 轮转不能把更可信的确认题排到低证据题之后。
    ordered = _rotate_pool(ordered, session.get("rotate", 0))
    if session["mode"] != "practice":
        ordered.sort(key=lambda q: not _policy(graph, q)["may_judge_weak"])
    return ordered[0], escalated


def next_item(graph, session):
    """返回下一题；重复调用不会重复累计升档或跳过当前题。"""
    if session.get("current"):
        cur = session["current"]
        question = graph.questions_by_id.get(cur["questionId"])
        if question is not None:
            return {"topicId": cur["topicId"], "question": question, "attempt": cur["attempt"]}
    while session["queue"]:
        tid = session["queue"][0]
        if session["state"].get(tid) in TERMINAL:
            session["queue"].pop(0)
            continue
        if len(session["log"]) >= session["limit"]:
            session["current"] = None
            session["stop_reason"] = "limit"
            return None
        attempt = session["asked"].get(tid, 0) + 1
        if attempt > MODES[session["mode"]]["max_attempts"]:
            session["state"][tid] = "insufficient_evidence"
            session.setdefault("evidence_gaps", {})[tid] = "unresolved_attempts"
            session["queue"].pop(0)
            continue
        question, escalated = _pick_question(graph, session, tid, attempt)
        if question is None:
            session["queue"].pop(0)
            session["state"][tid] = "insufficient_evidence" if attempt > 1 else "untested"
            session.setdefault("evidence_gaps", {})[tid] = "insufficient_items"
            continue
        if escalated:
            session["escalated"] += 1
        session["current"] = {
            "topicId": tid, "questionId": question["id"], "attempt": attempt,
            "difficulty": question.get("difficulty"), "questionType": question.get("type"),
            "escalated": escalated, "evidence": dict(_policy(graph, question)),
        }
        session["stop_reason"] = None
        return {"topicId": tid, "question": question, "attempt": attempt}
    session["current"] = None
    session["stop_reason"] = "insufficient_evidence" if session.get("evidence_gaps") else "completed"
    return None


def _enqueue_prereqs(graph, session, tid):
    """仅 hard 先修构成诊断回溯；soft 只能用于关联推荐。"""
    pending = []
    for edge in graph.prereqs.get(tid, ()):
        if edge["strength"] != "hard":
            continue
        pid = edge["prerequisiteId"]
        if session["state"].get(pid) in TERMINAL or pid in session["queue"]:
            continue
        pending.append(pid)
    session["queue"][0:0] = pending


def _remove(session, tid):
    session["queue"] = [x for x in session["queue"] if x != tid]


def _finish_practice(session, tid, entries):
    correct = [e for e in entries if e["verdict"] == "correct"]
    if len(correct) == len(entries):
        result = "practice_passed"
    elif (len(entries) > 1 and entries[0]["verdict"] == "correct"
          and DIFF_RANK.get(entries[-1].get("difficulty"), 0)
          > DIFF_RANK.get(entries[0].get("difficulty"), 0)):
        result = "practice_basic"
        session.setdefault("basic_only", []).append(tid)
    else:
        result = "practice_review"
    session["state"][tid] = result
    _remove(session, tid)


def apply_answer(graph, session, verdict, text=None, *, help_used=False) -> dict:
    """记录答题及当时的题目证据；不足的错答不能变成薄弱或触发回溯。"""
    if verdict not in ("correct", "wrong", "skip"):
        raise ValueError("verdict 只能是 correct / wrong / skip")
    if not isinstance(help_used, bool):
        raise ValueError("help_used 必须是明确的是或否")
    current = session.get("current")
    if not current:
        raise ValueError("当前没有待作答的题目，请先调用 next_item")
    tid, attempt = current["topicId"], current["attempt"]
    if attempt != session["asked"].get(tid, 0) + 1:
        raise ValueError("作答顺序与出题记录不一致")
    # 不能靠调用方在 current 中自填一个更强的 evidence 标记绕过门槛。
    bank = graph.practice_bank if session["mode"] == "practice" else graph.bank
    question = next((q for q in bank.get(tid, ()) if q["id"] == current["questionId"]), None)
    if question is None:
        raise ValueError("当前题目不在此知识点的可用题池中")
    policy = dict(_policy(graph, question))
    if help_used:
        policy.update(may_confirm_mastery=False, may_judge_weak=False,
                      original_reason=policy.get("reason"), reason="recorded_help_used")
    session["asked"][tid] = attempt
    session["log"].append({
        "topicId": tid, "questionId": current["questionId"], "attempt": attempt,
        "verdict": verdict, "text": text, "difficulty": question.get("difficulty"),
        "questionType": question.get("type"), "escalated": current.get("escalated", False),
        "evidence": policy, "help_used": help_used,
    })
    session["seen"].append(current["questionId"])
    session["current"] = None
    entries = [e for e in session["log"] if e["topicId"] == tid]
    max_attempts = MODES[session["mode"]]["max_attempts"]
    if attempt < max_attempts and (verdict != "correct" or CONFIRM_ON_CORRECT
                                  or session["mode"] == "practice"):
        return session
    if session["mode"] == "practice":
        _finish_practice(session, tid, entries)
        return session

    reasons = []
    for entry in entries:
        capability = entry.get("evidence") or {}
        if entry.get("help_used"):
            reasons.append("recorded_help_used")
        if entry["verdict"] == "skip":
            reasons.append("skipped_answer")
        elif entry["verdict"] == "correct" and not capability.get("may_confirm_mastery"):
            reasons.append("answer_cannot_confirm_mastery")
        elif entry["verdict"] == "wrong" and not capability.get("may_judge_weak"):
            reasons.append("answer_cannot_judge_weak")
        if entry.get("escalated"):
            reasons.append("forced_difficulty_increase")
    if reasons:
        session["state"][tid] = "insufficient_evidence"
        session.setdefault("evidence_gaps", {})[tid] = ",".join(dict.fromkeys(reasons))
        _remove(session, tid)
        return session

    correct = sum(e["verdict"] == "correct" for e in entries)
    if correct == len(entries):
        session["state"][tid] = "mastered"
        # 这仍是临时推断，不计为已测掌握；明确选择的目标点必须实际作答。
        if session["mode"] == "diagnose" and len(entries) >= 2:
            for anc in graph.ancestors(tid, hard_only=True):
                if anc not in session["targets"]:
                    session["state"].setdefault(anc, "assumed")
    elif correct:
        first, last = entries[0], entries[-1]
        harder_failed = (first["verdict"] == "correct" and last["verdict"] == "wrong"
                         and DIFF_RANK.get(last.get("difficulty"), 0)
                         > DIFF_RANK.get(first.get("difficulty"), 0))
        session["state"][tid] = "shaky"
        if harder_failed and STEP_UP_FAIL_IS_BASIC_ONLY:
            # 一道基础题不够确认掌握；保留混合表现，记录应用层尚待练习。
            session.setdefault("basic_only", []).append(tid)
        if (DESCEND_SHAKY and MODES[session["mode"]]["descend"]
                and (not harder_failed or DESCEND_ON_HARDER_FAILURE)):
            _enqueue_prereqs(graph, session, tid)
    else:
        session["state"][tid] = "weak"
        if MODES[session["mode"]]["descend"]:
            _enqueue_prereqs(graph, session, tid)
    _remove(session, tid)
    return session


def _depth_of(session, tid):
    correct = [DIFF_RANK.get(e.get("difficulty") or "basic", 0) for e in session["log"]
               if e["topicId"] == tid and e["verdict"] == "correct"]
    return {0: "basic", 1: "standard", 2: "advanced"}[max(correct)] if correct else None


def _root_evidence(graph, session, unstable):
    """连续 hard 缺口链的前沿；绝不越过 mastered 或未测节点排除另一个缺口。"""
    state = session["state"]
    supported, candidates, details = [], [], {}
    for tid in graph.curriculum_order(unstable):
        parents = [e["prerequisiteId"] for e in graph.prereqs.get(tid, ())
                   if e["strength"] == "hard"]
        if any(state.get(p) in ("weak", "shaky") for p in parents):
            continue
        missing = [p for p in parents if state.get(p) != "mastered"]
        entries = [e for e in session["log"] if e["topicId"] == tid]
        reasons = []
        if state.get(tid) != "weak":
            reasons.append("mixed_answers")
        if len(entries) < 2:
            reasons.append("single_item_screening")
        if missing:
            reasons.append("prerequisites_not_confirmed")
        if any(not (e.get("evidence") or {}).get("may_judge_weak") for e in entries):
            reasons.append("insufficient_question_evidence")
        status = "candidate" if reasons else "supported_within_model"
        (candidates if reasons else supported).append(tid)
        details[tid] = {"status": status, "missing_prerequisites": missing, "reasons": reasons,
                        "scope": "provisional_hard_graph", "causal_claim": False}
    return supported, candidates, details


def summarize(graph, session) -> dict:
    """有限测量结果与下一步任务；未测祖先不会被整串当作必须补学内容。"""
    state = session["state"]
    mode = session["mode"]
    covered = (set(session["targets"]) if mode in ("screen", "practice") else
               graph.scope(session["targets"], hard_only=True))
    groups = {status: graph.curriculum_order(t for t in covered if state.get(t) == status)
              for status in TERMINAL}
    untested = graph.curriculum_order(t for t in covered if state.get(t) in (None, "untested"))
    pending = [t for t in session["queue"] if state.get(t) not in TERMINAL]
    incomplete = bool(pending or groups["insufficient_evidence"]
                      or any(state.get(t) == "untested" for t in session["targets"]))
    stop_reason = session.get("stop_reason")
    if stop_reason is None:
        stop_reason = "limit" if pending and len(session["log"]) >= session["limit"] else (
            "in_progress" if pending or session.get("current") else "completed")
    unstable = set(groups["weak"]) | set(groups["shaky"])
    roots, root_candidates, root_evidence = ([], [], {}) if mode == "practice" else (
        _root_evidence(graph, session, unstable))

    actions = {}
    for tid in groups["weak"]:
        actions[tid] = ("learn", "本次有可信错答，建议补学后复测")
    for tid in groups["shaky"]:
        actions[tid] = ("consolidate", "本次表现不一致，需要巩固与确认")
    for tid in groups["insufficient_evidence"] + groups["untested"] + pending:
        if tid in covered:
            actions[tid] = ("confirm", "题目证据或作答数量不足，不能据此判薄弱")
    for tid in groups["practice_review"]:
        actions[tid] = ("consolidate", "回看讲解，再做一道变式")
    for tid in groups["practice_basic"]:
        actions[tid] = ("practice_application", "基础题答对，应用题还需练习")
    path = graph.topo_order(actions, hard_only=True)
    support_tasks = [{"topicId": t, "action": actions[t][0], "reason": actions[t][1]} for t in path]

    by_chapter = {}
    for tid in graph.curriculum_order(covered):
        chapter = graph.by_id[tid].get("chapter", "未分章")
        bucket = by_chapter.setdefault(chapter, {s: 0 for s in
            ("mastered", "shaky", "weak", "untested", "assumed", "insufficient_evidence",
             "practice_passed", "practice_review", "practice_basic")})
        bucket[state.get(tid, "untested")] += 1
    mastered = groups["mastered"]
    practiced = groups["practice_passed"] + groups["practice_review"] + groups["practice_basic"]
    return {
        "mode": mode, "targets": session["targets"], "asked_count": len(session["log"]),
        "escalated": session.get("escalated", 0),
        "mastered": mastered, "shaky": groups["shaky"], "weak": groups["weak"],
        "assumed": groups["assumed"], "untested": untested,
        "insufficient_evidence": groups["insufficient_evidence"],
        "evidence_gaps": dict(session.get("evidence_gaps") or {}),
        "mastery_level": {t: _depth_of(session, t) for t in mastered},
        "mastered_by_level": {lv: [t for t in mastered if _depth_of(session, t) == lv]
                              for lv in DIFF_RANK},
        "step_up_failed": graph.curriculum_order(session.get("basic_only") or []),
        "roots": roots, "root_candidates": root_candidates, "root_evidence": root_evidence,
        "path": path, "support_tasks": support_tasks, "by_chapter": by_chapter,
        "incomplete": incomplete, "stop_reason": stop_reason,
        "assessment_kind": "practice" if mode == "practice" else "screening",
        "confidence": "provisional", "causal_claim": False,
        "reviewer_pending": any((e.get("evidence") or {}).get("reviewer_pending", True)
                                for e in session["log"]),
        "practice_results": {t: {"status": state[t], "demonstrated_level": _depth_of(session, t),
                                  "correct_count": sum(e["verdict"] == "correct" for e in session["log"]
                                                       if e["topicId"] == t),
                                  "attempts": session["asked"].get(t, 0)} for t in practiced},
        "step_up": {"scope": STEP_UP_SCOPE,
                    "enabled": mode == "practice" or STEP_UP_ON_CORRECT,
                    "coverage_all": round(_standard_coverage(graph, "all"), 4),
                    "coverage_core": round(_standard_coverage(graph, "core"), 4),
                    "threshold": STEP_UP_MIN_STANDARD_COVERAGE,
                    "ready": _standard_coverage(graph, STEP_UP_SCOPE) >= STEP_UP_MIN_STANDARD_COVERAGE},
    }


def verdict_label(summary) -> str:
    """学生可读结论：只陈述本次观测，区分练习、待确认和模型内线索。"""
    asked = summary["asked_count"]
    if summary["mode"] == "practice":
        needs = sum(v["status"] != "practice_passed" for v in summary.get("practice_results", {}).values())
        return f"本次练习 {asked} 题，{needs} 个知识点建议继续巩固；练习结果不作薄弱诊断。"
    tail = "本次未完成全部确认。" if summary.get("incomplete") else ""
    basic = len((summary.get("mastered_by_level") or {}).get("basic") or [])
    if basic:
        tail += f"{basic} 个知识点只验证了基础题表现。"
    if summary["weak"] or summary["shaky"]:
        return (f"本次 {asked} 题：{len(summary['weak'])} 个待补学线索、"
                f"{len(summary['shaky'])} 个待巩固知识点；"
                f"{len(summary.get('roots', []))} 个有本次先修证据支持的补学起点、"
                f"{len(summary.get('root_candidates', []))} 个待确认起点。"
                f"这些是初步线索，不代表已证实的学习原因。{tail}")
    if summary.get("insufficient_evidence") or summary.get("incomplete"):
        return f"本次 {asked} 题，证据尚不足以形成完整判断，请先完成待确认任务。{tail}"
    return f"本次 {asked} 题未发现明确的补学线索，仅反映本次题目表现。{tail}"
