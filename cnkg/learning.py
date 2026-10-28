"""独立教学单元：目标覆盖、可追溯版本及教学任务判分。

单元与教材目录节点分开保存。机器检查只保证结构、引用和给定算式一致；
开放任务始终等待评价，不用关键词、参考答案相似度或题目完成率确认掌握。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re

from .verify import check_question, compare, normalize_text, to_number
from .answers import grade_answer, validate_answer_spec

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DIAGRAM_DIR = ROOT / "web" / "static" / "diagrams"
PHASES = ("practice", "transfer", "delayed")
TASK_TYPES = {"numeric", "choice", "short", "rubric"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIAGRAM = re.compile(r"/static/diagrams/([A-Za-z0-9][A-Za-z0-9_.-]*\.svg)\Z")


class LearningUnitError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("教学单元校验失败：\n" + "\n".join(self.errors))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _reference_ids(records):
    if records is None:
        return None
    if isinstance(records, dict):
        return set(records)
    return {r if isinstance(r, str) else r["id"] for r in records}


def _string_list(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(_text(x) for x in value) and len(value) == len(set(value)))


def diagram_path(src: str) -> Path:
    """只映射应用自己维护的 SVG；拒绝任意 URL、目录穿越和越界符号链接。"""
    match = _DIAGRAM.fullmatch(src) if isinstance(src, str) else None
    if match is None:
        raise ValueError("diagram.src 必须为 /static/diagrams/<文件名>.svg")
    target = (DIAGRAM_DIR / match.group(1)).resolve()
    if not target.is_relative_to(DIAGRAM_DIR.resolve()) or not target.is_file():
        raise ValueError("diagram.src 指向的图不存在或不在允许目录内")
    return target


def _check_diagram(diagram, prefix):
    if not isinstance(diagram, dict):
        return [f"{prefix} 必须是包含 src/alt 的对象"]
    errors = []
    if not _text(diagram.get("alt")):
        errors.append(f"{prefix}.alt 不能为空")
    try:
        diagram_path(diagram.get("src"))
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
    return errors


def unit_content_sha256(unit: dict) -> str:
    """教学内容的规范哈希；审核元数据不参与，被引用 SVG 的字节参与。

    修改图中的坐标/标注与修改题干一样会改变版本。审核状态切换不会使
    原本有效的内容指纹循环失效；本函数本身不宣称内容已获教师审核。
    """
    content = {k: v for k, v in unit.items() if k not in
               {"teacher_review", "review_status", "content_sha256"}}
    diagrams = [unit.get("diagram")]
    diagrams += [t.get("diagram") for t in unit.get("tasks", []) if isinstance(t, dict)]
    assets = {}
    for diagram in diagrams:
        if not isinstance(diagram, dict):
            continue
        src = diagram.get("src")
        try:
            assets[src] = hashlib.sha256(diagram_path(src).read_bytes()).hexdigest()
        except (OSError, ValueError):
            # 无效资源仍由校验器报告；不能因哈希阶段先抛异常而漏掉其它错误。
            assets[str(src)] = "unresolved"
    payload = json.dumps({"content": content, "diagram_assets": assets}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _computation_errors(item, prefix, *, required=False):
    if not item.get("verify"):
        return [f"{prefix}.verify：数值任务必须有可复算表达式"] if required else []
    if not isinstance(item["verify"], str):
        return [f"{prefix}.verify 必须是字符串"]
    try:
        result = check_question(item)
    except (ArithmeticError, TypeError, ValueError, RecursionError) as exc:
        return [f"{prefix}.verify 无法复算：{exc}"]
    return [] if result["status"] == "pass" else [f"{prefix}.verify：{result['detail']}"]


def _grading_errors(task, prefix="task"):
    errors = []
    if "answer_spec" in task:
        errors.extend(f"{prefix}: {error}" for error in validate_answer_spec(task["answer_spec"]))
    kind = task.get("type")
    if not isinstance(kind, str) or kind not in TASK_TYPES:
        return [f"{prefix}.type 不支持：{kind}"]
    if kind == "rubric":
        rows = task.get("rubric")
        if not isinstance(rows, list) or not rows:
            return [f"{prefix}.rubric 必须包含评价标准"]
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"{prefix}.rubric[{i}] 必须是对象")
                continue
            if not _text(row.get("criterion")) or not _text(row.get("example")):
                errors.append(f"{prefix}.rubric[{i}] 需要 criterion 和 example")
            points = row.get("points")
            try:
                valid_points = (not isinstance(points, bool) and isinstance(points, (int, float))
                                and math.isfinite(points) and points > 0)
            except OverflowError:
                valid_points = False
            if not valid_points:
                errors.append(f"{prefix}.rubric[{i}].points 必须为有限正数")
        return errors
    if not _text(task.get("answer")):
        errors.append(f"{prefix}.answer 必须为非空字符串")
    if kind == "numeric":
        if to_number(task.get("answer")) is None:
            errors.append(f"{prefix}.answer 必须是有限数或一个分数")
        errors.extend(_computation_errors(task, prefix, required=True))
    elif kind == "short":
        if "accept" in task and not _string_list(task["accept"]):
            errors.append(f"{prefix}.accept 必须为无重复的字符串列表")
    elif kind == "choice":
        choices = task.get("choices")
        if not isinstance(choices, list) or len(choices) < 2:
            return errors + [f"{prefix}.choices 至少需要两个选项"]
        if not all(isinstance(c, dict) and _text(c.get("id")) and _text(c.get("text"))
                   for c in choices):
            return errors + [f"{prefix}.choices 每项必须有 id 和 text"]
        ids = [c["id"] for c in choices]
        texts = [c["text"].strip() for c in choices]
        if len(set(ids)) != len(ids) or len(set(texts)) != len(texts):
            errors.append(f"{prefix}.choices 不能重复 ID 或选项正文")
        if task.get("answer") not in ids:
            errors.append(f"{prefix}.answer 不对应有效选项 ID")
    return errors


def check_unit(unit, topics=None, standards=None) -> list[str]:
    """检查结构、引用和目标覆盖；topics/standards 可为 ID 集合或记录集合。"""
    if not isinstance(unit, dict):
        return ["unit 必须是对象"]
    errors = []
    if not isinstance(unit.get("id"), str) or not _ID.fullmatch(unit["id"]):
        errors.append("id 必须为不含路径分隔符的稳定标识")
    for key in ("title", "author_type"):
        if not _text(unit.get(key)):
            errors.append(f"{key} 不能为空")
    if not isinstance(unit.get("stage"), str) or unit["stage"] not in {
            "primary", "junior", "senior", "小学", "初中", "高中"}:
        errors.append("stage 必须是小学、初中或高中")
    if not isinstance(unit.get("review_status"), str) or unit["review_status"] not in {
            "draft", "reviewed", "rejected"}:
        errors.append("review_status 必须明确为 draft/reviewed/rejected")
    coverage_kind = unit.get("coverage_kind", "topic_aligned")
    if not isinstance(coverage_kind, str) or coverage_kind not in {
            "topic_aligned", "curriculum_supplement"}:
        errors.append("coverage_kind 必须为 topic_aligned/curriculum_supplement")
    supplement = coverage_kind == "curriculum_supplement"
    if supplement and not _text(unit.get("navigation_note")):
        errors.append("课程补充单元必须用 navigation_note 说明独立导航与覆盖范围")
    for key, known, required in (("topic_ids", _reference_ids(topics), not supplement),
                                  ("standard_ids", _reference_ids(standards), True)):
        refs = unit.get(key)
        if not _string_list(refs, nonempty=required):
            errors.append(f"{key} 必须为{'非空、' if required else ''}无重复的 ID 列表")
        elif known is not None:
            errors.extend(f"{key} 引用了不存在的 ID：{ref}" for ref in refs if ref not in known)
    if not _string_list(unit.get("prerequisites")):
        errors.append("prerequisites 必须为无重复的可读基础说明列表")
    if "prerequisite_topic_ids" in unit:
        refs = unit["prerequisite_topic_ids"]
        if not _string_list(refs):
            errors.append("prerequisite_topic_ids 必须为无重复的 ID 列表")
        elif topics is not None:
            known = _reference_ids(topics)
            errors.extend(f"prerequisite_topic_ids 引用了不存在的 ID：{ref}"
                          for ref in refs if ref not in known)

    objectives = unit.get("objectives")
    objective_ids = set()
    if not isinstance(objectives, list) or not objectives:
        errors.append("objectives 必须包含可观测的学习目标")
    else:
        for i, objective in enumerate(objectives):
            if (not isinstance(objective, dict) or not isinstance(objective.get("id"), str)
                    or not _ID.fullmatch(objective["id"]) or not _text(objective.get("statement"))):
                errors.append(f"objectives[{i}] 需要有效 id 和 statement")
                continue
            if objective["id"] in objective_ids:
                errors.append(f"目标 ID 重复：{objective['id']}")
            objective_ids.add(objective["id"])
    explanation = unit.get("explanation")
    if not isinstance(explanation, dict):
        errors.append("explanation 必须为对象")
    else:
        for key in ("intuition", "reasoning", "counterexample"):
            if not _text(explanation.get(key)):
                errors.append(f"explanation.{key} 不能为空")
        if not _string_list(explanation.get("conditions"), nonempty=True):
            errors.append("explanation.conditions 必须说明适用条件")
    examples = unit.get("worked_examples")
    if not isinstance(examples, list) or not examples:
        errors.append("worked_examples 必须有完整示例")
    else:
        for i, example in enumerate(examples):
            prefix = f"worked_examples[{i}]"
            if not isinstance(example, dict):
                errors.append(f"{prefix} 必须为对象")
                continue
            for key in ("prompt", "answer"):
                if not _text(example.get(key)):
                    errors.append(f"{prefix}.{key} 不能为空")
            if not _string_list(example.get("steps"), nonempty=True):
                errors.append(f"{prefix}.steps 必须包含推理步骤")
            errors.extend(_computation_errors(example, prefix))
    if "diagram" in unit:
        errors.extend(_check_diagram(unit["diagram"], "diagram"))

    tasks = unit.get("tasks")
    coverage = {oid: set() for oid in objective_ids}
    task_ids, phases = set(), set()
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks 必须包含练习、迁移与延迟复测任务")
        tasks = []
    for i, task in enumerate(tasks):
        prefix = f"tasks[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} 必须为对象")
            continue
        tid = task.get("id")
        if not isinstance(tid, str) or not _ID.fullmatch(tid):
            errors.append(f"{prefix}.id 必须为有效标识")
        elif tid in task_ids:
            errors.append(f"任务 ID 重复：{tid}")
        else:
            task_ids.add(tid)
        phase = task.get("phase")
        if phase not in PHASES:
            errors.append(f"{prefix}.phase 必须为 practice/transfer/delayed")
        else:
            phases.add(phase)
        refs = task.get("objective_ids")
        if not _string_list(refs, nonempty=True):
            errors.append(f"{prefix}.objective_ids 必须明确考查目标")
        else:
            for oid in refs:
                if oid not in objective_ids:
                    errors.append(f"{prefix} 引用了未定义目标：{oid}")
                elif phase in PHASES:
                    coverage[oid].add(phase)
        for key in ("prompt", "explanation"):
            if not _text(task.get(key)):
                errors.append(f"{prefix}.{key} 不能为空")
        feedback = task.get("feedback")
        if not isinstance(feedback, dict) or not all(_text(feedback.get(k)) for k in ("correct", "incorrect")):
            errors.append(f"{prefix}.feedback 必须同时包含 correct 和 incorrect 反馈")
        errors.extend(_grading_errors(task, prefix))
        if "diagram" in task:
            errors.extend(_check_diagram(task["diagram"], prefix + ".diagram"))
    for phase in PHASES:
        if phase not in phases:
            errors.append(f"缺少 {phase} 阶段任务")
    for oid, seen in coverage.items():
        if "practice" not in seen:
            errors.append(f"目标 {oid} 缺少 practice 任务证据")
        if not seen.intersection({"transfer", "delayed"}):
            errors.append(f"目标 {oid} 缺少独立迁移或延迟任务证据")

    from .instruction import check_supports
    errors.extend(check_supports(unit))
    review = unit.get("teacher_review")
    if (not isinstance(review, dict) or not isinstance(review.get("status"), str)
            or review["status"] not in {"pending", "approved", "rejected"}):
        errors.append("teacher_review.status 必须明确为 pending/approved/rejected")
    elif review["status"] == "approved":
        if (not isinstance(review.get("reviewer_type"), str)
                or review["reviewer_type"] not in {"teacher", "subject_expert"}
                or not _text(review.get("reviewer_id")) or not _text(review.get("record_id"))):
            errors.append("教师审核通过必须有独立审核身份和记录")
        try:
            if review.get("content_sha256") != unit_content_sha256(unit):
                errors.append("教师审核记录与当前教学内容版本不一致")
        except (TypeError, ValueError):
            errors.append("当前教学内容不能生成有效的审核哈希")
    if unit.get("review_status") == "reviewed" and (not isinstance(review, dict) or review.get("status") != "approved"):
        errors.append("reviewed 状态必须有有效教师审核，AI 自查不能替代")
    return errors


def load_units(data_dir=None) -> dict[str, dict]:
    """从 data 根目录下 learning_units/*.json 严格加载；无目录返回空表。"""
    data = Path(data_dir or DATA_DIR)
    directory = data / "learning_units"
    if not directory.exists():
        return {}
    errors, units, all_tasks = [], {}, set()
    references = {}
    for name in ("topics", "standards"):
        try:
            references[name] = json.loads((data / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LearningUnitError([f"无法加载 {name} 引用表：{exc}"]) from exc
    for path in sorted(directory.glob("*.json")):
        try:
            unit = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}：无法读取 JSON：{exc}")
            continue
        problems = check_unit(unit, references["topics"], references["standards"])
        errors.extend(f"{path.name}: {error}" for error in problems)
        if problems:
            continue
        if unit["id"] in units:
            errors.append(f"{path.name}: 单元 ID 重复：{unit['id']}")
            continue
        for task in unit["tasks"]:
            if task["id"] in all_tasks:
                errors.append(f"{path.name}: 跨单元任务 ID 重复：{task['id']}")
            all_tasks.add(task["id"])
        units[unit["id"]] = unit
    if errors:
        raise LearningUnitError(errors)
    return units


def public_unit(unit: dict) -> dict:
    """学生可读单元；所有任务使用白名单，不提前下发参考答案或评价示例。"""
    out = {key: copy.deepcopy(unit.get(key)) for key in
           ("id", "title", "stage", "topic_ids", "standard_ids", "review_status", "author_type",
            "prerequisites", "explanation")}
    out["objectives"] = [{k: obj[k] for k in ("id", "statement")} for obj in unit["objectives"]]
    out["worked_examples"] = [{k: copy.deepcopy(ex[k]) for k in ("prompt", "steps", "answer")}
                              for ex in unit["worked_examples"]]
    out["teacher_review"] = {"status": unit.get("teacher_review", {}).get("status", "pending")}
    out["content_sha256"] = unit_content_sha256(unit)
    for key in ("coverage_kind", "navigation_note", "prerequisite_topic_ids",
                "standard_alignment", "coverage_note"):
        if key in unit:
            out[key] = copy.deepcopy(unit[key])
    if unit.get("diagram"):
        out["diagram"] = {k: unit["diagram"][k] for k in ("src", "alt")}
    out["tasks"] = []
    for task in unit["tasks"]:
        shown = {k: copy.deepcopy(task[k]) for k in ("id", "objective_ids", "phase", "type", "prompt")}
        if task.get("choices"):
            shown["choices"] = [{k: c[k] for k in ("id", "text")} for c in task["choices"]]
        if task.get("rubric"):
            shown["rubric"] = [{k: r[k] for k in ("criterion", "points")} for r in task["rubric"]]
        if task.get("diagram"):
            shown["diagram"] = {k: task["diagram"][k] for k in ("src", "alt")}
        out["tasks"].append(shown)
    return out


def grade_task(task: dict, answer) -> dict:
    """判定一个任务；调用方须在 transfer/delayed 整组完成后才展示反馈。

    与教材题共用答案契约；精确值、合法等价变体与指定表示形式分开检查。
    rubric 永不自动正确，也不产生自动得分或掌握结论。
    """
    if not isinstance(task, dict) or _grading_errors(task):
        return {"status": "needs_review", "feedback": "题目判分条件待核对，暂不自动判分。"}
    kind = task["type"]
    decision = grade_answer(task, answer)
    if decision["status"] == "invalid":
        return decision
    if kind == "rubric":
        return {"status": "needs_review", "explanation": task.get("explanation", ""),
                "rubric": copy.deepcopy(task["rubric"]),
                "feedback": "请对照评价标准逐条自查，并请教师或同伴提供反馈；本题不自动评分。"}
    if decision["status"] == "needs_review":
        return decision
    correct = decision["status"] == "correct"
    feedback = task.get("feedback") if isinstance(task.get("feedback"), dict) else {}
    result = {**decision, "answer": task["answer"],
              "explanation": task.get("explanation", ""),
              "feedback": decision.get("feedback") or feedback.get("correct" if correct else "incorrect", "")}
    if kind == "choice":
        result["answer_text"] = next(c["text"] for c in task["choices"] if c["id"] == task["answer"])
    return result
