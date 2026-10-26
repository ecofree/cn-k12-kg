"""生成复核工作台：把抽样题目嵌进一个单页，人点完导出 CSV，和 inbox/review.csv 同格式。

    python tools/build_review_page.py
    # 打开 http://127.0.0.1:8765/static/review.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from export_inbox import pick_review_sample  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "web" / "static" / "review.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>题目复核 · K12 知识图谱</title>
<style>
  :root {
    --bg: #f7f8f7; --surface: #fff; --line: #e3e6e4; --text: #1c1f1d;
    --muted: #6b726c; --accent: #2f6f5e; --accent-weak: #e8f2ee;
    --warn: #b45309; --bad: #b42318; --bad-weak: #fdecea;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.6 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  }
  header {
    position: sticky; top: 0; z-index: 5; background: var(--bg);
    border-bottom: 1px solid var(--line); padding: 16px 24px 12px;
  }
  h1 { font-size: 17px; font-weight: 600; margin: 0 0 4px; }
  .muted { color: var(--muted); font-size: 13px; margin: 0; }
  .bar { height: 6px; background: var(--line); border-radius: 99px; margin: 10px 0 6px; overflow: hidden; }
  .bar > div { height: 100%; width: 0; background: var(--accent); transition: width .2s; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
  .stats { font-size: 13px; color: var(--muted); }
  button, select {
    font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 8px;
    border: 1px solid var(--line); background: var(--surface); color: var(--text); cursor: pointer;
  }
  button:hover { background: var(--accent-weak); border-color: #cfe6de; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  main { max-width: 880px; margin: 0 auto; padding: 18px 24px 80px; }
  .card {
    background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px 18px; margin-bottom: 12px;
  }
  .card.done { border-color: #cfe6de; }
  .card.fail { border-color: #f3c2bd; background: #fffdfc; }
  .card.focus { box-shadow: 0 0 0 2px #cfe6de; }
  .meta { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .prompt { font-size: 15px; margin: 0 0 10px; white-space: pre-wrap; }
  .opts { margin: 0 0 10px; padding-left: 0; list-style: none; font-size: 14px; }
  .opts li { padding: 2px 0; }
  .opts .k { color: var(--muted); margin-right: 6px; }
  .ans { font-size: 13px; color: var(--accent); margin-bottom: 6px; }
  details { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
  details summary { cursor: pointer; }
  .actions { display: flex; gap: 8px; align-items: center; }
  .actions button[data-v="fail"].on { background: var(--bad-weak); border-color: var(--bad); color: var(--bad); }
  .actions button[data-v="pass"].on { background: var(--accent-weak); border-color: var(--accent); color: var(--accent); }
  .actions button[data-v="skip"].on { background: #f3f4f3; }
  textarea {
    width: 100%; margin-top: 8px; font: inherit; font-size: 13px; padding: 8px 10px;
    border: 1px solid var(--line); border-radius: 8px; resize: vertical; min-height: 44px;
  }
  .empty { text-align: center; color: var(--muted); padding: 40px 0; }
  kbd {
    background: #eef0ef; border: 1px solid var(--line); border-bottom-width: 2px;
    border-radius: 4px; padding: 0 4px; font-size: 11px;
  }
</style>
</head>
<body>
<header>
  <h1>题目复核</h1>
  <p class="muted">共 __TOTAL__ 题。标记只存在本机浏览器；导出后放回 <code>inbox/review.csv</code>，再跑 <code>tools/ingest.py --check</code>。</p>
  <div class="bar"><div id="fill"></div></div>
  <div class="row">
    <span class="stats" id="stats"></span>
    <select id="filter">
      <option value="all">全部</option>
      <option value="todo">只看未复核</option>
      <option value="fail">只看有问题</option>
    </select>
    <button class="primary" id="export">导出 review.csv</button>
    <button id="reset">清空记录</button>
  </div>
  <p class="muted" style="margin-top:6px">快捷键：<kbd>1</kbd> 通过 · <kbd>2</kbd> 有问题 · <kbd>3</kbd> 跳过 · <kbd>↑</kbd><kbd>↓</kbd> 切换题目</p>
</header>
<main id="list"></main>
<script>
const DATA = __DATA__;
const KEY = "cnkg-review-v1";
let store = {};
try { store = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { store = {}; }
let focusIdx = 0;

const list = document.getElementById("list");
const statsEl = document.getElementById("stats");
const fillEl = document.getElementById("fill");
const filterEl = document.getElementById("filter");

function save() { localStorage.setItem(KEY, JSON.stringify(store)); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function render() {
  const mode = filterEl.value;
  list.textContent = "";
  let visible = 0;
  DATA.forEach((q, i) => {
    const rec = store[q.id] || {};
    if (mode === "todo" && rec.verdict) return;
    if (mode === "fail" && rec.verdict !== "fail") return;
    visible += 1;

    const card = document.createElement("article");
    card.className = "card" + (rec.verdict ? " done" : "") + (rec.verdict === "fail" ? " fail" : "") + (i === focusIdx ? " focus" : "");
    card.dataset.idx = i;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${q.volume} · ${q.topicName} · ${q.typeLabel} · ${q.difficulty} · ${q.id}`;
    card.append(meta);

    const prompt = document.createElement("p");
    prompt.className = "prompt";
    prompt.textContent = q.prompt;
    card.append(prompt);

    if (q.choices && q.choices.length) {
      const ol = document.createElement("ul");
      ol.className = "opts";
      q.choices.forEach((c, idx) => {
        const li = document.createElement("li");
        const k = document.createElement("span");
        k.className = "k";
        k.textContent = String.fromCharCode(65 + idx) + ".";
        const t = document.createElement("span");
        t.textContent = c.text;
        li.append(k, t);
        ol.append(li);
      });
      card.append(ol);
    }

    const ans = document.createElement("div");
    ans.className = "ans";
    ans.textContent = "答案：" + q.answerDisplay;
    card.append(ans);

    if (q.explanation) {
      const d = document.createElement("details");
      const s = document.createElement("summary");
      s.textContent = "解析";
      const p = document.createElement("p");
      p.textContent = q.explanation;
      d.append(s, p);
      card.append(d);
    }

    const actions = document.createElement("div");
    actions.className = "actions";
    [["pass", "通过 1"], ["fail", "有问题 2"], ["skip", "跳过 3"]].forEach(([v, label]) => {
      const b = document.createElement("button");
      b.dataset.v = v;
      b.textContent = label;
      if (rec.verdict === v) b.classList.add("on");
      b.addEventListener("click", (e) => { e.stopPropagation(); mark(q.id, v); });
      actions.append(b);
    });
    card.append(actions);

    if (rec.verdict === "fail") {
      const note = document.createElement("textarea");
      note.placeholder = "哪里有问题？写清应改成什么";
      note.value = rec.note || "";
      note.addEventListener("input", () => {
        store[q.id] = { ...(store[q.id] || {}), note: note.value };
        save();
      });
      actions.after(note);
    }

    card.addEventListener("click", () => setFocus(i));
    list.append(card);
  });

  if (!visible) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "这个筛选下没有题目。";
    list.append(p);
  }
  updateStats();
}

function mark(id, verdict) {
  const rec = store[id] || {};
  if (rec.verdict === verdict) {
    delete rec.verdict;
  } else {
    rec.verdict = verdict;
    if (verdict === "fail" && !rec.note) rec.note = "";
  }
  store[id] = rec;
  save();
  render();
  if (verdict === "fail") {
    const el = document.querySelector(`.card[data-idx="${focusIdx}"] textarea`);
    if (el) el.focus();
  }
}

function setFocus(i) {
  focusIdx = i;
  document.querySelectorAll(".card").forEach((c) => {
    c.classList.toggle("focus", Number(c.dataset.idx) === i);
  });
  const el = document.querySelector(`.card[data-idx="${i}"]`);
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

function updateStats() {
  let pass = 0, fail = 0, skip = 0;
  for (const q of DATA) {
    const v = (store[q.id] || {}).verdict;
    if (v === "pass") pass++;
    else if (v === "fail") fail++;
    else if (v === "skip") skip++;
  }
  const done = pass + fail + skip;
  statsEl.textContent = `通过 ${pass} · 有问题 ${fail} · 跳过 ${skip} · 未复核 ${DATA.length - done} / 共 ${DATA.length}`;
  fillEl.style.width = (done / DATA.length * 100).toFixed(1) + "%";
}

document.getElementById("export").addEventListener("click", () => {
  const head = ["question_id", "topic_id", "type", "difficulty", "prompt", "answer", "explanation", "verdict", "note"];
  const rows = DATA.map((q) => {
    const rec = store[q.id] || {};
    return [q.id, q.topicId, q.type, q.difficulty, q.prompt, q.answerDisplay,
            q.explanation, rec.verdict || "", rec.note || ""];
  });
  const csv = [head, ...rows].map((r) => r.map((v) => {
    const s = String(v == null ? "" : v);
    return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(",")).join("\\r\\n");
  const blob = new Blob(["\\ufeff" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "review.csv";
  a.click();
  URL.revokeObjectURL(a.href);
});

document.getElementById("reset").addEventListener("click", () => {
  if (!confirm("清空本机所有复核记录？导出的 CSV 不受影响。")) return;
  store = {};
  save();
  render();
});

filterEl.addEventListener("change", render);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
  if (e.key === "1" || e.key === "2" || e.key === "3") {
    const q = DATA[focusIdx];
    if (!q) return;
    mark(q.id, { "1": "pass", "2": "fail", "3": "skip" }[e.key]);
    e.preventDefault();
  } else if (e.key === "ArrowDown") {
    setFocus(Math.min(focusIdx + 1, DATA.length - 1));
    e.preventDefault();
  } else if (e.key === "ArrowUp") {
    setFocus(Math.max(focusIdx - 1, 0));
    e.preventDefault();
  }
});

render();
</script>
</body>
</html>
"""

