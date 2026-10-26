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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = pathlib.Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from cnkg import engine, learning  # noqa: E402
from cnkg.graph import VOLUME_ORDER, load_graph  # noqa: E402
from cnkg.verify import compare, compare_short, to_number  # noqa: E402

# 外部题（真题源）默认不加载。
# 可选题库的实际准入量由当前证据策略决定；题量不等于独立学科审核。
# 教学场景要开，显式设 CNKG_INCLUDE_EXTERNAL=1。
# 开关用环境变量而不是命令行参数，方便后台启动时控制。
_INCLUDE_EXTERNAL = os.environ.get("CNKG_INCLUDE_EXTERNAL", "0") == "1"
GRAPH = load_graph(include_external=_INCLUDE_EXTERNAL)
# 出题轮转计数器：每建一个会话 +1，让同一知识点的后续诊断抽到不同的题。
# 不用随机数——同一组参数要可复现，诊断记录要可审计。
_SESSION_COUNTER = 0
_SESSIONS: dict[str, dict] = {}
_SESSION_LOCK = threading.RLock()
_UNIT_CACHE = None
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
    """服务端判题：选择题比 option_id，数值题按容差比数值，短答题归一化后比对。"""
    qtype = question["type"]
    if qtype == "choice":
        return ("correct" if option_id_for(question, text) == str(question["answer"]).strip()
                else "wrong")
    if qtype == "short":
        return "correct" if compare_short(text, question["answer"], question.get("accept")) else "wrong"
    # 不把格式错误当作概念错误送进诊断状态机；客户端应提示重新输入。
    if to_number(text) is None:
        return "invalid"
    return "correct" if compare(text, question["answer"], question.get("tolerance", 1e-6)) else "wrong"


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


def session_question(record: dict) -> dict | None:
    if record["kind"] == "unit":
        if record["index"] >= len(record["task_ids"]):
            return None
        unit = learning_units()[record["unit_id"]]
        return next(t for t in unit["tasks"] if t["id"] == record["task_ids"][record["index"]])
    current = record["engine"].get("current")
    return GRAPH.questions_by_id.get(current["questionId"]) if current else None


def session_item(record: dict) -> dict | None:
    question = session_question(record)
    if question is None:
        return None
    if record["kind"] == "unit":
        unit = learning_units()[record["unit_id"]]
        public = next(t for t in learning.public_unit(unit)["tasks"] if t["id"] == question["id"])
        return {"topicId": (unit.get("topic_ids") or [None])[0], "topicName": unit["title"],
                "attempt": record["index"] + 1, "question": public}
    current = record["engine"]["current"]
    return {"topicId": current["topicId"], "topicName": GRAPH.name(current["topicId"]),
            "attempt": current["attempt"], "question": public_question(question)}


def session_summary(record: dict) -> dict:
    if record["kind"] == "topic":
        summary = engine.summarize(GRAPH, record["engine"])
        summary["title"] = "本次练习" if record["mode"] == "practice" else "本次检查"
    else:
        unit = learning_units()[record["unit_id"]]
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
    summary["context"] = record.get("context", {})
    summary["created_at"] = record["created_at"]
    return summary


