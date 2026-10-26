"""极简本地 Web 服务：把诊断引擎接到浏览器上。

只依赖标准库。默认只绑定 127.0.0.1，不对外网开放。

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

from cnkg import engine, learning, evidence, learning_store, projects  # noqa: E402
from cnkg.graph import VOLUME_ORDER, load_graph  # noqa: E402
from cnkg.verify import compare, compare_short, to_number, to_fraction, normalize_text  # noqa: E402
from cnkg.answers import grade_answer  # noqa: E402

# 外部题（真题源）默认不加载。
# 可选题库的实际准入量由当前证据策略决定；题量不等于独立学科审核。
# 教学场景要开，显式设 CNKG_INCLUDE_EXTERNAL=1。
# 开关用环境变量而不是命令行参数，方便后台启动时控制。
_INCLUDE_EXTERNAL = os.environ.get("CNKG_INCLUDE_EXTERNAL", "0") == "1"
GRAPH = load_graph(include_external=_INCLUDE_EXTERNAL)
# 出题轮转计数器：每建一个会话 +1，让同一知识点的后续诊断抽到不同的题。
# 不用随机数——同一组参数要可复现，诊断记录要可审计。
_SESSION_COUNTER = 0
_STORE = None
_OWNER_COOKIE = "cnkg_owner"
_OWNER_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SESSION_LOCK = threading.RLock()
_UNIT_CACHE = None
_PROJECT_CACHE = None
ALLOWED_STATIC = {"index.html", "app.js", "style.css", "review.html", "math.js",
                  "ext.html", "ext-data.json"}
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
    return {
        "id": question["id"],
        "topicId": question["topicId"],
        "difficulty": question.get("difficulty"),
        "type": question.get("type"),
        "prompt": question["prompt"],
        "choices": question.get("choices"),
    }


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


_CHALLENGE_CACHE: dict | None = None


def _challenge_pool() -> dict:
    """挑战题（TAL 竞赛题）：按 links.json 的点→id 索引取正文。懒加载并缓存。"""
    global _CHALLENGE_CACHE
    if _CHALLENGE_CACHE is not None:
        return _CHALLENGE_CACHE
    items_path = DATA / "challenge" / "tal.json"
    links_path = DATA / "challenge" / "links.json"
    if not (items_path.is_file() and links_path.is_file()):
        _CHALLENGE_CACHE = {"items": {}, "links": {}}
        return _CHALLENGE_CACHE
    items = json.loads(items_path.read_text(encoding="utf-8"))["items"]
    links = json.loads(links_path.read_text(encoding="utf-8"))["samples"]
    _CHALLENGE_CACHE = {"items": {x["id"]: x for x in items}, "links": links}
    return _CHALLENGE_CACHE


def learning_units() -> dict:
    global _UNIT_CACHE
    if _UNIT_CACHE is None:
        _UNIT_CACHE = learning.load_units(DATA)
    return _UNIT_CACHE


def learning_projects() -> dict:
    global _PROJECT_CACHE
    if _PROJECT_CACHE is None:
        _PROJECT_CACHE = projects.load_projects(DATA)
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
    path = DATA / "teach" / f"{topic_id}.json"
    mode, card = "none", {}
    if path.is_file():
        card = json.loads(path.read_text(encoding="utf-8"))
        mode = "full"
    else:
        group = tier.get("reference_group") or tier.get("brief_group")
        brief = DATA / "teach" / "clusters" / f"{group}.json"
        if group and brief.is_file():
            card = json.loads(brief.read_text(encoding="utf-8"))
            mode = "brief"
    pool = GRAPH.practice_bank.get(topic_id, [])
    by_id = {q["id"]: q for q in pool}
    refs = [ref for ref in card.get("practice", []) if ref.get("id") in by_id]
    selected = [by_id[ref["id"]] for ref in refs] or pool[:4]
    return {
        **card, "topicId": topic_id, "mode": mode, "tier": tier.get("tier"),
        "topicName": topic["name"], "description": topic.get("description"),
        "mastery_criteria": topic.get("mastery_criteria", []),
        "review_status": card.get("review_status", "draft"),
        "practice_questions": [public_question(q) for q in selected],
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
    if _STORE is None:
        _STORE = learning_store.LearningStore()
    return _STORE


def _hash(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def graph_version():
    # 数据与推断规则共同决定一次诊断的含义；不能用升级后的图悄悄继续旧会话。
    cached = getattr(GRAPH, "_web_content_version", None)
    if cached is None:
        sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                   for name in ("cnkg/engine.py", "cnkg/graph.py", "cnkg/evidence.py", "cnkg/verify.py",
                                "cnkg/answers.py", "cnkg/contracts.py", "schema/answer-spec.schema.json")}
        cached = _hash({"topics": GRAPH.topics, "dependencies": GRAPH.dependencies,
                        "questions": GRAPH.questions, "tiers": GRAPH.tiers, "runtime": sources})
        GRAPH._web_content_version = cached
    return cached


def unit_grader_version():
    return _hash({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                  for name in ("cnkg/learning.py", "cnkg/verify.py", "cnkg/answers.py",
                               "cnkg/contracts.py", "schema/answer-spec.schema.json")})


def _seconds(later, earlier):
    if not earlier:
        return None
    return max(0.0, (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds())


def _event(kind, when, record=None, question=None, **payload):
    return {"event_type": kind, "occurred_at": when,
            "resource_id": (record.get("unit_id") if record else None),
            "question_id": question.get("id") if question else None,
            "content_sha256": (question_hash(record, question) if question else
                               record.get("content_sha256") if record else None),
            "payload": payload}


def question_hash(record, question):
    return (_hash({"task": question, "unit_content_sha256": record["content_sha256"]})
            if record["kind"] == "unit" else evidence.question_content_sha256(question))


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
        public = next(t for t in learning.public_unit(unit)["tasks"] if t["id"] == question["id"])
        return {"topicId": (unit.get("topic_ids") or [None])[0], "topicName": unit["title"],
                "attempt": record["index"] + 1, "question": public}
    current = record["engine"]["current"]
    return {"topicId": current["topicId"],
            "topicName": record.get("topic_names", {}).get(current["topicId"], current["topicId"]),
            "attempt": current["attempt"], "question": public_question(question)}


def _present_question(record, events):
    if record["kind"] == "topic" and record["engine"].get("current"):
        qid = record["engine"]["current"]["questionId"]
        record["question_snapshots"][qid] = copy.deepcopy(GRAPH.questions_by_id[qid])
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
                       "payload": {"feedback_policy": record["feedback_policy"],
                                   "evidence": "server_response_available_not_proof_of_reading"}})


def session_summary(record: dict) -> dict:
    if record.get("summary_snapshot"):
        return copy.deepcopy(record["summary_snapshot"])
    if record["kind"] == "topic":
        summary = engine.summarize(GRAPH, record["engine"])
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
    return summary


def _freeze_result(record):
    """完成时连同文字结论一起冻结；升级后的图与规则不重算历史结果。"""
    summary = session_summary(record)
    record["summary_snapshot"] = summary
    record["verdict_snapshot"] = (engine.verdict_label(summary) if record["kind"] == "topic" else
                                  "逐个目标查看本次表现；过程题等待教师或同伴按量规复核。")


def _version_problem(record):
    if record.get("completed_at"):
        return None
    if record["kind"] == "topic" and record["content_sha256"] != graph_version():
        return "题库或诊断规则已经更新。旧记录已保留，请重新开始检查；本题尚未计分。"
    if (record["kind"] == "unit" and
            learning.unit_content_sha256(record["unit_snapshot"]) != record["content_sha256"]):
        return "本次任务引用的图形已更新。旧记录已保留，请重新开始；本题尚未计分。"
    if record["kind"] == "unit" and record.get("grader_sha256") != unit_grader_version():
        return "任务判分规则已经更新。旧记录已保留，请重新开始；本题尚未计分。"
    return None


def session_payload(record: dict) -> dict:
    item = session_item(record)
    done = item is None
    response = {"session": {"id": record["id"], "kind": record["kind"],
                            "mode": record["mode"], "answered_count": len(record["answers"]),
                            "targets": record.get("engine", {}).get("targets", []),
                            "unit_id": record.get("unit_id"), "created_at": record["created_at"],
                            "feedback_policy": record["feedback_policy"],
                            "content_sha256": record["content_sha256"],
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
    return response


def _same_scope(record, previous):
    if not previous or record["kind"] != previous["kind"]:
        return False
    if record["kind"] == "unit":
        return record["unit_id"] == previous["unit_id"]
    return set(record["engine"]["targets"]) == set(previous["engine"]["targets"])


def history_entry(record):
    return {"id": record["id"], "kind": record["kind"], "mode": record["mode"],
            "title": record.get("title", record.get("unit_id", "知识点检查")),
            "unit_id": record.get("unit_id"), "targets": record.get("engine", {}).get("targets", []),
            "created_at": record["created_at"], "completed_at": record.get("completed_at"),
            "answered_count": len(record["answers"]), "content_sha256": record["content_sha256"],
            "grader_sha256": record.get("grader_sha256"),
            "context": copy.deepcopy(record["context"]), "resume_blocked": bool(_version_problem(record))}


def export_record(record):
    # 不直接导出内部状态：单元快照中含有尚未作答的任务及答案。
    out = history_entry(record)
    out["answers"] = []
    raw_fields = ("question_id", "prompt", "raw_answer", "normalized_answer", "normalized_answer_format", "objective_ids",
                  "presented_at", "answered_at", "duration_ms", "client_elapsed_ms",
                  "content_sha256", "feedback_available_at", "skipped")
    for entry in record["answers"]:
        shown = {k: copy.deepcopy(entry[k]) for k in raw_fields if k in entry}
        if entry["question_id"] in record["feedback_exposed"]:
            shown.update({k: copy.deepcopy(entry[k]) for k in
                          ("status", "reason", "answer", "explanation", "feedback", "rubric") if k in entry})
        out["answers"].append(shown)
    if record.get("completed_at"):
        out["summary"] = session_summary(record)
        out["verdictText"] = record.get("verdict_snapshot", "这是原内容版本保存的结果，请查看各项目证据。")
    return out


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


def build_topics() -> dict:
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


class Handler(BaseHTTPRequestHandler):
    server_version = "cnkg/0.1"

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
        owner = token.value if token else ""
        if not _OWNER_PATTERN.fullmatch(owner):
            owner = secrets.token_hex(32)
            self._pending_owner_cookie = (f"{_OWNER_COOKIE}={owner}; Path=/; HttpOnly; "
                                          "SameSite=Strict; Max-Age=31536000")
        self._owner_id = owner
        return owner

    def _material(self, resource_id, content_sha256):
        get_store().add_event(self._owner(), {"event_type": "material_requested",
            "occurred_at": _now(), "resource_id": resource_id, "content_sha256": content_sha256,
            "payload": {"evidence": "http_request_only_not_completion"}})

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(urllib.parse.unquote(path[len("/static/"):]))
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
            self._material("unit:" + uid, learning.unit_content_sha256(unit))
            teaching = learning.public_unit(unit)
            # 教学页保留例题；各评价阶段的题面仅在会话中逐题呈现。
            teaching.pop("tasks", None)
            teaching["task_counts"] = unit_summary(unit)["task_counts"]
            return self._json(200, teaching)
        if path.startswith("/api/session/"):
            sid = path.rsplit("/", 1)[-1]
            with _SESSION_LOCK:
                record = get_store().load(sid, self._owner())
                return self._json(200, session_payload(record)) if record else self._error(404, "会话不存在或不属于本浏览器")
        if path == "/api/history":
            records = get_store().records(self._owner())
            return self._json(200, {"sessions": [history_entry(r) for r in records[:100]],
                                    "total": len(records), "limit": 100})
        if path == "/api/export":
            owner = self._owner()
            with _SESSION_LOCK:
                return self._json(200, {"schema": 1, "exported_at": _now(),
                    "evidence_notice": "匿名浏览器记录；请求材料和服务端放出反馈不证明实际阅读，时间间隔不证明独立作答。",
                    "sessions": [export_record(r) for r in get_store().records(owner)],
                    "events": get_store().events(owner)})
        if path == "/api/topics":
            return self._json(200, build_topics())
        if path.startswith("/api/teach/"):
            tid = urllib.parse.unquote(path[len("/api/teach/"):])
            card = build_teach(tid)
            if card is None:
                return self._error(404, f"{tid} 还没有讲解卡")
            self._material("topic:" + tid, _hash(card))
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
        if root not in target.parents or not target.is_file():
            return self._error(404, "not found")
        ext = name.rsplit(".", 1)[-1].lower()
        self._send(200, target.read_bytes(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(400, "请求长度不合法")
        if length < 0 or length > 1_000_000:
            return self._error(413, "请求过大")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._error(400, "请求不是合法 JSON")
        if not isinstance(payload, dict):
            return self._error(400, "请求必须是 JSON 对象")
        with _SESSION_LOCK:
            try:
                if path == "/api/session":
                    return self._create_session(payload)
                if path == "/api/answer":
                    return self._answer(payload)
            except learning_store.ConflictError as exc:
                return self._error(409, str(exc))
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
        prior_records = get_store().records(owner)
        if payload.get("unit_id"):
            unit = learning_units().get(payload["unit_id"])
            if unit is None or mode not in ("practice", "transfer", "delayed"):
                return self._error(400, "学习单元或练习阶段不合法")
            task_ids = [t["id"] for t in unit["tasks"] if t["phase"] == mode]
            if not task_ids:
                return self._error(409, "这个阶段还没有可用任务")
            record.update(kind="unit", unit_id=unit["id"], title=unit["title"],
                          unit_snapshot=copy.deepcopy(unit), task_ids=task_ids, index=0,
                          content_sha256=learning.unit_content_sha256(unit),
                          grader_sha256=unit_grader_version())
            matching = [r for r in prior_records if r["kind"] == "unit" and r["unit_id"] == unit["id"]]
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
                                                rotate=len(prior_records))
                _SESSION_COUNTER += 1
            except (TypeError, ValueError) as exc:
                return self._error(400, str(exc))
            matching = [r for r in prior_records if r["kind"] == "topic"]
            # 正式检查排除本浏览器已经呈现的题；练习允许重复但必须记下曝光。
            if mode != "practice":
                session["seen"] = sorted({qid for r in matching for qid in r["presentations"]})
            engine.next_item(GRAPH, session)
            record.update(kind="topic", engine=session, content_sha256=graph_version(),
                          topic_names={tid: GRAPH.name(tid) for tid in GRAPH.by_id},
                          title="、".join(GRAPH.name(tid) for tid in targets))
            resources = ["topic:" + tid for tid in session["targets"]]
        record["prior_seen_question_ids"] = sorted({qid for r in matching for qid in r["presentations"]})
        material = get_store().latest_material(owner, resources)
        if material:
            record["context"].update(material_requested=True,
                material_requested_at=material["occurred_at"], material_content_sha256=material["content_sha256"],
                material_exposure_evidence="http_request_only_not_completion")
        if previous:
            same_scope = _same_scope(record, previous)
            delay = _seconds(record["created_at"], previous.get("completed_at")) if same_scope else None
            record["context"].update(previous_session_id=previous["id"], same_learning_scope=same_scope,
                previous_created_at=previous["created_at"], previous_completed_at=previous.get("completed_at"),
                actual_delay_seconds=delay, delay_window_met=bool(delay is not None and delay >= 86400))
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
            result = learning.grade_task(question, raw)
            if question.get("type") == "choice" and result["status"] in ("correct", "wrong"):
                result["answer"] = display_answer(question)
        else:
            result = {**grade_answer(question, raw), "answer": display_answer(question),
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
                 "objective_ids": question.get("objective_ids", [])}
        record["answers"].append(entry)
        events = [_event("answer_submitted", now, record, question,
                          raw_answer=entry["raw_answer"], normalized_answer=normalized, skipped=skipped,
                          duration_ms=entry["duration_ms"], client_elapsed_ms=client_elapsed,
                          duration_evidence="server_wall_clock; client_elapsed_ms_is_reported")]
        if record["kind"] == "unit":
            record["index"] += 1
        else:
            verdict = "skip" if skipped else result["status"]
            engine.apply_answer(GRAPH, record["engine"], verdict, text=raw)
            engine.next_item(GRAPH, record["engine"])
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
    print(f"服务已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
