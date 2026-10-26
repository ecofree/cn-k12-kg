/* Versioned learner drafts, reflections and self-assessment. No automatic grade. */
window.CNKGWork = (() => {
  const editors = new Set();
  const labels = { not_checked: "还没检查", met: "我已做到", partial: "还需补充", revisit: "需要重做" };
  const create = (tag, text, cls) => { const el = document.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el; };
  const identifier = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function statusBanner() {
    const pending = [...editors].filter(editor => editor.unsaved());
    const banner = document.getElementById("save-notice");
    if (!banner) return;
    banner.hidden = !pending.some(editor => editor.failed);
    if (!banner.hidden) document.getElementById("save-notice-text").textContent = `有 ${pending.length} 份记录尚未保存成功。文字仍留在本页，可以重试保存。`;
    for (const editor of editors) if (!editor.unsaved() && !editor.root.isConnected) editors.delete(editor);
  }
  async function mount(root, source, options = {}) {
    root.classList.add("work-editor");
    const loading = create("p", "正在恢复记录…", "muted"); root.append(loading);
    let data;
    try { data = await api("/api/work?" + new URLSearchParams(source)); }
    catch (error) { loading.textContent = error.message; throw error; }
    loading.remove();
    const descriptor = data.descriptor, snapshot = descriptor.resource_snapshot;
    const saved = data.work?.payload || {};
    const fields = new Map();
    for (const field of snapshot.fields) {
      let input = options.externalFields?.[field.id];
      if (!input) {
        const group = create("div", null, "work-field"), label = create("label", field.label);
        input = create("textarea"); input.rows = options.compact ? 4 : 6; input.maxLength = 16000;
        input.id = `work-${identifier()}`; label.htmlFor = input.id;
        if (field.prompt && !options.hidePrompt) { const prompt = create("div", null, "work-prompt"); window.setRichText(prompt, field.prompt); group.append(prompt); }
        group.append(label, input);
        if (field.deliverable) group.append(create("p", "这一步留下：" + field.deliverable, "muted"));
        root.append(group);
      }
      input.value = saved.draft?.[field.id] ?? field.initial_value ?? "";
      fields.set(field.id, input);
    }
    const reflection = create("textarea"); reflection.rows = 3; reflection.maxLength = 8000;
    reflection.id = `reflection-${identifier()}`; reflection.value = saved.reflection || "";
    const self = create("details", null, "self-assessment"); self.append(create("summary", "我的自评与反思"));
    self.append(create("p", "对照这些检查点记录自己的判断，之后还可以修改。自评不会改变原题的判分。", "muted"));
    const selections = [];
    for (const [index, criterion] of snapshot.criteria.entries()) {
      const row = create("div", null, "self-assessment-row"), label = create("label", criterion), select = create("select");
      select.id = `criterion-${identifier()}`; label.htmlFor = select.id;
      for (const [value, text] of Object.entries(labels)) { const option = create("option", text); option.value = value; select.append(option); }
      select.value = saved.self_assessment?.find(item => item.criterion_index === index)?.status || "not_checked";
      row.append(label, select); self.append(row); selections.push(select);
    }
    const reflectionLabel = create("label", "我改进了什么？还有什么问题？"); reflectionLabel.htmlFor = reflection.id;
    self.append(reflectionLabel, reflection); root.append(self);
    const controls = create("div", null, "actions"), save = create("button", "保存记录", "ghost"), finish = create("button", "提交我的自查", "ghost");
    save.type = finish.type = "button";
    const status = create("p", data.work ? "已恢复保存的记录。" : "填写后会自动保存。", "muted work-status"); status.setAttribute("role", "status");
    const conflict = create("button", "保留两版并保存本页内容", "ghost"); conflict.type = "button"; conflict.hidden = true;
    controls.append(save, finish, conflict); root.append(controls, status);
    let revision = data.work?.revision || 0, learnerStatus = saved.status || "draft";
    let pending = null, timer = null, running = null, lastSaved = null, failed = false, conflictSeen = false;
    const values = () => ({ draft: Object.fromEntries([...fields].map(([key, input]) => [key, input.value])),
      reflection: reflection.value,
      self_assessment: selections.map((select, index) => ({ criterion_index: index, status: select.value })).filter(row => row.status !== "not_checked"),
      status: learnerStatus });
    lastSaved = JSON.stringify(values());
    const editor = { root, descriptor, get failed() { return failed; }, unsaved: () => pending !== null || JSON.stringify(values()) !== lastSaved,
      value: key => fields.get(key)?.value || "", save: () => persist() };
    editors.add(editor);
    async function persist() {
      clearTimeout(timer);
      if (running) { await running; if (!failed && editor.unsaved()) return persist(); return; }
      if (!editor.unsaved() || conflictSeen) return;
      if (!pending) pending = { ...source, resource_id: descriptor.resource_id, expected_revision: revision, request_id: identifier(), ...values() };
      const request = pending;
      const sent = JSON.stringify({ draft: request.draft, reflection: request.reflection, self_assessment: request.self_assessment, status: request.status });
      save.disabled = finish.disabled = true; status.textContent = "正在保存…";
      running = (async () => {
        try {
          const response = await api("/api/work", request);
          revision = response.work.revision; pending = null; lastSaved = sent; failed = false;
          status.textContent = request.status === "submitted" ? "自查记录已保存，仍可继续修订。" : "已保存。";
          root.dataset.resourceId = descriptor.resource_id; root.dataset.revision = String(revision);
        } catch (error) {
          failed = true; conflictSeen = error.status === 409; conflict.hidden = !conflictSeen;
          status.textContent = conflictSeen ? "另一处已保存较新的版本。本页文字保留，旧版和新版都可以查阅。" : `尚未保存：${error.message}`;
        } finally { save.disabled = finish.disabled = false; running = null; statusBanner(); }
      })();
      await running;
      if (!failed && editor.unsaved()) return persist();
    }
    function changed() { learnerStatus = "draft"; clearTimeout(timer); status.textContent = "有修改，等待保存…"; timer = setTimeout(persist, 500); }
    for (const input of [...fields.values(), reflection]) input.addEventListener("input", changed);
    for (const select of selections) select.addEventListener("change", changed);
    save.addEventListener("click", persist);
    finish.addEventListener("click", () => { learnerStatus = "submitted"; persist(); });
    conflict.addEventListener("click", async () => {
      try {
        const fresh = await api("/api/work?" + new URLSearchParams({ resource_id: descriptor.resource_id }));
        revision = fresh.work.revision; pending = null; conflictSeen = false; failed = false; conflict.hidden = true;
        lastSaved = null; await persist();
      } catch (error) { status.textContent = error.message; }
    });
    const history = create("details", null, "work-revisions"); history.append(create("summary", "查看已保存的历史版本"));
    const historyList = create("div"); history.append(historyList); root.append(history);
    history.addEventListener("toggle", async () => {
      if (!history.open) return;
      if (!revision) { historyList.textContent = "填写并保存后，这里会保留每次修订。"; return; }
      historyList.textContent = "正在读取…";
      try {
        const result = await api("/api/work/revisions?" + new URLSearchParams({ resource_id: descriptor.resource_id, limit: "50" }));
        historyList.textContent = "";
        for (const record of result.revisions) {
          const item = create("details", null, "answer-entry"); item.append(create("summary", `第 ${record.revision} 版 · ${formatTime(record.updated_at)}`));
          for (const field of snapshot.fields) item.append(create("strong", field.label), create("p", record.payload.draft?.[field.id] || "尚未填写", "preserve-lines"));
          if (record.payload.reflection) item.append(create("p", record.payload.reflection, "preserve-lines"));
          for (const row of record.payload.self_assessment || []) item.append(create("p", `${snapshot.criteria[row.criterion_index]}：${labels[row.status]}`));
          historyList.append(item);
        }
        if (result.revisions.length === 50) historyList.append(create("p", "这里显示最近50版，完整历史包含在学习记录导出中。", "muted"));
      } catch (error) { historyList.textContent = error.message; }
    });
    return editor;
  }
  async function flushAll() { await Promise.allSettled([...editors].map(editor => editor.save())); statusBanner(); }
  addEventListener("online", flushAll);
  addEventListener("beforeunload", event => { if ([...editors].some(editor => editor.unsaved())) { event.preventDefault(); event.returnValue = ""; } });
  return { mount, flushAll, hasUnsaved: () => [...editors].some(editor => editor.unsaved()) };
})();
