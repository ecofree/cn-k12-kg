const state = { session: null, item: null, nextItem: null, done: false, selected: null,
  pending: false, volumes: [], units: [], projects: [], savedAnswers: [], lastResultSession: null, stage: "", historyOffset: 0, worksOffset: 0, viewedTopics: new Set(), viewedUnits: new Set(),
  navigation: 0, view: "home", routeIndex: 0, renderedUrl: location.href, restoringHistory: false };
const $ = (id) => document.getElementById(id);
const PHASE = { diagnose: "知识点检查", screen: "整册抽查", practice: "练习", transfer: "迁移任务", delayed: "复测题", support: "辨别与指导练习" };
const STATUS = { correct: "答对了", wrong: "还需练习", invalid: "请检查格式", needs_review: "过程已记录，可按量规自查",
  not_attempted: "暂未作答", demonstrated: "本组题已展示该能力", practice_needed: "建议再练习", not_observed: "本组未观察到该目标" };
async function api(path, body) {
  const res = await fetch(path, { method: body ? "POST" : "GET", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
    if (!res.ok) { const error = new Error(data.error || `请求失败（${res.status}）`); error.status = res.status; error.data = data; throw error; }
  return data;
}
function show(view) {
  state.view = view;
  for (const name of ["home", "teach", "unit", "quiz", "result", "project", "history", "work", "needs", "support", "profile", "scope"]) $(`view-${name}`).hidden = name !== view;
  $("restart").hidden = view === "home";
  $("action-notice").hidden = true;
  if (view === "home") refreshPlan();
  window.scrollTo({ top: 0, behavior: "instant" });
  requestAnimationFrame(() => {
    if (state.view !== view) return;
    const heading = $(`view-${view}`).querySelector("h2, [role=heading]");
    if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); document.title = `${heading.textContent} · K12 数学`; }
  });
}
async function prepareNavigation() {
  if (state.pending) { notice("正在记录本次操作，请稍候再切换页面。"); return null; }
  const token = ++state.navigation;
  const saved = await window.CNKGWork.flushAll();
  if (token !== state.navigation) return null;
  if (!saved) {
    const banner = $("save-notice"); banner.hidden = false; banner.tabIndex = -1; banner.focus();
    return null;
  }
  return token;
}
function rememberRoute(params, mode = "push") {
  const url = new URL(location.href); url.search = new URLSearchParams(params).toString(); url.hash = "";
  if (mode !== "none") {
    const same = url.href === state.renderedUrl;
    if (mode === "push" && !same) state.routeIndex++;
    history[mode === "replace" || same ? "replaceState" : "pushState"]({ cnkgIndex: state.routeIndex }, "", url);
  } else state.routeIndex = history.state?.cnkgIndex ?? state.routeIndex;
  state.renderedUrl = location.href;
}
async function goHome(routeMode = "push", keyword = "") {
  const token = await prepareNavigation(); if (!token) return false;
  $("search").value = keyword; renderHome(); show("home");
  rememberRoute(keyword ? { q: keyword } : {}, routeMode); return true;
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
  if (!/^\/(?:static\/diagrams|api\/session\/[a-f0-9]{32}\/assets)\/[a-zA-Z0-9_.-]+\.svg$/.test(value.src)) return el;
  const img = node("img"); img.src = value.src; img.alt = value.alt || "数学示意图";
  const enlarge = node("a", "查看大图（新标签页）", "diagram-enlarge");
  enlarge.href = value.src; enlarge.target = "_blank"; enlarge.rel = "noopener";
  enlarge.title = "在新标签页查看原图，可放大阅读；本页文字会保留。";
  el.append(img, node("figcaption", value.alt || "", "muted"), enlarge); return el;
}
function reviewLabel(value) { return value && value.status === "approved" ? "已有独立审核记录，请查看具体版本" : "学习材料 · 支持练习与过程自查"; }
function unitCard(unit) {
  const card = node("article", null, "card unit-card");
  const goalCount = unit.objectives.filter(goal => goal.granularity === "observable").length || unit.objectives.length;
  card.append(node("p", { primary: "小学", junior: "初中", senior: "高中" }[unit.stage], "eyebrow"), node("h3", unit.title),
    node("p", `${goalCount} 项学习目标 · ${unit.task_counts.practice || 0} 道练习 · 含迁移任务`, "muted"),
    button("开始学习", () => openUnit(unit.id), "primary")); return card;
}
function projectCard(project) {
  const card = node("article", null, "card unit-card");
  card.append(node("p", { primary: "小学", junior: "初中", senior: "高中" }[project.stage], "eyebrow"), node("h3", project.title),
    node("p", `${project.objective_count} 个活动目标 · ${project.duration_hint}`, "muted"),
    button("查看实践任务", () => openProject(project.id), "primary")); return card;
}
function notice(message, action) {
  const root = $("action-notice"); root.replaceChildren(node("p", message)); root.hidden = false;
  if (action) root.append(button("继续复习练习", () => { root.hidden = true; startSession(action); }, "primary"));
  root.tabIndex = -1; root.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
}
function renderHome() {
  document.body.dataset.stage = state.stage;
  const units = state.units.filter(unit => !state.stage || unit.stage === state.stage);
  const projects = state.projects.filter(project => !state.stage || project.stage === state.stage);
  $("learning-units").replaceChildren(...units.map(unitCard));
  $("learning-projects").replaceChildren(...projects.map(projectCard));
  if (!projects.length) $("learning-projects").append(node("p", "当前学段可以先从学习单元的情境任务开始。", "muted"));
  const chain = ["fraction_equivalence", "ratio_models", "linear_function_representations"].map(id => state.units.find(unit => unit.id === id)).filter(unit => unit && (!state.stage || unit.stage === state.stage));
  $("learning-path").replaceChildren(...chain.map(unitCard)); $("path-section").hidden = state.stage === "senior";
  const dataChain = ["data_reading_and_mean", "sampling_and_variation", "correlation_and_regression"]
    .map(id => state.units.find(unit => unit.id === id)).filter(unit => unit && (!state.stage || unit.stage === state.stage));
  $("data-learning-path").replaceChildren(...dataChain.map(unitCard)); $("data-path-section").hidden = !dataChain.length;
  renderChapters($("search").value.trim());
}
async function refreshPlan() {
  const requestedStage = state.stage;
  try {
    const [plan, topics] = await Promise.all([api("/api/plan" + (requestedStage ? "?stage=" + requestedStage : "")), api("/api/topics")]);
    if (state.stage !== requestedStage) return;
    state.volumes = topics.volumes; renderChapters($("search").value.trim());
    const root = $("learning-plan"); root.textContent = "";
    const activeSteps = plan.next_steps;
    for (const step of activeSteps.slice(0, 3)) {
      const row = node("article", null, "plan-row"), detail = node("div");
      detail.append(node("strong", step.title), node("p", step.reason, "muted"));
      if (step.due_at) detail.append(node("p", `建议复测时间：${formatTime(step.due_at)}`, "muted"));
      const label = step.continuation === "reflect_and_revise" ? "自查并修订" : step.work_resource_id && !step.session_id ? "继续作品" : step.action === "continue" ? "继续" : step.action === "due_review" ? step.ready ? "开始复测" : "回看讲解" : step.action === "transfer" ? "迁移任务" : "开始学习";
      row.append(detail, button(label, () => followPlan(step, plan))); root.append(row);
    }
    if (!root.children.length) root.append(node("p", "还没有保存的学习活动。选择学段后，可从下面的学习路径或教材目录开始。", "muted"));
    const counts = plan.need_counts || {};
    $("need-counts").textContent = `各学段累计：${counts.open || 0} 项待处理，${counts.deferred || 0} 项已暂缓，${counts.resolved || 0} 项已有具体后续证据。`;
    $("plan-policy").textContent = plan.policy.notice;
  } catch (error) { $("learning-plan").textContent = error.message; }
}
async function followPlan(step, plan) {
  if (step.session_id) return resumeSession(step.session_id);
  if (step.work_resource_id) return openSavedWork(step.work_resource_id);
  if (step.need_ids?.length) {
    const need = plan.learning_needs.find(n => n.need_id === step.need_ids[0]);
    if (need) return actOnNeed(need);
  }
  if (step.targets?.length) return openTeach(step.targets[0]);
  if (step.action === "practice" || !step.ready) return openUnit(step.unit_id);
  return startSession({ unit_id: step.unit_id, mode: step.phase || "practice", previous_session_id: step.previous_session_id,
    focus_skill_ids: step.skill_ids || [] });
}
async function openProfile(routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  show("profile"); rememberRoute({ profile: "1" }, routeMode);
  try { await window.CNKGProfile.mount($("profile-content"), { api, node, button, formatTime, prepare: prepareNavigation, busy: value => { state.pending = value; },
    identityChanged: () => { try { sessionStorage.removeItem("cnkg-session-id"); } catch (_) {} location.replace("/"); } }); }
  catch (error) { $("profile-content").textContent = error.message; }
  return true;
}
async function openScope(routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  show("scope"); rememberRoute({ scope: "1" }, routeMode);
  $("scope-summary").textContent = "正在读取逐目标目录…";
  try {
    const report = await api("/api/quality-scope"); if (token !== state.navigation) return false;
    const policy = $("scope-policy"); policy.replaceChildren(rich("p", report.scope));
    for (const role of Object.values(report.roles)) policy.append(section(role.label, list([role.learner_requirement, role.author_requirement])));
    policy.append(node("p", report.limits.join(" "), "muted"));
    const render = () => {
      const role = $("scope-role").value, stage = $("scope-stage").value, search = $("scope-search").value.trim().toLowerCase();
      const goals = report.goals.filter(g => (!role || g.role === role) && (!stage || g.stage === stage) && (!search || (g.title + g.statement + g.goal_id).toLowerCase().includes(search)));
      $("scope-summary").textContent = `当前显示 ${goals.length} 项。完整清单：${report.summary.topic_goals} 项教材目标、${report.summary.unit_goals} 项单元目标、${report.summary.project_goals} 项活动目标；这些层级分别统计，不相加为课标覆盖率。`;
      const root = $("scope-goals"); root.replaceChildren();
      let offset = 0;
      const more = button("显示更多目标", append);
      function append() {
        more.remove();
        for (const goal of goals.slice(offset, offset + 30)) {
          const card = node("article", null, "card");
          card.append(node("h3", goal.title), rich("p", goal.statement), node("p", `${report.roles[goal.role].label} · ${goal.status === "materials_ready" ? "材料与任务已有对应" : "仍有材料缺口"}`, "muted"), rich("p", goal.observation_boundary || "按活动步骤、作品和量规核对，仍需保留未解决的问题。"));
          if (goal.gaps.length) card.append(list(goal.gaps.map(g => g.message || g.code)));
          card.append(button("打开学习材料与任务", () => goal.resource_kind === "topic" ? openTeach(goal.resource_id) : goal.resource_kind === "unit" ? openUnit(goal.resource_id) : openProject(goal.resource_id), "primary"));
          const sources = node("details"); sources.append(node("summary", "目标与材料引用"), node("p", `稳定目标ID：${goal.goal_id}`, "muted"));
          for (const ref of goal.material_refs) sources.append(node("p", ref.path + ref.json_pointer, "muted"));
          card.append(sources); root.append(card);
        }
        offset += 30; if (offset < goals.length) root.append(more);
      }
      append();
    };
    $("scope-role").onchange = $("scope-stage").onchange = $("scope-search").oninput = render; render(); return true;
  } catch (error) { $("scope-summary").textContent = error.message; return false; }
}
function renderSupport(data, routeMode) {
  show("support"); rememberRoute({ session: data.session.id }, routeMode);
  window.CNKGSupport.mount($("support-content"), data, { api, node, rich, button, diagram, list, notice, openNeeds, openUnit, busy: value => { state.pending = value; } });
  try { sessionStorage.setItem("cnkg-session-id", data.session.id); } catch (_) {}
}
async function openSupport(unitId, supportId, needIds = [], routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  state.pending = true;
  try {
    const data = await api("/api/support/start", { unit_id: unitId, support_id: supportId, need_ids: needIds });
    if (token !== state.navigation) return false;
    renderSupport(data, routeMode); return true;
  } catch (error) { notice(error.message); return false; } finally { state.pending = false; }
}
async function actOnNeed(need) {
  if (need.kind === "work_revision") return openSavedWork(need.work_resource_id);
  if (need.kind === "process_revision" || need.kind === "support_revision") return resumeSession(need.source_ref.session_id);
  if (need.unit_id && need.objective_ids?.length) {
    try {
      const unit = await api(`/api/learning/units/${encodeURIComponent(need.unit_id)}`);
      const support = unit.instructional_support?.find(s => need.objective_ids.includes(s.objective_id));
      if (support) return openSupport(need.unit_id, support.id, [need.need_id]);
    } catch (error) { notice(error.message); return false; }
    return openUnit(need.unit_id);
  }
  if (need.targets?.length) return openTeach(need.targets[0]);
  return resumeSession(need.source_ref.session_id);
}
async function openNeeds(routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  show("needs"); rememberRoute({ needs: "1" }, routeMode);
  $("needs-status").textContent = "正在读取各学段的学习事项…";
  try {
    const plan = await api("/api/plan"); if (token !== state.navigation) return false;
    const render = () => {
      const filter = $("need-filter").value, root = $("needs-list"); root.replaceChildren();
      const rows = plan.learning_needs.filter(n => filter === "all" || n.status === filter);
      $("needs-status").textContent = `当前显示 ${rows.length} 项；全部 ${plan.learning_needs.length} 项均保留原始来源与内容版本。`;
      for (const need of rows) {
        const card = node("article", null, "card"), actions = node("div", null, "actions");
        const kind = { answer_support: "作答后需进一步辨别", process_revision: "过程待自查或修订", work_revision: "作品待修订", support_revision: "指导练习待确认" };
        card.append(node("h3", need.title), node("p", `${kind[need.kind] || "学习事项"} · ${formatTime(need.observed_at)}`, "muted"), node("p", need.notice));
        if (need.criterion_outcomes?.length) card.append(list(need.criterion_outcomes.filter(r => r.status !== "met").map(r => `${r.criterion}：${{ partial: "部分达到", revisit: "需要重做", not_checked: "尚未核对" }[r.status] || r.status}`)));
        if (need.resolution) card.append(node("p", need.resolution.notice, "muted"));
        if (need.explicit_state) card.append(node("p", `暂缓或重开说明：${need.explicit_state.reason}`, "muted"));
        if (need.status !== "resolved") actions.append(button(need.kind === "answer_support" ? "辨别与补学" : "继续处理", () => actOnNeed(need), "primary"));
        if (need.unit_id && need.skill_ids?.length && need.status !== "resolved") actions.append(button("针对这些目标再练习", () => startSession({ unit_id: need.unit_id, mode: "practice", focus_skill_ids: need.skill_ids })));
        if (need.source_ref.session_id) actions.append(button("查看原作答", () => resumeSession(need.source_ref.session_id)));
        if (need.status !== "resolved") {
          const details = node("details"), summary = node("summary", need.status === "deferred" ? "重新列入计划" : "暂缓这件事");
          const label = node("label", "说明原因"), input = node("textarea"); input.id = "reason-" + need.need_id; input.rows = 2; input.maxLength = 1000; label.htmlFor = input.id;
          const message = node("p", "", "muted"); message.setAttribute("role", "status");
          const save = button("保存原因与状态", async () => {
            save.disabled = true;
            try { await api("/api/learning-needs/state", { need_id: need.need_id, action: need.status === "deferred" ? "reopen" : "defer", reason: input.value }); await openNeeds("replace"); }
            catch (error) { message.textContent = error.message; } finally { save.disabled = false; }
          }); details.append(summary, label, input, save, message); card.append(details);
        }
        const provenance = node("details"); provenance.append(node("summary", "来源与观察边界"), node("p", `内容版本：${need.resource_version || "旧记录未保存版本"}`, "muted"),
          node("p", need.family_status === "declared" ? `已登记题族：${need.family_id}` : "原题没有已确认的题族信息，不能把它当作独立的新题证据。", "muted"));
        card.append(actions, provenance); root.append(card);
      }
      if (!rows.length) root.append(node("p", "这个状态下没有记录。可以切换状态查看暂缓事项或已有后续证据的事项。", "muted"));
    };
    $("need-filter").onchange = render; render(); return true;
  } catch (error) { $("needs-status").textContent = error.message; return false; }
}
function trackReference(element, resource, reference) {
  element.addEventListener("toggle", () => {
    if (element.open) api("/api/exposure", { resource_id: resource, event_type: "reference_opened", reference_key: reference }).catch(() => {});
  });
}
async function initHome() {
  const [data, lessons, activities] = await Promise.all([api("/api/topics"), api("/api/learning/units"), api("/api/projects")]);
  state.volumes = data.volumes; window.__volumes = data.volumes; state.units = lessons.units; state.projects = activities.projects;
  $("stats").textContent = `${data.stats.edition}参考目录 · 小学至高中`;
  try { state.stage = localStorage.getItem("cnkg-stage") || ""; } catch (_) { /* optional preference */ }
  if (!["", "primary", "junior", "senior"].includes(state.stage)) state.stage = "";
  $("stage-select").value = state.stage; renderHome();
}
function renderChapters(keyword) {
  const root = $("chapters"); root.textContent = "";
  const match = (t) => !keyword || t.name.includes(keyword) || t.section.includes(keyword);
  for (const vol of state.volumes) {
    if (state.stage && vol.stage !== { primary: "小学", junior: "初中", senior: "高中" }[state.stage]) continue;
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
        const check = button("检查", () => startSession({ targets: [topic.id], mode: "diagnose", instruction_seen: state.viewedTopics.has(topic.id) })); check.disabled = !(topic.unseenCount ?? topic.questionCount);
        if (check.disabled && topic.practiceCount) check.title = "暂无未呈现的检查题，可继续复习练习。";
        actions.append(study, practice, check); row.append(label, actions); rows.append(row);
      }
      wrap.append(head, rows); root.append(wrap);
    }
  }
  if (!root.children.length) root.append(node("p", "没有匹配的知识点。", "muted"));
}
async function openTeach(id, routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  try {
    const card = await api(`/api/teach/${encodeURIComponent(id)}`); if (token !== state.navigation) return false;
    state.viewedTopics.add(id); show("teach"); rememberRoute({ teach: id }, routeMode);
    $("teach-title").textContent = card.topicName;
    $("teach-status").textContent = `${card.tier === "tool" ? "基础支持" : "核心学习"} · ${card.mode === "full" ? "已有单点讲解" : card.mode === "brief" ? "辅助速查；完整细目标覆盖仍需检查" : "完整讲解待补充"} · 内容初稿`;
    const root = $("teach-content"); root.textContent = "";
    root.append(section("本节目标", list(card.mastery_criteria)));
    if (card.hook) root.append(section("先想一想", rich("div", card.hook)));
    if (card.definition || card.one_liner) root.append(section("理解概念", rich("div", card.definition || card.one_liner)));
    if (card.why) root.append(section("为什么成立", rich("div", card.why)));
    if (card.diagram) root.append(section("把条件放到图上", diagram(card.diagram)));
    if (card.when_to_use) root.append(section("什么时候用", list(card.when_to_use)));
    if (card.conditions) root.append(section("使用前先核对", list(card.conditions)));
    if (card.representation_note) root.append(section("怎样摆、画或表示", rich("p", card.representation_note)));
    if (card.steps || card.key_points) root.append(section("思路与要点", list(card.steps || card.key_points, true)));
    if (card.worked_example) root.append(workedExample(card.worked_example, 0, "topic:" + id));
    if (card.pitfalls) root.append(section("检查这些易错处", list(card.pitfalls)));
    if (card.objective_coverage && card.objective_coverage.length) {
      root.append(section("逐项目标自检", node("p", "先独立写出解答或完成作图，再展开参考过程逐条比较。开放任务的自检记录用于整理思路。", "muted")));
      for (const objective of card.objective_coverage) {
        const task = objective.self_check;
        const content = node("div");
        content.append(rich("p", objective.explanation), rich("p", task.prompt, "self-check-prompt"));
        content.append(node("p", objective.observation_note || "这次自检观察下面的具体检查点。完成一题或自报达到，仍不足以确认其他情形与稍后迁移。", "muted"));
        if (objective.difficulty_support) {
          const support = node("details"), help = objective.difficulty_support;
          support.append(node("summary", "遇到困难时，从这里辨别与修订"), rich("p", help.discriminating_prompt), rich("p", help.hint), rich("p", help.revision_prompt));
          trackReference(support, "topic:" + id, "self_check:" + objective.criterion_index); content.append(support);
        }
        const label = node("label", "我的思路与过程");
        const input = node("textarea"); input.rows = 4;
        input.id = `self-check-${objective.criterion_index}`; label.htmlFor = input.id;
        input.placeholder = "可以写计算步骤、理由，或记录纸上作图的结果。填写后会自动保存。";
        content.append(label, input);
        const workRoot = node("div"); content.append(workRoot); input.disabled = true;
        window.CNKGWork.mount(workRoot, { kind: "self_check", topic_id: id, criterion_index: objective.criterion_index },
          { externalFields: { response: input }, hidePrompt: true, compact: true }).catch(() => {});
        const answer = node("details", null, "self-check-answer");
        answer.append(node("summary", "完成后查看参考过程与检查点"), rich("div", task.reference_answer), list(task.criteria));
        trackReference(answer, "topic:" + id, "self_check:" + objective.criterion_index);
        content.append(answer);
        root.append(section(`目标 ${objective.criterion_index + 1} · ${objective.title}`, content));
      }
    }
    if (card.check) root.append(section("学习后能做到什么", rich("div", card.check)));
    $("teach-units").replaceChildren(...card.learning_units.map(unitCard));
    const actions = $("teach-actions"); actions.textContent = "";
    const practice = button("开始练习", () => startSession({ targets: [id], mode: "practice", instruction_seen: true }), "primary"); practice.disabled = !card.practice_count;
    const retest = button("用未呈现题检查", () => startSession({ targets: [id], mode: "diagnose", instruction_seen: true, previous_session_id: state.lastResultSession })); retest.disabled = !(card.availability?.unseen_count ?? card.diagnostic_count);
    actions.append(practice, retest);
    if (retest.disabled) actions.append(node("p", "暂无未呈现且未暴露同族参考的检查题；仍可继续练习。", "muted"));
    return true;
  } catch (err) { notice(err.message); return false; }
}
function workedExample(ex, index, resource) {
  const root = node("details", null, "card worked-example"); root.append(node("summary", `例题${index == null ? "" : index + 1} · 展开推导`));
  root.append(rich("div", ex.prompt), list(ex.steps, true), rich("div", `结论：${ex.answer}`));
  if (resource) trackReference(root, resource, "example:" + (index || 0)); return root;
}
async function openUnit(id, routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  try {
    const unit = await api(`/api/learning/units/${encodeURIComponent(id)}`); if (token !== state.navigation) return false;
    state.viewedUnits.add(id); state.unit = unit; show("unit"); rememberRoute({ unit: id }, routeMode);
    $("unit-title").textContent = unit.title; $("unit-status").textContent = reviewLabel(unit.teacher_review);
    const root = $("unit-content"); root.textContent = "";
    const scopeNotes = [unit.navigation_note, unit.coverage_note, ...(unit.standard_alignment || []).map(row => row.note)].filter(Boolean);
    if (scopeNotes.length) root.append(section("这个单元的学习范围", list([...new Set(scopeNotes)])));
    const goals = unit.objectives.some(goal => goal.granularity === "observable") ? unit.objectives.filter(goal => goal.granularity === "observable") : unit.objectives;
    root.append(section("这次逐项练习", list(goals.map(o => o.statement))));
    if (unit.prerequisites && unit.prerequisites.length) root.append(section("先准备这些基础", list(unit.prerequisites)));
    if (unit.prerequisite_unit_ids?.length) {
      const links = node("div", null, "actions");
      for (const prerequisite of unit.prerequisite_unit_ids) links.append(button(state.units.find(row => row.id === prerequisite)?.title || "回看基础", () => openUnit(prerequisite)));
      root.append(section("需要时回到这些基础", links));
    }
    const ex = unit.explanation;
    root.append(section("从直观开始", rich("div", ex.intuition)), section("为什么成立", rich("div", ex.reasoning)));
    if (unit.diagram) root.append(section("把信息放到图上", diagram(unit.diagram)));
    root.append(section("使用条件", list(ex.conditions)), section("用反例检查边界", rich("div", ex.counterexample)));
    root.append(...unit.worked_examples.map((example, index) => workedExample(example, index, "unit:" + id)));
    if (window.CNKGManipulatives) window.CNKGManipulatives.mount(root, unit, { node, rich, button, api, busy: value => { state.pending = value; } });
    if (unit.instructional_support?.length) {
      const help = node("div");
      for (const support of unit.instructional_support) {
        const row = node("article", null, "support-link");
        row.append(node("h4", support.title), rich("p", support.observation_note), button("辨别困难并跟着练", () => openSupport(id, support.id)));
        help.append(row);
      }
      root.append(section("遇到困难，从具体目标开始", help));
    }
    const notes = node("details", null, "card"); notes.append(node("summary", "保存我的解释与疑问"));
    const notesRoot = node("div"); notes.append(notesRoot); root.append(notes);
    window.CNKGWork.mount(notesRoot, { kind: "unit", unit_id: id }, { compact: true }).catch(() => {});
    const actions = $("unit-actions"); actions.textContent = "";
    for (const phase of ["practice", "transfer", "delayed"]) actions.append(button(phase === "delayed" ? "复测题（建议隔1–3天）" : PHASE[phase], () => startSession({ unit_id: id, mode: phase, instruction_seen: true, previous_session_id: state.lastResultSession }), phase === "practice" ? "primary" : "ghost"));
    return true;
  } catch (err) { notice(err.message); return false; }
}
async function openProject(id, routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  try {
    const project = await api(`/api/projects/${encodeURIComponent(id)}`); if (token !== state.navigation) return false;
    show("project"); rememberRoute({ project: id }, routeMode);
    $("project-title").textContent = project.title;
    $("project-status").textContent = `${reviewLabel(project.teacher_review)} · ${project.duration_hint}`;
    const root = $("project-content"); root.textContent = "";
    root.append(section("要完成的任务", rich("div", project.task_brief)),
      section("活动中需要展示", list(project.objectives.map(goal => goal.statement))),
      section("准备材料", list(project.materials)));
    const workRoot = node("div", null, "card project-work"); root.append(workRoot);
    window.CNKGWork.mount(workRoot, { kind: "project", project_id: id }).catch(() => {});
    root.append(section("整理这些作品", list(project.deliverables)));
    const rubric = node("div"); renderRubric(rubric, project.rubric, false);
    rubric.append(rich("p", project.assessment_note), node("p", "先按检查点自评，补充依据并保存修订。", "muted"));
    root.append(section("怎样评价作品", rubric));
    const scope = node("details", null, "card"); scope.append(node("summary", "活动范围与课程关联"), rich("p", project.standard_alignment_note)); root.append(scope);
    return true;
  } catch (err) { notice(err.message); return false; }
}
function formatTime(value) {
  if (!value) return "尚未完成";
  const date = new Date(value); if (Number.isNaN(date.getTime())) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}
