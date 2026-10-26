"""极简本地 Web 服务：把诊断引擎接到浏览器上。

只依赖标准库。默认只绑定 127.0.0.1，不对外网开放。

    python web/server.py            # http://127.0.0.1:8765
    python web/server.py --port 9000

接口（无状态，session 由前端保存并回传）：
    GET  /api/topics                知识点树 + 统计
    POST /api/session               {targets:[...]} 或 {volume:"七上", mode:"screen"}
    POST /api/answer                {session, verdict}
"""
from __future__ import annotations

import argparse
import os
import json
import pathlib
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = pathlib.Path(__file__).resolve().parent / "static"
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from cnkg import engine  # noqa: E402
from cnkg.graph import VOLUME_ORDER, load_graph  # noqa: E402
from cnkg.verify import compare, compare_short, to_number  # noqa: E402

# 外部题（真题源）默认不加载。
# 打开后题池 2235 → 11240（自有 + 9005 道可诊断外部题），477 个点里 268 个
# 能有真题撑着（按主知识点），题量下限不足的点从 54/117/139 降到 30/50/45
# （小学/初中/高中）。数字很诱人，但外部题只做了「两个来源互证」，
# 没有第二人复核，所以默认仍关闭；教学场景要开，显式设 CNKG_INCLUDE_EXTERNAL=1。
# 开关用环境变量而不是命令行参数，方便后台启动时控制。
_INCLUDE_EXTERNAL = os.environ.get("CNKG_INCLUDE_EXTERNAL", "0") == "1"
GRAPH = load_graph(include_external=_INCLUDE_EXTERNAL)
# 出题轮转计数器：每建一个会话 +1，让同一知识点的后续诊断抽到不同的题。
# 不用随机数——同一组参数要可复现，诊断记录要可审计。
_SESSION_COUNTER = 0
ALLOWED_STATIC = {"index.html", "app.js", "style.css", "review.html", "math.js",
                  "ext.html", "ext-data.json"}
# 第三方静态资源只允许这个子目录，避免把整个磁盘暴露出去
ALLOWED_PREFIX = "vendor/"
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


