const state = {
  session: null,
  item: null,
  nextItem: null,
  done: false,
  selected: null,
  pending: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}

function show(view) {
  for (const id of ["view-home", "view-quiz", "view-result"]) {
    $(id).hidden = id !== `view-${view}`;
  }
  $("restart").hidden = view === "home";
}

/* ------------------------------------------------------------------ 首页 */

async function initHome() {
  const data = await api("/api/topics");
  const s = data.stats;
  $("stats").textContent =
    `${s.edition} · 框架 ${s.topics} 点（已建 ${s.filled}）· `
    + `${s.edges} 条先修 · ${s.questions} 道题`;
  renderChapters(data.volumes, "");
  $("search").addEventListener("input", (event) => renderChapters(data.volumes, event.target.value.trim()));
  window.__volumes = data.volumes;
}

function renderChapters(volumes, keyword) {
  const root = $("chapters");
  root.textContent = "";
  const match = (t) => !keyword || t.name.includes(keyword) || t.section.includes(keyword);

  for (const vol of volumes) {
    const chapters = vol.chapters
      .map((c) => ({ ...c, topics: c.topics.filter(match) }))
      .filter((c) => c.topics.length);
    if (!chapters.length) continue;

    const heading = document.createElement("div");
    heading.className = "volume-title";
    const label = document.createElement("span");
    label.textContent = `${vol.stage} · ${vol.volume}`;
    const screener = document.createElement("button");
    screener.type = "button";
    screener.className = "mini";
    screener.textContent = "整册筛查";
    screener.addEventListener("click", () => startSession([], "screen", vol.volume));
    heading.append(label, screener);
    root.append(heading);

    // 小学部分册次是课时级（一上/一下/二上/三上/三下），一章多点，
    // 不能再按“一章一点”平铺，统一走折叠分组。
    for (const chapter of chapters) {
      const wrap = document.createElement("div");
      wrap.className = "chapter";

      const head = document.createElement("button");
      head.type = "button";
      head.setAttribute("aria-expanded", keyword ? "true" : "false");
      const title = document.createElement("span");
      title.textContent = chapter.title;
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = `${chapter.topics.length} 个点`;
      head.append(title, count);

      const rows = document.createElement("div");
      rows.className = "rows";
      rows.hidden = !keyword;
      for (const topic of chapter.topics) {
        rows.append(makeRow(topic.name, `${topic.section} · ${topic.questionCount} 题`, topic));
      }

      head.addEventListener("click", () => {
        const open = head.getAttribute("aria-expanded") === "true";
        head.setAttribute("aria-expanded", String(!open));
        rows.hidden = open;
      });

      wrap.append(head, rows);
      root.append(wrap);
    }
  }

  if (!root.children.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "没有匹配的知识点。";
    root.append(empty);
  }
}

function makeRow(name, sub, topic) {
  const row = document.createElement("div");
  row.className = "row";
  const left = document.createElement("div");
  const nameEl = document.createElement("div");
  nameEl.className = "name";
  nameEl.textContent = name;
  const secEl = document.createElement("div");
  secEl.className = "sec";
  secEl.textContent = sub;
  left.append(nameEl, secEl);

  const btn = document.createElement("button");
  btn.type = "button";
  if (!topic.questionCount) {
    btn.textContent = "待建";
    btn.disabled = true;
    btn.className = "pending";
    nameEl.classList.add("todo");
  } else {
    btn.textContent = "诊断";
    btn.addEventListener("click", () => startSession([topic.id], "diagnose"));
  }
  row.append(left, btn);
  return row;
}

/* ------------------------------------------------------------------ 答题 */

async function startSession(targets, mode, volume) {
  try {
    const data = await api("/api/session", mode === "screen" ? { mode, volume } : { targets });
    state.session = data.session;
    state.item = data.item;
    state.done = data.done;
    state.selected = null;
    if (mode === "screen") {
      $("quiz-hint").textContent = "整册筛查：每个知识点抽查 1 题，仅提供待确认线索，不作根因判断。";
    } else {
      $("quiz-hint").textContent = "";
    }
    show("quiz");
    renderItem();
  } catch (err) {
    alert(err.message);
  }
}

function renderItem() {
  const item = state.item;
  $("feedback").hidden = true;
  $("submit").disabled = false;
  $("submit").hidden = false;
  state.selected = null;

  $("quiz-topic").textContent = item.topicName;
  $("quiz-progress").textContent = `第 ${state.session.log.length + 1} 题`;
  $("quiz-meta").textContent = `难度：${item.question.difficulty} · ${item.attempt === 1 ? "首次作答" : "追问一次"}`;
  window.setRichText($("quiz-prompt"), item.question.prompt);

  const choices = $("choices");
  choices.textContent = "";
  const numeric = $("numeric");
  if (item.question.type === "choice") {
    numeric.hidden = true;
    item.question.choices.forEach((choice, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      const key = document.createElement("span");
      key.className = "key";
      // 字母是显示标签，按当前顺序生成；身份是 choice.id
      key.textContent = String.fromCharCode(65 + idx) + ".";
      const text = document.createElement("span");
      window.setRichText(text, choice.text);
      btn.append(key, text);
      btn.addEventListener("click", () => {
        state.selected = choice.id || choice.key;
        for (const other of choices.children) other.classList.remove("selected");
        btn.classList.add("selected");
      });
      choices.append(btn);
    });
  } else {
    numeric.hidden = false;
    choices.textContent = "";
    const input = $("numeric-input");
    const isShort = item.question.type === "short";
    input.value = "";
    input.placeholder = isShort ? "填答案（一个词或一个数）" : "填数字，如 -3 或 3/4";
    input.inputMode = isShort ? "text" : "decimal";
    setTimeout(() => input.focus(), 30);
  }
}

