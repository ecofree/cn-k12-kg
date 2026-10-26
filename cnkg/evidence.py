"""题目证据分级：把"答案可不可信"从声明变成可复算的判据。

## 为什么要这个模块

项目里 9005 道外部题都标着 `admission_level = cross-checked`，理由写作
「原卷答案与解析互相印证」。但实测发现这句话经常是空的：

- CMM 有一部分解析就是一句「答案:C」，没有推导，也谈不上印证；
- smse 的解析是「先根据题意得出相似比，再……即可」，通篇没有数值，
  答案根本没出现在解析里；
- 全库 763 道 smse 里，答案文本真的出现在解析里的只有 57 道（7.5%）。

也就是说 `cross-checked` 描述的是**导入时的动作**，不是**题目现在的证据**。
拿它当准入门槛，等于拿一句自述当证据。

## 五级证据（互斥，按最强证据定级）

    verified      给定算式与答案计算一致；不能证明题干建模、题义或教学适切性正确。
                  机器复算不等于独立教师审核。
    corroborated  跨源同题：同一道题在另一个来源出现，且答案一致。
                  两条独立来源同时错的概率低（但同源转载不算）。
    explained     解析具有推导信号，且结句（故选 X / 答案为 X）与答案一致。
                  比 bare 强，但它仍是"作者自述"，不是独立证据。
    bare          只有答案，没有可用的推导。可用于练习，不应用于判薄弱。
    broken        结构残缺（选项空缺/重复、答案不在选项里），直接隔离。
    defective     发现**确证矛盾**（结句字母与答案不符、复算与答案不符）。

## 关键设计：错误答案的后果不对称

答案错了有两种后果，严重程度完全不同：

- 学生**会**，答案错 → 学生答对但被判错 → 假薄弱 → 触发回溯 → 根因跑偏。
  这是**误诊**，代价高。
- 学生**不会**，答案错 → 学生蒙对但被判对 → 漏诊。
  这也是错，但漏诊只会让这一次诊断少发现一个问题，不会把根因指错。

bare 只保留为待审核练习材料，不能确认掌握或判薄弱；explained 的错答
也不能判薄弱。question_policy 是图索引和状态机共同使用的运行时门槛。
"""
from __future__ import annotations

import ast
import hashlib
import json
import operator
import re

# 证据强度排序（越大越强）
LEVEL_RANK = {
    "defective": -1,
    "broken": 0,
    "bare": 1,
    "explained": 2,
    "corroborated": 3,
    "verified": 4,
}

# 可用于诊断的最低等级。bare 不进诊断，只进练习。
DIAGNOSABLE = {"verified", "corroborated", "explained"}


def rank(level: str) -> int:
    return LEVEL_RANK.get(level, 0)


# 自有题**不是没有 source**——自产记忆检验题标的是
# `source: "self-generated-memory-drill"`。
# 用 `not q.get("source")` 判断自有/外部会把 900 多道自产题当成外部题，
# 它们的 evidence_level 是空的，于是整批从"可判薄弱"的统计里消失。
OWN_SOURCE_PREFIX = "self-generated"


def is_own(q: dict) -> bool:
    """是不是我方自产题（含题库里没有 source 字段的老题）。"""
    src = q.get("source")
    return not src or str(src).startswith(OWN_SOURCE_PREFIX)


# ---------------------------------------------------------------- 文本规范化

_LATEX_SIMPLE = [
    (r"\\left\s*", ""), (r"\\right\s*", ""),
    (r"\\times", "*"), (r"\\cdot", "*"), (r"\\div", "/"),
    (r"\\pi", "3.141592653589793"),
    (r"\\text\s*\{([^{}]*)\}", r"\1"),
    (r"\\mathrm\s*\{([^{}]*)\}", r"\1"),
    (r"\\mathbf\s*\{([^{}]*)\}", r"\1"),
    (r"\\,|\\;|\\!|\\quad|\\qquad", ""),
    (r"\$", ""),
    (r"％", "%"), (r"（", "("), (r"）", ")"),
    (r"﹣", "-"), (r"－", "-"), (r"＋", "+"),
    (r"＝", "="), (r"×", "*"), (r"÷", "/"),
    (r"．", "."), (r"，", ","),
]

