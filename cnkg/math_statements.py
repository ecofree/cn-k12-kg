"""Conservative checking of explicit numerical equalities in teaching prose.

Only complete numeric expressions which evaluate to exact rationals are
checked. Variables, diagrams, modelling, approximate claims and other notation
remain outside this check. An intentionally wrong worked example requires an
explicit, source-bound review; it is never silently ignored by a keyword.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

from .verify import VerifyError, evaluate_precise

CLAIM_FIELDS = frozenset({"definition", "why", "explanation", "reference_answer", "answer", "hint", "hints",
    "revision_prompt", "counterexample", "conditions", "steps", "pitfalls", "reasoning", "intuition",
    "instructions", "correct", "incorrect", "conclusion", "verify_note", "observation_note"})
# A boundary may end a sentence, but must never drop a mathematical prefix or
# suffix. These unsupported symbols stay inside the run so a numeric suffix of
# log₂8, f′(2), ∠1, □+7 or a ratio cannot masquerade as a complete equality.
# Chinese place-value and remainder notation also needs its full meaning; it
# is deliberately left to semantic review rather than treated as a decimal.
RUN = re.compile(r"[A-Za-z0-9_\\{}\[\].,+\-*/^()=≈<>≤≥≠√%:：∶'′″°!|?？□○△◇☆〇"
                 r"\u0370-\u03ff\u2070-\u209f∑∏∫∞∘∠⊥∥∈∉∅∪∩⊆⊂∝∴∵"
                 r"十百千万亿个余…\s]+")
NUMERIC = re.compile(r"[0-9.+\-*/()\s]+")
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def numerical_claims(text):
    """Yield exact-rational pairs; never take a numeric suffix of an algebraic expression."""
    value = text.translate(str.maketrans({"−": "-", "﹣": "-", "－": "-", "＋": "+", "×": "*", "÷": "/",
                                        "＝": "=", "（": "(", "）": ")", "％": "%", "·": "*", "⋅": "*"}))
    value = value.replace(r"\times", "*").replace(r"\cdot", "*").replace(r"\div", "/")
    value = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+", lambda item: "**(" + item.group().translate(SUPERSCRIPTS) + ")", value)
    value = re.sub(r"\\(?:d|t)?frac\{([+-]?\d+(?:\.\d+)?)\}\{([+-]?\d+(?:\.\d+)?)\}", r"(\1/\2)", value)
    for match in RUN.finditer(value):
        run = match.group().strip(" \t\r\n,:：")
        if "=" not in run or any(symbol in run for symbol in ("≈", "<", ">", "≤", "≥", "≠")):
            continue
        parts = run.split("=")
        # A Chinese quantity label may act as a variable: 原来人数+7=16.
        # Without a notation parser its +7 suffix is not a complete operand.
        # Be conservative even if a Chinese sentence could precede a genuine
        # signed numeral; a separate, punctuated numeric equation is checkable.
        ambiguous_prefix = (match.group().lstrip().startswith(("+", "-")) and match.start() > 0
                            and value[match.start() - 1].isalpha())
        for index, (left, right) in enumerate(zip(parts, parts[1:])):
            if index == 0 and ambiguous_prefix:
                continue
            operands = []
            for expression in (left, right):
                expression = expression.strip().replace("^", "**")
                expression = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expression)
                if not expression or not NUMERIC.fullmatch(expression):
                    break
                try:
                    result = evaluate_precise(expression)
                except (VerifyError, ArithmeticError, ValueError):
                    break
                if not isinstance(result, Fraction):
                    break
                operands.append((expression, result))
            if len(operands) == 2:
                yield {"left": operands[0][0], "right": operands[1][0],
                       "left_value": str(operands[0][1]), "right_value": str(operands[1][1]),
                       "equal": operands[0][1] == operands[1][1]}


def _strings(value, pointer="", field=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _strings(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"), key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _strings(child, pointer + "/" + str(index), field)
    elif isinstance(value, str):
        parts = pointer.split("/")
        is_hint_text = field == "text" and any(part in {"hints", "branch_hints"} for part in parts)
        is_rubric_example = field == "example" and "rubric" in parts
        if field in CLAIM_FIELDS or field == "feedback" or is_hint_text or is_rubric_example:
            yield pointer, value


def inspect_resource(value, path, source_sha256):
    checked, findings = 0, []
    for pointer, statement in _strings(value):
        for claim in numerical_claims(statement):
            checked += 1
            if claim["equal"]:
                continue
            identity = _digest(json.dumps([path, pointer, statement, claim], ensure_ascii=False, sort_keys=True).encode())
            findings.append({"id": identity, "path": path, "pointer": pointer, "source_sha256": source_sha256,
                             "statement": statement, **claim})
    return checked, findings


def scan(root):
    root = Path(root); data = root / "data"
    manifest = json.loads((data / "content_sources.json").read_text())
    admissions = json.loads((data / "task_admissions.json").read_text())["tasks"]
    paths = [data / row["path"] for row in manifest["content_sources"]]
    paths += [data / "question_sources/application.json"]
    paths += [path for folder in ("teach", "learning_units", "projects", "assessments") for path in sorted((data / folder).glob("*.json"))]
    checked, findings, sources = 0, [], {}
    for path in paths:
        raw = path.read_bytes(); value = json.loads(raw); name = path.relative_to(root).as_posix()
        sources[name] = _digest(raw)
        # The trace archive can contain retired records. Their retained history
        # must not be silently edited or treated as currently served lessons.
        if isinstance(value, dict) and isinstance(value.get("questions"), list):
            value = {**value, "questions": [question if admissions.get(question["id"], {}).get("policy", {}).get("practice_eligible", True)
                                             else {} for question in value["questions"]]}
        elif isinstance(value, list):
            value = [question if admissions.get(question.get("id"), {}).get("policy", {}).get("practice_eligible", True)
                     else {} for question in value]
        count, problems = inspect_resource(value, name, sources[name]); checked += count; findings.extend(problems)
    reviews_path = data / "math_statement_reviews.json"
    reviews = json.loads(reviews_path.read_text()) if reviews_path.exists() else {"schema": 1, "items": []}
    errors, approved = [], set()
    if reviews.get("schema") != 1 or not isinstance(reviews.get("items"), list):
        errors.append("数字等式审查登记格式不正确")
        reviews = {"items": []}
    by_id = {item["id"]: item for item in findings}
    for review in reviews["items"]:
        identity = review.get("id"); current = by_id.get(identity)
        if (not current or identity in approved or review.get("source_sha256") != current["source_sha256"]
                or review.get("decision") != "intentional_counterexample" or not review.get("reason", "").strip()
                or review.get("reviewer_kind") != "ai" or review.get("teacher_review") != "pending"):
            errors.append(f"数字等式审查缺失、重复或已过期：{identity}")
        else:
            approved.add(identity)
    for finding in findings:
        finding["reviewed_intentional_counterexample"] = finding["id"] in approved
        if finding["id"] not in approved:
            errors.append(f"{finding['path']}{finding['pointer']}：{finding['left']}={finding['right']} 不相等，须修正或明确审查反例用途")
    return {"schema": 1, "checked_numeric_equalities": checked, "source_sha256": sources,
            "findings": findings, "errors": errors, "ok": not errors,
            "limits": ["只检验完整解析出的精确有理数等式，不核定建模、条件、作图、变量式、近似式或全部数学语义。",
                       "数值不相等可能是作者故意展示的错误；须逐项绑定原文与当前来源审查，不能按错误关键词自动忽略。"]}
