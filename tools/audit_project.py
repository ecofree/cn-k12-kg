"""项目体检：把「现状」算成数字，每个数字都能追到文件或规则。

    python tools/audit_project.py            # 打印报告
    python tools/audit_project.py --json     # 另存 build/audit_report.json

与 status_report.py 的分工：
  status_report 是**快照**（有哪些资产、多少道题），给日常看。
  audit_project 是**体检**（哪里不够、哪里自相矛盾），按学段逐条对账，
  并把 stage_policy.json 里的口径与实际数据交叉核对，抓过期记录。

三条硬规矩（都是踩过坑写下的）：
  1. 自有题没有 usable_for_diagnosis 字段，诊断池必须写成「自有全部 + 外部可诊断」，
     直接对合并列表过滤会把 2235 道自有题整段漏掉。
  2. 「题点对」和「主知识点」是两套账：前者把复合题的每个关联点都记一道，
     后者只认 primaryTopicId。诊断器按前者索引、复合题按后者排序，
     两个数都对，混着报就是假的。本工具默认报**主知识点**（更保守）。
  3. 不可诊断题会把「题量下限」「难度分层」填满，但诊断器抽不到它们。
     所以题量/难度一律同时报 all 与 usable 两个口径。

只读，不写 data/。
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
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

VOL_RE = re.compile(r"^(m(?:[1-9][ab]|h[1-5]))\.")

# 只认真的把选项写进题干的宏；\begin{array} 是矩阵/分段函数的正常写法，
# 一并算进来会虚报 274 条（2026-10-26 踩过）。
INLINE_CHOICE_RE = re.compile(r"\\choices(?:five|four|three)\b")
LETTER_REF_RE = re.compile(r"(选项\s*[A-D]|\b[A-D]\s*选项|故答案为[:：]?\s*[A-D])")


def load(name, default=None):
    p = DATA / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def stage_of(tid: str) -> str:
    m = VOL_RE.match(tid or "")
    if not m:
        return "unknown"
    key = m.group(1)
    if key.startswith("mh"):
        return "senior"
    return "primary" if int(key[1]) <= 6 else "junior"


def q_topic(q):
    return q.get("primaryTopicId") or q.get("topicId")


def ratio(a, b):
    return round(a / b, 4) if b else None


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "-"


# --------------------------------------------------------------------------- #
def audit_graph(topics, deps):
    ids = {t["id"] for t in topics}
    out_edges, in_edges = collections.defaultdict(list), collections.defaultdict(list)
    cross, strength = collections.Counter(), collections.Counter()
    for d in deps:
        a, b = d["topicId"], d["prerequisiteId"]
        out_edges[b].append(a)
        in_edges[a].append(b)
        strength[d.get("strength")] += 1
        if stage_of(a) != stage_of(b):
            cross[f"{stage_of(b)}->{stage_of(a)}"] += 1

    memo = {}

    def depth(t, seen=frozenset()):
        if t in memo:
            return memo[t]
        if t in seen:
            return 0
        seen = seen | {t}
        best = max((depth(p, seen) + 1 for p in in_edges.get(t, [])), default=0)
        memo[t] = best
        return best

    depths = {t: depth(t) for t in ids}
    return {
        "topics": len(topics),
        "by_stage": dict(collections.Counter(stage_of(t["id"]) for t in topics)),
        "by_volume": len({t.get("volume") for t in topics}),
        "by_type": dict(collections.Counter(t.get("type") for t in topics)),
        "by_domain": dict(collections.Counter(t.get("domain") for t in topics)),
        "by_exam_scope": dict(collections.Counter(t.get("exam_scope") for t in topics)),
        "by_review_status": dict(collections.Counter(
            t.get("review_status") for t in topics)),
        "edges": len(deps),
        "edges_by_strength": dict(strength),
        "cross_stage_edges": dict(cross),
        "roots": len(ids - set(in_edges)),
        "leaves": len(ids - set(out_edges)),
        "only_soft_out": sum(
            1 for t in ids
            if out_edges.get(t) and all(
                d.get("strength") == "soft" for d in deps
                if d["prerequisiteId"] == t)),
        "longest_chain_edges": max(depths.values()) if depths else 0,
        "deepest": sorted(depths.items(), key=lambda kv: -kv[1])[:5],
        "isolated": sum(1 for t in ids if not in_edges.get(t) and not out_edges.get(t)),
    }


def audit_own(questions):
    per = collections.Counter(q_topic(q) for q in questions)
    return {
        "count": len(questions),
        "by_stage": dict(collections.Counter(stage_of(q_topic(q)) for q in questions)),
        "by_type": dict(collections.Counter(q.get("type") for q in questions)),
        "by_difficulty": dict(collections.Counter(
            q.get("difficulty") for q in questions)),
        "generated": sum(1 for q in questions if q.get("generated")),
        "with_verify": sum(1 for q in questions if q.get("verify")),
        "no_explanation": sum(1 for q in questions if not q.get("explanation")),
        "short_without_accept": sum(
            1 for q in questions
            if q.get("type") == "short" and not q.get("accept")),
        "topics_covered": len(per),
    }


def audit_ext(ext):
    usable = [q for q in ext if q.get("usable_for_diagnosis")]
    per = collections.Counter(q_topic(q) for q in usable)
    return {
        "count": len(ext),
        "by_source": dict(collections.Counter(q.get("source") for q in ext)),
        "by_admission": dict(collections.Counter(q.get("admission_level") for q in ext)),
        "by_license": dict(collections.Counter(q.get("source_license") for q in ext)),
        "usable": len(usable),
        "usable_by_stage": dict(collections.Counter(stage_of(q_topic(q)) for q in usable)),
        "usable_by_type": dict(collections.Counter(q.get("type") for q in usable)),
        "usable_by_difficulty": dict(collections.Counter(
            q.get("difficulty") for q in usable)),
        "usable_topics": len(per),
        "usable_multi_topic": sum(1 for q in usable if len(q.get("topicIds") or []) > 1),
        "choice_answer_dist": dict(collections.Counter(
            q.get("answer") for q in usable if q.get("type") == "choice")),
        "numeric_no_verify": sum(
            1 for q in ext if q.get("type") == "numeric" and not q.get("verify")),
        "short_no_accept": sum(
            1 for q in ext if q.get("type") == "short" and not q.get("accept")),
    }


# --------------------------------------------------------------------------- #
def pool_audit(topics, pool, policy):
    """按主知识点口径，逐学段对账「题量下限 / 题型种类 / 难度档数」。"""
    per = collections.Counter(q_topic(q) for q in pool)
    types = collections.defaultdict(set)
    diffs = collections.defaultdict(set)
    for q in pool:
        t = q_topic(q)
        types[t].add(q.get("type"))
        diffs[t].add(q.get("difficulty"))

    out = {}
    for stage in ("primary", "junior", "senior"):
        need = policy.get(stage, {}).get("targets", {}).get("min_questions_per_topic", 0)
        pref = policy.get(stage, {}).get("diagnosis", {}).get("prefer_types") or []
        tids = [t["id"] for t in topics if stage_of(t["id"]) == stage]
        out[stage] = {
            "topics": len(tids),
            "with_questions": sum(1 for t in tids if per[t]),
            "with_none": sum(1 for t in tids if not per[t]),
            "target": need,
            "deficit": sum(1 for t in tids if per[t] < need),
            "deficit_sample": sorted(
                ((t, per[t]) for t in tids if per[t] < need), key=lambda x: x[1])[:5],
            "deficit_types": sum(
                1 for t in tids if len(types[t]) < max(len(pref), 2)),
            "single_type": sum(1 for t in tids if len(types[t]) < 2),
            "single_difficulty": sum(1 for t in tids if len(diffs[t]) < 2),
            "total_pairs": sum(per[t] for t in tids),
        }
    return out


def audit_runtime(topics, own, ext, policy):
    """默认运行时到底加载了什么。

    web/server.py 与 tools/query.py 都走 load_graph() 默认值，
    即 include_external=False —— 外部 23313 道不进诊断。
    这一节专门把「默认」和「开外部题」两种运行时算清楚，
    免得拿含外部题的指标去描述一个默认不加载外部题的系统。
    """
    usable_ext = [q for q in ext if q.get("usable_for_diagnosis")]
    default = pool_audit(topics, own, policy)
    opened = pool_audit(topics, own + usable_ext, policy)

    def std_cov(pool):
        """标准题覆盖率，按**引擎的索引口径**算（topicIds 或 topicId）。

        这个数是 STEP_UP_MIN_STANDARD_COVERAGE 的判据，必须和
        engine._standard_coverage() 同口径，否则会出现
        「工具说 0.52 没到门槛、引擎说 0.61 到了」的分裂。
        """
        have = set()
        for q in pool:
            if q.get("difficulty") == "standard":
                have.update(q.get("topicIds") or
                            ([q.get("topicId")] if q.get("topicId") else []))
        return ratio(len({t["id"] for t in topics} & have), len(topics))

    def std_cov_primary(pool):
        """保守口径：只认主知识点。"""
        have = {q_topic(q) for q in pool if q.get("difficulty") == "standard"}
        return ratio(len({t["id"] for t in topics} & have), len(topics))

    return {
        "default_pool": len(own),
        "opened_pool": len(own) + len(usable_ext),
        "default": default,
        "opened": opened,
        "standard_coverage_default": std_cov(own),
        "standard_coverage_opened": std_cov(own + usable_ext),
        "standard_coverage_opened_primary": std_cov_primary(own + usable_ext),
        "step_up_min_coverage": 0.6,
        "step_up_on_correct": False,
    }


# --------------------------------------------------------------------------- #
def audit_quality(own, ext, topics):
    usable_ext = [q for q in ext if q.get("usable_for_diagnosis")]
    both = own + ext

    def dup(qs, with_choices=False):
        seen = collections.Counter()
        for q in qs:
            p = re.sub(r"\s+", "", q.get("prompt") or "")
            if not p:
                continue
            if with_choices:
                p += "||" + "|".join(
                    re.sub(r"\s+", "", c.get("text") or "") for c in (q.get("choices") or []))
            seen[p] += 1
        groups = [n for n in seen.values() if n > 1]
        return len(groups), sum(n - 1 for n in groups)

    dup_prompt = dup(both)
    dup_full = dup(both, with_choices=True)

    # 模板化题干：题干只有「关于X，下列说法正确的是」，信息全在选项里
    generic = sum(
        1 for q in own
        if re.match(r"^关于.{0,12}下列说法正确的是", q.get("prompt") or "")
        or "下列说法正确的是哪一个" in (q.get("prompt") or ""))

    return {
        "dup_prompt_groups": dup_prompt[0],
        "dup_prompt_extra": dup_prompt[1],
        "dup_identical_groups": dup_full[0],
        "dup_identical_extra": dup_full[1],
        "generic_stem_questions": generic,
        "explanation_letter_refs_usable": sum(
            1 for q in usable_ext if LETTER_REF_RE.search(q.get("explanation") or "")),
        "empty_option_total": sum(
            1 for q in both
            if any(not (c.get("text") or "").strip() for c in (q.get("choices") or []))),
        "empty_option_usable": sum(
            1 for q in usable_ext
            if any(not (c.get("text") or "").strip() for c in (q.get("choices") or []))),
        "inline_choice_macro": sum(
            1 for q in both if INLINE_CHOICE_RE.search(q.get("prompt") or "")),
        "inline_choice_macro_usable": sum(
            1 for q in usable_ext if INLINE_CHOICE_RE.search(q.get("prompt") or "")),
        "answer_not_in_choices": sum(
            1 for q in usable_ext
            if q.get("type") == "choice"
            and q.get("answer") not in {c.get("id") for c in (q.get("choices") or [])}),
        "questions_without_explanation": sum(
            1 for q in both if not q.get("explanation")),
    }


def audit_human(topics, reviews, own):
    verdicts = collections.Counter()
    teacher = 0
    for v in (reviews or {}).values():
        if isinstance(v, dict):
            verdicts[v.get("verdict") or v.get("status") or "?"] += 1
            teacher += bool(v.get("teacher_reviewed"))
    return {
        "topic_review_status": dict(collections.Counter(
            t.get("review_status") for t in topics)),
        "own_questions_total": len(own),
        "review_records": len(reviews or {}),
        "review_verdicts": dict(verdicts),
        "review_coverage": ratio(len(reviews or {}), len(own)),
        "never_reviewed": len(own) - len(reviews or {}),
        "teacher_reviewed": teacher,
        "real_responses": 0,
    }


# --------------------------------------------------------------------------- #
def audit_policy_drift(policy, ext, diag, graph):
    """stage_policy 里写死的数字 vs 实测。

    这些是散文里嵌的具体数字，没有校验器管，最容易过期。
    抓出来不自动改——改哪边是人的判断（可能是文档错，也可能是数据错）。
    """
    drifts = []

    def check(where, doc_text, said, actual, actual_str):
        # 带「旧记录/已过期」的句子是历史留痕，不是当前口径——
        # 把它们算进漂移会永远报错，逼着人删掉有用的沿革记录。
        live = "。".join(
            s for s in doc_text.split("。")
            if "旧记录" not in s and "已过期" not in s)
        if str(said) in live and actual != said:
            drifts.append({"where": where, "said": f"{said}", "actual": actual_str})

    jun = json.dumps(policy.get("junior", {}), ensure_ascii=False)
    pri = json.dumps(policy.get("primary", {}), ensure_ascii=False)
    sen = json.dumps(policy.get("senior", {}), ensure_ascii=False)

    check("stage_policy.junior 上海中考古数", jun, 117,
          ext["by_source"].get("smse-shanghai-zhongkao", 0),
          f"{ext['by_source'].get('smse-shanghai-zhongkao', 0)} 道")
    cross = graph["cross_stage_edges"].get("primary->junior", 0)
    if "小学→初中先修链只有 2 条边" in jun and cross != 2:
        drifts.append({"where": "stage_policy.junior.diagnosis.note",
                       "said": "小学→初中先修链只有 2 条边",
                       "actual": f"{cross} 条"})
    # 小学「132 个点没有可诊断题」是**外部题**口径，不是诊断池口径
    ext_cov = diag["external_coverage"]["primary"]
    if "132 个仍然没有可诊断题" in pri:
        actual_zero = ext_cov["pairs_without"]
        if actual_zero != 132:
            drifts.append({
                "where": "stage_policy.primary.difficulty_mix.known_gap",
                "said": "小学 132 个点没有（外部）可诊断题",
                "actual": f"{actual_zero} 个（题点对口径）"})
    check("stage_policy.senior 可诊断题数", sen, 6426,
          ext["usable_by_stage"].get("senior", 0),
          f"{ext['usable_by_stage'].get('senior', 0)} 道")
    # 引擎注释里的 standard 覆盖率
    return drifts


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    topics = load("topics.json", [])
    deps = load("dependencies.json", [])
    own = load("questions.json", [])
    ext = load("questions_ext.json", [])
    reviews = load("reviews.json", {})
    policy = load("stage_policy.json", {})

    g = audit_graph(topics, deps)
    o = audit_own(own)
    e = audit_ext(ext)
    rt = audit_runtime(topics, own, ext, policy)
    q = audit_quality(own, ext, topics)
    h = audit_human(topics, reviews, own)

    usable_ext = [q_ for q_ in ext if q_.get("usable_for_diagnosis")]
    ext_cov = {}
    for st in ("primary", "junior", "senior"):
        tids = [t["id"] for t in topics if stage_of(t["id"]) == st]
        primary_have = {q_topic(x) for x in usable_ext}
        pairs_have = set()
        for x in usable_ext:
            pairs_have.update(x.get("topicIds") or
                              ([x.get("topicId")] if x.get("topicId") else []))
        ext_cov[st] = {
            "topics": len(tids),
            "primary_with": sum(1 for t in tids if t in primary_have),
            "primary_without": sum(1 for t in tids if t not in primary_have),
            "pairs_with": sum(1 for t in tids if t in pairs_have),
            "pairs_without": sum(1 for t in tids if t not in pairs_have),
        }
    diag = {"external_coverage": ext_cov,
            "default": rt["default"], "opened": rt["opened"]}
    drift = audit_policy_drift(policy, e, diag, g)

    report = {
        "graph": g, "own": o, "ext": e, "runtime": rt,
        "external_coverage": ext_cov, "quality": q, "human": h,
        "policy_drift": drift,
    }

    L = []
    add = L.append
    add("=" * 80)
    add("一、知识图谱")
    add("=" * 80)
    add(f"知识点 {g['topics']}　册次 {g['by_volume']}　"
        f"小学 {g['by_stage'].get('primary', 0)} / 初中 {g['by_stage'].get('junior', 0)} / "
        f"高中 {g['by_stage'].get('senior', 0)}")
    add(f"认知类型 {g['by_type']}")
    add(f"领域 {g['by_domain']}　考试范围 {g['by_exam_scope']}")
    add(f"审核状态 {g['by_review_status']}")
    add(f"先修边 {g['edges']}（{g['edges_by_strength']}）　跨学段 "
        f"{sum(g['cross_stage_edges'].values())} 条 {g['cross_stage_edges']}")
    add(f"根 {g['roots']}　末端 {g['leaves']}　仅 soft 出边 {g['only_soft_out']}　"
        f"最长链 {g['longest_chain_edges']} 边　孤立点 {g['isolated']}")

    add("")
    add("=" * 80)
    add("二、自有题库（默认运行时唯一加载的题）")
    add("=" * 80)
    add(f"题目 {o['count']}　{o['by_type']}")
    add(f"难度 {o['by_difficulty']}")
    add(f"分学段 {o['by_stage']}　生成题 {o['generated']}")
    add(f"可机器验算 {o['with_verify']}（{pct(o['with_verify'], o['count'])}）　"
        f"无解析 {o['no_explanation']}　短答无 accept {o['short_without_accept']}")

    add("")
    add("=" * 80)
    add("三、外部题库（默认不加载）")
    add("=" * 80)
    add(f"总量 {e['count']}　来源 {e['by_source']}")
    add(f"许可 {e['by_license']}")
    add(f"准入 {e['by_admission']}")
    add(f"可诊断 {e['usable']}　分学段 {e['usable_by_stage']}　"
        f"覆盖 {e['usable_topics']} 点（主知识点口径）")
    add(f"可诊断题型 {e['usable_by_type']}　难度 {e['usable_by_difficulty']}")
    add(f"复合题 {e['usable_multi_topic']} 道　选择题答案分布 {e['choice_answer_dist']}")
    add(f"数值题无 verify {e['numeric_no_verify']}　短答题无 accept {e['short_no_accept']}")

    add("")
    add("=" * 80)
    add("四、运行时对账：默认（自有）vs 开外部题")
    add("=" * 80)
    add("口径：主知识点（保守）。check_stage_policy 用题点对口径，数字会更小。")
    for st, label in (("primary", "小学"), ("junior", "初中"), ("senior", "高中")):
        a, b = rt["default"][st], rt["opened"][st]
        add(f"{label}　点 {a['topics']}　题量下限 {a['target']}")
        add(f"  题量不足　默认 {a['deficit']} 点 → 开外部 {b['deficit']} 点")
        add(f"  难度单一　默认 {a['single_difficulty']} 点 → 开外部 {b['single_difficulty']} 点")
        add(f"  诊断池题点对　默认 {a['total_pairs']} → 开外部 {b['total_pairs']}")
    add(f"standard 覆盖率（引擎口径）　默认 {rt['standard_coverage_default']} → "
        f"开外部 {rt['standard_coverage_opened']}（升档门槛 {rt['step_up_min_coverage']}）")
    add(f"standard 覆盖率（主知识点）　开外部 {rt['standard_coverage_opened_primary']}")
    add(f"升档开关 STEP_UP_ON_CORRECT = {rt['step_up_on_correct']}")

    add("")
    add("=" * 80)
    add("五、外部真题覆盖")
    add("=" * 80)
    for st, label in (("primary", "小学"), ("junior", "初中"), ("senior", "高中")):
        x = ext_cov[st]
        add(f"{label}：题点对 {x['pairs_with']}/{x['topics']} 点　"
            f"主知识点 {x['primary_with']}/{x['topics']} 点　"
            f"纯靠自有题（主知识点）{x['primary_without']} 点")

    add("")
    add("=" * 80)
    add("六、内容质量")
    add("=" * 80)
    add(f"题干重复 {q['dup_prompt_groups']} 组（多出 {q['dup_prompt_extra']} 道）；"
        f"题干+选项完全重复 {q['dup_identical_groups']} 组"
        f"（多出 {q['dup_identical_extra']} 道）")
    add(f"模板化题干（「关于X，下列说法正确的是」）{q['generic_stem_questions']} 道")
    add(f"可诊断题解析仍指字母 {q['explanation_letter_refs_usable']}　"
        f"答案不在选项 {q['answer_not_in_choices']}")
    add(f"空选项 共 {q['empty_option_total']}（可诊断 {q['empty_option_usable']}）　"
        f"题干内联选项宏 {q['inline_choice_macro']}"
        f"（可诊断 {q['inline_choice_macro_usable']}）")
    add(f"无解析 {q['questions_without_explanation']}")

    add("")
    add("=" * 80)
    add("七、人工侧")
    add("=" * 80)
    add(f"知识点审核 {h['topic_review_status']}")
    add(f"复核记录 {h['review_records']} / 自有题 {h['own_questions_total']}"
        f"（覆盖 {h['review_coverage']}）　结论 {h['review_verdicts']}")
    add(f"从未复核 {h['never_reviewed']} 道　教师审核 {h['teacher_reviewed']} 道　"
        f"真实作答 {h['real_responses']} 条")

    add("")
    add("=" * 80)
    add("八、口径漂移（stage_policy 写死的数字 vs 实测）")
    add("=" * 80)
    if not drift:
        add("无")
    for x in drift:
        add(f"[漂移] {x['where']}")
        add(f"        文档：{x['said']}　实测：{x['actual']}")

    print("\n".join(L))

    if args.json:
        BUILD.mkdir(exist_ok=True)
        out = BUILD / "audit_report.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
