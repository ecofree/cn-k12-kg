"""极简本地 Web 服务：把诊断引擎接到浏览器上。

本地服务；完整备份使用 requirements.txt 中的 cryptography。
默认只绑定 127.0.0.1，不对外网开放。

    python web/server.py            # http://127.0.0.1:8765
    python web/server.py --port 9000

接口（服务端持有会话，前端只提交会话ID与答案）：
    GET  /api/topics                知识点树 + 统计
    POST /api/session               {targets:[...]} 或 {volume:"七上", mode:"screen"}
    GET  /api/learning/units        细目标教学单元
    POST /api/answer                {session_id, question_id, answer}
"""
from __future__ import annotations

import argparse
import base64
import os
import json
import pathlib
import sys
import urllib.parse
import copy
import threading
import uuid
import hashlib
import re
import secrets
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = pathlib.Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from cnkg import engine, learning, evidence, learning_store, learning_needs, projects, session_content, portfolio, planner, instruction, profile_recovery  # noqa: E402
from cnkg import catalog as catalog_module  # noqa: E402
from cnkg.graph import VOLUME_ORDER, load_graph  # noqa: E402
from cnkg.verify import compare, compare_short, to_number, to_fraction, normalize_text  # noqa: E402
from cnkg.answers import grade_answer  # noqa: E402
from cnkg.review_timing import interval_context  # noqa: E402
from cnkg import manipulatives  # noqa: E402
from cnkg.content_repository import ContentRepository  # noqa: E402
from cnkg.runtime_version import freeze_grader  # noqa: E402

# This process runs the imported grading functions until it is restarted.
# Freeze eagerly; the first new session may arrive after files have changed.
_UNIT_GRADER_RUNTIME = freeze_grader("unit")
_TOPIC_GRADER_RUNTIME = freeze_grader("topic")

# 外部题（真题源）默认不加载。
# 可选题库的实际准入量由当前证据策略决定；题量不等于独立学科审核。
# 教学场景要开，显式设 CNKG_INCLUDE_EXTERNAL=1。
# 开关用环境变量而不是命令行参数，方便后台启动时控制。
_INCLUDE_EXTERNAL = os.environ.get("CNKG_INCLUDE_EXTERNAL", "0") == "1"
_CONTENT = ContentRepository(DATA, include_external=_INCLUDE_EXTERNAL)
GRAPH = _CONTENT.graph
# 出题轮转计数器：每建一个会话 +1，让同一知识点的后续诊断抽到不同的题。
# 不用随机数——同一组参数要可复现，诊断记录要可审计。
_SESSION_COUNTER = 0
_STORE = None
_OWNER_COOKIE = "cnkg_owner"
_OWNER_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SESSION_LOCK = threading.RLock()
_REQUEST_CONTEXT = threading.local()
_UNIT_CACHE = None
_PROJECT_CACHE = None
_CATALOG_CACHE = None
ALLOWED_STATIC = {"index.html", "app.js", "style.css", "review.html", "math.js",
                  "ext.html", "ext-data.json", "work.js", "support.js", "profile.js", "manipulatives.js"}
_STATIC_SNAPSHOTS = {name: (STATIC / name).read_bytes() for name in ALLOWED_STATIC
                     if (STATIC / name).is_file()}
_STATIC_SNAPSHOTS["manipulatives.js"] = manipulatives.renderer_bytes()
# 第三方静态资源只允许这个子目录，避免把整个磁盘暴露出去
ALLOWED_PREFIX = "vendor/"
ALLOWED_DIAGRAM_PREFIX = "diagrams/"
CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "js": "application/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "woff2": "font/woff2",
    "woff": "font/woff",
    "ttf": "font/ttf",
    "svg": "image/svg+xml",
}


def option_id_for(question: dict, token: str) -> str:
    """把前端提交的内容解析成 option_id。

    正常路径：前端直接提交 option_id（o2）。
    兼容路径：提交的是字母（来自旧客户端或手输），按选项当前顺序换算成 id。
    注意字母是**位置**，不是身份——所以兼容路径只在同一份 choices 顺序下成立。
    """
    token = str(token or "").strip()
    ids = [c.get("id") for c in question.get("choices") or []]
    if token in ids:
        return token
    if len(token) == 1 and token.isalpha():
        idx = ord(token.upper()) - ord("A")
        if 0 <= idx < len(ids):
            return ids[idx]
    return token


def judge(question: dict, text) -> str:
    """统一答案契约判分；作者复算容差不参与学生精确答案的判断。"""
    return grade_answer(question, text)["status"]


def public_question(question: dict) -> dict:
    """给前端的题面：不含答案与解析，避免在浏览器里提前泄露。"""
    shown = {
        "id": question["id"],
        "topicId": question.get("topicId"),
        "difficulty": question.get("difficulty"),
        "type": question.get("type"),
        "prompt": question["prompt"],
        "choices": [{k: choice[k] for k in ("id", "text") if k in choice}
                    for choice in question.get("choices") or []] if question.get("choices") is not None else None,
    }
    for key in ("objective_ids", "phase", "diagram", "answer_spec"):
        if key in question:
            shown[key] = copy.deepcopy(question[key])
    if question.get("rubric"):
        shown["rubric"] = [{k: r[k] for k in ("criterion", "points")} for r in question["rubric"]]
    return shown


def display_answer(question: dict) -> str:
    """选择题显示选项文字，不把 o1 等内部键当作教学反馈。"""
    if question.get("type") == "choice":
        for index, choice in enumerate(question.get("choices") or []):
            if choice.get("id") == question.get("answer"):
                return f"{chr(65 + index)}. {choice['text']}"
    return str(question.get("answer", ""))


def item_payload(item) -> dict | None:
    if item is None:
        return None
    return {
        "topicId": item["topicId"],
        "topicName": GRAPH.name(item["topicId"]),
        "attempt": item["attempt"],
        "question": public_question(item["question"]),
    }


def learning_units() -> dict:
    global _UNIT_CACHE
    if _UNIT_CACHE is None:
        _UNIT_CACHE = copy.deepcopy(_CONTENT.units)
    return _UNIT_CACHE


def learning_projects() -> dict:
    global _PROJECT_CACHE
    if _PROJECT_CACHE is None:
        _PROJECT_CACHE = copy.deepcopy(_CONTENT.projects)
    return _PROJECT_CACHE


def unit_summary(unit: dict) -> dict:
    return {"id": unit["id"], "title": unit["title"], "stage": unit["stage"],
            "topic_ids": unit.get("topic_ids", []), "objectives": unit["objectives"],
            "review_status": unit.get("review_status", "draft"),
            "teacher_review": unit.get("teacher_review", {"status": "pending"}),
            "task_counts": {phase: sum(t["phase"] == phase for t in unit["tasks"])
                            for phase in ("practice", "transfer", "delayed")}}