_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")


def delatex(text: str) -> str:
    """把常见 LaTeX 压成可读/可算的纯文本。只处理算式里真会出现的写法。"""
    s = text or ""
    for _ in range(3):                      # 嵌套分式要反复展开
        s2 = _FRAC.sub(r"(\1)/(\2)", s)
        if s2 == s:
            break
        s = s2
    for pat, rep in _LATEX_SIMPLE:
        s = re.sub(pat, rep, s)
    return s


_WS = re.compile(r"\s+")


def norm_stem(text: str) -> str:
    """题干归一化：用于跨源同题识别。

    只去掉排版差异（空白、LaTeX、标点、全半角），**保留全部数字**——
    把数字也去掉会让「3+5」和「4+6」撞成同一题。
    """
    s = delatex(text).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return _WS.sub("", s)


def stem_hash(text: str) -> str:
    return hashlib.sha1(norm_stem(text).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 答案文本

def answer_text(q: dict) -> str:
    """把答案转成可比对的文本：选择题换成选项正文，其余用原答案。"""
    ans = str(q.get("answer") or "").strip()
    if q.get("type") == "choice":
        for c in q.get("choices") or []:
            if str(c.get("id")) == ans:
                return str(c.get("text") or "").strip()
    return ans


def choice_letters(q: dict) -> dict:
    """选项 id -> 字母（o1->A, o2->B ...）。用于和「故选C」对账。"""
    out = {}
    for i, c in enumerate(q.get("choices") or []):
        cid = str(c.get("id") or f"o{i + 1}")
        out[cid] = chr(ord("A") + i)
    return out


# ---------------------------------------------------------------- 结构检查

def structural_defects(q: dict) -> list[str]:
    """结构级缺陷。这些是**确定的**错，不需要判断力。"""
    defects = []
    prompt = (q.get("prompt") or "").strip()
    if len(prompt) < 8:
        defects.append("题干过短")
    choices = q.get("choices") or []
    if q.get("type") == "choice":
        if len(choices) < 2:
            defects.append("选项不足")
        texts = [(c.get("text") or "").strip() for c in choices]
        if any(not t for t in texts):
            defects.append("空选项")
        filled = [t for t in texts if t]
        if len(set(filled)) < len(filled):
            defects.append("选项重复")
        if str(q.get("answer")) not in {str(c.get("id")) for c in choices}:
            defects.append("答案不在选项里")
    if not str(q.get("answer") or "").strip():
        defects.append("无答案")
    return defects


# ---------------------------------------------------------------- 安全算式求值

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}

_NUM = r"-?\d+(?:\.\d+)?"

# 算术片段：只由数字、四则运算、括号、空格和等号组成。
# 中文、字母、单位会把片段自然切断，正好用来定位「纯数字连等式」。
_ARITH_RUN = re.compile(r"[0-9+\-*/^().\s=]+")
_OP = re.compile(r"[-+*/^]")


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("不支持的表达式")


def safe_eval(expr: str) -> float | None:
    """只允许四则与乘方。任何其他节点都拒绝——不 eval 字符串。"""
    expr = expr.replace("^", "**")
    try:
        return _eval_node(ast.parse(expr, mode="eval"))
    except Exception:
        return None


def arithmetic_chains(text: str) -> tuple[int, int, float | None]:
    """返回 (成立的连等式数, 矛盾的连等式数, 最后一处成立的值)。

    为什么要按**连等式**解析而不是逐条 `a op b = c`：
    中文数学解析里 `12×63÷36 = 756÷36 = 21` 是一整条链，
    只抓第一段会得到 `12*63/36 = 756`，然后把一道完全正确的题判成错。
    这类误判在实测里占了「复算不通」的 100%——所以这里改成整体分段求值。

    **注意：矛盾的连等式不能用来封存题目。** 数据里 `2²` 常被展平成 `22`，
    这种排版损坏会让一条正确的连等式算不通，但这道题本身可能是对的。
    矛盾只记信号，不升级为 defective。
    """
    s = delatex(text or "")
    ok = bad = 0
    last = None
    for m in _ARITH_RUN.finditer(s):
        run = m.group(0)
        if "=" not in run:
            continue
        parts = [p.strip() for p in run.split("=") if p.strip()]
        if len(parts) < 2 or not any(_OP.search(p) for p in parts):
            continue
        vals = []
        for p in parts:
            v = safe_eval(p)
            if v is None:
                vals = []
                break
            vals.append(v)
        if len(vals) < 2:
            continue
        scale = max(1.0, max(abs(v) for v in vals))
        if all(abs(v - vals[0]) <= 1e-6 * scale for v in vals):
            ok += 1
            last = vals[-1]
        elif abs(vals[-1] - vals[0]) > 1e-3 * scale:
            bad += 1
    return ok, bad, last


