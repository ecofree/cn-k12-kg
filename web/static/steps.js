/* Bounded equation practice: exact step feedback, saved attempts, no mastery score. */
window.CNKGSteps = (() => {
  const labels = {equivalent: "这一步保持了解集", non_equivalent: "这一步改变了解集", reason_mismatch: "等式与操作理由需要分开核对", unsupported: "本练习暂不支持这种输入", conclusion_correct: "已记录本题结论与检验", conclusion_incorrect: "结论还需要核对"};
  function el(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
  async function api(path, body) {
    const r = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const value = await r.json(); if (!r.ok) { const e = new Error(value.error || "保存未完成，请保留输入后重试。"); e.status = r.status; throw e; } return value;
  }
  function key() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
  async function mount(root, {unitId = "equation_equivalence"} = {}) {
    let listing, activity = null, busy = false, message = "", feedback = null, more = false;
    const emptyDraft = () => ({equation: "", operation: "simplify", operand: "", used_help: false, conclusion: "unique", value: ""});
    let draft = emptyDraft(), lastSaved = JSON.stringify(draft), pending = null;
    let draftTimer = null, draftRunning = null, draftPending = null, failed = false, draftStatus = "草稿尚未填写。", notify = () => {};
    const draftDirty = () => Boolean(activity && JSON.stringify(draft) !== lastSaved);
    function draftNotice() { const n = root.querySelector("[data-step-draft-status]"); if (n) n.textContent = draftStatus; notify(); }
    function restoreDraft(next) {
      activity = next; draft = {...emptyDraft(), ...next.draft}; lastSaved = JSON.stringify(draft); draftPending = null; failed = false;
      draftStatus = "草稿已从档案恢复；尚未核对的内容不计为一次作答。";
    }
    async function saveDraft() {
      clearTimeout(draftTimer);
      if (draftRunning) { const saved = await draftRunning; return saved && draftDirty() ? saveDraft() : saved; }
      if (!draftDirty()) { draftNotice(); return true; }
      if (activity.read_only || activity.completed) { failed = true; draftStatus = "当前活动已只读，请保留本页文字；旧草稿仍在原记录中。"; draftNotice(); return false; }
      const fields = {...draft}, signature = JSON.stringify({id: activity.id, revision: activity.revision, fields});
      if (!draftPending || draftPending.signature !== signature) draftPending = {signature, request_id: key()};
      const body = {activity_id: activity.id, expected_revision: activity.revision, request_id: draftPending.request_id, draft: fields};
      draftStatus = "正在保存未核对的草稿……"; draftNotice();
      draftRunning = (async () => {
        try {
          const result = await api("/api/steps/draft", body);
          activity = result; lastSaved = JSON.stringify({...emptyDraft(), ...result.draft}); draftPending = null; failed = false;
          draftStatus = "草稿已保存，尚未核对；没有新增作答或判定。"; return true;
        } catch (e) {
          failed = true; draftStatus = `草稿尚未保存：${e.message} 请保留本页并重试。`;
          if (e.status === 409) {
            try { activity = await api(`/api/steps?activity_id=${encodeURIComponent(activity.id)}`); lastSaved = JSON.stringify({...emptyDraft(), ...activity.draft}); draftPending = null; }
            catch (_) { /* Keep local fields and the same request identity until retry. */ }
          }
          return false;
        } finally { draftRunning = null; draftNotice(); }
      })();
      const saved = await draftRunning;
      return saved && draftDirty() ? saveDraft() : saved;
    }
    function changed() {
      draftStatus = "未核对的草稿等待保存……"; draftNotice(); clearTimeout(draftTimer);
      draftTimer = setTimeout(saveDraft, 500);
    }
    if (window.CNKGWork?.registerActivity) notify = window.CNKGWork.registerActivity({root,
      get failed() { return failed; }, unsaved: () => draftDirty() || Boolean(draftRunning) || busy, save: saveDraft});
    function button(text, action, cls) { const b = el("button", text, cls); b.type = "button"; b.addEventListener("click", action); b.disabled = busy; return b; }
    async function loadListing(offset = 0) {
      const next = await api(`/api/steps?unit_id=${encodeURIComponent(unitId)}&offset=${offset}`);
      listing = offset ? {...next, activities: [...listing.activities, ...next.activities]} : next;
      more = listing.activities.length < listing.total;
    }
    async function choose(id) {
      if (busy) return; busy = true; draw();
      try { if (!await saveDraft()) return; restoreDraft(await api(`/api/steps?activity_id=${encodeURIComponent(id)}`)); feedback = null; pending = null; message = "已从档案读取原始步骤和未核对草稿。"; }
      catch (e) { message = e.message; } finally { busy = false; draw(); notify(); }
    }
    async function start(caseId) {
      if (busy) return; busy = true; draw();
      const body = {unit_id: unitId, case_id: caseId, expected_resource_version: listing.resource_version, request_id: key()};
      try { if (!await saveDraft()) return; restoreDraft(await api("/api/steps/start", body)); await loadListing(); feedback = null; pending = null; message = "新活动已保存；以前的尝试和草稿仍可打开。"; }
      catch (e) { message = e.message; } finally { busy = false; draw(); notify(); }
    }
    async function submit(kind, extra = {}) {
      if (busy || !activity) return;
      busy = true; message = "正在保存这一步……"; draw();
      try {
        if (!await saveDraft()) { message = "草稿尚未保存，未进行这一步的数学核对；请先重试保存。"; return; }
        const fields = kind === "hint" ? extra : kind === "conclusion" ? {kind, conclusion: draft.conclusion, value: draft.value, used_help: draft.used_help} : {kind: "equation", equation: draft.equation, operation: draft.operation, operand: draft.operand, used_help: draft.used_help};
        const signature = JSON.stringify({id: activity.id, revision: activity.revision, kind, fields});
        if (!pending || pending.signature !== signature) pending = {signature, request_id: key()};
        const body = {activity_id: activity.id, expected_revision: activity.revision, request_id: pending.request_id, ...fields};
        const result = await api(kind === "hint" ? "/api/steps/hint" : "/api/steps/answer", body);
        restoreDraft(result); feedback = result.feedback || null; pending = null; message = "原始输入和反馈已保存到当前档案。";
        await loadListing();
      } catch (e) {
        message = e.message;
        if (e.status === 409) { try { activity = await api(`/api/steps?activity_id=${encodeURIComponent(activity.id)}`); lastSaved = JSON.stringify({...emptyDraft(), ...activity.draft}); message += " 已读回当前记录，你的输入仍保留。"; } catch (_) {} }
      } finally { busy = false; draw(); notify(); root.querySelector(".step-feedback")?.focus(); }
    }
    function textInput(label, name, maxLength = 256) {
      const wrap = el("label", label), input = el("input"); input.type = "text"; input.id = "step-" + name; input.value = draft[name]; input.maxLength = maxLength; input.autocomplete = "off";
      input.addEventListener("input", () => { draft[name] = input.value; changed(); }); input.disabled = busy; wrap.append(input); return wrap;
    }
    function select(label, name, options) {
      const wrap = el("label", label), input = el("select"); input.id = "step-" + name;
      for (const [value, title] of options) { const o = el("option", title); o.value = value; input.append(o); }
      input.value = draft[name]; input.disabled = busy; input.addEventListener("change", () => { draft[name] = input.value; changed(); }); wrap.append(input); return wrap;
    }
    function draw() {
      root.replaceChildren(); root.classList.add("step-practice");
      root.append(el("h2", "方程分步练习"), el("p", "保留原始尝试，逐步核对解集和操作理由。这里的反馈用于修订，不自动确认掌握或关闭待解决事项。", "muted"));
      if (!listing) { root.append(el("p", message || "正在读取练习与档案……")); return; }
      const scope = el("details"); scope.append(el("summary", "支持范围与保存方式")); for (const line of listing.scope) scope.append(el("p", line)); root.append(scope);
      const choices = el("div", null, "actions");
      for (const c of listing.cases) { const b = button("新练习 · " + c.title, () => start(c.id)); b.dataset.stepCase = c.id; choices.append(b); } root.append(choices);
      if (listing.activities.length) {
        const history = el("details"); history.open = !activity; history.append(el("summary", `保存的活动（已列 ${listing.activities.length} / ${listing.total}）`));
        for (const a of listing.activities) history.append(button(`${a.title} · ${a.updated_at} · ${a.attempt_count}次输入${a.read_only ? " · 旧版只读" : ""}`, () => choose(a.id)));
        if (more) history.append(button("继续读取较早活动", async () => { try { await loadListing(listing.activities.length); draw(); } catch (e) { message = e.message; draw(); } })); root.append(history);
      }
      if (message) { const n = el("p", message, "muted"); n.setAttribute("role", "status"); root.append(n); }
      if (!activity) return;
      const header = el("section", null, "card"); header.append(el("h3", activity.configuration.title), el("p", `原方程：${activity.configuration.initial_equation}`), el("p", activity.configuration.prompt), el("p", `当前有效行：${activity.current_equation}`)); root.append(header);
      const log = el("ol"); log.dataset.stepHistory = "true";
      for (const row of activity.timeline) {
        const li = el("li", null, "card");
        if (row.kind === "hint") li.append(el("strong", `提示 ${row.level}`), el("p", row.text));
        else { li.append(el("strong", labels[row.feedback.status] || "输入记录"), el("p", row.kind === "conclusion" ? `我的结论：${({unique:"有唯一解",none:"无解",all:"任意实数都是解"})[row.conclusion] || row.conclusion}${row.value ? "，x=" + row.value : ""}` : `我的原始输入：${row.equation}`));
          if (row.kind === "equation") li.append(el("p", `所述操作：${listing.operation_labels[row.operation] || row.operation}${row.operand ? "；" + row.operand : ""}`));
          li.append(el("p", row.feedback.feedback), el("p", row.help_used ? "本次有提示、先前反馈或报告的帮助。" : "尚未记录先前反馈或额外帮助；这仍是指导练习。", "muted")); }
        log.append(li);
      } root.append(log);
      if (feedback) { const box = el("section", null, "card step-feedback"); box.tabIndex = -1; box.setAttribute("role", "status"); box.append(el("h3", labels[feedback.status] || "提示已记录"), el("p", feedback.feedback || feedback.text)); root.append(box); }
      if (activity.read_only) {
        root.append(el("p", activity.read_only_reason, "card"));
        if (draft.equation || draft.operand || draft.value || draft.conclusion !== "unique" || draft.used_help) { const archive = el("section", null, "card"); archive.append(el("h3", "原活动的未核对草稿"), el("p", `方程：${draft.equation}`), el("p", `所述操作：${activity.rules_snapshot.operations[draft.operation] || draft.operation}；操作数：${draft.operand}`), el("p", `解的情况：${({unique:"有唯一解",none:"无解",all:"任意实数都是解"})[draft.conclusion]}；结论数值：${draft.value}`), el("p", draft.used_help ? "已勾选使用额外帮助。" : "尚未勾选使用额外帮助。"), el("p", "以上是原样保存的草稿，没有重新判定。", "muted")); root.append(archive); }
        return;
      }
      if (activity.completed) { root.append(el("p", "本次结论和原方程检验已留下。你可以回看错步、另开练习；既有待解决事项仍然保留。", "card")); return; }
      const form = el("form", null, "card"); form.append(el("h3", "写下一步"), textInput("下一行完整方程（两边都写出，用一个等号连接）", "equation"), select("这一步所做的操作", "operation", Object.entries(listing.operation_labels)), textInput("两边同加、同减、同乘或同除的量（其他操作留空）", "operand", 128));
      const helpLabel = el("label"), help = el("input"); help.type = "checkbox"; help.id = "step-used-help"; help.checked = draft.used_help; help.disabled = busy; help.addEventListener("change", () => { draft.used_help = help.checked; changed(); }); helpLabel.append(help, document.createTextNode("这次还用了书本、他人提示或其他帮助")); form.append(helpLabel);
      const saved = el("p", draftStatus, "muted"); saved.dataset.stepDraftStatus = "true"; saved.setAttribute("role", "status");
      const retry = button("仅保存草稿（不核对）", saveDraft); retry.dataset.stepAction = "draft";
      const check = el("button", "核对并记录此步", "primary"); check.type = "submit"; check.disabled = busy; check.dataset.stepAction = "answer"; form.append(saved, retry, check); form.addEventListener("submit", e => { e.preventDefault(); submit("equation"); }); root.append(form);
      const hints = el("div", null, "actions"); for (const level of [1, 2, 3]) { const b = button(["提醒操作条件", "看当前式的化简", "看下一步示范"][level - 1], () => submit("hint", {level})); b.dataset.stepHint = level; hints.append(b); } root.append(hints);
      const conclusion = el("section", null, "card"); conclusion.append(el("h3", "检查原方程并记录结论"), select("我判断解的情况", "conclusion", [["unique", "有唯一解"], ["none", "无解"], ["all", "任意实数都是解"]]), textInput("若有唯一解，x 的值（数值或分数）", "value", 128));
      const finish = button("检验并保存结论", () => submit("conclusion")); finish.dataset.stepAction = "conclusion"; conclusion.append(finish); root.append(conclusion);
    }
    draw();
    try { await loadListing(); if (listing.activities.length) restoreDraft(await api(`/api/steps?activity_id=${encodeURIComponent(listing.activities[0].id)}`)); }
    catch (e) { message = e.message; } draw();
  }
  return { mount };
})();