def build_teach(topic_id: str) -> dict | None:
    """讲解卡 + 该点的练习题（题面脱敏）+ 拓展挑战。

    讲解卡是 `data/teach/<topicId>.json`。工具点没有独立卡，退回
    `data/teach/clusters/<brief_group>.json` 的簇速查卡——一层一张，
    覆盖同册同类的十来个基础点，不再逐点写四步讲解。
    练习题只给题面，答案与解析留在服务端：讲完之后学生要真的算，
    不是照着解析抄。
    挑战题**给解析**：它是「见识竞赛怎么想」的阅读材料，不是测验。
    """
    topic = GRAPH.by_id.get(topic_id)
    tier = GRAPH.tiers.get(topic_id) or {}
    card, mode = None, "full"
    path = DATA / "teach" / f"{topic_id}.json"
    if path.is_file():
        card = json.loads(path.read_text(encoding="utf-8"))
    elif tier.get("tier") == "tool" and tier.get("brief_group"):
        brief = DATA / "teach" / "clusters" / f"{tier['brief_group']}.json"
        if brief.is_file():
            card = json.loads(brief.read_text(encoding="utf-8"))
            mode = "brief"
    if card is None:
        # 还没有卡的知识点也要能打开：学生至少能看到这个点是什么、
        # 有哪几道题、竞赛怎么考。返回 None 会让 309 个核心点在页面上是空白。
        if topic is None:
            return None
        out = {
            "topicId": topic_id,
            "mode": "none",
            "tier": tier.get("tier"),
            "cluster": tier.get("cluster"),
            "topicName": topic.get("name"),
            "description": topic.get("description"),
            "mastery_criteria": topic.get("mastery_criteria") or [],
            "practice": [],
            "practice_questions": [
                public_question(q) for q in GRAPH.bank.get(topic_id, [])[:4]
            ],
        }
        pool = _challenge_pool()
        ids = (pool["links"].get(topic_id) or [])[:3]
        out["challenge"] = [
            {k: pool["items"][i].get(k) for k in
             ("id", "level", "prompt", "choices", "answer", "solution",
              "steps", "source_competition")}
            for i in ids if i in pool["items"]
        ]
        return out
    out = dict(card)
    out["mode"] = mode
    out["tier"] = tier.get("tier")
    out["cluster"] = tier.get("cluster")
    out["topicName"] = topic.get("name") if topic else card.get("name")
    out["description"] = topic.get("description") if topic else None
    out["mastery_criteria"] = topic.get("mastery_criteria") if topic else []
    practice = []
    for ref in card.get("practice") or []:
        q = next((x for x in GRAPH.questions if x["id"] == ref.get("id")), None)
        if q:
            practice.append({**public_question(q), "note": ref.get("note")})
    out["practice_questions"] = practice
    # 例题的答案与步骤要给学生看，这是"教会"的部分；
    # 练习题的答案不能给，这是"检查"的部分。两者明确分开。

    pool = _challenge_pool()
    ids = (pool["links"].get(topic_id) or [])[:3]
    out["challenge"] = [
        {k: pool["items"][i].get(k) for k in
         ("id", "level", "prompt", "choices", "answer", "solution",
          "steps", "source_competition")}
        for i in ids if i in pool["items"]
    ]
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
        allowed = name in ALLOWED_STATIC or name.startswith(ALLOWED_PREFIX)
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
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            return self._error(413, "请求过大")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._error(400, "请求不是合法 JSON")

        if path == "/api/session":
            return self._create_session(payload)
        if path == "/api/answer":
            return self._answer(payload)
        return self._error(404, "not found")

    def _create_session(self, payload):
        global _SESSION_COUNTER
        self._session_counter = _SESSION_COUNTER
        mode = payload.get("mode", "diagnose")
        limit = payload.get("limit")
        if mode == "screen":
            volume = payload.get("volume")
            targets = [t["id"] for t in GRAPH.topics
                       if t["volume"] == volume and GRAPH.bank[t["id"]]]
            if not targets:
                return self._error(400, f"{volume} 还没有可用的题目")
        else:
            targets = payload.get("targets") or []
        # 出题轮转偏移：同一知识点反复诊断时换一道题问。
        # 由服务端维护的会话计数给出（不用随机数，保证可复现可审计）。
        rotate = payload.get("rotate")
        if rotate is None:
            rotate = _SESSION_COUNTER
            _SESSION_COUNTER += 1
        try:
            session = engine.create_session(GRAPH, targets, mode=mode,
                                           limit=limit, rotate=int(rotate))
        except ValueError as exc:
            return self._error(400, str(exc))
        if all(not GRAPH.bank.get(tid) for tid in session["targets"]):
            return self._error(409, "这个知识点还是骨架，尚未录入题目")
        item = engine.next_item(GRAPH, session)
        return self._json(200, {"session": session, "item": item_payload(item), "done": item is None})

    def _answer(self, payload):
        session = payload.get("session")
        if not isinstance(session, dict):
            return self._error(400, "缺少 session")
        if not session.get("current"):
            return self._error(409, "当前没有待答的题")

        current = session["current"]
        question = next((q for q in GRAPH.questions if q["id"] == current["questionId"]), None)
        if question is None:
            return self._error(409, "题目不存在")

        # 判题在服务端做：前端只提交作答文本，避免"自我宣布答对"
        if "answer" in payload:
            verdict = judge(question, payload.get("answer"))
        else:
            return self._error(400, "缺少 answer")
        if verdict == "invalid":
            return self._error(422, "请输入数值，例如 -3、0.5 或 1/2；单位按题干约定填写，不必重复输入。")

        try:
            engine.apply_answer(GRAPH, session, verdict, text=str(payload["answer"]))
        except ValueError as exc:
            return self._error(409, str(exc))

        item = engine.next_item(GRAPH, session)
        done = item is None
        response = {
            "session": session,
            "item": item_payload(item),
            "done": done,
            "feedback": {
                "correct": verdict == "correct",
                "answer": display_answer(question),
                "explanation": question.get("explanation", ""),
            },
        }
        if done:
            response["summary"] = engine.summarize(GRAPH, session)
            response["verdictText"] = engine.verdict_label(response["summary"])
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
