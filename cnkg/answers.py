"""Bounded, deterministic answer contracts shared by learning and topic tasks.

Student answers never reach eval, a CAS, or random-point equivalence tests.
Numbers remain rational; polynomials use exact sparse arithmetic with fixed
input, token, degree and term limits. Legacy inference is an explicit migration
helper; compiled content should persist answer_spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import re

from .verify import normalize_text, to_fraction
from .contracts import load_schema

SPEC_VERSION = 1
_SPEC_SCHEMA = load_schema("answer-spec")
KINDS = frozenset(_SPEC_SCHEMA["properties"]["kind"]["enum"])
_FIELDS = frozenset(_SPEC_SCHEMA["properties"])
_NUMBER_FORMS = frozenset(_SPEC_SCHEMA["properties"]["representation"]["enum"]) - {"expanded", "factored"}
_TOLERANCE_PATTERN = re.compile(_SPEC_SCHEMA["properties"]["absolute_tolerance"]["pattern"])
_INPUT_TRANSLATION = str.maketrans({"−": "-", "–": "-", "×": "*", "·": "*",
                                  "÷": "/", "，": ",", "；": ";", "［": "[", "］": "]",
                                  "｛": "{", "｝": "}", "≤": "<=", "≥": ">="})
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


def validate_answer_spec(spec) -> list[str]:
    """Runtime safety checks; JSON Schema is the authoring contract authority."""
    if not isinstance(spec, dict):
        return ["answer_spec 必须是对象"]
    errors = []
    if set(spec) - _FIELDS:
        errors.append("answer_spec 包含未知字段")
    if spec.get("version", 1) != 1 or isinstance(spec.get("version", 1), bool):
        errors.append("不支持的 answer_spec.version")
    kind = spec.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        return errors + ["不支持的 answer_spec.kind"]
    comparison = spec.get("comparison", "exact")
    if comparison not in ("exact", "rounded", "tolerance"):
        errors.append("不支持的数值比较方式")
    if "comparison" in spec and kind != "number":
        errors.append("comparison 仅用于 number")
    form = spec.get("representation", "any")
    allowed_forms = {"any", "expanded", "factored"} if kind == "polynomial" else _NUMBER_FORMS
    if not isinstance(form, str) or form not in allowed_forms:
        errors.append("不支持的答案表示形式")
    if "representation" in spec and kind not in ("number", "polynomial"):
        errors.append("representation 仅用于 number 或 polynomial")
    places = spec.get("decimal_places")
    if places is not None and (isinstance(places, bool) or not isinstance(places, (int, float))
                               or not math.isfinite(places) or not 0 <= places <= 12 or int(places) != places):
        errors.append("decimal_places 必须在 0—12 之间")
    if comparison == "rounded" and places is None:
        errors.append("rounded 比较必须声明 decimal_places")
    if "decimal_places" in spec and (kind != "number" or comparison != "rounded"):
        errors.append("decimal_places 仅用于 number/rounded")
    if spec.get("rounding", "half_up") not in ("half_up", "half_even"):
        errors.append("不支持的舍入方式")
    if "rounding" in spec and (kind != "number" or comparison != "rounded"):
        errors.append("rounding 仅用于 number/rounded")
    declared_tolerance = False
    for key in ("absolute_tolerance", "relative_tolerance"):
        if key not in spec:
            continue
        value = (to_fraction(spec[key]) if isinstance(spec[key], str)
                 and _TOLERANCE_PATTERN.fullmatch(spec[key]) else None)
        if value is None or value < 0:
            errors.append(f"{key} 必须是非负的数值字符串")
        declared_tolerance = True
        if kind != "number" or comparison != "tolerance":
            errors.append(f"{key} 仅用于 number/tolerance")
    if comparison == "tolerance" and not declared_tolerance:
        errors.append("tolerance 比较必须逐题声明误差")
    if "dimensions" in spec:
        d = spec["dimensions"]
        if (kind != "coordinate" or isinstance(d, bool) or not isinstance(d, (int, float))
                or not math.isfinite(d) or not 2 <= d <= 4 or int(d) != d):
            errors.append("coordinate.dimensions 必须为 2—4 的整数")
    if "order" in spec and (kind != "number_list" or spec["order"] not in ("given", "ascending", "descending")):
        errors.append("不支持的 number_list.order")
    if "variables" in spec:
        variables = spec["variables"]
        if (kind != "polynomial" or not isinstance(variables, list) or not 1 <= len(variables) <= 4
                or any(not isinstance(v, str) or not re.fullmatch("[a-z]", v) for v in variables)
                or len(set(variables)) != len(variables)):
            errors.append("polynomial.variables 须为 1—4 个不同的小写字母")
    for key in ("integer_coefficients", "complete_factorization"):
        if key in spec and (kind != "polynomial" or not isinstance(spec[key], bool)):
            errors.append(f"{key} 仅接受 polynomial 的布尔值")
    return errors


def option_id(question, value):
    """Stable option IDs are identities; letter input denotes display position."""
    token = str(value or "").strip()
    ids = [c.get("id") for c in question.get("choices") or []]
    if token in ids:
        return token
    if len(token) == 1 and "A" <= token.upper() <= "Z":
        index = ord(token.upper()) - ord("A")
        if index < len(ids):
            return ids[index]
    return None


def option_labels(question):
    return [(chr(65 + i), c["id"], c["text"])
            for i, c in enumerate(question.get("choices") or [])]


def display_answer(question):
    if question.get("type") == "choice":
        for label, identity, text in option_labels(question):
            if identity == question.get("answer"):
                return f"{label}. {text}"
    return str(question.get("answer", ""))


def _math_text(value):
    # Digit whitespace is not silently changed into a different integer.
    raw = str(value).strip()
    if re.search(r"\d\s+\d", raw):
        raise ValueError("数字内部不能有空格")
    raw = normalize_text(raw).translate(_INPUT_TRANSLATION)
    raw = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda m: "^" + m[0].translate(_SUPERSCRIPT), raw)
    return raw.replace("**", "^")


def _numbers(value, wrapper=None):
    text = _math_text(value)
    if len(text) > 1024:
        raise ValueError("输入过长")
    if wrapper:
        if not (text.startswith(wrapper[0]) and text.endswith(wrapper[1])):
            raise ValueError("括号形式不正确")
        text = text[1:-1]
    parts = text.replace(";", ",").split(",")
    if not 1 <= len(parts) <= 64:
        raise ValueError("数值列表过长")
    values = [to_fraction(p) for p in parts]
    if any(v is None for v in values):
        raise ValueError("每个分量应为有限数字或分数")
    return tuple(values)


def _round_fraction(value, places, rounding):
    scale = 10 ** int(places)
    scaled = abs(value) * scale
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    twice = remainder * 2
    if twice > scaled.denominator or (twice == scaled.denominator
                                      and (rounding == "half_up" or quotient % 2)):
        quotient += 1
    return Fraction(quotient if value >= 0 else -quotient, scale)


def _number_form(raw, spec):
    text = _math_text(raw)
    form = spec.get("representation", "any")
    if form == "any":
        return True
    if form == "integer":
        return bool(re.fullmatch(r"[+-]?\d+", text))
    if form == "decimal":
        match = re.fullmatch(r"[+-]?(?:\d+)?\.([0-9]+)", text)
        if not match:
            return False
        return spec.get("comparison") != "rounded" or len(match[1]) == spec["decimal_places"]
    if form == "scientific":
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))[eE]([+-]?\d+)", text)
        if not match:
            return False
        coefficient = to_fraction(match[1])
        return coefficient == 0 or 1 <= abs(coefficient) < 10
    match = re.fullmatch(r"([+-]?\d+)/([+-]?\d+)", text)
    if not match or int(match[2]) == 0:
        return False
    if form == "fraction":
        return True
    numerator, denominator = int(match[1]), int(match[2])
    return denominator > 0 and math.gcd(abs(numerator), denominator) == 1


# Sparse polynomials are exact maps of exponent tuples to rational coefficients.
# Resource limits make both parsing and expansion bounded on adversarial input.
_MAX_TERMS = 128
_MAX_DEGREE = 24
_MAX_COEFFICIENT_BITS = 1024


@dataclass
class _Expr:
    value: dict
    op: str
    children: tuple = ()
    exponent: int = 1


def _clean(poly):
    out = {m: c for m, c in poly.items() if c}
    if len(out) > _MAX_TERMS:
        raise ValueError("多项式项数过多")
    for monomial, coefficient in out.items():
        if (sum(monomial) > _MAX_DEGREE
                or coefficient.numerator.bit_length() > _MAX_COEFFICIENT_BITS
                or coefficient.denominator.bit_length() > _MAX_COEFFICIENT_BITS):
            raise ValueError("多项式超出支持范围")
    return out


def _add(left, right, sign=1):
    out = dict(left)
    for monomial, coefficient in right.items():
        out[monomial] = out.get(monomial, Fraction(0)) + sign * coefficient
    return _clean(out)


def _mul(left, right):
    if len(left) * len(right) > _MAX_TERMS * _MAX_TERMS:
        raise ValueError("乘积展开过大")
    out = {}
    for lm, lc in left.items():
        for rm, rc in right.items():
            monomial = tuple(a + b for a, b in zip(lm, rm))
            out[monomial] = out.get(monomial, Fraction(0)) + lc * rc
    return _clean(out)


class _PolynomialParser:
    def __init__(self, raw, variables):
        text = _math_text(raw)
        if not text or len(text) > 512:
            raise ValueError("表达式为空或过长")
        token_re = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?|[a-z]|[()+*/^\-]")
        self.tokens = token_re.findall(text)
        if "".join(self.tokens) != text or len(self.tokens) > 96:
            raise ValueError("表达式含不支持的符号或过长")
        for left, right in zip(self.tokens, self.tokens[1:]):
            if to_fraction(left) is not None and to_fraction(right) is not None:
                raise ValueError("两个数字之间须有运算符")
        self.variables = tuple(variables)
        self.zero = (0,) * len(variables)
        self.index = 0
        self.depth = 0

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def pop(self):
        token = self.peek()
        if token is None:
            raise ValueError("表达式不完整")
        self.index += 1
        return token

    def parse(self):
        out = self.sum()
        if self.peek() is not None:
            raise ValueError("表达式存在多余符号")
        return out

    def sum(self):
        value = self.product()
        while self.peek() in ("+", "-"):
            op = self.pop()
            right = self.product()
            value = _Expr(_add(value.value, right.value, 1 if op == "+" else -1), op, (value, right))
        return value

    def product(self):
        value = self.unary()
        while self.peek() is not None:
            token = self.peek()
            implicit = token == "(" or token in self.variables or bool(re.fullmatch(r"(?:\d.*|\.\d.*)", token))
            if token not in ("*", "/") and not implicit:
                break
            op = self.pop() if not implicit else "*"
            right = self.unary()
            if op == "/":
                if set(right.value) != {self.zero} or not right.value[self.zero]:
                    raise ValueError("多项式只支持除以非零常数")
                divisor = right.value[self.zero]
                value = _Expr(_clean({m: c / divisor for m, c in value.value.items()}), "/", (value, right))
            else:
                value = _Expr(_mul(value.value, right.value), "*", (value, right))
        return value

    def unary(self):
        if self.peek() in ("+", "-"):
            sign = self.pop()
            self.depth += 1
            if self.depth > 20:
                raise ValueError("表达式嵌套过深")
            child = self.unary()
            self.depth -= 1
            return child if sign == "+" else _Expr({m: -c for m, c in child.value.items()}, "neg", (child,))
        return self.power()

    def power(self):
        value = self.atom()
        if self.peek() == "^":
            self.pop()
            token = self.pop()
            if not re.fullmatch(r"\d{1,2}", token) or int(token) > 12:
                raise ValueError("仅支持 0—12 的非负整数指数")
            exponent = int(token)
            if exponent == 0 and not value.value:
                raise ValueError("零的零次幂未定义")
            out = {self.zero: Fraction(1)}
            for _ in range(exponent):
                out = _mul(out, value.value)
            value = _Expr(out, "pow", (value,), exponent)
        return value

    def atom(self):
        token = self.pop()
        if token == "(":
            self.depth += 1
            if self.depth > 20:
                raise ValueError("表达式嵌套过深")
            out = self.sum()
            if self.pop() != ")":
                raise ValueError("括号不匹配")
            self.depth -= 1
            return out
        if token in self.variables:
            monomial = tuple(int(v == token) for v in self.variables)
            return _Expr({monomial: Fraction(1)}, "atom")
        number = to_fraction(token)
        if number is None:
            raise ValueError("不支持的变量或常数")
        return _Expr(_clean({self.zero: number}), "atom")


def _polynomial(value, spec):
    variables = spec.get("variables") or sorted(set(re.findall("[a-z]", _math_text(value))))
    if not 1 <= len(variables) <= 4:
        raise ValueError("变量范围不明确")
    return _PolynomialParser(value, variables).parse()


def _sum_terms(expression, sign=1):
    if expression.op in ("+", "-"):
        left, right = expression.children
        return _sum_terms(left, sign) + _sum_terms(right, sign if expression.op == "+" else -sign)
    if expression.op == "neg":
        if expression.children[0].op in ("+", "-"):
            return [(expression, sign)]
        return _sum_terms(expression.children[0], -sign)
    return [(expression, sign)]


def _monomial_syntax(expression):
    """Do not let algebraic cancellation disguise an unexpanded product."""
    return (expression.op not in ("+", "-")
            and all(_monomial_syntax(child) for child in expression.children))


def _expanded(expression):
    terms = _sum_terms(expression)
    seen = set()
    for expression, _ in terms:
        poly = expression.value
        if not _monomial_syntax(expression) or len(poly) > 1 or (not poly and len(terms) > 1):
            return False
        if poly:
            monomial = next(iter(poly))
            if monomial in seen:
                return False
            seen.add(monomial)
    return True


def _factor_nodes(expression):
    if expression.op == "*":
        return _factor_nodes(expression.children[0]) + _factor_nodes(expression.children[1])
    if expression.op == "pow":
        return _factor_nodes(expression.children[0]) * expression.exponent
    if expression.op == "neg":
        size = len(next(iter(expression.value), next(iter(expression.children[0].value), ())))
        return [{(0,) * size: Fraction(-1)}, *_factor_nodes(expression.children[0])]
    # Retain the reciprocal coefficient: integer-coefficient factorization must
    # not accept an explicit denominator hidden outside the product.
    if expression.op == "/":
        denominator = expression.children[1].value
        monomial, value = next(iter(denominator.items()))
        return [*_factor_nodes(expression.children[0]), {monomial: 1 / value}]
    return [expression.value]


def _factor_signature(expression):
    """Factor multiplicities up to nonzero rational units; no factor discovery."""
    factors = []
    for poly in _factor_nodes(expression):
        if not poly:
            return None
        if len(poly) == 1:
            monomial = next(iter(poly))
            for index, exponent in enumerate(monomial):
                for _ in range(exponent):
                    factors.append(("variable", index))
        else:
            ordered = sorted(poly.items())
            lead = ordered[-1][1]
            normalized = tuple((m, c / lead) for m, c in ordered)
            factors.append(("polynomial", normalized))
    return sorted(factors, key=repr)


def _integer_factors(expression):
    return all(c.denominator == 1 for p in _factor_nodes(expression) for c in p.values())


def _primitive_factors(expression):
    for poly in _factor_nodes(expression):
        if len(poly) > 1:
            coefficients = list(poly.values())
            if any(c.denominator != 1 for c in coefficients):
                return False
            if math.gcd(*(abs(c.numerator) for c in coefficients)) != 1:
                return False
    return True


def _bound(raw):
    if raw in ("-∞", "-inf", "-infinity"):
        return -math.inf
    if raw in ("∞", "+∞", "inf", "+inf", "infinity", "+infinity"):
        return math.inf
    value = to_fraction(raw)
    if value is None:
        raise ValueError("区间端点必须为有理数或无穷")
    return value


def _interval(raw):
    text = _math_text(raw)
    if len(text) > 256:
        raise ValueError("区间表达式过长")
    match = re.fullmatch(r"([\[(])([^,]+),([^,]+)([\])])", text)
    if match:
        lo, hi = _bound(match[2]), _bound(match[3])
        left, right = match[1] == "[", match[4] == "]"
    else:
        # Single-variable chained inequalities, with either orientation.
        pieces = re.split(r"(<=|>=|<|>)", text)
        if len(pieces) not in (3, 5):
            raise ValueError("请写一个区间或单变量不等式")
        variables = {p for p in pieces[::2] if re.fullmatch("[a-z]", p)}
        if len(variables) != 1:
            raise ValueError("区间只接受一个变量")
        variable = next(iter(variables))
        lo, hi, left, right = -math.inf, math.inf, False, False
        for i in range(0, len(pieces) - 2, 2):
            a, op, b = pieces[i:i + 3]
            if a == variable and b != variable:
                bound = _bound(b)
                lower = op.startswith(">")
            elif b == variable and a != variable:
                bound = _bound(a)
                lower = op.startswith("<")
            else:
                raise ValueError("每个关系必须含一个变量与一个端点")
            closed = "=" in op
            if lower:
                if bound > lo:
                    lo, left = bound, closed
                elif bound == lo:
                    left = left and closed
            else:
                if bound < hi:
                    hi, right = bound, closed
                elif bound == hi:
                    right = right and closed
    if (lo > hi or lo == math.inf or hi == -math.inf
            or (lo == hi and not (left and right))
            or (lo == -math.inf and left) or (hi == math.inf and right)):
        raise ValueError("区间端点或开闭不合法")
    return lo, hi, left, right


def infer_answer_spec(task):
    """Explicit legacy migration helper, with conservative representation rules.

    It never treats a general rational function as a polynomial, never accepts
    a symbolic expression by numeric sampling, and never invents a tolerance.
    Persist its result in compiled content; authors can override it explicitly.
    """
    kind = task.get("type")
    if kind in ("choice", "rubric"):
        return {"version": 1, "kind": kind}
    answer = str(task.get("answer", ""))
    prompt = str(task.get("prompt", ""))
    spec = {"version": 1, "kind": "text"}
    if kind == "numeric" or to_fraction(answer) is not None:
        # Bare legacy short fractions have historically required the listed
        # representation; explicit task metadata resolves this during migration.
        if kind == "short" and "/" in answer and not prompt:
            return spec
        spec.update(kind="number", comparison="exact", representation="any")
        # A fraction mentioned in the stem need not be the requested answer:
        # many legacy tasks explicitly ask for just its numerator/denominator.
        component = bool(re.search(r"分[子母]是多少|只填分[子母]", prompt))
        if "最简分数" in prompt and "/" in answer and not component:
            spec["representation"] = "simplest_fraction"
        elif ("/" in answer and not component and "可填分数" not in prompt
              and re.search(r"填分数|用[“\"]?分子/分母|写成分数", prompt)):
            spec["representation"] = "fraction"
        elif re.search(r"用小数表示|填小数", prompt) and "." in answer:
            spec["representation"] = "decimal"
        elif "科学记数法" in prompt and re.search(r"[eE]", answer):
            spec["representation"] = "scientific"
        elif re.search(r"只填.*整数|填一个整数", prompt):
            spec["representation"] = "integer"
        return spec
    try:
        coordinate = _numbers(answer, "()")
        if 2 <= len(coordinate) <= 4:
            return {"version": 1, "kind": "coordinate", "dimensions": len(coordinate)}
    except ValueError:
        pass
    try:
        values = _numbers(answer)
        if len(values) > 1:
            order = "ascending" if "从小到大" in prompt else "descending" if "从大到小" in prompt else "given"
            return {"version": 1, "kind": "number_list", "order": order}
    except ValueError:
        pass
    if any(operator in answer for operator in ("<", ">", "≤", "≥")):
        try:
            _interval(answer)
            return {"version": 1, "kind": "interval"}
        except ValueError:
            pass
    text = _math_text(answer)
    variables = sorted(set(re.findall("[a-z]", text)))
    if variables and re.search(r"[0-9+*/^\-]", text):
        candidate = {"version": 1, "kind": "polynomial", "variables": variables, "representation": "any"}
        try:
            _polynomial(answer, candidate)
            if "分解" in prompt:
                candidate.update(representation="factored", complete_factorization=True,
                                 integer_coefficients="整数系数" in prompt)
            elif "展开" in prompt:
                candidate["representation"] = "expanded"
            return candidate
        except ValueError:
            pass
    return spec


def _result(status, reason=None):
    out = {"status": status}
    if reason:
        out["reason"] = reason
    if reason == "representation":
        out["feedback"] = "数学值或表达式等价，请再按本题要求的表示形式整理答案。"
    elif status == "invalid":
        out["feedback"] = "请按题目要求填写可识别的答案；格式问题不计作答错。"
    elif status == "needs_review":
        out["feedback"] = "题目的评分配置待核对，暂不自动判分。"
    return out


def grade_answer(task, raw):
    """Return a status without exposing the reference answer or solution."""
    if not isinstance(task, dict):
        return _result("needs_review", "invalid_task")
    try:
        spec = task.get("answer_spec") if "answer_spec" in task else infer_answer_spec(task)
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        return _result("needs_review", "invalid_reference")
    if validate_answer_spec(spec):
        return _result("needs_review", "invalid_answer_spec")
    kind = spec["kind"]
    input_type = task.get("type")
    if ((input_type in ("choice", "rubric") and kind != input_type)
            or (input_type == "numeric" and kind != "number")
            or (input_type == "short" and kind in ("choice", "rubric"))):
        return _result("needs_review", "incompatible_answer_spec")
    if (raw is None or isinstance(raw, (bool, list, dict, tuple))
            or not str(raw).strip() or len(str(raw)) > (20000 if kind == "rubric" else 2048)):
        return _result("invalid", "invalid_input")
    if kind == "rubric":
        return _result("needs_review", "process_evidence")
    expected = str(task.get("answer", ""))
    if not expected:
        return _result("needs_review", "missing_reference")
    if kind == "choice":
        choices = task.get("choices")
        if not isinstance(choices, list) or any(not isinstance(c, dict) for c in choices):
            return _result("needs_review", "invalid_reference")
        ids = [c.get("id") for c in choices]
        if any(not isinstance(identity, str) or not identity for identity in ids):
            return _result("needs_review", "invalid_reference")
        if expected not in ids or len(ids) < 2 or len(set(ids)) != len(ids):
            return _result("needs_review", "invalid_reference")
        identity = option_id(task, raw)
        return _result("invalid", "unknown_option") if identity is None else _result("correct" if identity == expected else "wrong")
    accepted = [expected, *(task.get("accept") or [])]
    if kind == "text":
        return _result("correct" if normalize_text(raw) in {normalize_text(x) for x in accepted} else "wrong")
    if kind == "number":
        target = to_fraction(expected)
        if target is None:
            return _result("needs_review", "invalid_reference")
        value = to_fraction(raw)
        if value is None:
            # Author-listed lexical aliases (e.g. 四分之三) remain explicit.
            aliases = [x for x in accepted if to_fraction(x) is None]
            if normalize_text(raw) in {normalize_text(x) for x in aliases}:
                return _result("correct")
            return _result("invalid", "invalid_number")
        comparison = spec.get("comparison", "exact")
        if comparison == "exact":
            correct = value == target
        elif comparison == "rounded":
            correct = _round_fraction(value, spec["decimal_places"], spec.get("rounding", "half_up")) == _round_fraction(target, spec["decimal_places"], spec.get("rounding", "half_up"))
        else:
            absolute = to_fraction(spec.get("absolute_tolerance", "0"))
            relative = to_fraction(spec.get("relative_tolerance", "0"))
            correct = abs(value - target) <= max(absolute, abs(target) * relative)
        if correct and not _number_form(raw, spec):
            return _result("wrong", "representation")
        return _result("correct" if correct else "wrong")
    # Parse canonical answers first: bad author data must never penalize learners.
    try:
        if kind == "coordinate":
            target = _numbers(expected, "()")
            dimensions = spec.get("dimensions", len(target))
            if len(target) != dimensions:
                raise ValueError("坐标维度不一致")
        elif kind in ("number_list", "number_set"):
            target = _numbers(expected, "{}" if kind == "number_set" and expected.strip().startswith("{") else None)
            if kind == "number_list" and spec.get("order") in ("ascending", "descending"):
                if tuple(sorted(target, reverse=spec["order"] == "descending")) != target:
                    raise ValueError("参考数列顺序不符合要求")
        elif kind == "interval":
            target = _interval(expected)
        else:
            # Variables are fixed by author metadata or the reference, never by input.
            spec = dict(spec)
            spec.setdefault("variables", sorted(set(re.findall("[a-z]", _math_text(expected)))))
            target = _polynomial(expected, spec)
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        return _result("needs_review", "invalid_reference")
    try:
        if kind == "coordinate":
            value = _numbers(raw, "()")
            if len(value) != dimensions:
                return _result("invalid", "coordinate_dimensions")
            correct = value == target
        elif kind in ("number_list", "number_set"):
            value = _numbers(raw, "{}" if kind == "number_set" and str(raw).strip().startswith("{") else None)
            correct = set(value) == set(target) if kind == "number_set" else value == target
        elif kind == "interval":
            correct = _interval(raw) == target
        else:
            value = _polynomial(raw, spec)
            correct = value.value == target.value
            if correct:
                form = spec.get("representation", "any")
                if form == "expanded" and not _expanded(value):
                    return _result("wrong", "representation")
                if form == "factored":
                    actual = _factor_signature(value)
                    reference = _factor_signature(target)
                    if spec.get("complete_factorization", True):
                        form_ok = actual == reference
                    else:
                        form_ok = actual is not None and len(actual) >= min(2, len(reference or []))
                    if spec.get("integer_coefficients") and not _integer_factors(value):
                        form_ok = False
                    if (spec.get("integer_coefficients") and spec.get("complete_factorization", True)
                            and not _primitive_factors(value)):
                        form_ok = False
                    if not form_ok:
                        return _result("wrong", "representation")
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        return _result("invalid", "invalid_expression")
    return _result("correct" if correct else "wrong")
