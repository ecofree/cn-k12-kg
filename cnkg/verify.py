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

class VerifyError(ValueError):
    """表达式不合法或不安全。"""


def evaluate_precise(expr: str):
    """Bounded AST arithmetic: rational operations stay exact.

    Only an intrinsically non-rational operation (such as sqrt(2)) returns a
    float. Explicit round converts the requested rounded decimal back to an
    exact rational. No Python code is executed, including author expressions.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise VerifyError("verify 表达式为空")
    if len(expr) > 4096:
        raise VerifyError("verify 表达式过长")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise VerifyError("verify 语法错误或嵌套过深") from exc
    if sum(1 for _ in ast.walk(tree)) > 512:
        raise VerifyError("verify 语法节点过多")

    def bounded(value):
        if isinstance(value, bool) or not isinstance(value, (Fraction, int, float)):
            raise VerifyError("只允许实数运算")
        if isinstance(value, int):
            value = Fraction(value)
        if isinstance(value, Fraction):
            if max(value.numerator.bit_length(), value.denominator.bit_length()) > 1024:
                raise VerifyError("中间有理数过大")
        elif not math.isfinite(value) or abs(value) > 1e200:
            raise VerifyError("中间浮点数超出范围")
        return value

    def integer_root(value, degree):
        """Return an integer root only when its power proves exact equality."""
        if value in (0, 1) or degree == 1:
            return value
        if degree > value.bit_length():
            return None
        low, high = 1, 1 << ((value.bit_length() + degree - 1) // degree)
        while low <= high:
            middle = (low + high) // 2
            power = middle ** degree
            if power == value:
                return middle
            if power < value:
                low = middle + 1
            else:
                high = middle - 1
        return None

    def rational_power(base, exponent):
        if abs(exponent) > 12:
            raise VerifyError("计算后的幂次过大")
        if base == 0 and exponent <= 0:
            raise VerifyError("零的非正次幂未定义")
        if isinstance(exponent, Fraction) and exponent.denominator == 1:
            return bounded(base ** exponent.numerator)
        if isinstance(base, Fraction) and isinstance(exponent, Fraction):
            degree = exponent.denominator
            if base < 0 and degree % 2 == 0:
                raise VerifyError("负数没有实数偶次方根")
            numerator = integer_root(abs(base.numerator), degree)
            denominator = integer_root(base.denominator, degree)
            if numerator is not None and denominator is not None:
                root = Fraction(numerator if base >= 0 else -numerator, denominator)
                # A nontrivial exact root limits its degree by the operand's
                # bit length; cap the resulting power before constructing it.
                if max(root.numerator.bit_length(), root.denominator.bit_length()) * abs(exponent.numerator) > 2048:
                    if abs(root) != 1 and root != 0:
                        raise VerifyError("中间有理数过大")
                return bounded(root ** exponent.numerator)
        return bounded(math.pow(float(base), float(exponent)))

    def sqrt_call(node):
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "sqrt" and len(node.args) == 1 and not node.keywords)

    def walk(node, depth=0):
        if depth > 32:
            raise VerifyError("verify 嵌套过深")
        if isinstance(node, ast.Expression):
            return walk(node.body, depth + 1)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise VerifyError("只允许数字常量")
            if isinstance(node.value, int):
                return bounded(node.value)
            literal = ast.get_source_segment(expr, node)
            value = to_fraction(literal.replace("_", "") if literal else str(node.value))
            if value is None:
                raise VerifyError("数字常量超出范围")
            return bounded(value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = walk(node.operand, depth + 1)
            return bounded(value if isinstance(node.op, ast.UAdd) else -value)
        if isinstance(node, ast.BinOp):
            if (isinstance(node.op, (ast.Mult, ast.Div))
                    and sqrt_call(node.left) and sqrt_call(node.right)):
                # An exact identity on the nonnegative real domain, rather
                # than tolerating a floating residue around an integer.
                left = walk(node.left.args[0], depth + 2)
                right = walk(node.right.args[0], depth + 2)
                if left < 0 or right < 0 or (isinstance(node.op, ast.Div) and right == 0):
                    raise VerifyError("平方根乘除的定义域无效")
                product = left * right if isinstance(node.op, ast.Mult) else left / right
                return rational_power(bounded(product), Fraction(1, 2))
            left, right = walk(node.left, depth + 1), walk(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                return bounded(left + right)
            if isinstance(node.op, ast.Sub):
                return bounded(left - right)
            if isinstance(node.op, ast.Mult):
                if (isinstance(left, Fraction) and left == 0) or (isinstance(right, Fraction) and right == 0):
                    return Fraction(0)
                return bounded(left * right)
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise VerifyError("不能除以零")
                if isinstance(left, Fraction) and left == 0:
                    return Fraction(0)
                return bounded(left / right)
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise VerifyError("不能对零取余")
                return bounded(left % right)
            if isinstance(node.op, ast.Pow):
                return rational_power(left, right)
            raise VerifyError("不支持的算术运算")
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS
                    or node.keywords or not 1 <= len(node.args) <= 64):
                raise VerifyError("只能调用有界的白名单函数")
            name = node.func.id
            if name in ("min", "max"):
                nodes = node.args
                if len(nodes) == 1 and isinstance(nodes[0], ast.Tuple):
                    nodes = nodes[0].elts
                if not 1 <= len(nodes) <= 64:
                    raise VerifyError("min/max 参数数量无效")
                values = [walk(item, depth + 1) for item in nodes]
                return bounded(min(values) if name == "min" else max(values))
            if name == "abs" and len(node.args) == 1:
                return bounded(abs(walk(node.args[0], depth + 1)))
            if name == "sqrt" and len(node.args) == 1:
                value = walk(node.args[0], depth + 1)
                if value < 0:
                    raise VerifyError("实数平方根的被开方数不能为负")
                return rational_power(value, Fraction(1, 2))
            if name == "round" and len(node.args) in (1, 2):
                value = walk(node.args[0], depth + 1)
                digits = walk(node.args[1], depth + 1) if len(node.args) == 2 else Fraction(0)
                if int(digits) != digits or abs(digits) > 12:
                    raise VerifyError("round 位数必须是 -12—12 的整数")
                rounded = round(value, int(digits))
                return bounded(Fraction(str(rounded)))
            raise VerifyError("函数参数数量不正确")
        raise VerifyError(f"不允许的语法：{type(node).__name__}")

    try:
        return walk(tree)
    except VerifyError:
        raise
    except (ArithmeticError, ValueError, TypeError, RecursionError) as exc:
        raise VerifyError(f"表达式无法安全求值：{exc}") from exc


def evaluate(expr: str):
    """兼容作者工具的 int/float 接口，内部仍走有界 AST 解释器。"""
    value = evaluate_precise(expr)
    if isinstance(value, Fraction):
        return value.numerator if value.denominator == 1 else float(value)
    return value


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?"
_NUMBER_RE = re.compile(rf"(?:{_NUMBER})(?:\s*/\s*(?:{_NUMBER}))?\Z")
_NUMERIC_TRANSLATION = str.maketrans(
    "０１２３４５６７８９＋－．／−", "0123456789+-./-")


def to_fraction(text):
    """精确解析有限小数、科学记数法或一个分数，不执行输入表达式。

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
        return number
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def to_number(text):
    """旧分析/导出接口的有限浮点值；学生判分必须使用 to_fraction。

    原始作答另行保留。此接口保留兼容性，不作为数学等价判定依据。
    """
    number = to_fraction(text)
    if number is None:
        return None
    try:
        value = float(number)
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def compare(answer, expected, tol=1e-6) -> bool:
    """旧工具的浮点比较兼容接口，不用于正式作者核验或学生判分。

    作者核验使用 check_question 的有界精确求值；学生输入必须走
    cnkg.answers.grade_answer 的逐题精确/近似/表示形式契约。
    """
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
        a, b = to_fraction(norm), to_fraction(target)
        if a is not None and b is not None and a == b:
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
        value = evaluate_precise(expr)
    except VerifyError as exc:
        return {"id": qid, "status": "error", "detail": str(exc)}
    from .answers import grade_answer, validate_answer_spec
    declared = question.get("answer_spec")
    if declared is not None and not isinstance(declared, dict):
        return {"id": qid, "status": "error", "detail": "数值验算的 answer_spec 无效"}
    spec = dict(declared if declared is not None else {"kind": "number", "comparison": "exact"})
    if spec.get("kind") != "number" or validate_answer_spec(spec):
        return {"id": qid, "status": "error", "detail": "数值验算的 answer_spec 无效"}
    # Representation is a student response requirement, not a demand that the
    # compiler print its exact rational in a particular decimal form.
    spec["representation"] = "any"
    grading_task = {"type": "numeric", "answer": str(answer), "answer_spec": spec}
    if isinstance(value, Fraction):
        result = grade_answer(grading_task, str(value))
    else:
        # Irrational arithmetic requires explicit precision. A narrow interval
        # around the floating computation must agree under that precision;
        # uncertain boundary cases are sent back to the author, never passed.
        if spec.get("comparison", "exact") == "exact":
            return {"id": qid, "status": "error", "detail": "非有理数结果必须声明舍入或误差，或在 verify 中明确 round"}
        center = to_fraction(str(value))
        error = to_fraction(str(math.ulp(value) * 8))
        if center is None or error is None:
            return {"id": qid, "status": "error", "detail": "非有理数结果超出可核验精度"}
        results = [grade_answer(grading_task, str(center + sign * error)) for sign in (-1, 1)]
        if results[0]["status"] != results[1]["status"]:
            return {"id": qid, "status": "error", "detail": "计算结果接近精度边界，需提高验算精度或澄清题意"}
        result = results[0]
    if result["status"] == "correct":
        return {"id": qid, "status": "pass", "detail": f"{expr} = {value}"}
    if result["status"] in ("invalid", "needs_review"):
        return {"id": qid, "status": "error", "detail": "答案或评分契约无法核验"}
    return {"id": qid, "status": "mismatch", "detail": f"{expr} = {value}，但答案写的是 {answer}"}