function contextText(context = {}, mode) {
  const notes = ["仅反映本次任务的表现；未覆盖的目标仍需另行观察。"];
  if (context.material_requested) notes.push("本次作答前曾打开相关讲解；打开记录不能说明已经读完或学会。");
  else if (context.reported_instruction_seen) notes.push("本次提交标记为看过讲解，这是自述信息，尚无相关材料的打开记录。");
  const repeats = context.repeated_question_ids || [];
  if (repeats.length) notes.push(`本组包含 ${repeats.length} 道先前呈现过的题，重复作答不作为新的独立题证据。`);
  if (context.repeated_family_ids?.length) notes.push(`本组有 ${context.repeated_family_ids.length} 类题的相同结构或参考过程已经提供过，这些作答按复习记录保留。`);
  if (context.unclassified_question_ids?.length) notes.push("部分题目尚未完成结构分族，题号不同本身不能证明考查独立。");
  if (typeof context.actual_delay_seconds === "number" && Number.isFinite(context.actual_delay_seconds)) {
    const seconds = Math.max(0, context.actual_delay_seconds);
    const duration = seconds < 60 ? "不足1分钟" : seconds < 3600 ? `约${Math.floor(seconds / 60)}分钟` : seconds < 86400 ? `约${(seconds / 3600).toFixed(1)}小时` : `约${(seconds / 86400).toFixed(1)}天`;
    notes.push(`距同一目标的前次完成${duration}。`);
    if (context.interval_notice) {
      if (mode === "delayed" && !context.delay_window_met) notes.push("相关活动记录的间隔未满24小时，尚不计作延迟复测证据。");
    } else if (!context.delay_window_met || seconds < 86400) notes.push("间隔未满24小时，尚不计作延迟复测证据。");
    else notes.push("已达到建议的最短间隔；时间记录本身不能证明独立作答或知识保持。");
  } else if (mode === "delayed") notes.push("尚无可核对的同一目标前次完成时间；本次先作为复测题作答记录。");
  if (mode === "delayed" && context.interval_notice) notes.push(context.interval_notice);
  return notes.join(" ");
}
async function resumeSession(id, routeMode = "push") {
  if (!id || state.pending) return false;
  const token = await prepareNavigation(); if (!token || state.pending) return false;
  state.pending = true;
  try {
    const data = await api(`/api/session/${encodeURIComponent(id)}`);
    if (token !== state.navigation) return false;
    if (data.session.kind === "support") { renderSupport(data, routeMode); return true; }
    state.session = data.session; state.item = data.item; state.done = data.done; state.summary = data.summary; state.verdictText = data.verdictText; state.availability = data.availability;
    state.savedAnswers = Array.isArray(data.saved_answers) ? data.saved_answers : [];
    try { sessionStorage.setItem("cnkg-session-id", data.session.id); $("resume").hidden = false; } catch (_) { /* 可选恢复 */ }
    if (data.done) showResult(); else { show("quiz"); renderItem(); }
    rememberRoute({ session: data.session.id }, routeMode); return true;
  } catch (err) { notice(err.message); return false; } finally { state.pending = false; }
}
async function openHistory(routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  show("history"); rememberRoute({ history: "1" }, routeMode); state.historyOffset = 0; state.worksOffset = 0;
  $("history-list").textContent = ""; $("history-works").textContent = "";
  await Promise.all([moreHistory(), moreWorks()]);
  return true;
}
async function moreHistory() {
  $("history-status").textContent = "正在读取记录…";
  try {
    const data = await api(`/api/history?limit=20&offset=${state.historyOffset}`); const root = $("history-list");
    state.historyOffset += data.sessions.length;
    $("history-status").textContent = data.total ? `共 ${data.total} 次作答，已显示 ${state.historyOffset} 次。` : "本浏览器还没有作答记录，可以从目录选择一个目标开始。";
    for (const item of data.sessions) {
      const card = node("article", null, "card history-card"), detail = node("div");
      detail.append(node("h3", item.title || "知识点检查"), node("p", `${PHASE[item.mode] || "作答"} · 已记录 ${item.answered_count} 题 · ${item.completed_at ? "已完成" : "未完成"}`, "muted"),
        node("p", `开始：${formatTime(item.created_at)}${item.completed_at ? ` · 完成：${formatTime(item.completed_at)}` : ""}`, "muted"));
      if (item.resume_blocked) detail.append(node("p", "材料或规则已更新，旧记录保留；请回目录重新开始。", "muted"));
      card.append(detail, button(item.completed_at ? "查看结果" : item.resume_blocked ? "查看原作答" : "继续作答", () => resumeSession(item.id)));
      root.append(card);
    }
    $("history-more").hidden = state.historyOffset >= data.total;
  } catch (err) { $("history-status").textContent = err.message; }
}
async function moreWorks() {
  try {
    const data = await api(`/api/works?limit=20&offset=${state.worksOffset}`), root = $("history-works");
    state.worksOffset += data.works.length;
    for (const work of data.works) {
      const card = node("article", null, "card history-card"), detail = node("div");
      detail.append(node("h3", work.title || "学习作品"), node("p", `最近保存：${formatTime(work.updated_at)} · 保留 ${work.revision} 版`, "muted"));
      card.append(detail, button("继续整理", () => openSavedWork(work.resource_id))); root.append(card);
    }
    if (!data.total) root.append(node("p", "填写自检、过程题或实践任务后，作品会保存在这里。", "muted"));
    $("works-more").hidden = state.worksOffset >= data.total;
  } catch (error) { $("history-works").append(node("p", error.message, "muted")); }
}
async function openSavedWork(resourceId, routeMode = "push") {
  const token = await prepareNavigation(); if (!token) return false;
  show("work"); rememberRoute({ work: resourceId }, routeMode); $("work-title").textContent = "正在恢复作品…";
  const root = $("work-content"); root.replaceChildren();
  try { await window.CNKGWork.mount(root, { resource_id: resourceId }, { onReady: editor => {
    if (token === state.navigation) { $("work-title").textContent = editor.descriptor.resource_snapshot.title; document.title = `${$("work-title").textContent} · K12 数学`; }
  } }); }
  catch (error) { $("work-title").textContent = "暂时无法恢复作品"; }
  return true;
}
async function exportHistory() {
  const control = $("export-history"); if (control.disabled) return; control.disabled = true;
  try {
    const data = await api("/api/export"); const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = "cnkg-learning-records.json";
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("history-status").textContent = "已导出本浏览器的学习记录；材料打开和时间间隔仍需结合实际学习情况解释。";
  } catch (err) { $("history-status").textContent = err.message; } finally { control.disabled = false; }
}
async function startSession(options, routeMode = "push") {
  if (state.pending) return false;
  const token = await prepareNavigation(); if (!token || state.pending) return false;
  state.pending = true; state.savedAnswers = []; $("quiz-saved-answers").replaceChildren();
  try {
    const data = await api("/api/session", options); if (token !== state.navigation) return false;
    state.session = data.session; state.item = data.item; state.done = data.done; state.summary = data.summary; state.verdictText = data.verdictText; state.availability = data.availability;
    try { sessionStorage.setItem("cnkg-session-id", data.session.id); $("resume").hidden = false; } catch (_) { /* 无存储权限时仍可作答 */ }
    if (data.done) showResult(); else { show("quiz"); renderItem(); }
    rememberRoute({ session: data.session.id }, routeMode); return true;
  } catch (err) { if (err.data?.code === "no_fresh_tasks") notice(err.message, err.data.availability.practice_count ? err.data.next_action : null); else notice(err.message); return false; } finally { state.pending = false; }
}
function renderItem() {
  const item = state.item, blocked = !!state.session.resume_blocked;
  $("quiz-resume-notice").hidden = !blocked; $("quiz-resume-message").textContent = state.session.resume_message || "本次材料已经更新，原记录已保留，请回目录重新开始。";
  const saved = $("quiz-saved-answers"); saved.replaceChildren();
  if (blocked) {
    saved.append(node("h3", "已保存的作答"));
    for (const [index, answer] of state.savedAnswers.entries()) {
      const entry = node("details", null, "answer-entry");
      entry.append(node("summary", `第 ${index + 1} 条 · ${formatTime(answer.answered_at)}`),
        rich("div", answer.prompt || "本题题干未记录"),
        rich("div", `你的答案：${answer.raw_answer == null || answer.raw_answer === "" ? "暂未作答" : answer.raw_answer}`));
      saved.append(entry);
    }
    if (!state.savedAnswers.length) saved.append(node("p", "本次尚无已保存的作答。", "muted"));
  }
  if (!item) { $("submit").disabled = true; $("skip").disabled = true; return; }
  state.shownAt = performance.now(); state.selected = null;
  $("feedback").hidden = true; $("submit").disabled = false; $("submit").hidden = false; $("skip").disabled = false; $("skip").hidden = false;
  $("quiz-topic").textContent = item.topicName; $("quiz-progress").textContent = `第 ${state.session.answered_count + 1} 题`;
  $("quiz-meta").textContent = `${PHASE[state.session.mode]}${item.question.difficulty ? ` · 设计难度 ${item.question.difficulty}（待实测校准）` : ""}`;
  $("quiz-hint").textContent = state.session.feedback_policy === "deferred" ? "先完成这组题，再一起看答案与解析。" : "练习反馈用于学习，不作为初始掌握的证明。";
  if (state.session.mode === "delayed" && state.session.context?.interval_notice) $("quiz-hint").textContent += " " + state.session.context.interval_notice;
  window.setRichText($("quiz-prompt"), item.question.prompt); $("quiz-diagram").replaceChildren(...(item.question.diagram ? [diagram(item.question.diagram)] : []));
  renderRubric($("quiz-rubric"), item.question.rubric, false);
  const type = item.question.type; $("choices").replaceChildren(node("legend", "选择一个答案", "sr-only")); $("choices").hidden = type !== "choice";
  $("numeric").hidden = type === "choice" || type === "rubric"; $("process-answer").hidden = type !== "rubric";
  if (type === "choice") {
    item.question.choices.forEach((choice, index) => {
      const label = node("label", null, "choice-option"), input = node("input");
      input.type = "radio"; input.name = "quiz-choice"; input.value = choice.id;
      input.addEventListener("change", () => {
        state.selected = input.value;
        for (const row of $("choices").querySelectorAll(".choice-option")) row.classList.toggle("selected", row.querySelector("input").checked);
      });
      label.append(input, node("span", String.fromCharCode(65 + index) + ".", "key"), rich("span", choice.text)); $("choices").append(label);
    });
  } else if (type === "rubric") {
    const root = $("process-answer"), label = node("label", "写出过程、依据和结论"), input = node("textarea"), workRoot = node("div");
    input.id = "process-input"; input.rows = 6; input.maxLength = 8000; input.disabled = true; label.htmlFor = input.id;
    root.replaceChildren(label, input, workRoot);
    const qid = item.question.id, sid = state.session.id;
    const current = () => root.isConnected && state.item?.question.id === qid && state.session?.id === sid;
    state.currentWorkMount = window.CNKGWork.mount(workRoot, { kind: "session_task", session_id: state.session.id, question_id: qid },
      { externalFields: { response: input }, hidePrompt: true, compact: true, disabled: blocked,
        onReady: editor => {
          if (current() && !blocked) { $("submit").disabled = false; $("skip").disabled = false; state.currentWorkMount = Promise.resolve(editor); }
        }, onError: () => { if (current()) { input.disabled = true; $("submit").disabled = true; $("skip").disabled = true; } }
      }).catch(() => null);
  }
  else { const input = $("numeric-input"); input.value = ""; input.placeholder = type === "short" ? "按题目要求填写" : "如 -3、0.5 或 1/2；单位见题干"; input.inputMode = "text"; }
  $("submit").disabled = blocked || type === "rubric"; $("skip").disabled = blocked || type === "rubric";
  $("numeric-input").disabled = blocked; $("process-input").disabled = blocked || type === "rubric";
  for (const choice of $("choices").querySelectorAll("input")) choice.disabled = blocked;
  if (blocked) $("quiz-hint").textContent = "这份作答暂不能继续提交，请从目录重新开始。";
  $("quiz-prompt").focus({ preventScroll: true });
}
function renderRubric(root, rubric, examples) {
  root.textContent = ""; if (!rubric || !rubric.length) return;
  root.append(node("p", "过程评价标准", "muted"));
  const items = rubric.map(r => `${r.criterion}（${r.points}分）${examples && r.example ? `：${r.example}` : ""}`); root.append(list(items));
}
async function submit(skipped = false) {
  if (state.pending || !state.item || state.session.resume_blocked) return;
  const type = state.item.question.type;
  if (type === "rubric" && state.currentWorkMount) await state.currentWorkMount;
  const answer = type === "choice" ? state.selected || "" : type === "rubric" ? $("process-input").value.trim() : $("numeric-input").value.trim();
  if (!skipped && !answer) {
    $("quiz-hint").textContent = "请填写答案或选择暂时不会。";
    (type === "choice" ? $("choices").querySelector("input") : type === "rubric" ? $("process-input") : $("numeric-input"))?.focus();
    return;
  }
  state.pending = true; $("submit").disabled = true; $("skip").disabled = true;
  try {
    const data = await api("/api/answer", { session_id: state.session.id, question_id: state.item.question.id, answer, skip: skipped,
      elapsed_ms: Math.round(performance.now() - state.shownAt) });
    state.session = data.session; state.nextItem = data.item; state.done = data.done; state.summary = data.summary; state.verdictText = data.verdictText; state.availability = data.availability;
    showFeedback(data.feedback);
  } catch (err) { $("quiz-hint").textContent = err.message; $("submit").disabled = false; $("skip").disabled = false; }
  finally { state.pending = false; }
}
function showFeedback(feedback) {
  $("submit").hidden = true; $("skip").hidden = true; $("feedback").hidden = false;
  for (const input of $("choices").querySelectorAll("input")) input.disabled = true;
  $("numeric-input").disabled = true;
  const deferred = feedback.deferred; const status = feedback.status;
  $("fb-title").textContent = deferred ? "答案已记录" : STATUS[status] || "已提交";
  $("fb-title").className = `fb-title ${status === "correct" ? "ok" : status === "wrong" ? "no" : ""}`;
  window.setRichText($("fb-answer"), !deferred && feedback.answer ? `参考：${feedback.answer}` : "");
  const detail = typeof feedback.feedback === "string" ? feedback.feedback : "";
  window.setRichText($("fb-explain"), deferred ? "整组结束后显示答案，避免影响下一题的独立作答。" : `${detail}\n${feedback.explanation || ""}`);
  renderRubric($("fb-rubric"), deferred ? null : feedback.rubric, true);
  $("next").textContent = state.done ? "看本次结果" : "下一题"; $("next").focus();
}
async function next() {
  const token = await prepareNavigation(); if (!token) return;
  if (state.done) showResult(); else { state.item = state.nextItem; renderItem(); }
}
function nameOf(id) {
  for (const vol of state.volumes) for (const chapter of vol.chapters) for (const topic of chapter.topics) if (topic.id === id) return topic.name;
  return id;
}
function showResult() {
  const summary = state.summary; if (!summary) return; show("result"); state.lastResultSession = state.session.id;
  $("result-title").textContent = summary.title || "本次结果"; $("verdict").textContent = state.verdictText || "";
  $("result-context").textContent = contextText(summary.context || state.session.context, state.session.mode);
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
  for (const [index, answer] of (summary.answers || []).entries()) {
    const entry = node("details", null, "answer-entry"); entry.append(node("summary", `第 ${index + 1} 题 · ${STATUS[answer.status] || "已提交"}`), rich("div", answer.prompt), rich("div", `你的答案：${answer.raw_answer || "暂未作答"}`));
    if (answer.answer) entry.append(rich("div", `参考：${answer.answer}`)); if (answer.explanation) entry.append(rich("div", answer.explanation));
    const rubric = node("div"); renderRubric(rubric, answer.rubric, true); entry.append(rubric);
    if (answer.status === "needs_review") {
      const workRoot = node("div"); entry.append(workRoot);
      window.CNKGWork.mount(workRoot, { kind: "session_task", session_id: state.session.id, question_id: answer.question_id }, { hidePrompt: true, compact: true }).catch(() => {});
    }
    review.append(entry);
  }
  const actions = $("result-actions"); actions.textContent = "";
  if (isUnit) {
    actions.append(button("回看讲解", () => openUnit(state.session.unit_id)), button("迁移任务", () => startSession({ unit_id: state.session.unit_id, mode: "transfer", previous_session_id: state.lastResultSession }), "primary"),
      button("复测题（建议稍后）", () => startSession({ unit_id: state.session.unit_id, mode: "delayed", previous_session_id: state.lastResultSession })));
  } else if (state.session.targets.length) {
    const check = button("未呈现题检查", () => startSession({ targets: state.session.targets, mode: "diagnose", previous_session_id: state.lastResultSession }), "primary");
    check.disabled = state.availability?.unseen_count === 0;
    actions.append(check, button("复习练习", () => startSession({ targets: state.session.targets, mode: "practice", previous_session_id: state.lastResultSession })));
    if (check.disabled) actions.append(node("p", "当前检查题已呈现完，复习练习仍可继续。", "muted"));
  }
}
$("search").addEventListener("input", e => {
  const keyword = e.target.value.trim(); renderChapters(keyword);
  if (state.view === "home") rememberRoute(keyword ? { q: keyword } : {}, "replace");
});
document.querySelector(".skip-link").addEventListener("click", event => {
  event.preventDefault(); $("main-content").focus(); $("main-content").scrollIntoView({ block: "start" });
});
$("submit").addEventListener("click", () => submit(false)); $("skip").addEventListener("click", () => submit(true)); $("next").addEventListener("click", next);
$("numeric-input").addEventListener("keydown", e => { if (e.key === "Enter") submit(false); });
$("restart").addEventListener("click", () => goHome()); $("again").addEventListener("click", () => goHome());
$("resume").addEventListener("click", () => { try { resumeSession(sessionStorage.getItem("cnkg-session-id")); } catch (_) { $("resume").hidden = true; } });
$("open-history").addEventListener("click", () => openHistory()); $("refresh-history").addEventListener("click", () => openHistory("replace"));
$("open-profile").addEventListener("click", () => openProfile()); $("open-needs").addEventListener("click", () => openNeeds());
$("open-scope").addEventListener("click", () => openScope());
$("export-history").addEventListener("click", exportHistory);
$("history-more").addEventListener("click", moreHistory); $("works-more").addEventListener("click", moreWorks);
$("work-return").addEventListener("click", () => openHistory()); $("retry-save").addEventListener("click", () => window.CNKGWork.flushAll());
$("stage-select").addEventListener("change", event => { state.stage = event.target.value; try { localStorage.setItem("cnkg-stage", state.stage); } catch (_) {} renderHome(); refreshPlan(); });
$("project-return").addEventListener("click", () => goHome()); $("quiz-return").addEventListener("click", () => goHome());
async function restoreLocation(routeMode) {
  const params = new URLSearchParams(location.search);
  if (params.get("session")) return resumeSession(params.get("session"), routeMode);
  if (params.get("work")) return openSavedWork(params.get("work"), routeMode);
  if (params.has("history")) return openHistory(routeMode);
  if (params.has("profile")) return openProfile(routeMode);
  if (params.has("needs")) return openNeeds(routeMode);
  if (params.has("scope")) return openScope(routeMode);
  if (params.get("project")) return openProject(params.get("project"), routeMode);
  if (params.get("unit")) return openUnit(params.get("unit"), routeMode);
  if (params.get("teach")) return openTeach(params.get("teach"), routeMode);
  // A launch link becomes the concrete saved session URL before the learner can refresh it.
  if (params.get("target")) return startSession({ targets: [params.get("target")], mode: "diagnose" }, "replace");
  if (params.get("volume")) return startSession({ volume: params.get("volume"), mode: "screen" }, "replace");
  return goHome(routeMode, params.get("q") || "");
}
addEventListener("popstate", async event => {
  if (state.restoringHistory) { state.restoringHistory = false; return; }
  const previous = { url: state.renderedUrl, index: state.routeIndex };
  const destinationIndex = event.state?.cnkgIndex;
  if (await restoreLocation("none")) return;
  if (state.renderedUrl !== previous.url) return;
  const delta = Number.isInteger(destinationIndex) ? previous.index - destinationIndex : 0;
  if (delta) { state.restoringHistory = true; history.go(delta); }
  else history.replaceState({ cnkgIndex: previous.index }, "", previous.url);
});
async function boot() {
  try {
    state.routeIndex = Number.isInteger(history.state?.cnkgIndex) ? history.state.cnkgIndex : 0;
    history.replaceState({ cnkgIndex: state.routeIndex }, "", location.href);
    await initHome();
    try { $("resume").hidden = !sessionStorage.getItem("cnkg-session-id"); } catch (_) { /* 可选恢复 */ }
    await restoreLocation("replace");
  } catch (err) { $("stats").textContent = `加载失败：${err.message}`; }
  finally { $("open-history").disabled = false; }
}
boot();