# ---------------------------------------------------------------- 结句对账

# 只认**明确的最终结论**，而且要能区分「故 A 正确」与「故 A 错误」。
#
# 这一步的误判成本极高：把「故 A 错误」当成结论，就会把一道好题判成
# defective 并隔离掉。所以：
#   强写法（故选/应选/答案为/答案选）直接采信；
#   弱写法（故 X）必须后接 正确/符合/成立，且不能是 错误/不正确/不合题意。
_STRONG = re.compile(
    r"(?:故选|应选|答案选|答案[是为]|答案[：:])\s*[：:]?\s*"
    r"(?:\\text\s*\{\s*)?([A-H])(?![A-Za-z])")
# 「不正确」里也含「正确」，所以必须用 (?<!不) 否定前缀挡住。
_WEAK = re.compile(
    r"故\s*(?:\\text\s*\{\s*)?([A-H])(?![A-Za-z])\s*"
    r"(?:选项)?\s*(?<![不])(?:正确|成立|符合)")


def concluding_letter(text: str) -> str | None:
    """抽出最终结论字母；不唯一时返回 None（宁可不判，也不猜）。

    只看「故选 X」「答案为 X」这类强写法，以及「故 X 正确」这类弱写法。
    「故 X 错误」不采信——它说的是别的选项错，不是结论。
    """
    s = delatex(text or "")
    letters = {m.group(1) for m in _STRONG.finditer(s)}
    letters |= {m.group(1) for m in _WEAK.finditer(s)}
    return letters.pop() if len(letters) == 1 else None


# ---------------------------------------------------------------- 主评级

def _has_steps(analysis: str) -> bool:
    """解析是否像推导，而不是一句「答案:C」。

    阈值压到 18 字是因为初中/小学题的正确推导常常很短
    （「设 x，则 2x=10，得 x=5」只有 18 字），用 40 字会把它们误判成 bare。
    而一句「答案:C」只有 5 字，无论如何都过不了。
    """
    s = (analysis or "").strip()
    if len(s) < 18:
        return False
    markers = ("=", "因为", "所以", "则", "得", "故", "【详解】", "【分析】",
               "设", "由", "代入", "故选")
    return any(m in s for m in markers)


