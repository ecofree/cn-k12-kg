"""题目答案核验：受限表达式求值。

思路来自参考项目的 verify 字段（如 "3+1" 表示答案应为 4），但按安全要求重写：
只允许数字常量、四则运算、乘方、取模，以及 abs / round / min / max / sqrt 五个函数，
用 AST 白名单校验，禁止属性访问、下标、推导式、关键字参数与任何名字注入。
"""
from __future__ import annotations

import ast
import math
import re
from fractions import Fraction

_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call,
    ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
    ast.Mod, ast.USub, ast.UAdd, ast.Tuple,
)


class VerifyError(ValueError):
    """表达式不合法或不安全。"""


def evaluate(expr: str):
    """在受限沙箱里求值，返回数值。"""
    if not isinstance(expr, str) or not expr.strip():
        raise VerifyError("verify 表达式为空")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise VerifyError(f"语法错误：{exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise VerifyError(f"不允许的语法：{type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise VerifyError("只允许数字常量")
        if isinstance(node, ast.Name) and node.id not in _FUNCS:
            raise VerifyError(f"不允许的名字：{node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise VerifyError("只能调用白名单函数")
            if node.keywords:
                raise VerifyError("不允许关键字参数")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and abs(node.right.value) > 12:
                raise VerifyError("幂次过大")

    return eval(compile(tree, "<verify>", "eval"), {"__builtins__": {}}, dict(_FUNCS))  # noqa: S307


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?"
_NUMBER_RE = re.compile(rf"(?:{_NUMBER})(?:\s*/\s*(?:{_NUMBER}))?\Z")
_NUMERIC_TRANSLATION = str.maketrans(
    "０１２３４５６７８９＋－．／−", "0123456789+-./-")


def to_number(text):
    """解析有限小数、科学记数法或一个分数；不执行学生输入的表达式。

    例如 1/2、2/4 与 0.5 等值。长度和指数有界，零分母、NaN、
    无穷、单位、算式和代码均不是数值答案。单位由题干约定。
    """
    if text is None or isinstance(text, bool):
        return None
    value = str(text).strip().translate(_NUMERIC_TRANSLATION)
    if len(value) > 128 or not _NUMBER_RE.fullmatch(value):
        return None
    if any(abs(int(exp)) > 100 for exp in re.findall(r"[eE]([+-]?\d+)", value)):
        return None
    try:
        parts = value.split("/")
        number = Fraction(parts[0].strip())
        if len(parts) == 2:
            number /= Fraction(parts[1].strip())
        result = float(number)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def compare(answer, expected, tol=1e-6) -> bool:
    """比较答案与表达式结果。数值按容差比，非数值按去空白字符串比。"""
    a = to_number(answer)
    b = to_number(expected)
    if a is not None and b is not None:
        return abs(a - b) <= max(tol, abs(b) * 1e-9)
    # 一边可解析时不能回落为字符串；非有限数值也永不视为正确。
    if a is not None or b is not None:
        return False
    invalid = {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}
    if str(answer).strip().lower() in invalid or str(expected).strip().lower() in invalid:
        return False
    return str(answer).strip() == str(expected).strip()


_FULL2HALF = str.maketrans("０１２３４５６７８９（）＋－＝．，％", "0123456789()+-=.,%")
_TRAIL = "。．.；;，,、 "


def normalize_text(value) -> str:
    """短答归一化：全角转半角、去空白、去句末标点。

    只做形式归一，不做同义改写——同义说法必须显式写进 accept，
    否则"差不多算对"会让猜测率统计失真。
    """
    import re
    text = str(value if value is not None else "").strip().translate(_FULL2HALF)
    text = re.sub(r"\s+", "", text)
    return text.rstrip(_TRAIL)


def compare_short(value, answer, accept=()) -> bool:
    """短答判分：与 answer 或 accept 中任一项归一化后相等；两边都是数字时按数值比。"""
    norm = normalize_text(value)
    if not norm:
        return False
    for candidate in (answer, *(accept or ())):
        target = normalize_text(candidate)
        if not target:
            continue
        if norm == target:
            return True
        a, b = to_number(norm), to_number(target)
        if a is not None and b is not None and abs(a - b) <= 1e-9:
            return True
    return False


def check_question(question: dict) -> dict:
    """核验单题，返回 {id, status, detail}。

    status:
      pass         有 verify 且与 answer 一致
      mismatch     有 verify 但与 answer 不一致
      unverifiable 没有 verify（选择题等），只能人工/模型复核
      error        verify 表达式非法
    """
    qid = question.get("id", "<no-id>")
    expr = question.get("verify")
    answer = question.get("answer")
    if answer in (None, ""):
        return {"id": qid, "status": "error", "detail": "answer 为空"}
    if not expr:
        return {"id": qid, "status": "unverifiable", "detail": "无 verify 表达式"}
    try:
        value = evaluate(expr)
    except VerifyError as exc:
        return {"id": qid, "status": "error", "detail": str(exc)}
    if compare(answer, value):
        return {"id": qid, "status": "pass", "detail": f"{expr} = {value}"}
    return {"id": qid, "status": "mismatch", "detail": f"{expr} = {value}，但答案写的是 {answer}"}