def build_teach(topic_id: str) -> dict | None:
    """返回真实存在的讲解和目标单元；速查仅作为辅助参考。"""
    topic = GRAPH.by_id.get(topic_id)
    if topic is None:
        return None
    tier = GRAPH.tiers.get(topic_id) or {}
    group = tier.get("reference_group") or tier.get("brief_group")
    mode, card = _CONTENT.topic_card(topic_id, group)
    pool = GRAPH.practice_bank.get(topic_id, [])
    return {
        **card, "topicId": topic_id, "mode": mode, "tier": tier.get("tier"),
        "topicName": topic["name"], "description": topic.get("description"),
        "mastery_criteria": topic.get("mastery_criteria", []),
        "review_status": card.get("review_status", "draft"),
        "diagnostic_count": len(GRAPH.bank.get(topic_id, [])),
        "practice_count": len(pool),
        "learning_units": [unit_summary(u) for u in learning_units().values()
                           if topic_id in u.get("topic_ids", [])],
        # 板块散列匹配没有目标关联证据，不能作为本课后的推荐。
        "challenge": [], "challenge_status": "target_alignment_pending",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def get_store():
    global _STORE
    requested_store = getattr(_REQUEST_CONTEXT, "store", None)
    if requested_store is not None:
        return requested_store
    if _STORE is None:
        _STORE = learning_store.LearningStore()
    return _STORE


def get_profile():
    store = get_store()
    service = getattr(store, "_profile_service", None)
    if service is None:
        service = profile_recovery.ProfileRecovery(store)
        store._profile_service = service
    return service


def get_catalog():
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        _CATALOG_CACHE = _CONTENT.catalog
    return _CATALOG_CACHE


def unit_assets(unit):
    if unit == _CONTENT.units.get(unit.get("id")):
        return _CONTENT.assets_for([unit, *unit["tasks"]])
    # Explicit in-process test fixtures can supply their own local assets.
    return session_content.unit_assets(unit)


def task_metadata(question):
    task = get_catalog().tasks.get(question["id"], {})
    if task and task.get("prompt") != question.get("prompt"):
        task = {}
    return {key: copy.deepcopy(task[key]) for key in
            ("resource_version", "skill_ids", "family_id", "family_status", "alignment", "answer_spec",
             "representation", "purpose", "topic_ids")
            if key in task}


def presented_metadata(record, question_id):
    return record.get("task_metadata", {}).get(question_id, {})


def exposure_exclusions(owner):
    observed = get_store().exposures(owner)
    questions = set(observed["questions"])
    families = set(observed["families"])
    excluded = questions | {qid for qid, task in get_catalog().tasks.items()
                            if task.get("family_status") == "declared" and task.get("family_id") in families}
    return excluded, questions, families


def available_questions(targets, excluded=()):
    blocked = set(excluded)
    diagnostic = {q["id"] for tid in targets for q in GRAPH.bank.get(tid, [])}
    practice = {q["id"] for tid in targets for q in GRAPH.practice_bank.get(tid, [])}
    return {"unseen_count": len(diagnostic - blocked), "diagnostic_count": len(diagnostic),
            "practice_count": len(practice), "novelty_basis": "recorded_question_and_declared_family_exposure"}


def _hash(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def graph_version():
    # 数据与推断规则共同决定一次诊断的含义；不能用升级后的图悄悄继续旧会话。
    cached = getattr(GRAPH, "_web_content_version", None)
    if cached is None:
        sources = dict(_TOPIC_GRADER_RUNTIME.source_hashes)
        cached = _hash({"topics": GRAPH.topics, "dependencies": GRAPH.dependencies,
                        "questions": GRAPH.questions, "tiers": GRAPH.tiers, "runtime": sources})
        GRAPH._web_content_version = cached
    return cached


def unit_grader_version():
    return _UNIT_GRADER_RUNTIME.version


def topic_grader_version():
    return _TOPIC_GRADER_RUNTIME.version


def session_graph(record):
    return session_content.restore_graph(record["graph_snapshot"]) if record.get("graph_snapshot") else GRAPH


def _seconds(later, earlier):
    if not earlier:
        return None
    return max(0.0, (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds())


def _event(kind, when, record=None, question=None, **payload):
    meta = presented_metadata(record, question["id"]) if record and question else {}
    return {"event_type": kind, "occurred_at": when,
            "resource_id": (record.get("unit_id") if record else None),
            "question_id": question.get("id") if question else None,
            "content_sha256": (question_hash(record, question) if question else
                               record.get("content_sha256") if record else None),
            "family_id": meta.get("family_id"), "family_status": meta.get("family_status", "unclassified"),
            "payload": {**payload, "skill_ids": meta.get("skill_ids", [])}}


def question_hash(record, question):
    base = (_hash({"task": question, "unit_content_sha256": record["content_sha256"]})
            if record["kind"] == "unit" else evidence.question_content_sha256(question))
    meta = presented_metadata(record, question["id"])
    return _hash({"content": base, "metadata": meta}) if meta else base


def session_question(record: dict) -> dict | None:
    if record["kind"] == "unit":
        if record["index"] >= len(record["task_ids"]):
            return None
        return next(t for t in record["unit_snapshot"]["tasks"]
                    if t["id"] == record["task_ids"][record["index"]])
    current = record["engine"].get("current")
    return record.get("question_snapshots", {}).get(current["questionId"]) if current else None


def session_item(record: dict) -> dict | None:
    question = session_question(record)
    if question is None:
        return None
    if record["kind"] == "unit":
        unit = record["unit_snapshot"]
        public = public_question(question)
        if public.get("diagram"):
            public["diagram"] = session_content.public_diagram(record, public["diagram"])
        return {"topicId": (unit.get("topic_ids") or [None])[0], "topicName": unit["title"],
                "attempt": record["index"] + 1, "question": public}
    current = record["engine"]["current"]
    public = public_question(question)
    if public.get("diagram"):
        public["diagram"] = session_content.public_diagram(record, public["diagram"])
    return {"topicId": current["topicId"],
            "topicName": record.get("topic_names", {}).get(current["topicId"], current["topicId"]),
            "attempt": current["attempt"], "question": public}


def _present_question(record, events):
    if record["kind"] == "topic" and record["engine"].get("current"):
        qid = record["engine"]["current"]["questionId"]
        record["question_snapshots"][qid] = copy.deepcopy(session_graph(record).questions_by_id[qid])
    question = session_question(record)
    if question is None or question["id"] in record["presentations"]:
        return
    now = _now()
    record["presentations"][question["id"]] = {
        "presented_at": now, "content_sha256": question_hash(record, question)}
    events.append(_event("question_presented", now, record, question,
                         prompt=question["prompt"], question_type=question["type"]))
    if question["id"] in record.get("prior_seen_question_ids", []):
        repeated = record["context"]["repeated_question_ids"]
        if question["id"] not in repeated:
            repeated.append(question["id"])
    meta = presented_metadata(record, question["id"])
    family = meta.get("family_id")
    if family and meta.get("family_status") == "declared" and family in record.get("prior_seen_family_ids", []):
        repeated = record["context"].setdefault("repeated_family_ids", [])
        if family not in repeated:
            repeated.append(family)
    if meta.get("family_status") != "declared":
        record["context"]["unclassified_question_ids"] = sorted(set(
            record["context"].get("unclassified_question_ids", []) + [question["id"]]))


def _release_feedback(record, question_ids, events):
    now = _now()
    for entry in record["answers"]:
        qid = entry["question_id"]
        if qid not in question_ids or qid in record["feedback_exposed"]:
            continue
        entry["feedback_available_at"] = now
        record["feedback_exposed"].append(qid)
        events.append({"event_type": "feedback_released", "occurred_at": now,
                       "question_id": qid, "content_sha256": entry["content_sha256"],
                       "family_id": presented_metadata(record, qid).get("family_id"),
                       "family_status": presented_metadata(record, qid).get("family_status", "unclassified"),
                       "payload": {"feedback_policy": record["feedback_policy"],
                                   "evidence": "server_response_available_not_proof_of_reading"}})


def session_summary(record: dict) -> dict:
    if record.get("summary_snapshot"):
        return copy.deepcopy(record["summary_snapshot"])
    if record["kind"] == "support":
        summary = {"kind": "support", "title": record["title"], "asked_count": len(record["answers"]),
                   "independent_evidence": False, "incomplete": not bool(record.get("completed_at")),
                   "notice": "有帮助的练习与确认，不能据此声明独立掌握。"}
    elif record["kind"] == "topic":
        summary = engine.summarize(session_graph(record), record["engine"])
        summary["title"] = "本次练习" if record["mode"] == "practice" else "本次检查"
    else:
        unit = record["unit_snapshot"]
        tasks = {t["id"]: t for t in unit["tasks"]}
        goals = []
        for objective in unit["objectives"]:
            entries = [a for a in record["answers"]
                       if objective["id"] in tasks[a["question_id"]]["objective_ids"]]
            statuses = [a["status"] for a in entries]
            status = ("not_observed" if not entries else "needs_review" if "needs_review" in statuses
                      else "practice_needed" if any(x != "correct" for x in statuses) else "demonstrated")
            goals.append({**objective, "status": status, "observations": len(entries)})
        summary = {"kind": "unit", "unit_id": record["unit_id"], "mode": record["mode"],
                   "title": unit["title"], "objective_results": goals,
                   "asked_count": len(record["answers"]), "confidence": "provisional",
                   "teacher_review": unit.get("teacher_review", {"status": "pending"}),
                   "incomplete": record["index"] < len(record["task_ids"])}
    summary["answers"] = copy.deepcopy(record["answers"])
    summary["context"] = copy.deepcopy(record.get("context", {}))
    summary["created_at"] = record["created_at"]
    summary["completed_at"] = record.get("completed_at")
    summary["content_sha256"] = record["content_sha256"]
    summary["resource_version"] = record.get("resource_version", record["content_sha256"])
    return summary


def _freeze_result(record):
    """完成时连同文字结论一起冻结；升级后的图与规则不重算历史结果。"""
    summary = session_summary(record)
    record["summary_snapshot"] = summary
    record["verdict_snapshot"] = (engine.verdict_label(summary) if record["kind"] == "topic" else
                                  "逐个目标查看本次表现；过程题已保存，可以按量规自查、补充依据并继续修订。")


def _version_problem(record):
    if record.get("completed_at"):
        return None
    if record["kind"] == "support":
        return instruction.version_problem(record)
    if record.get("metadata_sha256") and _hash(record.get("task_metadata", {})) != record["metadata_sha256"]:
        return "保存的任务契约校验失败。原记录保留，本题尚未计分。"
    if record["kind"] == "topic":
        if record.get("graph_snapshot"):
            if session_content.digest(record["graph_snapshot"]) != record["content_sha256"]:
                return "保存的题目版本校验失败。原记录保留，本题尚未计分。"
            if record.get("grader_sha256") != topic_grader_version():
                return "判分规则已经更新。原题目与作答已保留，请重新开始检查；本题尚未计分。"
        elif record["content_sha256"] != graph_version():
            return "旧版会话缺少完整内容快照。原记录已保留，请重新开始检查；本题尚未计分。"
    if record["kind"] == "unit":
        try:
            current_hash = (session_content.unit_hash(record["unit_snapshot"], record["asset_snapshots"])
                            if "asset_snapshots" in record else learning.unit_content_sha256(record["unit_snapshot"]))
        except (KeyError, TypeError, ValueError):
            return "保存的任务或图示快照不完整。原记录已保留，本题尚未计分。"
        if current_hash != record["content_sha256"]:
            return "本次任务的内容版本校验失败。旧记录已保留，请重新开始；本题尚未计分。"
    if record["kind"] == "unit" and record.get("grader_sha256") != unit_grader_version():
        return "任务判分规则已经更新。旧记录已保留，请重新开始；本题尚未计分。"
    return None


def session_payload(record: dict) -> dict:
    if record["kind"] == "support":
        return instruction.public_session(record)
    item = session_item(record)
    done = item is None
    response = {"session": {"id": record["id"], "kind": record["kind"],
                            "mode": record["mode"], "answered_count": len(record["answers"]),
                            "targets": record.get("engine", {}).get("targets", []),
                            "unit_id": record.get("unit_id"), "created_at": record["created_at"],
                            "feedback_policy": record["feedback_policy"],
                            "content_sha256": record["content_sha256"],
                            "resource_version": record.get("resource_version", record["content_sha256"]),
                            "context": copy.deepcopy(record["context"])},
                "item": item, "done": done}
    problem = _version_problem(record)
    if problem:
        response["session"]["resume_blocked"] = True
        response["session"]["resume_message"] = problem
        # 旧图中的节点可能已被删除。已答记录直接读快照，并维持原反馈隔离。
        response["saved_answers"] = export_record(record)["answers"]
    if done:
        response["summary"] = session_summary(record)
        response["verdictText"] = record.get("verdict_snapshot", "这是原内容版本保存的结果，请查看各项目证据。")
        if record["kind"] == "topic":
            excluded, _, _ = exposure_exclusions(record["owner_id"])
            response["availability"] = available_questions(record["engine"]["targets"], excluded)
    return response


def _same_scope(record, previous):
    if not previous or record["kind"] != previous["kind"]:
        return False
    if record["kind"] == "unit":
        return record["unit_id"] == previous["unit_id"]
    return set(record["engine"]["targets"]) == set(previous.get("engine", {}).get("targets", previous.get("targets", [])))


def history_entry(record):
    return {"id": record["id"], "kind": record["kind"], "mode": record["mode"],
            "title": record.get("title", record.get("unit_id", "知识点检查")),
            "unit_id": record.get("unit_id"), "targets": record.get("engine", {}).get("targets", []),
            "created_at": record["created_at"], "completed_at": record.get("completed_at"),
            "answered_count": len(record["answers"]), "content_sha256": record["content_sha256"],
            "grader_sha256": record.get("grader_sha256"),
            "selection_basis": copy.deepcopy(record.get("selection_basis")),
            "resource_version": record.get("resource_version", record["content_sha256"]),
            "context": copy.deepcopy(record["context"]), "resume_blocked": bool(_version_problem(record))}


def export_record(record):
    # 不直接导出内部状态：单元快照中含有尚未作答的任务及答案。
    out = history_entry(record)
    out["answers"] = []
    raw_fields = ("question_id", "prompt", "raw_answer", "normalized_answer", "normalized_answer_format", "objective_ids", "skill_ids", "family_id", "family_status",
                  "presented_at", "answered_at", "duration_ms", "client_elapsed_ms",
                  "content_sha256", "feedback_available_at", "skipped")
    for entry in record["answers"]:
        shown = {k: copy.deepcopy(entry[k]) for k in raw_fields if k in entry}
        if entry["question_id"] in record["feedback_exposed"]:
            shown.update({k: copy.deepcopy(entry[k]) for k in
                          ("status", "reason", "answer", "explanation", "feedback", "rubric") if k in entry})
        out["answers"].append(shown)
    # Public task snapshots make stable option IDs and diagram-dependent
    # responses interpretable without today's catalog. Only tasks that were
    # actually presented are exported; unreleased reference answers stay hidden.
    tasks = ({task["id"]: task for task in record.get("unit_snapshot", {}).get("tasks", [])}
             if record["kind"] == "unit" else record.get("question_snapshots", {}))
    out["presented_tasks"], out["assets"] = [], {}
    observed_objectives = set()
    for qid, presentation in record.get("presentations", {}).items():
        question = tasks.get(qid)
        item = {"question_id": qid, **copy.deepcopy(presentation)}
        if question is None:
            answer = next((entry for entry in record["answers"] if entry["question_id"] == qid), {})
            item.update(question={"id": qid, "prompt": answer.get("prompt", "")},
                        snapshot_status="partial_legacy_record")
        else:
            item.update(question=public_question(question), snapshot_status="saved_public_task")
            metadata = presented_metadata(record, qid)
            item["task_metadata"] = {key: copy.deepcopy(metadata[key]) for key in
                ("resource_version", "skill_ids", "family_id", "family_status", "alignment", "answer_spec")
                if key in metadata}
            if "answer_spec" in metadata:
                item["question"]["answer_spec"] = copy.deepcopy(metadata["answer_spec"])
            observed_objectives.update(question.get("objective_ids", []))
            diagram = question.get("diagram")
            if diagram:
                src = diagram["src"]
                if src not in out["assets"]:
                    try:
                        raw = session_content.asset_bytes(record, src.rsplit("/", 1)[-1])
                        out["assets"][src] = ({"status": "verified_frozen_asset", "content_type": "image/svg+xml",
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "body_base64": base64.b64encode(raw).decode("ascii")} if raw is not None else
                            {"status": "not_saved_in_original_record"})
                    except (KeyError, ValueError):
                        out["assets"][src] = {"status": "saved_asset_failed_integrity_check"}
        out["presented_tasks"].append(item)
    if record["kind"] == "unit":
        out["objective_definitions"] = [copy.deepcopy(objective)
            for objective in record.get("unit_snapshot", {}).get("objectives", [])
            if objective["id"] in observed_objectives]
    if record.get("completed_at"):
        out["summary"] = session_summary(record)
        out["verdictText"] = record.get("verdict_snapshot", "这是原内容版本保存的结果，请查看各项目证据。")
    return out


def public_learning_inputs(owner, *, source_records=None, check_resume=False):
    """Use complete history while withholding results not yet released to its owner.

    Compact summaries remain private server facts. Visibility is taken from
    the original session, and this projection never writes either record.
    """
    scopes = get_store().planning_records(owner)
    sources = {record["id"]: record for record in source_records or ()}
    for scope in scopes:
        if scope["id"] not in sources:
            sources[scope["id"]] = (scope if scope.get("completed_at")
                                    else get_store().load(scope["id"], owner) or {})
        if check_resume and not scope.get("completed_at"):
            saved = sources[scope["id"]]
            scope["resume_blocked"] = not saved or bool(_version_problem(saved))
    visible = learning_needs.public_learning_records(scopes, sources)
    works = []
    for work in get_store().works(owner, limit=None):
        if work.get("kind") == "session_task":
            reference = work.get("source_ref")
            if (not isinstance(reference, dict) or not learning_needs.feedback_is_public(
                    sources.get(reference.get("session_id")), reference.get("question_id"))):
                continue
        works.append(work)
    return visible, works, get_store().need_states(owner)


def public_learning_needs(owner, *, source_records=None):
    scopes, works, states = public_learning_inputs(owner, source_records=source_records)
    return learning_needs.derive_needs(get_catalog(), scopes, works, states)


def set_public_need_state(owner, need_id, action, reason):
    """Authorize against public evidence, including when a hidden later grade exists."""
    if not isinstance(need_id, str) or not need_id.strip() or len(need_id) > 512:
        raise ValueError("学习事项须为有效的非空标识")
    need = next((row for row in public_learning_needs(owner) if row["need_id"] == need_id), None)
    if need is None:
        raise KeyError("找不到已放出反馈的学习事项")
    if action not in {"defer", "reopen"}:
        raise ValueError("学习事项只允许暂缓或重新打开，不能手工改成已掌握")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ValueError("请填写不超过1000字的暂缓或重新打开理由")
    if need["status"] == "resolved":
        raise ValueError("该事项已有具体后续证据；如有新困难，请保留新的学习记录")
    # Re-deriving this decision from private summaries would reveal whether an
    # unfinished delayed-feedback answer already resolves the visible need.
    payload = {"need_id": need_id, "reason": reason.strip(), "source_ref": need["source_ref"],
               "resource_version": need.get("resource_version")}
    get_store().add_event(owner, {"event_type": "learning_need_deferred" if action == "defer" else "learning_need_reopened",
                                 "occurred_at": _now(), "resource_id": need_id, "payload": payload},
                          session_id=need["source_ref"].get("session_id"))
    return next(row for row in get_store().need_states(owner) if row["need_id"] == need_id)


def stage_of(volume: str) -> str:
    if volume[0] in "一二三四五六":
        return "小学"
    if volume[0] in "七八九":
        return "初中"
    return "高中"


def topic_sort_key(topic_id: str):
    """把 m1a.1.10 拆成可比的自然序，避免 1.10 排在 1.2 前面。"""
    parts = topic_id.split(".")[0], topic_id.split(".")[1:]
    return (parts[0],) + tuple(int(x) for x in parts[1])


def build_topics(excluded=()) -> dict:
    """按册→章分组，供前端做选择列表。"""
    volumes = {}
    for topic in GRAPH.topics:
        vol = volumes.setdefault(topic["volume"], {"volume": topic["volume"], "chapters": {}})
        chapter = vol["chapters"].setdefault(topic["chapter"], {"title": topic["chapter"], "topics": []})
        chapter["topics"].append({
            "id": topic["id"],
            "name": topic["name"],
            "type": topic["type"],
            "section": topic["section"],
            "status": topic["review_status"],
            "questionCount": len(GRAPH.bank[topic["id"]]),
            "unseenCount": len({q["id"] for q in GRAPH.bank[topic["id"]]} - set(excluded)),
            "practiceCount": len(GRAPH.practice_bank[topic["id"]]),
            "tier": (GRAPH.tiers.get(topic["id"]) or {}).get("tier"),
        })
    for vol in volumes.values():
        for chapter in vol["chapters"].values():
            chapter["topics"].sort(key=lambda t: topic_sort_key(t["id"]))
    out = []
    for vol in volumes.values():
        chapters = sorted(vol["chapters"].values(), key=lambda c: min(
            GRAPH.index[t["id"]] for t in c["topics"]))
        out.append({"volume": vol["volume"], "stage": stage_of(vol["volume"]), "chapters": chapters})
    out.sort(key=lambda v: VOLUME_ORDER.index(v["volume"])
             if v["volume"] in VOLUME_ORDER else 99)
    return {
        "volumes": out,
        "stats": {
            "topics": len(GRAPH.topics),
            "filled": sum(1 for t in GRAPH.topics if t["review_status"] != "skeleton"),
            "edges": len(GRAPH.dependencies),
            "questions": len(GRAPH.questions),
            "edition": "人教版（高中为人教 A 版）",
            "curriculumVersion": "小学·初中 2024 版 / 高中 2019 版",
        },
    }


def work_descriptor(values, owner):
    """Resolve learner work from server content, never from a client snapshot."""
    if "resource_id" in values and (not isinstance(values["resource_id"], str) or not values["resource_id"]):
        raise ValueError("作品ID必须为非空字符串")
    if values.get("resource_id"):
        work = get_store().load_work(owner, values["resource_id"])
        if work:
            return portfolio.from_work(work)
        if not values.get("kind"):
            raise KeyError("作品不存在或不属于本浏览器")
    kind = values.get("kind")
    if kind == "self_check":
        tid = values.get("topic_id")
        if isinstance(values.get("criterion_index"), bool):
            raise ValueError("目标序号不正确")
        try:
            index = int(values.get("criterion_index", -1))
        except (ValueError, TypeError):
            raise ValueError("目标序号不正确") from None
        card = build_teach(tid) if isinstance(tid, str) else None
        row = next((row for row in (card or {}).get("objective_coverage", [])
                    if row["criterion_index"] == index), None)
        if row is None:
            raise KeyError("这个目标暂时没有自检任务")
        task = get_catalog().self_check(tid, index)
        version = _hash({"task": task["resource_version"], "source": row})
        resource = portfolio.descriptor(kind, f"topic:{tid}:check:{index}", version,
            f"{card['topicName']} · {row['title']}",
            [{"id": "response", "label": "我的思路与过程", "prompt": row["self_check"]["prompt"]}],
            row["self_check"]["criteria"], stage=get_catalog().placements[tid].get("stage"),
            work_id=f"self_check:{task['id']}@{version[:16]}")
        resource["resource_snapshot"].update(task_ids=[task["id"]], family_ids=[task["family_id"]]
            if task.get("family_status") == "declared" else [])
        return resource
    if kind == "project":
        pid = values.get("project_id")
        project = learning_projects().get(pid) if isinstance(pid, str) else None
        if project is None:
            raise KeyError("实践任务不存在")
        tasks = get_catalog().project_steps(pid)
        fields = [{"id": task["id"], "label": step["title"],
                   "prompt": step["instructions"], "deliverable": step["deliverable"]}
                  for step, task in zip(project["steps"], tasks)]
        version = _hash({"source": projects.project_content_sha256(project),
                         "catalog": get_catalog().projects[pid]["resource_version"]})
        resource = portfolio.descriptor(kind, "project:" + pid, version,
            project["title"], fields, [row["criterion"] for row in project["rubric"]], stage=project["stage"])
        resource["resource_snapshot"].update(task_ids=[task["id"] for task in tasks], family_ids=sorted({
            task["family_id"] for task in tasks if task.get("family_status") == "declared"}))
        return resource
    if kind == "unit":
        uid = values.get("unit_id")
        unit = learning_units().get(uid) if isinstance(uid, str) else None
        if unit is None:
            raise KeyError("学习单元不存在")
        version = get_catalog().units.get(uid, {}).get("resource_version", learning.unit_content_sha256(unit))
        return portfolio.descriptor(kind, "unit:" + uid, version, unit["title"],
            [{"id": "notes", "label": "我的解释、疑问和笔记", "prompt": "把你理解的方法与仍想核对的问题写下来。"}],
            [obj["statement"] for obj in unit["objectives"]], stage=unit["stage"])
    if kind == "session_task":
        sid, qid = values.get("session_id"), values.get("question_id")
        record = get_store().load(sid, owner) if isinstance(sid, str) else None
        if record is None or not isinstance(qid, str) or qid not in record["presentations"]:
            raise KeyError("任务尚未呈现或不属于本浏览器")
        tasks = (record["unit_snapshot"]["tasks"] if record["kind"] == "unit"
                 else list(record["question_snapshots"].values()))
        question = next((task for task in tasks if task["id"] == qid), None)
        if question is None:
            raise KeyError("保存的任务不存在")
        answer = next((entry for entry in record["answers"] if entry["question_id"] == qid), {})
        criteria = [row["criterion"] for row in question.get("rubric", [])]
        if not criteria:
            criteria = ["说明所用的方法与条件", "检查计算、单位与结论"]
        stage = record.get("unit_snapshot", {}).get("stage")
        if not stage:
            topic = next((t for t in record.get("graph_snapshot", {}).get("topics", []) if t["id"] == question.get("topicId")), {})
            if topic.get("volume"):
                stage = {"小学": "primary", "初中": "junior", "高中": "senior"}.get(stage_of(topic["volume"]))
        return portfolio.descriptor(kind, {"session_id": sid, "question_id": qid}, question_hash(record, question),
            record.get("title", "任务") + " · 过程记录",
            [{"id": "response", "label": "我的过程与修订", "prompt": question["prompt"],
              "initial_value": answer.get("raw_answer", "")}], criteria,
            work_id=f"session_task:{sid}:{qid}", stage=stage)
    raise ValueError("不支持的作品类型")


def export_works(owner):
    output = []
    for offset in range(0, get_store().count_works(owner), 1000):
        for summary in get_store().works(owner, limit=1000, offset=offset):
            revisions = []
            cursor = 0
            while True:
                page = get_store().work_history(owner, summary["resource_id"], limit=1000, offset=cursor)
                revisions.extend(page)
                if len(page) < 1000:
                    break
                cursor += len(page)
            output.append({"resource_id": summary["resource_id"], "revisions": revisions})
    return output


class Handler(BaseHTTPRequestHandler):
    server_version = "cnkg/0.1"

    def setup(self):
        super().setup()
        _REQUEST_CONTEXT.store = getattr(self.server, "learning_store", None)

    def finish(self):
        try:
            super().finish()
        finally:
            _REQUEST_CONTEXT.store = None

    def log_message(self, fmt, *args):  # 安静一点
        sys.stderr.write("[web] " + fmt % args + "\n")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_pending_owner_cookie", None):
            self.send_header("Set-Cookie", self._pending_owner_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str):
        self._json(code, {"error": message})

    def _owner(self):
        if hasattr(self, "_owner_id"):
            return self._owner_id
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            cookies = SimpleCookie()
        token = cookies.get(_OWNER_COOKIE)
        owner = get_profile().resolve_owner(token.value if token else "")
        if not owner:
            owner = secrets.token_hex(32)
            self._pending_owner_cookie = (f"{_OWNER_COOKIE}={owner}; Path=/; HttpOnly; "
                                          "SameSite=Strict; Max-Age=31536000")
        self._owner_id = owner
        return owner

    def _profile_response(self, result):
        result = copy.deepcopy(result)
        token = result.pop("cookie_token", None)
        if token:
            self._pending_owner_cookie = (f"{_OWNER_COOKIE}={token}; Path=/; HttpOnly; "
                                          "SameSite=Strict; Max-Age=31536000")
            self._owner_id = get_profile().resolve_owner(token)
        return self._json(200, result)

    def _material(self, resource_id, content_sha256):
        get_store().add_event(self._owner(), {"event_type": "material_requested",
            "occurred_at": _now(), "resource_id": resource_id, "content_sha256": content_sha256,
            "payload": {"evidence": "http_request_only_not_completion"}})
        material = get_catalog().materials.get(resource_id)
        if material:
            families = [fid for fid in material["family_ids"]
                        if get_catalog().families[fid]["status"] == "declared"]
            tasks = [tid for tid in material["task_ids"]
                     if get_catalog().tasks[tid]["source_kind"] == "self_check"]
            get_store().add_event(self._owner(), {"event_type": "material_provided", "occurred_at": _now(),
                "resource_id": resource_id, "content_sha256": material["resource_version"],
                "payload": {"family_ids": families, "task_ids": tasks,
                            "evidence": "reference_available_in_response_not_proof_of_reading"}})
            if material["kind"] == "project":
                families = [fid for fid in material["assessment_family_ids"]
                            if get_catalog().families[fid]["status"] == "declared"]
                get_store().add_event(self._owner(), {"event_type": "question_presented", "occurred_at": _now(),
                    "resource_id": resource_id, "content_sha256": material["resource_version"],
                    "payload": {"task_ids": material["task_ids"], "family_ids": families,
                                "evidence": "project_steps_provided_not_proof_of_completion"}})

    def _work_exposure(self, resource):
        snapshot = resource["resource_snapshot"]
        task_ids = snapshot.get("task_ids", [])
        if resource["kind"] == "session_task":
            source = resource["source_ref"]
            record = get_store().load(source["session_id"], self._owner())
            if record:
                self._provided_tasks(record, [source["question_id"]])
            return
        if task_ids:
            get_store().add_event(self._owner(), {"event_type": "question_presented", "occurred_at": _now(),
                "resource_id": resource["resource_id"], "content_sha256": resource["resource_version"],
                "payload": {"task_ids": task_ids, "family_ids": snapshot.get("family_ids", []),
                            "evidence": "work_task_provided_not_proof_of_completion"}})

    def _provided_tasks(self, record, task_ids, event_type="session_content_provided"):
        """Append a replay exposure without changing frozen answers or timing."""
        known = set(record.get("presentations", {})) | {entry["question_id"] for entry in record.get("answers", [])}
        shown = sorted(set(task_ids) & known)
        if not shown:
            return
        families = sorted({metadata["family_id"] for qid in shown
            if (metadata := presented_metadata(record, qid)).get("family_status") == "declared"
            and metadata.get("family_id")})
        get_store().add_event(self._owner(), {"event_type": event_type, "occurred_at": _now(),
            "resource_id": record.get("unit_id") or ("unit:" + record["source_origin"]["unit_id"] if record["kind"] == "support" else None), "content_sha256": record.get("resource_version", record["content_sha256"]),
            "payload": {"task_ids": shown, "family_ids": families,
                        "evidence": "saved_content_provided_again_not_proof_of_reading"}}, session_id=record["id"])

    def _session_response_exposure(self, record, response):
        shown = [entry["question_id"] for entry in response.get("summary", {}).get("answers", [])]
        shown += [entry["question_id"] for entry in response.get("saved_answers", [])]
        if record["kind"] == "support":
            shown += [entry["question_id"] for entry in response.get("answers", [])]
        if response.get("item"):
            shown.append(response["item"]["id"] if record["kind"] == "support" else response["item"]["question"]["id"])
        self._provided_tasks(record, shown)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(urllib.parse.unquote(path[len("/static/"):]))
        if path == "/api/profile":
            return self._json(200, get_profile().status(self._owner()))
        if path == "/api/quality-scope":
            return self._json(200, _CONTENT.scope_report)
        if path == "/api/manipulative":
            uid = params.get("unit_id")
            if uid not in manipulatives.CONFIG:
                return self._error(404, "操作表征不存在")
            version = get_catalog().units[uid]["resource_version"]
            representation_version = manipulatives.representation_version(uid)
            saved = get_store().latest_resource_event(self._owner(), "unit:" + uid, "manipulation_saved")
            return self._json(200, {"saved": saved, "resource_version": version,
                "representation_version": representation_version,
                "same_version": bool(saved and saved["content_sha256"] == version
                    and saved["payload"].get("representation_version") == representation_version)})
        if path == "/api/learning/units":
            return self._json(200, {"units": [unit_summary(u) for u in learning_units().values()]})
        if path == "/api/projects":
            return self._json(200, {"projects": [projects.project_summary(p)
                                               for p in learning_projects().values()]})
        if path.startswith("/api/projects/"):
            pid = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            project = learning_projects().get(pid)
            if project is None:
                return self._error(404, "实践任务不存在")
            self._material("project:" + pid, projects.project_content_sha256(project))
            return self._json(200, projects.public_project(project))
        if path.startswith("/api/learning/units/"):
            uid = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            unit = learning_units().get(uid)
            if unit is None:
                return self._error(404, "学习单元不存在")
            unit_digest = session_content.unit_hash(unit, unit_assets(unit))
            self._material("unit:" + uid, unit_digest)
            teaching = learning.public_unit(unit)
            teaching["content_sha256"] = unit_digest
            # 教学页保留例题；各评价阶段的题面仅在会话中逐题呈现。
            teaching.pop("tasks", None)
            teaching["task_counts"] = unit_summary(unit)["task_counts"]
            teaching["prerequisite_unit_ids"] = unit.get("prerequisite_unit_ids", [])
            teaching["objectives"] = copy.deepcopy(unit["objectives"])
            teaching["instructional_support"] = instruction.support_index(unit)
            teaching["resource_version"] = get_catalog().units.get(uid, {}).get("resource_version", teaching["content_sha256"])
            if uid in manipulatives.CONFIG:
                teaching["manipulative"] = manipulatives.public_config(uid)
            return self._json(200, teaching)
        if path.startswith("/api/session/"):
            asset = re.fullmatch(r"/api/session/([0-9a-f]{32})/assets/([^/]+)", path)
            if asset:
                record = get_store().load(asset.group(1), self._owner())
                if record is None:
                    return self._error(404, "图示不存在或不属于本浏览器")
                try:
                    body = session_content.asset_bytes(record, urllib.parse.unquote(asset.group(2)))
                except ValueError as exc:
                    return self._error(409, str(exc))
                if body:
                    tasks = (record.get("unit_snapshot", {}).get("tasks", []) if record["kind"] == "unit"
                             else record.get("question_snapshots", {}).values())
                    self._provided_tasks(record, [task["id"] for task in tasks
                        if task.get("diagram", {}).get("src", "").rsplit("/", 1)[-1] == urllib.parse.unquote(asset.group(2))],
                        "diagram_provided")
                return self._send(200, body, CONTENT_TYPES["svg"]) if body else self._error(404, "图示尚未呈现")
            sid = path.rsplit("/", 1)[-1]
            with _SESSION_LOCK:
                record = get_store().load(sid, self._owner())
                if record is None:
                    return self._error(404, "会话不存在或不属于本浏览器")
                response = session_payload(record)
                self._session_response_exposure(record, response)
                return self._json(200, response)
        if path == "/api/history":
            try:
                limit, offset = int(params.get("limit", "50")), int(params.get("offset", "0"))
                records = get_store().records(self._owner(), limit=limit, offset=offset)
            except ValueError as exc:
                return self._error(400, str(exc))
            return self._json(200, {"sessions": [history_entry(r) for r in records],
                                    "total": get_store().count(self._owner()), "limit": limit, "offset": offset})
        if path == "/api/plan":
            stage = params.get("stage")
            if stage not in (None, "primary", "junior", "senior"):
                return self._error(400, "学段不正确")
            owner = self._owner()
            scopes, works, states = public_learning_inputs(owner, check_resume=True)
            return self._json(200, planner.plan(get_catalog(), scopes, works,
                need_states=states, exposures=get_store().exposures(owner), stage=stage))
        if path in ("/api/work", "/api/works", "/api/work/revisions"):
            owner = self._owner()
            try:
                if path == "/api/works":
                    limit, offset = int(params.get("limit", "50")), int(params.get("offset", "0"))
                    return self._json(200, {"works": get_store().works(owner, limit=limit, offset=offset),
                        "total": get_store().count_works(owner), "limit": limit, "offset": offset})
                resource = work_descriptor(params, owner)
                self._work_exposure(resource)
                if path == "/api/work/revisions":
                    return self._json(200, {"revisions": get_store().work_history(owner, resource["resource_id"],
                        limit=int(params.get("limit", "50")), offset=int(params.get("offset", "0")))})
                return self._json(200, {"descriptor": resource,
                    "work": get_store().load_work(owner, resource["resource_id"])})
            except KeyError:
                return self._error(404, "作品资源不存在或不属于本浏览器")
            except ValueError as exc:
                return self._error(400, str(exc))
        if path == "/api/catalog/coverage":
            return self._json(200, {"version": get_catalog().version, "coverage": get_catalog().coverage})
        if path == "/api/export":
            owner = self._owner()
            with _SESSION_LOCK:
                records = get_store().records(owner)
                exported_sessions = [export_record(record) for record in records]
                for record in records:
                    self._provided_tasks(record, list(record.get("presentations", {})), "export_content_provided")
                exported_works = export_works(owner)
                for work in exported_works:
                    if work["revisions"]:
                        self._work_exposure(portfolio.from_work(work["revisions"][0]))
                visible_need_ids = {need["need_id"] for need in public_learning_needs(owner, source_records=records)}
                public_events = learning_needs.public_learning_events(get_store().events(owner),
                    {record["id"]: record for record in records}, visible_need_ids)
                return self._json(200, {"schema": 3, "exported_at": _now(),
                    "export_kind": "readable_learning_record_not_database_backup",
                    "evidence_notice": "匿名浏览器记录；请求材料和服务端放出反馈不证明实际阅读，时间间隔不证明独立作答。",
                    "sessions": exported_sessions,
                    "works": exported_works,
                    "events": public_events})
        if path == "/api/topics":
            excluded, _, _ = exposure_exclusions(self._owner())
            return self._json(200, build_topics(excluded))
        if path.startswith("/api/teach/"):
            tid = urllib.parse.unquote(path[len("/api/teach/"):])
            card = build_teach(tid)
            if card is None:
                return self._error(404, f"{tid} 还没有讲解卡")
            self._material("topic:" + tid, _hash(card))
            excluded, _, _ = exposure_exclusions(self._owner())
            card["availability"] = available_questions([tid], excluded)
            return self._json(200, card)
        return self._error(404, "not found")

    def _static(self, name: str):
        """name 是 /static/ 之后的相对路径，可以含子目录（KaTeX 有 60+ 字体文件）。"""
        name = name.replace("\\", "/").lstrip("/")
        allowed = (name in ALLOWED_STATIC or name.startswith(ALLOWED_PREFIX)
                   or name.startswith(ALLOWED_DIAGRAM_PREFIX) and name.endswith(".svg"))
        if not allowed or ".." in name.split("/"):
            return self._error(404, "not found")
        target = (STATIC / name).resolve()
        root = STATIC.resolve()
        # 解析后必须仍在 static/ 内，挡住 ../ 与符号链接逃逸
        if root not in target.parents:
            return self._error(404, "not found")
        ext = name.rsplit(".", 1)[-1].lower()
        frozen = _STATIC_SNAPSHOTS.get(name)
        if frozen is None:
            frozen = _CONTENT.asset_bytes("/static/" + name)
        if frozen is None and not target.is_file():
            return self._error(404, "not found")
        self._send(200, frozen if frozen is not None else target.read_bytes(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "请求长度不合法")
        maximum = profile_recovery.MAX_ENVELOPE_BYTES + 4096 if path == "/api/profile/restore" else 1_000_000
        if length < 0 or length > maximum:
            return self._error(413, "请求过大")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error(400, "请求不是合法 JSON")
        if not isinstance(payload, dict):
            return self._error(400, "请求必须是 JSON 对象")
        with _SESSION_LOCK:
            try:
                if path == "/api/profile/enable":
                    return self._profile_response(get_profile().enable(self._owner(), payload.get("label", "我的学习档案")))
                if path == "/api/profile/rotate":
                    return self._profile_response(get_profile().rotate(self._owner()))
                if path == "/api/profile/recover":
                    return self._profile_response(get_profile().recover(payload.get("recovery_code")))
                if path == "/api/profile/backup":
                    return self._json(200, get_profile().backup(self._owner(), payload.get("recovery_code")))
                if path == "/api/profile/restore":
                    return self._profile_response(get_profile().restore(payload.get("envelope"), payload.get("recovery_code")))
                if path == "/api/learning-needs/state":
                    return self._json(200, set_public_need_state(self._owner(), payload.get("need_id"),
                        payload.get("action"), payload.get("reason")))
                if path == "/api/manipulative":
                    uid = payload.get("unit_id")
                    manip_state = manipulatives.validate_state(uid, payload.get("state"))
                    version = get_catalog().units[uid]["resource_version"]
                    if (payload.get("resource_version") != version or payload.get("representation_version")
                            != manipulatives.representation_version(uid)):
                        return self._error(409, "材料或操作表征版本已改变，请保留当前解释后重新打开单元")
                    reflection = payload.get("reflection", "")
                    if not isinstance(reflection, str) or len(reflection) > 2000:
                        return self._error(400, "解释须为不超过2000字的文字")
                    event = {"event_type": "manipulation_saved", "occurred_at": _now(), "resource_id": "unit:" + uid,
                        "content_sha256": version, "payload": {"state": manip_state, "reflection": reflection,
                            "objective_ids": manipulatives.CONFIG[uid]["objective_ids"], "independent_evidence": False,
                            "representation_version": manipulatives.representation_version(uid),
                            "representation_snapshot": manipulatives.snapshot(uid),
                            "evidence": "learner_saved_operation_not_correctness"}}
                    get_store().add_event(self._owner(), event)
                    get_store().add_event(self._owner(), {**event, "event_type": "reference_opened",
                        "payload": {"family_ids": get_catalog().materials["unit:" + uid]["family_ids"],
                            "task_ids": [], "evidence": "interactive_representation_used_not_mastery"}})
                    return self._json(200, {"saved": True})
                if path == "/api/support/start":
                    owner = self._owner()
                    unit = learning_units().get(payload.get("unit_id"))
                    if not unit:
                        return self._error(404, "学习单元不存在")
                    need_ids = payload.get("need_ids", [])
                    if not isinstance(need_ids, list) or not all(isinstance(n, str) for n in need_ids) or len(need_ids) != len(set(need_ids)):
                        return self._error(400, "学习事项须为无重复的标识列表")
                    known = {n["need_id"]: n for n in public_learning_needs(owner)}
                    if any(n not in known for n in need_ids):
                        return self._error(404, "学习事项不存在或不属于当前档案")
                    record, events = instruction.create(unit, payload.get("support_id"), owner, get_catalog(),
                        get_store().exposures(owner), [known[n] for n in need_ids], _now(),
                        assets=unit_assets(unit))
                    get_store().save(record, events)
                    return self._json(200, instruction.public_session(record))
                if path in ("/api/support/answer", "/api/support/hint"):
                    record = get_store().load(payload.get("session_id"), self._owner())
                    if not record or record["kind"] != "support":
                        return self._error(404, "指导活动不存在或不属于当前档案")
                    if path.endswith("/hint"):
                        feedback, events = instruction.hint(record, payload.get("level"), _now())
                    else:
                        feedback, events = instruction.answer(record, payload.get("question_id"),
                            payload.get("answer", ""), _now(), payload.get("skipped") is True)
                    if events:
                        get_store().save(record, events)
                    return self._json(200, {**instruction.public_session(record), "feedback": feedback})
                if path == "/api/session":
                    return self._create_session(payload)
                if path == "/api/answer":
                    return self._answer(payload)
                if path == "/api/work":
                    owner = self._owner()
                    resource = work_descriptor(payload, owner)
                    if payload.get("resource_id") and payload["resource_id"] != resource["resource_id"]:
                        return self._error(409, "内容版本已改变，请恢复原作品或重新打开当前任务")
                    saved = get_store().save_work(owner, resource["resource_id"], portfolio.learner_payload(resource, payload),
                        payload.get("expected_revision"), payload.get("request_id"))
                    self._work_exposure(resource)
                    return self._json(200, {"descriptor": resource, "work": saved})
                if path == "/api/exposure":
                    resource = payload.get("resource_id")
                    if not isinstance(resource, str) or resource not in get_catalog().materials:
                        return self._error(400, "材料不存在")
                    material = get_catalog().material(resource)
                    if payload.get("event_type") != "reference_opened":
                        return self._error(400, "不支持的学习事件")
                    reference = payload.get("reference_key")
                    if not isinstance(reference, str) or not re.fullmatch(r"(?:self_check|example):\d{1,3}", reference):
                        return self._error(400, "参考项目不正确")
                    family_ids, task_ids = [], []
                    reference_kind, index_text = reference.split(":")
                    index = int(index_text)
                    if reference_kind == "self_check" and resource.startswith("topic:"):
                        task = get_catalog().self_check(resource[6:], index)
                        task_ids = [task["id"]]
                        if task["family_status"] == "declared":
                            family_ids = [task["family_id"]]
                    elif reference_kind == "example" and resource.startswith("unit:"):
                        examples = learning_units()[resource[5:]]["worked_examples"]
                        if index >= len(examples):
                            return self._error(400, "参考例题不存在")
                        family = examples[index].get("family_id")
                        if family in get_catalog().families and get_catalog().families[family]["status"] == "declared":
                            family_ids = [family]
                    elif reference_kind == "example" and resource.startswith("topic:") and index == 0:
                        checks = [get_catalog().tasks[tid]["family_id"] for tid in material["task_ids"]
                                  if get_catalog().tasks[tid]["source_kind"] == "self_check"]
                        family_ids = [fid for fid in material["family_ids"] if fid not in checks
                                      and get_catalog().families[fid]["status"] == "declared"]
                    else:
                        return self._error(400, "这份材料不包含该参考项目")
                    get_store().add_event(self._owner(), {"event_type": "reference_opened", "occurred_at": _now(),
                        "resource_id": resource, "content_sha256": material["resource_version"],
                        "payload": {"family_ids": family_ids, "task_ids": task_ids, "reference_key": reference,
                                    "evidence": "client_reported_open_not_proof_of_understanding"}})
                    return self._json(200, {"recorded": True})
            except (learning_store.ConflictError, profile_recovery.ProfileConflictError) as exc:
                return self._error(409, str(exc))
            except KeyError:
                return self._error(404, "学习资源不存在或不属于本浏览器")
            except ValueError as exc:
                return self._error(400, str(exc))
            except profile_recovery.ProfileDependencyError as exc:
                return self._error(503, str(exc))
        return self._error(404, "not found")

    def _create_session(self, payload):
        global _SESSION_COUNTER
        mode = payload.get("mode", "diagnose")
        if not isinstance(mode, str):
            return self._error(400, "模式必须是字符串")
        if "unit_id" in payload and not isinstance(payload["unit_id"], str):
            return self._error(400, "学习单元ID必须是字符串")
        owner = self._owner()
        record = {"id": uuid.uuid4().hex, "owner_id": owner, "mode": mode,
                  "created_at": _now(), "answers": [], "presentations": {}, "question_snapshots": {},
                  "feedback_exposed": [], "context": {
                      "reported_instruction_seen": payload.get("instruction_seen") is True,
                      "material_requested": False, "actual_delay_seconds": None,
                      "delay_window_met": False, "repeated_question_ids": [],
                      "independent_evidence": False}}
        previous_id = payload.get("previous_session_id")
        if previous_id is not None and not isinstance(previous_id, str):
            return self._error(400, "前次会话ID必须是字符串")
        previous = get_store().load(previous_id, owner) if previous_id else None
        if previous_id and not previous:
            return self._error(404, "前次会话不存在或不属于本浏览器")
        prior_count = get_store().count(owner)
        excluded, prior_questions, prior_families = exposure_exclusions(owner)
        if payload.get("unit_id"):
            unit = learning_units().get(payload["unit_id"])
            if unit is None or mode not in ("practice", "transfer", "delayed"):
                return self._error(400, "学习单元或练习阶段不合法")
            task_ids = [t["id"] for t in unit["tasks"] if t["phase"] == mode]
            focus = payload.get("focus_skill_ids", [])
            if not isinstance(focus, list) or not all(isinstance(sid, str) for sid in focus) or len(focus) != len(set(focus)):
                return self._error(400, "目标技能须为无重复的标识列表")
            if focus:
                if not set(focus).issubset(get_catalog().units.get(unit["id"], {}).get("skill_ids", [])):
                    return self._error(400, "所选技能不属于这个单元")
                from cnkg.learning_needs import task_options
                selected = task_options(get_catalog(), focus, get_store().planning_records(owner),
                    get_store().exposures(owner), allowed_task_ids=set(task_ids), limit=None)
                task_ids = [option["task_id"] for option in selected]
                record["selection_basis"] = {"focus_skill_ids": focus, "task_options": selected,
                    "notice": "仅按显式目标筛选，再按题族、表征及参考帮助曝光排序；间隔由实际记录另行计算。"}
                record["context"]["focus_skill_ids"] = list(focus)
            if not task_ids:
                return self._error(409, "这个阶段还没有可用任务")
            record.update(kind="unit", unit_id=unit["id"], title=unit["title"],
                          unit_snapshot=copy.deepcopy(unit), task_ids=task_ids, index=0,
                          content_sha256=session_content.unit_hash(unit, unit_assets(unit)),
                          asset_snapshots=unit_assets(unit),
                          resource_version=get_catalog().units.get(unit["id"], {}).get("resource_version", learning.unit_content_sha256(unit)),
                          grader_sha256=unit_grader_version())
            record["task_metadata"] = {q["id"]: task_metadata(q) for q in unit["tasks"]}
            resources = ["unit:" + unit["id"]]
        else:
            if mode not in engine.MODES:
                return self._error(400, "未知练习或诊断模式")
            if mode == "screen":
                volume = payload.get("volume")
                targets = [t["id"] for t in GRAPH.topics if t["volume"] == volume]
            else:
                targets = payload.get("targets") or []
            if not isinstance(targets, list) or not targets or len(targets) > len(GRAPH.topics):
                return self._error(400, "请选择有效的知识点")
            if any(not isinstance(t, str) for t in targets):
                return self._error(400, "知识点ID必须是字符串")
            try:
                limit = payload.get("limit")
                if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500):
                    raise ValueError("出题上限须为1到500的整数")
                session = engine.create_session(GRAPH, targets, mode=mode, limit=limit,
                                                rotate=prior_count)
                _SESSION_COUNTER += 1
            except (TypeError, ValueError) as exc:
                return self._error(400, str(exc))
            # 新题检查排除已有题目与已声明同族曝光；复习练习仍可重复。
            if mode != "practice":
                available = available_questions(session["targets"], excluded)
                if available["unseen_count"] == 0:
                    return self._json(409, {"error": "这些目标目前没有未呈现且未暴露同族参考的检查题。可以继续复习练习，重复记录会保留。",
                        "code": "no_fresh_tasks", "availability": available,
                        "next_action": {"targets": session["targets"], "mode": "practice"}})
                session["seen"] = sorted(excluded)
            frozen_graph = session_content.graph_snapshot(GRAPH, session["targets"],
                assets=_CONTENT.assets_for(GRAPH.questions) if GRAPH is _CONTENT.graph else None)
            local_graph = session_content.restore_graph(frozen_graph)
            engine.next_item(local_graph, session)
            record.update(kind="topic", engine=session, graph_snapshot=frozen_graph,
                          content_sha256=session_content.digest(frozen_graph), grader_sha256=topic_grader_version(),
                          topic_names={tid: GRAPH.name(tid) for tid in GRAPH.by_id},
                          title="、".join(GRAPH.name(tid) for tid in targets))
            record["resource_version"] = record["content_sha256"]
            record["task_metadata"] = {q["id"]: task_metadata(q) for q in frozen_graph["questions"]}
            resources = ["topic:" + tid for tid in session["targets"]]
        record["metadata_sha256"] = _hash(record["task_metadata"])
        record["prior_seen_question_ids"] = sorted(prior_questions)
        record["prior_seen_family_ids"] = sorted(prior_families)
        record["context"]["repeated_family_ids"] = []
        record["context"]["novelty_policy"] = "question_ids_and_declared_families; unclassified_not_independent"
        material = get_store().latest_material(owner, resources)
        if material:
            record["context"].update(material_requested=True,
                material_requested_at=material["occurred_at"], material_content_sha256=material["content_sha256"],
                material_exposure_evidence="http_request_only_not_completion")
        # A stale result page may name an old predecessor. The server chooses
        # the latest completed same-scope record and checks intervening exposure.
        # Keep the requested link for provenance instead of trusting it as a clock.
        candidates = [r for r in get_store().latest_scope_records(owner)
                      if r.get("completed_at") and r.get("answered_count", 0) and _same_scope(record, r)]
        families = {item.get("family_id") for item in record["task_metadata"].values()
                    if item.get("family_status") == "declared" and item.get("family_id")}
        for resource in resources:
            families.update(get_catalog().materials.get(resource, {}).get("family_ids", []))
        activity = get_store().latest_scope_activity(owner,
            [*resources, *([record["unit_id"]] if record["kind"] == "unit" else [])],
            question_ids=record["task_metadata"], family_ids=families)
        record["context"].update(interval_context(record, candidates,
            requested_previous=previous, requested_same_scope=_same_scope(record, previous), latest_activity=activity))
        record["feedback_policy"] = "immediate" if mode == "practice" else "deferred"
        events = [_event("session_started", record["created_at"], record, mode=mode, context=record["context"])]
        _present_question(record, events)
        if session_question(record) is None:
            record["completed_at"] = _now()
            _freeze_result(record)
        get_store().save(record, events)
        return self._json(200, session_payload(record))

    def _answer(self, payload):
        sid = payload.get("session_id")
        record = get_store().load(sid, self._owner()) if isinstance(sid, str) else None
        if record is None:
            return self._error(404, "会话不存在或不属于本浏览器，请重新开始")
        if record["kind"] == "support":
            return self._error(400, "指导活动请使用专用的支持任务入口")
        skipped = payload.get("skip") is True
        raw = payload.get("answer")
        if not skipped and (not isinstance(raw, str) or not raw.strip() or len(raw) > 8000):
            return self._error(400, "请填写答案（不超过8000字），或选择暂时不会")
        qid = payload.get("question_id")
        prior = next((a for a in record["answers"] if a["question_id"] == qid), None)
        if prior:
            if prior.get("skipped") != skipped or prior["raw_answer"] != ("" if skipped else raw):
                return self._error(409, "这道题已提交；原始作答保留，不能覆盖")
            response = session_payload(record)
            response["duplicate"] = True
            response["feedback"] = ({k: prior[k] for k in ("status", "reason", "answer", "explanation", "feedback", "rubric") if k in prior}
                                    if record["feedback_policy"] == "immediate" else {"deferred": True})
            self._session_response_exposure(record, response)
            self._provided_tasks(record, [qid])
            return self._json(200, response)
        problem = _version_problem(record)
        if problem:
            return self._error(409, problem)
        question = session_question(record)
        if question is None:
            return self._error(409, "当前会话已经完成")
        if qid != question["id"]:
            return self._error(409, "题号与当前题不一致，请恢复当前会话；答案尚未计入")
        if skipped:
            result = {"status": "not_attempted", "answer": display_answer(question),
                      "explanation": question.get("explanation", ""), "feedback": "先学习相关内容，再做一道新题确认。"}
            if question.get("type") == "rubric":
                result["rubric"] = question.get("rubric", [])
        elif record["kind"] == "unit":
            grading_question = {**question, **({"answer_spec": presented_metadata(record, qid)["answer_spec"]}
                if "answer_spec" in presented_metadata(record, qid) else {})}
            result = learning.grade_task(grading_question, raw)
            if question.get("type") == "choice" and result["status"] in ("correct", "wrong"):
                result["answer"] = display_answer(question)
        else:
            grading_question = {**question, **({"answer_spec": presented_metadata(record, qid)["answer_spec"]}
                if "answer_spec" in presented_metadata(record, qid) else {})}
            result = {**grade_answer(grading_question, raw), "answer": display_answer(question),
                      "explanation": question.get("explanation", "")}
        if result["status"] == "invalid":
            return self._error(422, result.get("feedback") or "答案格式无法识别。请按题干要求填写；格式问题不计作答错。")
        now = _now()
        presentation = record["presentations"][qid]
        client_elapsed = payload.get("elapsed_ms")
        if (isinstance(client_elapsed, bool) or not isinstance(client_elapsed, (int, float))
                or not 0 <= client_elapsed <= 604800000):
            client_elapsed = None
        normalized = (None if skipped else str(to_fraction(raw)) if question["type"] == "numeric"
                      else option_id_for(question, raw) if question["type"] == "choice"
                      else normalize_text(raw) if question["type"] == "short" else raw.strip())
        entry = {**result, "question_id": qid, "prompt": question["prompt"],
                 "raw_answer": raw if not skipped else "", "normalized_answer": normalized,
                 "normalized_answer_format": "rational_v1" if question["type"] == "numeric" and not skipped else "text_v1",
                 "skipped": skipped, "presented_at": presentation["presented_at"], "answered_at": now,
                 "duration_ms": round(_seconds(now, presentation["presented_at"]) * 1000),
                 "client_elapsed_ms": client_elapsed, "content_sha256": presentation["content_sha256"],
                 "objective_ids": question.get("objective_ids", []),
                 "skill_ids": presented_metadata(record, qid).get("skill_ids", []),
                 "family_id": presented_metadata(record, qid).get("family_id"),
                 "family_status": presented_metadata(record, qid).get("family_status", "unclassified")}
        record["answers"].append(entry)
        events = [_event("answer_submitted", now, record, question,
                          raw_answer=entry["raw_answer"], normalized_answer=normalized, skipped=skipped,
                          duration_ms=entry["duration_ms"], client_elapsed_ms=client_elapsed,
                          duration_evidence="server_wall_clock; client_elapsed_ms_is_reported")]
        if record["kind"] == "unit":
            record["index"] += 1
        else:
            verdict = "skip" if skipped else result["status"]
            local_graph = session_graph(record)
            engine.apply_answer(local_graph, record["engine"], verdict, text=raw)
            engine.next_item(local_graph, record["engine"])
        _present_question(record, events)
        if record["feedback_policy"] == "immediate":
            _release_feedback(record, [qid], events)
        if session_question(record) is None:
            record["completed_at"] = _now()
            _release_feedback(record, [a["question_id"] for a in record["answers"]], events)
            _freeze_result(record)
            events.append(_event("session_completed", record["completed_at"], record,
                                 answered_count=len(record["answers"])))
        record["updated_at"] = now
        get_store().save(record, events)
        response = session_payload(record)
        response["feedback"] = result if record["feedback_policy"] == "immediate" else {"deferred": True}
        return self._json(200, response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.learning_store = learning_store.LearningStore()
    print(f"服务已启动：http://{args.host}:{httpd.server_port}")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