TYPE_LABEL = {"choice": "选择题", "numeric": "填空题", "short": "短答"}


def build(cap: int, out: pathlib.Path = OUT) -> int:
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    by_topic = {t["id"]: t for t in topics}
    sample = pick_review_sample(questions, topics, cap)

    rows = []
    for q in sample:
        topic = by_topic[q["topicId"]]
        if q["type"] == "choice":
            texts = {c["id"]: c["text"] for c in q.get("choices", [])}
            answer_display = f"{q['answer']}. {texts.get(q['answer'], '')}"
        else:
            answer_display = str(q.get("answer", ""))
        rows.append({
            "id": q["id"], "topicId": q["topicId"], "topicName": topic["name"],
            "volume": topic["volume"], "type": q["type"],
            "typeLabel": TYPE_LABEL.get(q["type"], q["type"]),
            "difficulty": q.get("difficulty", ""), "prompt": q["prompt"],
            "choices": q.get("choices") or [], "answerDisplay": answer_display,
            "explanation": q.get("explanation", ""),
        })

    html = (TEMPLATE
            .replace("__TOTAL__", str(len(rows)))
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False)))
    out.write_text(html, encoding="utf-8")
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"已生成 {shown}：{len(rows)} 题，{out.stat().st_size / 1024:.0f} KB")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=160)
    args = ap.parse_args()
    build(args.cap)
    print("打开 http://127.0.0.1:8765/static/review.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