def session_payload(record: dict) -> dict:
    item = session_item(record)
    done = item is None
    response = {"session": {"id": record["id"], "kind": record["kind"],
                            "mode": record["mode"], "answered_count": len(record["answers"]),
                            "targets": record.get("engine", {}).get("targets", []),
                            "unit_id": record.get("unit_id"),
                            "feedback_policy": record["feedback_policy"]},
                "item": item, "done": done}
    if done:
        response["summary"] = session_summary(record)
        response["verdictText"] = (engine.verdict_label(response["summary"])
                                  if record["kind"] == "topic" else
                                  "逐个目标查看本次表现；过程题等待教师或同伴按量规复核。")
    return response


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
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str):
        self._json(code, {"error": message})

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(urllib.parse.unquote(path[len("/static/"):]))
        if path == "/api/learning/units":
            return self._json(200, {"units": [unit_summary(u) for u in learning_units().values()]})
        if path.startswith("/api/learning/units/"):
            uid = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            unit = learning_units().get(uid)
            return self._json(200, learning.public_unit(unit)) if unit else self._error(404, "学习单元不存在")
        if path.startswith("/api/session/"):
            sid = path.rsplit("/", 1)[-1]
            with _SESSION_LOCK:
                record = _SESSIONS.get(sid)
                return self._json(200, session_payload(record)) if record else self._error(404, "会话不存在或已过期")
        if path == "/api/topics":
            return self._json(200, build_topics())
        if path.startswith("/api/teach/"):
            tid = urllib.parse.unquote(path[len("/api/teach/"):])
            card = build_teach(tid)
            if card is None:
                return self._error(404, f"{tid} 还没有讲解卡")
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
            if path == "/api/session":
                return self._create_session(payload)
            if path == "/api/answer":
                return self._answer(payload)
        return self._error(404, "not found")

    def _create_session(self, payload):
        global _SESSION_COUNTER
        mode = payload.get("mode", "diagnose")
        if not isinstance(mode, str):
            return self._error(400, "模式必须是字符串")
        if "unit_id" in payload and not isinstance(payload["unit_id"], str):
            return self._error(400, "学习单元ID必须是字符串")
        record = {"id": uuid.uuid4().hex, "mode": mode, "created_at": _now(), "answers": [],
                  "feedback_exposed": [], "context": {"instruction_seen": bool(payload.get("instruction_seen"))}}
        previous = _SESSIONS.get(str(payload.get("previous_session_id", "")))
        if previous:
            record["context"].update(previous_session_id=previous["id"],
                                      previous_created_at=previous["created_at"], post_instruction=True)
        if payload.get("unit_id"):
            unit = learning_units().get(payload["unit_id"])
            if unit is None or mode not in ("practice", "transfer", "delayed"):
                return self._error(400, "学习单元或练习阶段不合法")
            task_ids = [t["id"] for t in unit["tasks"] if t["phase"] == mode]
            if not task_ids:
                return self._error(409, "这个阶段还没有可用任务")
            record.update(kind="unit", unit_id=unit["id"], task_ids=task_ids, index=0,
                          content_sha256=learning.unit_content_sha256(unit))
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
                session = engine.create_session(GRAPH, targets, mode=mode, limit=limit, rotate=_SESSION_COUNTER)
                _SESSION_COUNTER += 1
            except (TypeError, ValueError) as exc:
                return self._error(400, str(exc))
            if previous and previous["kind"] == "topic":
                # 复测不复用刚看过答案的题，题池不足时由引擎报告证据缺口。
                session["seen"] = list(previous["engine"]["seen"])
            engine.next_item(GRAPH, session)
            record.update(kind="topic", engine=session)
        record["feedback_policy"] = "immediate" if mode == "practice" else "deferred"
        _SESSIONS[record["id"]] = record
        return self._json(200, session_payload(record))

    def _answer(self, payload):
        sid = payload.get("session_id")
        record = _SESSIONS.get(sid) if isinstance(sid, str) else None
        if record is None:
            return self._error(404, "会话不存在，请重新开始")
        question = session_question(record)
        if question is None:
            return self._error(409, "当前会话已经完成")
        if payload.get("question_id") != question["id"]:
            return self._error(409, "题号与当前题不一致，请恢复当前会话；答案尚未计入")
        skipped = payload.get("skip") is True
        raw = payload.get("answer")
        if not skipped and (not isinstance(raw, str) or not raw.strip() or len(raw) > 8000):
            return self._error(400, "请填写答案（不超过8000字），或选择暂时不会")
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
            result = {"status": judge(question, raw), "answer": display_answer(question),
                      "explanation": question.get("explanation", "")}
        if result["status"] == "invalid":
            return self._error(422, "答案格式无法识别。数值可写小数或分数（如1/2），单位按题干约定；格式问题不计作答错。")
        entry = {**result, "question_id": question["id"], "prompt": question["prompt"],
                 "raw_answer": raw if not skipped else "", "answered_at": _now(),
                 "objective_ids": question.get("objective_ids", [])}
        record["answers"].append(entry)
        if record["kind"] == "unit":
            record["index"] += 1
        else:
            verdict = "skip" if skipped else result["status"]
            engine.apply_answer(GRAPH, record["engine"], verdict, text=raw)
            engine.next_item(GRAPH, record["engine"])
        response = session_payload(record)
        if record["feedback_policy"] == "immediate":
            response["feedback"] = result
            record["feedback_exposed"].append(question["id"])
        else:
            response["feedback"] = {"deferred": True}
        if response["done"]:
            record["feedback_exposed"] = [a["question_id"] for a in record["answers"]]
            record["completed_at"] = _now()
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