async function submit() {
  if (state.pending) return;
  const question = state.item.question;
  let answer = "";
  if (question.type === "choice") {
    answer = state.selected || "";
    if (!answer) {
      $("quiz-hint").textContent = "先选一个选项。";
      return;
    }
  } else {
    answer = $("numeric-input").value.trim();
    if (!answer) {
      $("quiz-hint").textContent = question.type === "short" ? "先填答案。" : "先填一个数。";
      return;
    }
  }

  state.pending = true;
  $("submit").disabled = true;
  try {
    const data = await api("/api/answer", { session: state.session, answer });
    state.session = data.session;
    state.nextItem = data.item;
    state.done = data.done;
    if (data.done) {
      state.summary = data.summary;
      state.verdictText = data.verdictText;
    }
    showFeedback(data.feedback);
  } catch (err) {
    $("quiz-hint").textContent = err.message;
    $("submit").disabled = false;
  } finally {
    state.pending = false;
  }
}

function showFeedback(feedback) {
  $("submit").hidden = true;
  $("feedback").hidden = false;
  const title = $("fb-title");
  title.textContent = feedback.correct ? "答对了" : "答错了";
  title.className = `fb-title ${feedback.correct ? "ok" : "no"}`;
  window.setRichText($("fb-answer"), `参考答案：${feedback.answer}`);
  window.setRichText($("fb-explain"), feedback.explanation);
  $("next").textContent = state.done ? "看结果" : "下一题";
  $("next").focus();
}

function next() {
  if (state.done) {
    showResult();
  } else {
    state.item = state.nextItem;
    renderItem();
  }
}

/* ------------------------------------------------------------------ 结果 */

function showResult() {
  const summary = state.summary;
  if (!summary) return;
  show("result");
  $("verdict").textContent = state.verdictText || "";
  $("res-weak").textContent = summary.weak.length
    ? summary.weak.map((id) => nameOf(id)).join("、") : "无";
  $("res-shaky").textContent = summary.shaky.length
    ? summary.shaky.map((id) => nameOf(id)).join("、") : "无";
  $("res-roots").textContent = summary.roots.length
    ? summary.roots.map((id) => nameOf(id)).join("、") : "无";
  const candidates = summary.root_candidates || [];
  $("res-candidates").textContent = (candidates.length
    ? `仍需确认：${candidates.map(nameOf).join("、")}。` : "")
    + (summary.incomplete ? "本次检查尚未覆盖全部必要范围，请结合未测项继续确认。" : "");

  const path = $("res-path");
  path.textContent = "";
  const weakSet = new Set(summary.weak);
  for (const id of summary.path) {
    const li = document.createElement("li");
    li.textContent = nameOf(id) + (weakSet.has(id) ? "（补学）"
      : summary.shaky.includes(id) ? "（巩固）" : "（需进一步确认）");
    path.append(li);
  }
  if (!summary.path.length) {
    const li = document.createElement("li");
    li.textContent = "本次作答尚未形成明确补学任务；这不代表所有相关内容均已掌握。";
    path.append(li);
  }

  const table = $("res-chapters");
  table.textContent = "";
  const head = document.createElement("tr");
  for (const label of ["章节", "掌握", "不稳", "薄弱", "未测"]) {
    const th = document.createElement("th");
    th.textContent = label;
    head.append(th);
  }
  table.append(head);
  for (const [chapter, bucket] of Object.entries(summary.by_chapter)) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = chapter;
    tr.append(name);
    for (const key of ["mastered", "shaky", "weak", "untested"]) {
      const td = document.createElement("td");
      td.className = "num";
      td.textContent = bucket[key];
      tr.append(td);
    }
    table.append(tr);
  }
}

let NAME_CACHE = {};
function nameOf(id) {
  if (NAME_CACHE[id]) return NAME_CACHE[id];
  for (const vol of window.__volumes || []) {
    for (const chapter of vol.chapters) {
      for (const topic of chapter.topics) {
        NAME_CACHE[topic.id] = topic.name;
      }
    }
  }
  return NAME_CACHE[id] || id;
}

/* ------------------------------------------------------------------ 事件 */

$("submit").addEventListener("click", submit);
$("next").addEventListener("click", next);
$("restart").addEventListener("click", () => {
  state.session = null;
  state.item = null;
  $("search").value = "";
  renderChapters(window.__volumes || [], "");
  show("home");
});
$("numeric-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") submit();
});
$("again").addEventListener("click", () => {
  $("restart").click();
});

async function boot() {
  try {
    await initHome();
    show("home");
    const params = new URLSearchParams(location.search);
    const target = params.get("target");
    const volume = params.get("volume");
    const query = params.get("q");
    if (query) {
      $("search").value = query;
      renderChapters(window.__volumes || [], query);
    }
    if (params.get("demo") === "wrong") {
      await runDemo();  // 自测用：全部答错，直接跑到结果页
    } else if (target) {
      await startSession([target], "diagnose");
    } else if (volume) {
      await startSession([], "screen", volume);
    }
  } catch (err) {
    $("stats").textContent = "加载失败：" + err.message;
  }
}

async function runDemo() {
  await startSession([new URLSearchParams(location.search).get("target") || "m7a.5.2"], "diagnose");
  let guard = 0;
  while (!state.done && guard < 30) {
    guard += 1;
    const question = state.item.question;
    const answer = question.type === "choice" ? "Z" : "-999999";
    const data = await api("/api/answer", { session: state.session, answer });
    state.session = data.session;
    state.item = data.item;
    state.done = data.done;
    if (data.done) {
      state.summary = data.summary;
      state.verdictText = data.verdictText;
    }
  }
  showResult();
}

boot();
