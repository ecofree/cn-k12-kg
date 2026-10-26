const state = { session: null, item: null, nextItem: null, done: false, selected: null,
  pending: false, volumes: [], units: [], lastResultSession: null, viewedTopics: new Set(), viewedUnits: new Set() };
const $ = (id) => document.getElementById(id);
const PHASE = { diagnose: "知识点检查", screen: "整册抽查", practice: "练习", transfer: "独立迁移", delayed: "复测题" };
const STATUS = { correct: "答对了", wrong: "还需练习", invalid: "请检查格式", needs_review: "过程已提交，等待复核",
  not_attempted: "暂未作答", demonstrated: "本组题已展示该能力", practice_needed: "建议再练习", not_observed: "本组未观察到该目标" };
async function api(path, body) {
  const res = await fetch(path, { method: body ? "POST" : "GET", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}
function show(view) {
  for (const name of ["home", "teach", "unit", "quiz", "result"]) $(`view-${name}`).hidden = name !== view;
  $("restart").hidden = view === "home";
  window.scrollTo({ top: 0, behavior: "instant" });
}
function node(tag, text, cls) {
  const el = document.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el;
}
function rich(tag, text, cls) { const el = node(tag, null, cls); window.setRichText(el, text || ""); return el; }
function button(text, action, cls = "ghost") {
  const el = node("button", text, cls); el.type = "button"; el.addEventListener("click", action); return el;
}
function list(items, ordered = false) {
  const el = node(ordered ? "ol" : "ul"); for (const text of items || []) el.append(rich("li", text)); return el;
}
function section(title, content) { const el = node("div", null, "card"); el.append(node("h3", title), content); return el; }
function diagram(value) {
  const el = node("figure", null, "lesson-diagram"); if (!value) return el;
  if (!/^\/static\/diagrams\/[a-zA-Z0-9_-]+\.svg$/.test(value.src)) return el;
  const img = node("img"); img.src = value.src; img.alt = value.alt || "数学示意图";
  el.append(img, node("figcaption", value.alt || "", "muted")); return el;
}
function reviewLabel(value) { return value && value.status === "approved" ? "已有独立审核记录，请查看具体版本" : "内容初稿 · 教师独立审核待完成"; }
function unitCard(unit) {
  const card = node("article", null, "card unit-card");
  card.append(node("p", { primary: "小学", junior: "初中", senior: "高中" }[unit.stage], "eyebrow"), node("h3", unit.title),
    node("p", `${unit.objectives.length} 个学习目标 · ${unit.task_counts.practice || 0} 道练习 · 含独立任务`, "muted"),
    button("开始学习", () => openUnit(unit.id), "primary")); return card;
}
async function initHome() {
  const [data, lessons] = await Promise.all([api("/api/topics"), api("/api/learning/units")]);
  state.volumes = data.volumes; window.__volumes = data.volumes; state.units = lessons.units;
  $("stats").textContent = `${data.stats.edition} · ${data.stats.topics} 个教材节点 · ${lessons.units.length} 个细目标单元`;
  $("learning-units").replaceChildren(...lessons.units.map(unitCard)); renderChapters("");
}
function renderChapters(keyword) {
  const root = $("chapters"); root.textContent = "";
  const match = (t) => !keyword || t.name.includes(keyword) || t.section.includes(keyword);
  for (const vol of state.volumes) {
    const chapters = vol.chapters.map(c => ({ ...c, topics: c.topics.filter(match) })).filter(c => c.topics.length);
    if (!chapters.length) continue;
    const heading = node("div", null, "volume-title"); heading.append(node("span", `${vol.stage} · ${vol.volume}`), button("整册抽查", () => startSession({ mode: "screen", volume: vol.volume }), "mini")); root.append(heading);
    for (const chapter of chapters) {
      const wrap = node("div", null, "chapter"), head = button("", () => { const open = head.getAttribute("aria-expanded") === "true"; head.setAttribute("aria-expanded", String(!open)); rows.hidden = open; });
      head.setAttribute("aria-expanded", keyword ? "true" : "false"); head.append(node("span", chapter.title), node("span", `${chapter.topics.length} 个点`, "count"));
      const rows = node("div", null, "rows"); rows.hidden = !keyword;
      for (const topic of chapter.topics) {
        const row = node("div", null, "row"), label = node("div"), actions = node("div", null, "row-actions");
        label.append(node("div", topic.name, "name"), node("div", `${topic.section} · 可练 ${topic.practiceCount} 题 · 可检查 ${topic.questionCount} 题`, "sec"));
        const study = button("讲解", () => openTeach(topic.id));
        const practice = button("练习", () => startSession({ targets: [topic.id], mode: "practice", instruction_seen: state.viewedTopics.has(topic.id) })); practice.disabled = !topic.practiceCount;
        const check = button("检查", () => startSession({ targets: [topic.id], mode: "diagnose", instruction_seen: state.viewedTopics.has(topic.id) })); check.disabled = !topic.questionCount;
        actions.append(study, practice, check); row.append(label, actions); rows.append(row);
      }
      wrap.append(head, rows); root.append(wrap);
    }
  }
  if (!root.children.length) root.append(node("p", "没有匹配的知识点。", "muted"));
}
async function openTeach(id) {
  try {
    const card = await api(`/api/teach/${encodeURIComponent(id)}`); state.viewedTopics.add(id); show("teach");
    $("teach-title").textContent = card.topicName;
    $("teach-status").textContent = `${card.mode === "full" ? "已有单点讲解" : card.mode === "brief" ? "辅助速查；完整细目标覆盖仍需检查" : "完整讲解待补充"} · 内容初稿`;
    const root = $("teach-content"); root.textContent = "";
    root.append(section("本节目标", list(card.mastery_criteria)));
    if (card.hook) root.append(section("先想一想", rich("div", card.hook)));
    if (card.definition || card.one_liner) root.append(section("理解概念", rich("div", card.definition || card.one_liner)));
    if (card.why) root.append(section("为什么成立", rich("div", card.why)));
    if (card.when_to_use) root.append(section("什么时候用", list(card.when_to_use)));
    if (card.steps || card.key_points) root.append(section("思路与要点", list(card.steps || card.key_points, true)));
    if (card.worked_example) root.append(workedExample(card.worked_example));
    if (card.pitfalls) root.append(section("检查这些易错处", list(card.pitfalls)));
    if (card.check) root.append(section("学习后能做到什么", rich("div", card.check)));
    $("teach-units").replaceChildren(...card.learning_units.map(unitCard));
    const actions = $("teach-actions"); actions.textContent = "";
    const practice = button("开始练习", () => startSession({ targets: [id], mode: "practice", instruction_seen: true }), "primary"); practice.disabled = !card.practice_count;
    const retest = button("用新题检查", () => startSession({ targets: [id], mode: "diagnose", instruction_seen: true, previous_session_id: state.lastResultSession })); retest.disabled = !card.diagnostic_count;
    actions.append(practice, retest);
  } catch (err) { alert(err.message); }
}
function workedExample(ex, index) {
  const root = node("details", null, "card worked-example"); root.append(node("summary", `例题${index == null ? "" : index + 1} · 展开推导`));
  root.append(rich("div", ex.prompt), list(ex.steps, true), rich("div", `结论：${ex.answer}`)); return root;
}
async function openUnit(id) {
  try {
    const unit = await api(`/api/learning/units/${encodeURIComponent(id)}`); state.viewedUnits.add(id); state.unit = unit; show("unit");
    $("unit-title").textContent = unit.title; $("unit-status").textContent = reviewLabel(unit.teacher_review);
    const root = $("unit-content"); root.textContent = "";
    root.append(section("学完后能够", list(unit.objectives.map(o => o.statement))));
    if (unit.prerequisites && unit.prerequisites.length) root.append(section("先准备这些基础", list(unit.prerequisites)));
    const ex = unit.explanation;
    root.append(section("从直观开始", rich("div", ex.intuition)), section("为什么成立", rich("div", ex.reasoning)));
    if (unit.diagram) root.append(section("把信息放到图上", diagram(unit.diagram)));
    root.append(section("使用条件", list(ex.conditions)), section("用反例检查边界", rich("div", ex.counterexample)));
    root.append(...unit.worked_examples.map(workedExample));
    const actions = $("unit-actions"); actions.textContent = "";
    for (const phase of ["practice", "transfer", "delayed"]) actions.append(button(phase === "delayed" ? "复测题（建议隔1–3天）" : PHASE[phase], () => startSession({ unit_id: id, mode: phase, instruction_seen: true, previous_session_id: state.lastResultSession }), phase === "practice" ? "primary" : "ghost"));
  } catch (err) { alert(err.message); }
}
async function startSession(options) {
  if (state.pending) return;
  state.pending = true;
  try {
    const data = await api("/api/session", options); state.session = data.session; state.item = data.item; state.done = data.done; state.summary = data.summary; state.verdictText = data.verdictText;
    try { sessionStorage.setItem("cnkg-session-id", data.session.id); $("resume").hidden = false; } catch (_) { /* 无存储权限时仍可作答 */ }
    if (data.done) showResult(); else { show("quiz"); renderItem(); }
  } catch (err) { alert(err.message); } finally { state.pending = false; }
}
function renderItem() {
  const item = state.item; if (!item) return;
  state.shownAt = performance.now(); state.selected = null;
  $("feedback").hidden = true; $("submit").disabled = false; $("submit").hidden = false; $("skip").disabled = false; $("skip").hidden = false;
  $("quiz-topic").textContent = item.topicName; $("quiz-progress").textContent = `第 ${state.session.answered_count + 1} 题`;
  $("quiz-meta").textContent = `${PHASE[state.session.mode]}${item.question.difficulty ? ` · 设计难度 ${item.question.difficulty}（待实测校准）` : ""}`;
  $("quiz-hint").textContent = state.session.feedback_policy === "deferred" ? "先完成这组题，再一起看答案与解析。" : "练习反馈用于学习，不作为初始掌握的证明。";
  window.setRichText($("quiz-prompt"), item.question.prompt); $("quiz-diagram").replaceChildren(...(item.question.diagram ? [diagram(item.question.diagram)] : []));
  renderRubric($("quiz-rubric"), item.question.rubric, false);
  const type = item.question.type; $("choices").textContent = ""; $("numeric").hidden = type === "choice" || type === "rubric"; $("process-answer").hidden = type !== "rubric";
  if (type === "choice") {
    item.question.choices.forEach((choice, index) => {
      const btn = button("", () => { state.selected = choice.id; for (const other of $("choices").children) other.classList.remove("selected"); btn.classList.add("selected"); }, "");
      btn.append(node("span", String.fromCharCode(65 + index) + ".", "key"), rich("span", choice.text)); $("choices").append(btn);
    });
  } else if (type === "rubric") { $("process-input").value = ""; $("process-input").focus(); }
  else { const input = $("numeric-input"); input.value = ""; input.placeholder = type === "short" ? "按题目要求填写" : "如 -3、0.5 或 1/2；单位见题干"; input.inputMode = "text"; input.focus(); }
}
function renderRubric(root, rubric, examples) {
  root.textContent = ""; if (!rubric || !rubric.length) return;
  root.append(node("p", "过程评价标准", "muted"));
  const items = rubric.map(r => `${r.criterion}（${r.points}分）${examples && r.example ? `：${r.example}` : ""}`); root.append(list(items));
}
async function submit(skipped = false) {
  if (state.pending || !state.item) return;
  const type = state.item.question.type;
  const answer = type === "choice" ? state.selected || "" : type === "rubric" ? $("process-input").value.trim() : $("numeric-input").value.trim();
  if (!skipped && !answer) { $("quiz-hint").textContent = "请填写答案或选择暂时不会。"; return; }
  state.pending = true; $("submit").disabled = true; $("skip").disabled = true;
  try {
    const data = await api("/api/answer", { session_id: state.session.id, question_id: state.item.question.id, answer, skip: skipped,
      elapsed_ms: Math.round(performance.now() - state.shownAt) });
    state.session = data.session; state.nextItem = data.item; state.done = data.done; state.summary = data.summary; state.verdictText = data.verdictText;
    showFeedback(data.feedback);
  } catch (err) { $("quiz-hint").textContent = err.message; $("submit").disabled = false; $("skip").disabled = false; }
  finally { state.pending = false; }
}
function showFeedback(feedback) {
  $("submit").hidden = true; $("skip").hidden = true; $("feedback").hidden = false;
  const deferred = feedback.deferred; const status = feedback.status;
  $("fb-title").textContent = deferred ? "答案已记录" : STATUS[status] || "已提交";
  $("fb-title").className = `fb-title ${status === "correct" ? "ok" : status === "wrong" ? "no" : ""}`;
  window.setRichText($("fb-answer"), !deferred && feedback.answer ? `参考：${feedback.answer}` : "");
  const detail = typeof feedback.feedback === "string" ? feedback.feedback : "";
  window.setRichText($("fb-explain"), deferred ? "整组结束后显示答案，避免影响下一题的独立作答。" : `${detail}\n${feedback.explanation || ""}`);
  renderRubric($("fb-rubric"), deferred ? null : feedback.rubric, true);
  $("next").textContent = state.done ? "看本次结果" : "下一题"; $("next").focus();
}
function next() { if (state.done) showResult(); else { state.item = state.nextItem; renderItem(); } }
function nameOf(id) {
  for (const vol of state.volumes) for (const chapter of vol.chapters) for (const topic of chapter.topics) if (topic.id === id) return topic.name;
  return id;
}
function showResult() {
  const summary = state.summary; if (!summary) return; show("result"); state.lastResultSession = state.session.id;
  $("result-title").textContent = summary.title || "本次结果"; $("verdict").textContent = state.verdictText || "";
  $("result-context").textContent = summary.context && (summary.context.instruction_seen || summary.context.post_instruction)
    ? "这是学习或反馈之后的表现，与首次独立检查分开记录。" : "仅反映本次任务的表现；未覆盖的目标仍需另行观察。";
  const isUnit = state.session.kind === "unit";
  $("diagnostic-result").hidden = isUnit; $("objective-result").hidden = !isUnit;
  if (isUnit) {
    $("objective-list").textContent = "";
    for (const goal of summary.objective_results) { const row = node("div", null, "objective-row"); row.append(node("p", goal.statement), node("span", STATUS[goal.status], "muted")); $("objective-list").append(row); }
  } else {
    $("res-weak").textContent = summary.weak.map(nameOf).join("、") || "本次未形成";
    $("res-shaky").textContent = summary.shaky.map(nameOf).join("、") || "本次未形成";
    $("res-roots").textContent = summary.roots.map(nameOf).join("、") || "尚无";
    $("res-candidates").textContent = ((summary.root_candidates || []).length ? `待确认起点：${summary.root_candidates.map(nameOf).join("、")}。` : "")
      + (summary.incomplete ? "本次确认未完成，请先处理待确认任务。" : "") + "先修推断与直接作答的证据分别记录。";
    const path = $("res-path"); path.textContent = "";
    for (const task of summary.support_tasks || []) {
      const li = node("li"), actions = node("div", null, "actions"); li.append(node("strong", nameOf(task.topicId)), node("p", task.reason, "muted"));
      actions.append(button("查看讲解", () => openTeach(task.topicId)), button("换题确认", () => startSession({ targets: [task.topicId], mode: "diagnose", previous_session_id: state.lastResultSession }))); li.append(actions); path.append(li);
    }
    if (!path.children.length) path.append(node("li", state.session.mode === "practice" ? "继续看逐题反馈，理解后用新题检查。" : "本次没有形成明确补学任务，未测内容仍需另行确认。"));
    const table = $("res-chapters"); table.textContent = ""; const header = node("tr");
    for (const label of ["章节", "已测表现", "推断", "不稳", "补学", "证据不足", "未测"]) header.append(node("th", label)); table.append(header);
    for (const [chapter, bucket] of Object.entries(summary.by_chapter || {})) { const row = node("tr"); row.append(node("td", chapter)); for (const key of ["mastered", "assumed", "shaky", "weak", "insufficient_evidence", "untested"]) row.append(node("td", bucket[key] || 0, "num")); table.append(row); }
  }
  const review = $("answer-review"); review.textContent = "";
  for (const answer of summary.answers || []) {
    const entry = node("details", null, "answer-entry"); entry.append(node("summary", `${STATUS[answer.status] || "已提交"} · ${answer.question_id}`), rich("div", answer.prompt), rich("div", `你的答案：${answer.raw_answer || "暂未作答"}`));
    if (answer.answer) entry.append(rich("div", `参考：${answer.answer}`)); if (answer.explanation) entry.append(rich("div", answer.explanation));
    const rubric = node("div"); renderRubric(rubric, answer.rubric, true); entry.append(rubric); review.append(entry);
  }
  const actions = $("result-actions"); actions.textContent = "";
  if (isUnit) {
    actions.append(button("回看讲解", () => openUnit(state.session.unit_id)), button("独立迁移", () => startSession({ unit_id: state.session.unit_id, mode: "transfer", previous_session_id: state.lastResultSession }), "primary"),
      button("复测题（建议稍后）", () => startSession({ unit_id: state.session.unit_id, mode: "delayed", previous_session_id: state.lastResultSession })));
  } else if (state.session.targets.length) actions.append(button("新题复测", () => startSession({ targets: state.session.targets, mode: "diagnose", previous_session_id: state.lastResultSession }), "primary"));
}
$("search").addEventListener("input", e => renderChapters(e.target.value.trim()));
$("submit").addEventListener("click", () => submit(false)); $("skip").addEventListener("click", () => submit(true)); $("next").addEventListener("click", next);
$("numeric-input").addEventListener("keydown", e => { if (e.key === "Enter") submit(false); });
$("restart").addEventListener("click", () => show("home")); $("again").addEventListener("click", () => show("home"));
$("resume").addEventListener("click", async () => {
  try {
    const id = sessionStorage.getItem("cnkg-session-id"); if (!id) return;
    const data = await api(`/api/session/${encodeURIComponent(id)}`);
    state.session = data.session; state.item = data.item; state.done = data.done; state.summary = data.summary; state.verdictText = data.verdictText;
    if (data.done) showResult(); else { show("quiz"); renderItem(); }
  } catch (err) { $("resume").hidden = true; alert(err.message); }
});
async function boot() {
  try {
    await initHome(); show("home"); const params = new URLSearchParams(location.search);
    try { $("resume").hidden = !sessionStorage.getItem("cnkg-session-id"); } catch (_) { /* 可选恢复 */ }
    if (params.get("unit")) await openUnit(params.get("unit"));
    else if (params.get("teach")) await openTeach(params.get("teach"));
    else if (params.get("target")) await startSession({ targets: [params.get("target")], mode: "diagnose" });
    else if (params.get("volume")) await startSession({ volume: params.get("volume"), mode: "screen" });
    else if (params.get("q")) { $("search").value = params.get("q"); renderChapters(params.get("q")); }
  } catch (err) { $("stats").textContent = `加载失败：${err.message}`; }
}
boot();
