"""生成外部题预览页 web/static/ext.html 与数据文件 web/static/ext-data.json。

为什么要分两个文件：
    外部题已有 14344 道。全部题干+选项内联进 HTML 会得到 18MB 的单文件，
    浏览器打开要等很久，而且每次筛选都要重排上万张卡片。
    改成「HTML 壳 + JSON 数据 + 前端分页」后，HTML 只有几 KB，
    数据文件按需加载一次，筛选和翻页都在内存里做。

顺带它也是人工复核的入口：每道题标了来源、准入等级、涉及的全部知识点
（复合题会列出多个）、答案核验状态。

用法：
    python tools/build_ext_page.py
"""
from __future__ import annotations

import html
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "web" / "static" / "ext.html"
DATA_OUT = ROOT / "web" / "static" / "ext-data.json"

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>外部题预览 · K12 数学知识图谱</title>
<link rel="stylesheet" href="./style.css">
<link rel="stylesheet" href="./vendor/katex/katex.min.css">
<style>
  body {{ max-width: 940px; margin: 0 auto; padding: 24px 20px 60px; }}
  .hint {{ color: #6b7280; font-size: 13px; margin: 6px 0 18px; }}
  .card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; }}
  .meta {{ font-size: 12px; color: #6b7280; margin-bottom: 8px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .tag {{ background: #f3f4f6; border-radius: 999px; padding: 1px 8px; }}
  .tag.warn {{ background: #fef3c7; color: #92400e; }}
  .tag.ok {{ background: #dcfce7; color: #166534; }}
  .tag.multi {{ background: #ede9fe; color: #5b21b6; }}
  .opts {{ margin-top: 10px; display: grid; gap: 6px; }}
  .opt {{ display: flex; gap: 8px; font-size: 15px; }}
  .opt b {{ color: #374151; min-width: 16px; }}
  .ans {{ margin-top: 10px; font-size: 13px; color: #166534; }}
  .toolbar {{ position: sticky; top: 0; background: #fff; padding: 10px 0 12px;
              border-bottom: 1px solid #e5e7eb; margin-bottom: 16px; z-index: 2; }}
  .toolbar input, .toolbar select {{ padding: 6px 9px; border: 1px solid #d1d5db;
              border-radius: 6px; font-size: 13px; margin-right: 8px; }}
  .toolbar input {{ width: 240px; }}
  .pager {{ margin: 24px 0; display: flex; gap: 12px; align-items: center;
            justify-content: center; font-size: 14px; }}
  .pager button {{ padding: 6px 16px; border: 1px solid #d1d5db; border-radius: 6px;
            background: #fff; cursor: pointer; font-size: 13px; }}
  .pager button:disabled {{ opacity: .4; cursor: default; }}
  .count {{ font-size: 13px; color: #6b7280; }}
  .loading {{ padding: 40px; text-align: center; color: #6b7280; font-size: 14px; }}
</style>
</head>
<body>
<h1>外部题预览</h1>
<p class="hint">{count} 道，来自 {sources}。人工复核与公式渲染验证入口。
复合题会列出它同时涉及的全部知识点。</p>
<div class="toolbar">
  <input id="q" type="search" placeholder="搜索题干 / 答案 / 题号 / 知识点">
  <select id="src"><option value="">全部来源</option>{src_options}</select>
  <select id="adm"><option value="">全部准入等级</option>{adm_options}</select>
  <select id="stage"><option value="">全部知识点</option></select>
  <span class="count" id="cnt"></span>
</div>
<div id="list"><div class="loading">正在载入题目数据…</div></div>
<div class="pager">
  <button id="prev">上一页</button>
  <span id="page"></span>
  <button id="next">下一页</button>
</div>
<script src="./vendor/katex/katex.min.js"></script>
<script src="./vendor/katex/auto-render.min.js"></script>
<script src="./math.js"></script>
<script>
(function () {{
  var PER_PAGE = {per_page};
  var TOPICS = {topics};
  var ALL = [];
  var state = {{ q: "", src: "", adm: "", topic: "", page: 0 }};

  function esc(s) {{
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }}

  function statusTag(st) {{
    if (st === "consistent") return '<span class="tag ok">答案与解析一致</span>';
    if (st === "derived") return '<span class="tag warn">答案由原卷解析推导</span>';
    if (st === "no_signal") return '<span class="tag warn">答案未独立验算</span>';
    return '<span class="tag warn">' + esc(st) + "</span>";
  }}

  function topicNames(ids) {{
    return (ids || []).map(function (i) {{ return TOPICS[i] || i; }}).join(" / ");
  }}

  function card(q) {{
    var opts = (q.c || []).map(function (t, i) {{
      return '<div class="opt"><b>' + String.fromCharCode(65 + i) + '.</b>' +
             '<span data-rich="' + esc(t) + '"></span></div>';
    }}).join("");
    var m = q.m || {{}};
    var origin = ((m.year || "") + " " + (m.paper || "")).trim() || q.s || "";
    var multi = (q.t || []).length > 1
      ? '<span class="tag multi">复合题 · ' + q.t.length + " 个知识点</span>" : "";
    var names = topicNames(q.t);
    return '<div class="card">' +
      '<div class="meta">' +
        '<span class="tag">' + esc(names) + "</span>" + multi +
        '<span class="tag">' + esc(q.ad || "") + "</span>" +
        '<span class="tag">' + esc(q.i) + "</span>" +
        statusTag(q.st) +
        '<span class="tag">' + esc(origin) + "</span>" +
      "</div>" +
      '<div data-rich="' + esc(q.p) + '"></div>' +
      '<div class="opts">' + opts + "</div>" +
      '<div class="ans">答案：' + esc(q.a) + "　｜　来源：" + esc(q.s) +
        " #" + esc(q.sid) + "</div>" +
    "</div>";
  }}

  function apply() {{
    var hits = ALL.filter(function (q) {{
      if (state.src && q.s !== state.src) return false;
      if (state.adm && q.ad !== state.adm) return false;
      if (state.topic && (q.t || []).indexOf(state.topic) === -1) return false;
      if (state.q) {{
        var hay = (q._search || "");
        if (hay.indexOf(state.q) === -1) return false;
      }}
      return true;
    }});
    var pages = Math.max(1, Math.ceil(hits.length / PER_PAGE));
    if (state.page >= pages) state.page = pages - 1;
    var slice = hits.slice(state.page * PER_PAGE, (state.page + 1) * PER_PAGE);
    var list = document.getElementById("list");
    list.innerHTML = slice.length
      ? slice.map(card).join("")
      : '<div class="loading">没有匹配的题目</div>';
    // 公式渲染：只对当前页的卡片做，翻页时才渲染，不会卡
    list.querySelectorAll("[data-rich]").forEach(function (el) {{
      window.setRichText(el, el.dataset.rich);
    }});
    document.getElementById("page").textContent =
      (state.page + 1) + " / " + pages;
    document.getElementById("cnt").textContent = "命中 " + hits.length + " 道";
    document.getElementById("prev").disabled = state.page === 0;
    document.getElementById("next").disabled = state.page >= pages - 1;
  }}

  fetch("./ext-data.json").then(function (r) {{ return r.json(); }}).then(function (data) {{
    ALL = data.questions.map(function (q) {{
      q._search = (q.p + " " + q.a + " " + q.i + " " + q.s + " " +
                   topicNames(q.t)).toLowerCase();
      return q;
    }});
    // 知识点下拉框
    var sel = document.getElementById("stage");
    var seen = {{}};
    ALL.forEach(function (q) {{ (q.t || []).forEach(function (t) {{ seen[t] = 1; }}); }});
    Object.keys(seen).sort().forEach(function (t) {{
      var o = document.createElement("option");
      o.value = t; o.textContent = topicNames([t]);
      sel.appendChild(o);
    }});
    apply();
  }}).catch(function (e) {{
    document.getElementById("list").innerHTML =
      '<div class="loading">数据加载失败：' + esc(e) + "</div>";
  }});

  document.getElementById("q").addEventListener("input", function (e) {{
    state.q = e.target.value.trim().toLowerCase(); state.page = 0; apply();
  }});
  document.getElementById("src").addEventListener("change", function (e) {{
    state.src = e.target.value; state.page = 0; apply();
  }});
  document.getElementById("adm").addEventListener("change", function (e) {{
    state.adm = e.target.value; state.page = 0; apply();
  }});
  document.getElementById("stage").addEventListener("change", function (e) {{
    state.topic = e.target.value; state.page = 0; apply();
  }});
  document.getElementById("prev").addEventListener("click", function () {{
    state.page = Math.max(0, state.page - 1); apply();
  }});
  document.getElementById("next").addEventListener("click", function () {{
    state.page = state.page + 1; apply();
  }});
}})();
</script>
</body>
</html>
"""


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def main() -> None:
    topics_list = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
    topic_names = {t["id"]: t.get("name", t["id"]) for t in topics_list}
    ext = json.loads((DATA / "questions_ext.json").read_text(encoding="utf-8"))

    sources = sorted({q.get("source", "") for q in ext if q.get("source")})
    levels = sorted({q.get("admission_level", "") for q in ext if q.get("admission_level")})
    src_options = "".join(
        f'<option value="{esc(s)}">{esc(s)}</option>' for s in sources)
    adm_options = "".join(
        f'<option value="{esc(s)}">{esc(s)}</option>' for s in levels)

    slim = []
    for q in ext:
        meta = q.get("meta") or {}
        slim.append({
            "i": q["id"],
            "p": q.get("prompt", ""),
            "c": [c.get("text", "") for c in (q.get("choices") or [])],
            "a": str(q.get("answer", "")),
            "s": q.get("source", ""),
            "sid": q.get("source_id", ""),
            "ad": q.get("admission_level", ""),
            "t": q.get("topicIds") or [q.get("topicId", "")],
            "st": (q.get("answer_check") or {}).get("status", ""),
            "m": {k: v for k, v in meta.items() if k in ("year", "paper")},
        })

    DATA_OUT.write_text(
        json.dumps({"questions": slim}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    OUT.write_text(PAGE.format(
        count=len(ext),
        sources=esc("、".join(sources)),
        per_page=100,
        src_options=src_options,
        adm_options=adm_options,
        topics=json.dumps(topic_names, ensure_ascii=False),
    ), encoding="utf-8")
    size_kb = DATA_OUT.stat().st_size // 1024
    print(f"已生成 {OUT.relative_to(ROOT)}（{OUT.stat().st_size // 1024} KB）"
          f" 与 {DATA_OUT.relative_to(ROOT)}（{size_kb} KB）：{len(ext)} 道")


if __name__ == "__main__":
    main()
