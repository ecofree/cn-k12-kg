"""题目答案核验：受限表达式求值。

思路来自参考项目的 verify 字段（如 "3+1" 表示答案应为 4），但按安全要求重写：
只允许数字常量、四则运算、乘方、取模，以及 abs / round / min / max / sqrt 五个函数，
用 AST 白名单校验，禁止属性访问、下标、推导式、关键字参数与任何名字注入。
"""
from __future__ import annotations

import ast
import math

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


def to_number(text):
    """把答案文本转成 float；失败返回 None。"""
    if text is None:
        return None
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def compare(answer, expected, tol=1e-6) -> bool:
    """比较答案与表达式结果。数值按容差比，非数值按去空白字符串比。"""
    a = to_number(answer)
    b = to_number(expected)
    if a is not None and b is not None:
        return abs(a - b) <= max(tol, abs(b) * 1e-9)
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
