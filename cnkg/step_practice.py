"""Bounded, owner-scoped linear-equation practice with immutable feedback events.

Only one real unknown x and rational linear expressions are supported. Stored
feedback is replayed, never recomputed by newer rules. This activity supplies
help and never publishes a mastery observation or resolves a learning need.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from . import answers
from .learning_store import ConflictError

ROOT = Path(__file__).resolve().parents[1]
UNIT_ID = "equation_equivalence"
EVENT_TYPES = ("step_practice_started", "step_practice_answered", "step_practice_hint_opened", "step_practice_draft_saved")
DRAFT_DEFAULTS = {"equation": "", "operation": "simplify", "operand": "", "used_help": False,
                  "conclusion": "unique", "value": ""}
MAX_ATTEMPTS = 60
MAX_HINTS = 12
OPERATIONS = {
    "simplify": "两边各自化简、去括号或合并同类项", "swap": "交换等号两边",
    "add": "两边同加", "subtract": "两边同减", "multiply": "两边同乘非零常数",
    "divide": "两边同除非零常数", "equivalent": "只检查等价式，暂未说明操作理由",
}
SCOPE = ["只支持一个实数未知数 x、有理数系数、常数分母的一次表达式；不处理含未知数的分母、二次项、函数或其他变量。",
         "可以采用不同的等价步骤。系统分别检查解集与所述操作，不推测你实际采用了哪种方法。",
         "每次核对会保存原输入、反馈和帮助记录。错误或暂不支持的输入也保留，只有接受的步骤会改变当前有效行。",
         "尚未核对的输入另存为草稿，可以包含未写完的式子；草稿不作数学判断、不计为已提交步骤。保存失败时先保留本页并重试。",
         "活动保存在当前档案并进入加密备份。换浏览器需恢复档案；换环境需导入备份。旧版本活动保留只读，不用新规则改判旧步骤。",
         "反馈和提示都是学习帮助，不是独立掌握证明；原有待解决事项不会因本活动完成而消失。"]
CASES = {
    "one_solution": {"id": "one_solution", "title": "括号与等价变形", "initial_equation": "3(x−2)=12",
        "prompt": "用你选择的等价步骤求解，每一步记录所做的操作，最后在原方程中检验。",
        "family_ids": ["equation.solve-linear"], "objective_ids": ["equation_equivalence:reversible", "equation_equivalence:distribute", "equation_equivalence:solve_check"]},
    "no_solution": {"id": "no_solution", "title": "消去未知数之后", "initial_equation": "2(x+3)=2x+7",
        "prompt": "先变形，再判断这个方程的解的情况。x 项消去后，不要急着除以一个数。",
        "family_ids": ["equation.solution-set-degeneracy"], "objective_ids": ["equation_equivalence:reversible", "equation_equivalence:solve_check"]},
    "identity": {"id": "identity", "title": "两边是否总是相等", "initial_equation": "2(x+3)=2x+6",
        "prompt": "检查变形后剩下的条件，区分某一个 x 成立与每个实数 x 都成立。",
        "family_ids": ["equation.solution-set-degeneracy"], "objective_ids": ["equation_equivalence:reversible", "equation_equivalence:solve_check"]},
}
_RENDERER_BYTES = (ROOT / "web/static/steps.js").read_bytes()
_RULES = {"schema": 1, "rule_name": "rational_linear_equation_steps_v1", "domain": "real", "variable": "x",
          "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "parser_sha256": hashlib.sha256(Path(answers.__file__).read_bytes()).hexdigest(),
          "normalizer_sha256": hashlib.sha256((ROOT / "cnkg/verify.py").read_bytes()).hexdigest(),
          "renderer_sha256": hashlib.sha256(_RENDERER_BYTES).hexdigest(),
          "limits": {"input_chars": 256, "operand_chars": 128, "max_attempts": MAX_ATTEMPTS, "max_hints": MAX_HINTS},
          "operations": OPERATIONS, "scope": SCOPE}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def rules_version():
    return _sha({"rules": _RULES, "cases": CASES})


def renderer_bytes():
    return _RENDERER_BYTES


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(raw, maximum, field, *, empty=False):
    if not isinstance(raw, str) or len(raw) > maximum or not empty and not raw.strip():
        raise ValueError(f"{field}须为不超过{maximum}字的{'非空' if not empty else ''}文字")
    return raw


def _expr(raw):
    """Reuse the bounded parser, but reject nonlinear intermediate syntax too."""
    _text(raw, 256, "表达式")
    parsed = answers._polynomial(raw, {"variables": ["x"]})
    stack = [parsed]
    while stack:
        node = stack.pop()
        if any(m[0] > 1 for m in node.value):
            raise ValueError("本练习只支持一次表达式，不能输入二次或更高次项")
        if node.op == "pow" and node.exponent == 0 and any(m[0] for m in node.children[0].value):
            raise ValueError("含未知数的零次幂可能需要额外定义域条件，本练习不处理")
        stack.extend(node.children)
    return parsed.value.get((1,), Fraction(0)), parsed.value.get((0,), Fraction(0))


def equation(raw):
    _text(raw, 256, "完整方程")
    value = raw.replace("＝", "=")
    if value.count("=") != 1:
        raise ValueError("请只写一个等号，等号左右都写完整表达式")
    left, right = value.split("=")
    return _expr(left), _expr(right)


def solution(eq):
    (la, lb), (ra, rb) = eq
    a, b = la - ra, lb - rb
    return ("unique", -b / a) if a else (("none", None) if b else ("all", None))


def _number(n):
    return str(n.numerator) if n.denominator == 1 else f"{n.numerator}/{n.denominator}"


def _linear(expr):
    a, b = expr
    if not a:
        return _number(b)
    prefix = "x" if a == 1 else "−x" if a == -1 else f"({_number(a)})x"
    return prefix if not b else prefix + ("+" if b > 0 else "−") + _number(abs(b))


def _evaluate(eq, x):
    return tuple(a * x + b for a, b in eq)


def _counterexample(old, new):
    before, after = solution(old), solution(new)
    if before[0] == "unique" and after[0] != "all":
        x = before[1]
    elif after[0] == "unique" and before[0] != "all":
        x = after[1]
    else:
        excluded = before[1] if before[0] == "unique" else after[1]
        x = Fraction(1) if excluded == 0 else Fraction(0)
    ol, or_ = _evaluate(old, x)
    nl, nr = _evaluate(new, x)
    return f"例如取 x={_number(x)}：原行两边为{_number(ol)}与{_number(or_)}，新行两边为{_number(nl)}与{_number(nr)}，成立情况不同。"


def _result(status, feedback, *, accepted=False, equivalence="not_checked", operation_status="not_checked", **extra):
    return {"status": status, "feedback": feedback, "accepted": accepted, "equivalence": equivalence,
            "operation_status": operation_status, "independent_evidence": False, "may_confirm_mastery": False, **extra}


def check_step(previous, raw, operation="equivalent", operand=""):
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ValueError("所述操作不在本练习支持范围内")
    _text(operand, 128, "操作数", empty=True)
    old = equation(previous)  # Invalid author data is not labelled a learner error.
    try:
        new = equation(raw)
    except ValueError as exc:
        return _result("unsupported", f"尚未判定这一步的数学对错：{exc}。请保留原输入，改用本练习支持的完整线性方程。")
    same = solution(old) == solution(new)
    equivalence = "same" if same else "different"
    if operation == "equivalent":
        if operand.strip():
            return _result("unsupported", "只检查等价式时不填写操作数；若要说明两边做了什么，请选择相应操作。", equivalence=equivalence)
        return (_result("equivalent", "新旧方程解集相同，已保存此行；所采用的操作理由仍未说明，不能据此确认你会解释等价条件。", accepted=True, equivalence="same", operation_status="unexplained")
                if same else _result("non_equivalent", "新旧方程的解集不同。" + _counterexample(old, new) + " 请从上一有效行修订。", equivalence="different", operation_status="unexplained"))
    if operation in {"simplify", "swap"}:
        if operand.strip():
            return _result("unsupported", "化简或交换两边不需要操作数，请清空该项。", equivalence=equivalence)
        expected = old if operation == "simplify" else (old[1], old[0])
    else:
        try:
            amount = _expr(operand)
        except ValueError as exc:
            return _result("unsupported", f"操作数暂未判定：{exc}。", equivalence=equivalence)
        if operation in {"multiply", "divide"}:
            if amount[0]:
                return _result("unsupported", "两边同乘或同除在这里仅支持已知非零常数；含x的量可能为0，也可能改变定义域，本练习不推断该附加条件。", equivalence=equivalence, operation_status="outside_scope")
            if not amount[1]:
                text = ("不能除以0，这个运算无定义。" if operation == "divide" else
                        "同乘0会把两边的信息都变成0，不能作为普遍可逆的等价变形规则。")
                text += ("本例新旧解集不同。" + _counterexample(old, new) if not same else
                         "本例新旧恰好都是恒等式，但这不证明同乘0是一般等价规则；请用化简解释原式为何恒等。")
                return _result("non_equivalent" if not same else "reason_mismatch", text, equivalence=equivalence, operation_status="unsafe")
            factor = amount[1] if operation == "multiply" else 1 / amount[1]
            expected = tuple((a * factor, b * factor) for a, b in old)
        else:
            sign = 1 if operation == "add" else -1
            expected = tuple((a + sign * amount[0], b + sign * amount[1]) for a, b in old)
    if new == expected:
        return _result("equivalent", "左右两边都符合你声明的操作，新旧方程解集相同。此步已保存；请继续核对原方程和最后的解。", accepted=True, equivalence="same", operation_status="verified")
    expected_text = f"按所述操作，左右两边应分别等价于 {_linear(expected[0])} 与 {_linear(expected[1])}。"
    if operation == "simplify":
        expected_text += " 去括号时外面的数要乘到每一项，前面的负号也要作用于每一项。"
    if same:
        return _result("reason_mismatch", "新方程的解集相同，但它不符合所选操作。" + expected_text + " 可修正这行，或另行选择实际采用的操作。", equivalence="same", operation_status="mismatch")
    return _result("non_equivalent", "本次变形改变了解集。" + expected_text + " " + _counterexample(old, new), equivalence="different", operation_status="mismatch")


def check_conclusion(initial, kind, value):
    if not isinstance(kind, str) or kind not in {"unique", "none", "all"}:
        raise ValueError("解的情况须选唯一解、无解或任意实数")
    _text(value, 128, "结论数值", empty=True)
    expected = solution(equation(initial))
    proposed = None
    if kind == "unique":
        try:
            a, proposed = _expr(value)
            if a:
                raise ValueError("唯一解的值须为一个数，不能再含x")
        except ValueError as exc:
            return _result("unsupported", f"结论尚未判定：{exc}。")
    elif value.strip():
        return _result("unsupported", "选择无解或任意实数时，请清空唯一解数值栏。")
    if (kind, proposed) != expected:
        detail = "先去括号再比较x的系数和常数；x项抵消后，检查留下的是相等常数还是矛盾。"
        if kind == "unique":
            left, right = _evaluate(equation(initial), proposed)
            detail = f"把x={_number(proposed)}代入原方程，左右分别为{_number(left)}和{_number(right)}。" + ("这个值虽然成立，但还需检查是否只有这一个解。" if left == right else "两边不相等。")
        return _result("conclusion_incorrect", detail + " 结论尚未完成，原有尝试已保留。")
    if kind == "unique":
        left, right = _evaluate(equation(initial), proposed)
        detail = f"原方程两边代入x={_number(proposed)}后同为{_number(left)}；合并后x的系数非零，所以只有这一个解。"
    elif kind == "none":
        (la, lb), (ra, rb) = equation(initial)
        detail = f"两边x系数相同，消去后常数为{_number(lb)}与{_number(rb)}，不相等，任何实数都不能使原式成立。"
    else:
        detail = "两边化简后的x系数与常数分别相同，原等式对每个实数x都成立；不需要也不能再除以0求一个特定x。"
    return _result("conclusion_correct", detail + " 已保存本题结论和检验；过程中的理由缺口与已有学习事项仍保留。", accepted=True)


def hint_text(current, level):
    if type(level) is not int or level not in (1, 2, 3):
        raise ValueError("提示级别只能为1、2或3")
    if level == 1:
        return "先检查等号两边做的是不是同一个操作。去括号要作用于每一项；同乘或同除的常数必须非零。原行没有成立的变形不要沿用。"
    left, right = equation(current)
    if level == 2:
        return f"当前有效行的左右两边分别化简为 {_linear(left)} 与 {_linear(right)}。对照展开后的每一项检查漏乘和负号，再决定下一步。"
    a, b = left[0] - right[0], left[1] - right[1]
    if a:
        return f"把右侧x项和左侧常数分别移去，可得到 {_linear((a, Fraction(0)))}={_number(-b)}。再同除非零常数{_number(a)}，得到x={_number(-b / a)}；须回原方程检验。这是已提供的帮助。"
    return ("合并后得到0=0，原方程对任意实数成立；不要再除以0。" if not b else
            f"合并后得到0={_number(-b)}，这是矛盾，原方程无解；不能通过除以0得到一个x。")


def _activity_id(owner, request_id):
    return hashlib.sha256(("cnkg-steps-v1\0" + owner + "\0" + request_id).encode()).hexdigest()[:32]


def _rows(db, owner, activity_id):
    if not isinstance(activity_id, str) or not re.fullmatch(r"[0-9a-f]{32}", activity_id):
        raise KeyError("分步活动不存在")
    return db.execute("""SELECT event_type, occurred_at, content_sha256, payload_json FROM events
        WHERE owner_id=? AND resource_id=? AND event_type IN (?,?,?,?) ORDER BY seq""",
        (owner, "steps:" + activity_id, *EVENT_TYPES)).fetchall()


def _replay(rows):
    if not rows:
        raise KeyError("分步活动不存在或不属于当前档案")
    first = json.loads(rows[0]["payload_json"])
    if rows[0]["event_type"] != EVENT_TYPES[0] or first.get("schema") != 1:
        raise ValueError("保存的分步活动格式暂不支持；原始事件仍保留")
    state = copy.deepcopy(first["activity"])
    state.update(timeline=[], current_equation=state["configuration"]["initial_equation"], completed=False,
                 draft=copy.deepcopy(DRAFT_DEFAULTS), revision=1, updated_at=rows[0]["occurred_at"],
                 requests={first["request_id"]: first["request_digest"]})
    for row in rows[1:]:
        payload = json.loads(row["payload_json"])
        if payload.get("schema") != 1 or payload.get("revision") != state["revision"] + 1:
            raise ValueError("活动事件次序或格式不一致；不能据此继续判定，原记录保留")
        state["revision"] = payload["revision"]
        state["updated_at"] = row["occurred_at"]
        state["requests"][payload["request_id"]] = payload["request_digest"]
        if row["event_type"] == EVENT_TYPES[3]:
            state["draft"] = copy.deepcopy(payload["draft"])
            continue
        item = payload["entry"]
        state["timeline"].append(item)
        if item["kind"] == "equation":
            state["draft"].update(equation="" if item["feedback"]["accepted"] else item["equation"],
                                  operation=item["operation"], operand=item["operand"], used_help=item["reported_help"])
        elif item["kind"] == "conclusion":
            state["draft"].update(conclusion=item["conclusion"], value=item["value"], used_help=item["reported_help"])
        if item["kind"] == "equation" and item["feedback"]["accepted"]:
            state["current_equation"] = item["equation"]
        if item["kind"] == "conclusion" and item["feedback"]["accepted"]:
            state["completed"] = True
    return state


def _public(state, current_resource_version):
    result = {k: copy.deepcopy(v) for k, v in state.items() if k != "requests"}
    result["read_only"] = (state["resource_version"] != current_resource_version or state["rules_version"] != rules_version())
    result["read_only_reason"] = ("内容或判定规则已经更新。本活动按原版本保留只读，历史反馈没有重算；可另开当前版本活动。" if result["read_only"] else "")
    result.update(independent_evidence=False, may_confirm_mastery=False)
    return result


def load(store, owner, activity_id, current_resource_version, *, expose=False):
    with store._connect() as db:
        state = _replay(_rows(db, owner, activity_id))
    if expose:
        store.add_event(owner, _help_exposure(state, _now(), initial=not state["timeline"]))
    return _public(state, current_resource_version)


def listing(store, owner, unit_id, current_resource_version, *, offset=0, limit=50):
    if unit_id != UNIT_ID:
        raise KeyError("本单元没有分步方程活动")
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("活动分页参数不正确")
    with store._connect() as db:
        total = db.execute("SELECT count(*) FROM events WHERE owner_id=? AND event_type=?", (owner, EVENT_TYPES[0])).fetchone()[0]
        identities = db.execute("""SELECT resource_id FROM events WHERE owner_id=? AND event_type IN (?,?,?,?)
            GROUP BY resource_id ORDER BY max(seq) DESC LIMIT ? OFFSET ?""", (owner, *EVENT_TYPES, limit, offset)).fetchall()
        activities = []
        for row in identities:
            state = _public(_replay(_rows(db, owner, row[0][6:])), current_resource_version)
            activities.append({"id": state["id"], "title": state["configuration"]["title"], "updated_at": state["updated_at"],
                "attempt_count": sum(e["kind"] != "hint" for e in state["timeline"]), "read_only": state["read_only"], "completed": state["completed"]})
    return {"unit_id": UNIT_ID, "resource_version": current_resource_version, "rules_version": rules_version(),
            "cases": [{"id": c["id"], "title": c["title"]} for c in CASES.values()], "scope": SCOPE,
            "operation_labels": OPERATIONS, "activities": activities, "offset": offset, "limit": limit, "total": total}


def _request(values, fields):
    if not isinstance(values, dict) or set(values) - fields:
        raise ValueError("分步请求含未知字段")
    _text(values.get("request_id"), 128, "请求标识")
    return _sha(values)


def _event(kind, when, identity, version, payload):
    return {"event_type": kind, "occurred_at": when, "resource_id": "steps:" + identity,
            "content_sha256": version, "payload": payload}


def _feedback_families(state, entry):
    """Only server-produced feedback declares the mathematical methods exposed."""
    families = set(state["configuration"]["family_ids"])
    if entry["kind"] == "hint":
        if entry["level"] in (1, 2):
            families.add("equation.distribution-sign-error")
        if entry["level"] == 1:
            families.update(("equation.multiply-zero-error", "equation.divide-variable-zero"))
        if entry["level"] == 3:
            families.add("equation.solve-linear" if solution(equation(state["current_equation"]))[0] == "unique"
                         else "equation.solution-set-degeneracy")
    elif entry["kind"] == "equation":
        feedback = entry["feedback"]
        if entry["operation"] == "simplify" and feedback["status"] in {"non_equivalent", "reason_mismatch"}:
            families.add("equation.distribution-sign-error")
        if entry["operation"] == "multiply" and feedback["operation_status"] == "unsafe":
            families.add("equation.multiply-zero-error")
        if entry["operation"] == "divide" and feedback["operation_status"] in {"unsafe", "outside_scope"}:
            families.add("equation.divide-variable-zero")
    return sorted(families)


def _help_exposure(state, when, *, initial=False, additional_families=()):
    # Reopening an activity re-exposes all the recorded feedback, not just its seed.
    families = set(state["configuration"]["family_ids"]) | set(additional_families)
    for entry in state.get("timeline", []):
        families.update(entry.get("reference_family_ids", []))
    # Index a real unit-resource activity so follow-up clocks include it.
    return {"event_type": "material_provided" if initial else "reference_opened", "occurred_at": when,
            "resource_id": "unit:" + UNIT_ID, "content_sha256": state["resource_version"],
            "payload": {"family_ids": sorted(families), "task_ids": [],
                        "activity_id": state["id"], "independent_evidence": False,
                        "evidence": "bounded_step_practice_provided" if initial else "step_feedback_or_hint_provided"}}


def start(store, owner, values, current_resource_version, *, now=None):
    digest = _request(values, {"unit_id", "case_id", "expected_resource_version", "request_id"})
    if values.get("unit_id") != UNIT_ID or not isinstance(values.get("case_id"), str) or values["case_id"] not in CASES:
        raise KeyError("分步练习不存在")
    identity = _activity_id(owner, values["request_id"])
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = _rows(db, owner, identity)
        if prior:
            state = _replay(prior)
            if state["requests"].get(values["request_id"]) != digest:
                raise ConflictError("相同请求标识不能用于不同的新活动")
            store._insert_events(db, owner, None, [_help_exposure(state, now or _now(), initial=not state["timeline"])])
            return _public(state, current_resource_version)
        if values.get("expected_resource_version") != current_resource_version:
            raise ConflictError("单元材料已更新，请重新打开单元后开始；原活动仍保留")
        when = now or _now()
        state = {"id": identity, "unit_id": UNIT_ID, "created_at": when,
                 "resource_version": current_resource_version, "rules_version": rules_version(),
                 "configuration": copy.deepcopy(CASES[values["case_id"]]), "rules_snapshot": copy.deepcopy(_RULES)}
        payload = {"schema": 1, "request_id": values["request_id"], "request_digest": digest, "activity": state}
        store._insert_events(db, owner, None, [_event(EVENT_TYPES[0], when, identity, current_resource_version, payload), _help_exposure(state, when, initial=True)])
        return _public(_replay(_rows(db, owner, identity)), current_resource_version)


def submit(store, owner, values, current_resource_version, *, hint=False, now=None):
    fields = {"activity_id", "expected_revision", "request_id", "level"} if hint else {
        "activity_id", "expected_revision", "request_id", "kind", "equation", "operation", "operand", "used_help", "conclusion", "value"}
    digest = _request(values, fields)
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _replay(_rows(db, owner, values.get("activity_id")))
        if values["request_id"] in state["requests"]:
            if state["requests"][values["request_id"]] != digest:
                raise ConflictError("相同请求标识对应不同输入，原记录没有被覆盖")
            store._insert_events(db, owner, None, [_help_exposure(state, now or _now(), initial=not state["timeline"])])
            response = _public(state, current_resource_version)
            response["replayed"] = True
            return response
        public = _public(state, current_resource_version)
        if public["read_only"]:
            raise ConflictError(public["read_only_reason"])
        if type(values.get("expected_revision")) is not int or values["expected_revision"] != state["revision"]:
            raise ConflictError("活动已有新记录，请读回后继续；你刚写的输入尚未覆盖任何历史")
        if state["completed"]:
            raise ConflictError("本次已经留下结论，可另开练习；已有步骤仍保留")
        when = now or _now()
        if hint:
            if sum(e["kind"] == "hint" for e in state["timeline"]) >= MAX_HINTS:
                raise ConflictError("本活动已达到提示记录上限，请回看已有提示或另开活动；原记录保留")
            text = hint_text(state["current_equation"], values.get("level"))
            entry = {"kind": "hint", "level": values["level"], "text": text, "occurred_at": when, "help_used": True}
            feedback = {"status": "hint", "text": text, "independent_evidence": False}
        else:
            if sum(e["kind"] != "hint" for e in state["timeline"]) >= MAX_ATTEMPTS:
                raise ConflictError("本活动已达到60次输入上限，请保留本记录并另开活动")
            if type(values.get("used_help", False)) is not bool:
                raise ValueError("帮助标记必须是布尔值")
            kind = values.get("kind", "equation")
            if kind == "equation":
                _text(values.get("equation"), 256, "下一行方程")
                operation = values.get("operation", "equivalent")
                if not isinstance(operation, str):
                    raise ValueError("所述操作必须为文字标识")
                operand = values.get("operand", "")
                feedback = check_step(state["current_equation"], values["equation"], operation, operand)
                entry = {"kind": kind, "equation": values["equation"], "operation": operation, "operand": operand,
                         "previous_equation": state["current_equation"]}
            elif kind == "conclusion":
                feedback = check_conclusion(state["configuration"]["initial_equation"], values.get("conclusion"), values.get("value", ""))
                entry = {"kind": kind, "conclusion": values.get("conclusion"), "value": values.get("value", "")}
            else:
                raise ValueError("输入类型须为方程步骤或结论")
            entry.update(feedback=feedback, occurred_at=when, reported_help=values.get("used_help", False),
                         help_used=bool(values.get("used_help", False) or state["timeline"]),
                         help_basis="guidance_activity_with_prior_feedback" if state["timeline"] else "learner_report_for_first_input")
        entry["reference_family_ids"] = _feedback_families(state, entry)
        payload = {"schema": 1, "revision": state["revision"] + 1, "request_id": values["request_id"],
                   "request_digest": digest, "entry": entry}
        event_type = EVENT_TYPES[2] if hint else EVENT_TYPES[1]
        store._insert_events(db, owner, None, [_event(event_type, when, state["id"], state["resource_version"], payload), _help_exposure(state, when, additional_families=entry["reference_family_ids"])])
        response = _public(_replay(_rows(db, owner, state["id"])), current_resource_version)
        response["feedback"] = feedback
        return response


def save_draft(store, owner, values, current_resource_version, *, now=None):
    """Persist unfinished literal fields without parsing, feedback, or an attempt."""
    digest = _request(values, {"activity_id", "expected_revision", "request_id", "draft"})
    draft = values.get("draft")
    if not isinstance(draft, dict) or set(draft) != set(DRAFT_DEFAULTS):
        raise ValueError("草稿字段不完整或含未知字段")
    for name, maximum in (("equation", 256), ("operand", 128), ("value", 128)):
        _text(draft[name], maximum, "草稿" + name, empty=True)
    if not isinstance(draft["operation"], str) or draft["operation"] not in OPERATIONS:
        raise ValueError("草稿所述操作不在支持范围内")
    if not isinstance(draft["conclusion"], str) or draft["conclusion"] not in {"unique", "none", "all"}:
        raise ValueError("草稿解的情况选项不正确")
    if type(draft["used_help"]) is not bool:
        raise ValueError("草稿帮助标记必须是布尔值")
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _replay(_rows(db, owner, values.get("activity_id")))
        if values["request_id"] in state["requests"]:
            if state["requests"][values["request_id"]] != digest:
                raise ConflictError("相同请求标识对应不同草稿，原记录没有被覆盖")
            return _public(state, current_resource_version)
        public = _public(state, current_resource_version)
        if public["read_only"]:
            raise ConflictError(public["read_only_reason"])
        if type(values.get("expected_revision")) is not int or values["expected_revision"] != state["revision"]:
            raise ConflictError("活动已有新记录，请读回后保存草稿；原有草稿仍保留")
        if state["completed"]:
            raise ConflictError("本次已记录结论，不能再改草稿；可另开练习")
        when = now or _now()
        payload = {"schema": 1, "revision": state["revision"] + 1, "request_id": values["request_id"],
                   "request_digest": digest, "draft": copy.deepcopy(draft), "evidence": "unsubmitted_draft_not_graded"}
        store._insert_events(db, owner, None, [
            _event(EVENT_TYPES[3], when, state["id"], state["resource_version"], payload),
            {"event_type": "material_requested", "occurred_at": when, "resource_id": "unit:" + UNIT_ID,
             "content_sha256": state["resource_version"], "payload": {"activity_id": state["id"],
                 "evidence": "learner_editing_unsubmitted_step_draft", "independent_evidence": False}}])
        return _public(_replay(_rows(db, owner, state["id"])), current_resource_version)