def grade(q: dict, cross_source: bool = False) -> dict:
    """给一道题定证据等级。不改题目本身，只产出判据。

    返回 {level, signals, notes}，可直接写进 `evidence` 字段。
    """
    notes = []
    defects = structural_defects(q)
    if defects:
        return {"level": "broken", "signals": {}, "notes": defects}

    analysis = q.get("explanation") or ""
    a_text = answer_text(q)
    letters = choice_letters(q)
    key_letter = letters.get(str(q.get("answer"))) if q.get("type") == "choice" else None

    arith_ok, arith_bad, last_val = arithmetic_chains(analysis)

    # 结句字母与答案对账。只在选择题上做：短答题没有字母。
    #
    # **反问题直接放弃这个信号**：题干问「下列说法错误的是」时，
    # 解析里那句「故 A 正确」说的是 A 对，而答案要选错的 B。
    # 实测里唯一一道被判 defective 的题就是这种，判据本身错了。
    inverted = bool(re.search(r"错误|不正确|不符合|不成立|不恰当|有误", q.get("prompt") or ""))
    final = concluding_letter(analysis) if (q.get("type") == "choice" and not inverted) else None
    final_match = bool(final and key_letter and final == key_letter)

    # 复算是否落到答案上：只看**最后一处成立的连等式**的值。
    # 不要求整个解析都对——中间步骤的排版损坏（`2²`→`22`）太常见，
    # 逐条比对只会误伤。
    recompute_hits_key = False
    if arith_ok and last_val is not None:
        if q.get("type") == "choice":
            for c in q.get("choices") or []:
                if _num_close(str(c.get("text") or ""), last_val):
                    recompute_hits_key = str(c.get("id")) == str(q.get("answer"))
                    break
        else:
            recompute_hits_key = _num_close(a_text, last_val)

    signals = {
        "analysis_chars": len(analysis),
        "has_steps": _has_steps(analysis),
        "final_letter": final,
        "final_match": final_match,
        "arith_ok": arith_ok,
        "arith_bad": arith_bad,
        "recompute_hits_key": recompute_hits_key,
        "answer_in_analysis": bool(a_text) and norm_stem(a_text) in norm_stem(analysis),
        "cross_source": bool(cross_source),
    }

    # 显式 verify 比解析文本的排版正则可靠，尤其是 round 等舍入表达式。
    # 这里只证明“给定算式 = 答案”，不能把题义/建模错误洗成教师审核通过。
    verified_expression = None
    if q.get("type") == "numeric" and q.get("verify"):
        from .verify import check_question
        try:
            verified_expression = check_question(q)
        except (ArithmeticError, TypeError, ValueError, RecursionError) as exc:
            verified_expression = {"status": "error", "detail": str(exc)}
        signals["verify_consistent"] = verified_expression["status"] == "pass"
        if verified_expression["status"] != "pass":
            return {"level": "defective", "signals": signals,
                    "notes": ["显式 verify 与答案不一致或无法安全复算："
                              + verified_expression.get("detail", "") ]}

    # 唯一能确证「这道题不能信」的信号，是结句字母与答案键不符。
    # 两种成因都指向同一处置：不能进诊断。
    #   1. 答案键错了（解析算出 1 个公共点，键写「不确定」）；
    #   2. 解析与题干错配（题干问十进制进率，解析在讲拼角）——
    #      CMM 里实测存在，且**答案键可能是对的**，但我们已经没有证据可用。
    # 复算矛盾**不**升级：数据里 2² 被展平成 22 之类会让正确连等式算不通，
    # 把排版损坏当答案错误，会把好题封掉（实测 347 例全是假阳性）。
    if final and key_letter and final != key_letter:
        return {"level": "defective", "signals": signals,
                "notes": [f"解析结论选 {final}，答案键是 {key_letter}"
                          "（答案错或解析与题干错配，两者都不可用于判分）"]}

    if verified_expression is not None or (arith_ok and recompute_hits_key):
        level = "verified"
        notes.append("给定算式与答案计算一致；题义、建模和教学适切性仍待独立审核")
    elif cross_source and (final_match or signals["answer_in_analysis"]):
        level = "corroborated"
        notes.append("跨源同题且答案一致")
    elif signals["has_steps"] and (final_match or signals["answer_in_analysis"]):
        level = "explained"
        notes.append("解析具有推导信号且结论与答案一致；尚无独立审核")
    else:
        level = "bare"
        notes.append("无可用推导，仅结构完整")
    return {"level": level, "signals": signals, "notes": notes}


def _num_close(text: str, value: float) -> bool:
    """选项文本是否就是这个数（容忍百分号、单位、千分位）。"""
    s = delatex(text).replace(",", "").strip()
    m = re.fullmatch(rf"\s*({_NUM})\s*%?\s*[^\d]*", s)
    if not m:
        return False
    try:
        got = float(m.group(1))
    except ValueError:
        return False
    scale = max(abs(value), 1.0)
    return abs(got - value) <= 1e-6 * scale


# ---------------------------------------------------------------- 可用边界

def diagnosable(level: str) -> bool:
    return level in DIAGNOSABLE


def may_judge_weak(level: str) -> bool:
    """答错时能否据此判「薄弱」。

    仅计算一致或跨源互证（verified / corroborated）允许用于初步筛查。
    这不等于题义已审核，也不等于学生的教育因果已经确证。
    explained 是作者自述，bare 只有答案——答案错的时候，
    会把"其实会"的学生判成薄弱，进而把根因指向上游，代价最大。
    """
    return level in ("verified", "corroborated")


def may_confirm_mastery(level: str) -> bool:
    """答对可用于本次基础表现确认的等级；这一策略仍待真实学生校准。"""
    return level in DIAGNOSABLE


def question_content_sha256(q: dict) -> str:
    """绑定审核记录到会影响题义、答案、难度或归属的内容版本。"""
    fields = ("id", "topicId", "topicIds", "primaryTopicId", "type", "prompt", "choices",
              "answer", "accept", "verify", "explanation", "difficulty", "source")
    payload = json.dumps({k: q.get(k) for k in fields}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def question_policy(q: dict) -> dict:
    """一道题在诊断和练习中能提供哪些证据；不修改原数据。

    旧自有题可从 verify/解析取得计算一致性或初步说明证据，绝不因“自有”
    自动标记为 verified/reviewed。明确的隔离标记和不良记忆题优先于证据标签。
    审核过的记忆题重新准入必须有显式许可、人工审核身份及可追溯记录。
    """
    review = q.get("quality_review") or {}
    human_reviewed = bool(
        isinstance(review, dict) and review.get("status") == "approved"
        and review.get("reviewer_type") in ("teacher", "subject_expert")
        and review.get("reviewer_id") and review.get("record_id")
        and review.get("content_sha256") == question_content_sha256(q)
    )
    result = {
        "diagnosis_eligible": False, "practice_eligible": False,
        "may_confirm_mastery": False, "may_judge_weak": False,
        "evidence_level": "bare", "reviewer_pending": not human_reviewed,
        "confidence": "screening", "reason": "insufficient_evidence",
    }
    if q.get("challenge"):
        return {**result, "reason": "challenge_only"}
    if q.get("review_status") in ("rejected", "quarantined") or q.get("quarantined"):
        return {**result, "reason": "quarantined"}
    if str(q.get("source", "")).startswith("self-generated-memory-drill"):
        if not (human_reviewed and q.get("diagnosis_eligible") is True):
            return {**result, "reason": "memory_drill_requires_independent_review"}

    # 不用“题干至少八字”等启发式排除合法短算式；这里只检查确定的结构损坏。
    choices = q.get("choices") or []
    if not str(q.get("prompt") or "").strip() or q.get("answer") in (None, ""):
        return {**result, "evidence_level": "broken", "reason": "missing_prompt_or_answer"}
    if q.get("type") == "choice":
        texts = [str(c.get("text") or "").strip() for c in choices]
        ids = [str(c.get("id") or "") for c in choices]
        if (len(choices) < 2 or not all(texts) or len(set(texts)) != len(texts)
                or not all(ids) or len(set(ids)) != len(ids)
                or str(q.get("answer")) not in ids):
            return {**result, "evidence_level": "broken", "reason": "broken_choices"}

    level = q.get("evidence_level")
    if level in ("broken", "defective"):
        return {**result, "evidence_level": level, "reason": "evidence_rejected"}
    if q.get("type") == "numeric" and q.get("verify"):
        from .verify import check_question
        try:
            checked = check_question(q)
        except (ArithmeticError, TypeError, ValueError, RecursionError):
            checked = {"status": "error"}
        if checked["status"] != "pass":
            return {**result, "evidence_level": "defective", "reason": "verify_failed"}
        # 显式分级可能包含题义/来源限制；算式一致不能覆盖较低的既有等级。
        if level is None:
            level = "verified" if is_own(q) else "bare"
    elif level is None:
        # 兼容既有自有内容，但没有证据的题只能练习，不能补一个“可信”标签。
        level = grade(q)["level"] if len(str(q.get("prompt", ""))) >= 8 else "bare"
    if level in ("broken", "defective"):
        return {**result, "evidence_level": level, "reason": "evidence_rejected"}

    allowed = (level in DIAGNOSABLE and q.get("diagnosis_eligible") is not False
               and q.get("for_diagnosis") is not False)
    return {
        **result, "evidence_level": level,
        "practice_eligible": q.get("practice_eligible") is not False,
        "diagnosis_eligible": allowed,
        "may_confirm_mastery": allowed and may_confirm_mastery(level),
        "may_judge_weak": allowed and may_judge_weak(level),
        "reason": ("provisional_evidence" if allowed else "practice_only"),
    }
