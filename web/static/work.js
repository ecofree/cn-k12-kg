/* Versioned learner drafts, reflections and self-assessment. No automatic grade. */
window.CNKGWork = (() => {
  const editors = new Set();
  const labels = { not_checked: "还没检查", met: "我已做到", partial: "还需补充", revisit: "需要重做" };
  const create = (tag, text, cls) => { const el = document.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el; };
  const identifier = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const attachmentTypes = new Set(["image/png", "image/jpeg", "image/webp", "audio/wav", "audio/webm", "audio/ogg", "audio/mp4"]);
  function renderAttachment(item, container) {
    if (!attachmentTypes.has(item.media_type) || typeof item.data_base64 !== "string") return;
    const row = create("figure", null, "work-attachment");
    const url = `data:${item.media_type};base64,${item.data_base64}`;
    if (item.media_type.startsWith("image/")) {
      const image = create("img"); image.src = url; image.alt = `保存的图稿：${item.filename}`; image.loading = "lazy"; row.append(image);
    } else {
      const recording = create("audio"); recording.src = url; recording.controls = true; recording.preload = "none"; row.append(recording);
    }
    const caption = create("figcaption", `${item.filename} · ${Math.ceil(item.byte_size / 1024)} KB`);
    const download = create("a", "下载这份原文件"); download.href = url; download.download = item.filename;
    row.append(caption, download); container.append(row); return row;
  }
  function statusBanner() {
    const pending = [...editors].filter(editor => editor.unsaved());
    const banner = document.getElementById("save-notice");
    if (!banner) return;
    banner.hidden = !pending.some(editor => editor.failed);
    if (!banner.hidden) document.getElementById("save-notice-text").textContent = `有 ${pending.length} 份记录尚未保存成功。请先在本页重试保存或处理版本冲突，再切换学习页面。`;
    for (const editor of editors) if (!editor.unsaved() && !editor.root.isConnected) editors.delete(editor);
  }
  async function mount(root, source, options = {}) {
    root.classList.add("work-editor");
    for (const input of Object.values(options.externalFields || {})) input.disabled = true;
    const loading = create("p", "正在恢复记录…", "muted"); root.append(loading);
    let data;
    try { data = await api("/api/work?" + new URLSearchParams(source)); }
    catch (error) {
      loading.textContent = `暂时无法恢复记录：${error.message}。恢复成功后才可填写，避免覆盖原有文字。`;
      loading.setAttribute("role", "alert");
      const retry = create("button", "重试恢复记录", "ghost"); retry.type = "button";
      retry.addEventListener("click", () => {
        root.replaceChildren();
        mount(root, source, options).catch(() => {});
      });
      root.append(retry); options.onError?.(error); throw error;
    }
    if (!root.isConnected) return null;
    loading.remove();
    const descriptor = data.descriptor, snapshot = descriptor.resource_snapshot;
    const draftOnly = snapshot.draft_only === true;
    if (!options.hidePrompt && ["updated", "legacy_unverified"].includes(data.current_content?.status)) {
      const update = create("aside", null, "work-version-notice");
      update.append(create("p", data.current_content.status === "legacy_unverified"
        ? "这份旧作品缺少可核对的逐题版本信息，暂时无法确认它与当前材料是否相同。这里保留已保存的原版本内容与修订过程；旧的待确认事项继续保留。"
        : "当前可用版本与这份作品的保存版本不同。这里保留已保存的原版本内容与修订过程；旧的待确认事项继续保留。"));
      if (data.current_content.url) {
        const link = create("a", "查看当前材料（新标签页）");
        link.href = data.current_content.url; link.target = "_blank"; link.rel = "noopener";
        update.append(link);
      }
      root.append(update);
    } else if (!options.hidePrompt && data.current_content?.status === "unavailable") {
      root.append(create("p", "当前目录中未找到这份作品对应的材料。以下继续显示已保存的原版本，历史过程和待确认事项仍然保留。", "work-version-notice"));
    }
    if (!options.hidePrompt && snapshot.diagram) {
      const asset = snapshot.asset_snapshots?.[snapshot.diagram.src];
      if (asset?.body_base64) {
        const figure = create("figure", null, "lesson-diagram"), image = create("img");
        image.src = "data:image/svg+xml;base64," + asset.body_base64;
        image.alt = snapshot.diagram.alt || "这份原作品使用的数学图示";
        figure.append(image, create("figcaption", image.alt, "muted")); root.append(figure);
      } else root.append(create("p", "这份作品未保存可恢复的原图示，无法还原；这里不会用当前图示替换。", "muted work-snapshot-notice"));
    }
    const saved = data.work?.payload || {};
    let attachments = [...(saved.attachments || [])], attachmentRead = null;
    const fields = new Map();
    const referenceStatus = create("p", "", "muted work-reference-status"); referenceStatus.setAttribute("role", "status");
    const support = snapshot.difficulty_support;
    const supportPanel = support ? create("details", null, "goal-support") : null;
    let hintPanel = null;
    if (supportPanel) supportPanel.append(create("summary", "遇到困难时，先辨别再修订"),
      create("p", "展开前会先保存当前思路。辨别和重新尝试分别保留，可以回看自己的变化。", "muted"));
    for (const field of snapshot.fields) {
      let input = options.externalFields?.[field.id];
      if (!input) {
        const group = create("div", null, "work-field"), label = create("label", field.label);
        input = create("textarea"); input.rows = options.compact ? 4 : 6; input.maxLength = 16000;
        input.id = `work-${identifier()}`; label.htmlFor = input.id;
        if (field.prompt && (!options.hidePrompt || field.phase)) { const prompt = create("div", null, "work-prompt"); window.setRichText(prompt, field.prompt); group.append(prompt); }
        group.append(label, input);
        if (field.deliverable) group.append(create("p", "这一步留下：" + field.deliverable, "muted"));
        if (field.phase && supportPanel) {
          if (!supportPanel.parentNode) root.append(supportPanel);
          if (field.phase === "revision" && support.hint) {
            hintPanel = create("details", null, "goal-support-hint"); hintPanel.append(create("summary", "需要时看这一步提示"));
            const hint = create("div"); window.setRichText(hint, support.hint); hintPanel.append(hint); supportPanel.append(hintPanel);
          }
          supportPanel.append(group);
        } else root.append(group);
      }
      input.value = saved.draft?.[field.id] ?? field.initial_value ?? "";
      input.disabled = Boolean(options.disabled);
      fields.set(field.id, input);
    }
    let referencePanel = null;
    if (!options.hidePrompt && snapshot.reference_answer) {
      referencePanel = create("details", null, "work-frozen-reference");
      referencePanel.append(create("summary", "查看这份作品原版本的参考过程"));
      const reference = create("div"); window.setRichText(reference, snapshot.reference_answer); referencePanel.append(reference);
      if (snapshot.observation_note) referencePanel.append(create("p", snapshot.observation_note, "muted"));
      root.append(referencePanel);
    }
    if (!options.hidePrompt && descriptor.kind === "self_check") {
      const missing = [];
      if (!snapshot.fields.some(field => field.prompt)) missing.push("原题面");
      if (!snapshot.criteria.length) missing.push("原量规");
      if (!support?.hint) missing.push("原提示");
      if (!snapshot.reference_answer) missing.push("原参考答案");
      if (missing.length) root.append(create("p", `这里只显示当时实际保存的内容。这份作品未保存${missing.join("、")}，无法还原，也不会用当前材料补写。`, "muted work-snapshot-notice"));
    }
    root.append(referenceStatus);
    const attachmentPanel = create("details", null, "work-attachments");
    attachmentPanel.append(create("summary", "保存图稿、操作照片或口头说明录音"),
      create("p", "文件与本次作品一起保存，可随修订查看和随学习档案备份。文件只作为待查看的过程证据，不自动评分。", "muted"),
      create("p", "支持 PNG、JPEG、WebP 图片及 WAV、WebM、Ogg、MP4 录音；每个文件最多500 KB，合计750 KB，最多6个。", "muted"));
    const attachmentField = create("select"), attachmentFieldLabel = create("label", "这份文件对应哪项过程");
    attachmentField.id = `attachment-field-${identifier()}`; attachmentFieldLabel.htmlFor = attachmentField.id;
    for (const field of snapshot.fields) { const option = create("option", field.label); option.value = field.id; attachmentField.append(option); }
    const fileInput = create("input"); fileInput.type = "file"; fileInput.multiple = true; fileInput.accept = [...attachmentTypes].join(",");
    fileInput.id = `attachment-file-${identifier()}`; const fileLabel = create("label", "选择图稿或录音文件"); fileLabel.htmlFor = fileInput.id;
    const attachmentMessage = create("p", "", "muted attachment-status"); attachmentMessage.setAttribute("role", "status");
    const attachmentList = create("div", null, "work-attachment-list");
    attachmentPanel.append(attachmentFieldLabel, attachmentField, fileLabel, fileInput, attachmentMessage, attachmentList);
    if (!draftOnly) root.append(attachmentPanel);
    const reflection = create("textarea"); reflection.rows = 3; reflection.maxLength = 8000;
    reflection.id = `reflection-${identifier()}`; reflection.value = saved.reflection || "";
    const self = create("details", null, "self-assessment"); self.append(create("summary", "我的自评与反思"));
    self.append(create("p", "对照这些检查点记录自己的判断，之后还可以修改。自评不会改变原题的判分。", "muted"));
    const selections = [], comments = [];
    for (const [index, criterion] of snapshot.criteria.entries()) {
      const row = create("div", null, "self-assessment-row"), label = create("label", criterion), select = create("select");
      select.id = `criterion-${identifier()}`; label.htmlFor = select.id;
      for (const [value, text] of Object.entries(labels)) { const option = create("option", text); option.value = value; select.append(option); }
      const previous = saved.self_assessment?.find(item => item.criterion_index === index);
      select.value = previous?.status || "not_checked";
      const detail = create("details", null, "self-assessment-comment"); detail.append(create("summary", "补充自评说明（可选）"));
      const comment = create("textarea"); comment.rows = 2; comment.maxLength = 2000;
      comment.id = `comment-${identifier()}`; comment.value = previous?.comment || "";
      const commentLabel = create("label", `关于“${criterion}”的说明`); commentLabel.htmlFor = comment.id;
      detail.append(commentLabel, comment); detail.open = Boolean(comment.value);
      row.append(label, select, detail); self.append(row); selections.push(select); comments.push(comment);
    }
    const reflectionLabel = create("label", "我改进了什么？还有什么问题？"); reflectionLabel.htmlFor = reflection.id;
    self.append(reflectionLabel, reflection); if (!draftOnly) root.append(self);
    const controls = create("div", null, "actions"), save = create("button", "保存记录", "ghost"), finish = create("button", "提交我的自查", "ghost");
    save.type = finish.type = "button";
    const status = create("p", data.work ? "已恢复保存的记录。" : "填写后会自动保存。", "muted work-status"); status.setAttribute("role", "status");
    const conflict = create("button", "保留两版并保存本页内容", "ghost"); conflict.type = "button"; conflict.hidden = true;
    controls.append(save); if (!draftOnly) controls.append(finish); controls.append(conflict); root.append(controls); root.prepend(status);
    if (draftOnly) root.append(create("p", "这里保存的是尚未提交的草稿及其修订。点击“记录这一步”后，正式作答另行保存；修改草稿不会改写已提交的答案。", "muted"));
    let revision = data.work?.revision || 0, learnerStatus = saved.status || "draft";
    root.dataset.resourceId = descriptor.resource_id; root.dataset.revision = String(revision);
    let pending = null, timer = null, running = null, lastSaved = null, failed = false, conflictSeen = false;
    const values = () => ({ draft: Object.fromEntries([...fields].map(([key, input]) => [key, input.value])),
      reflection: reflection.value,
      self_assessment: selections.map((select, index) => ({ criterion_index: index, status: select.value, comment: comments[index].value })).filter(row => row.status !== "not_checked" || row.comment),
      status: learnerStatus, attachments: [...attachments] });
    lastSaved = JSON.stringify(values());
    const editor = { root, descriptor, get failed() { return failed; }, unsaved: () => Boolean(attachmentRead) || pending !== null || JSON.stringify(values()) !== lastSaved,
      value: key => fields.get(key)?.value || "", save: () => persist() };
    editors.add(editor);
    async function persist() {
      clearTimeout(timer);
      if (attachmentRead) await attachmentRead;
      if (running) { await running; if (!failed && editor.unsaved()) return persist(); return; }
      if (!editor.unsaved() || conflictSeen) return;
      if (!pending) pending = { ...source, resource_id: descriptor.resource_id, expected_revision: revision, request_id: identifier(), ...values() };
      const request = pending;
      const sent = JSON.stringify({ draft: request.draft, reflection: request.reflection, self_assessment: request.self_assessment, status: request.status, attachments: request.attachments });
      // Keep explicit save/submit intent available while an autosave is in flight.
      // persist() serializes requests; disabling a button mid-pointer click can swallow it.
      controls.setAttribute("aria-busy", "true"); status.textContent = "正在保存…";
      running = (async () => {
        try {
          const response = await api("/api/work", request);
          revision = response.work.revision; pending = null; lastSaved = sent; failed = false;
          status.textContent = request.status === "submitted" ? "自查记录已保存，仍可继续修订。" : "已保存。";
          root.dataset.resourceId = descriptor.resource_id; root.dataset.revision = String(revision);
        } catch (error) {
          failed = true; conflictSeen = error.status === 409; conflict.hidden = !conflictSeen;
          status.textContent = conflictSeen ? "另一处已保存较新的版本。本页文字保留，旧版和新版都可以查阅。" : `尚未保存：${error.message}`;
        } finally { controls.setAttribute("aria-busy", "false"); running = null; statusBanner(); }
      })();
      await running;
      if (!failed && editor.unsaved()) return persist();
    }
    function guardReference(panel, key) {
      if (!panel) return;
      let opening = false;
      panel.querySelector("summary").addEventListener("click", async event => {
        if (panel.open) return;
        event.preventDefault(); if (opening) return; opening = true;
        referenceStatus.textContent = "正在保存当前过程与帮助记录…";
        try {
          if (!await flushAll()) throw new Error("当前过程还未保存，请先重试保存，再展开帮助");
          const record = await api("/api/work/reference", { ...source, resource_id: descriptor.resource_id, reference_key: key });
          if (!record.recorded || record.resource_version !== descriptor.resource_version) throw new Error("参考内容版本未核对成功");
          // Editing remains available during the request. Save those later
          // changes too; a failed or conflicting write must keep help closed.
          if (!await flushAll()) throw new Error("当前过程还未保存，请先重试保存，再展开帮助");
          panel.open = true; referenceStatus.textContent = "已记录这次帮助；后续仍需结合独立尝试判断。";
        } catch (error) { referenceStatus.textContent = error.message; }
        finally { opening = false; }
      });
    }
    guardReference(supportPanel, "difficulty"); guardReference(hintPanel, "hint"); guardReference(referencePanel, "answer");
    function changed() { learnerStatus = "draft"; clearTimeout(timer); status.textContent = "有修改，等待保存…"; timer = setTimeout(persist, 500); }
    function showAttachments() {
      attachmentList.replaceChildren();
      for (const item of attachments) {
        const row = renderAttachment(item, attachmentList); if (!row) continue;
        const field = snapshot.fields.find(field => field.id === item.field_id);
        row.append(create("p", `对应：${field?.label || "原过程项"}`, "muted"));
        const remove = create("button", "从本次修订移除", "ghost"); remove.type = "button";
        remove.addEventListener("click", () => { attachments = attachments.filter(value => value !== item); showAttachments(); changed(); }); row.append(remove);
      }
    }
    showAttachments();
    fileInput.addEventListener("change", () => {
      if (attachmentRead) return;
      const selected = [...fileInput.files], fieldId = attachmentField.value;
      attachmentMessage.textContent = "正在读取原文件…"; fileInput.disabled = true;
      attachmentRead = (async () => {
        if (attachments.length + selected.length > 6) throw new Error("每份作品最多6个附件，已有记录保留。");
        const additions = [];
        for (const file of selected) {
          let mediaType = file.type.split(";", 1)[0]; if (mediaType === "audio/x-wav") mediaType = "audio/wav";
          if (!attachmentTypes.has(mediaType) || !file.size || file.size > 512000) throw new Error(`${file.name}的类型或大小不符合要求，已有记录保留。`);
          const bytes = new Uint8Array(await file.arrayBuffer());
          if (!globalThis.crypto?.subtle) throw new Error("当前浏览器无法核对文件，请从本机服务地址打开后重试；原文字仍可保存。");
          const sha256 = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(value => value.toString(16).padStart(2, "0")).join("");
          if ([...attachments, ...additions].some(item => item.field_id === fieldId && item.sha256 === sha256)) throw new Error("该过程项已经保存相同文件。");
          let binary = ""; for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
          additions.push({ field_id: fieldId, filename: file.name, media_type: mediaType, byte_size: bytes.length, sha256, data_base64: btoa(binary) });
        }
        if ([...attachments, ...additions].reduce((sum, item) => sum + item.byte_size, 0) > 768000) throw new Error("合计附件大小超过750 KB，已有记录保留。");
        attachments = [...attachments, ...additions]; showAttachments(); changed();
        attachmentMessage.textContent = "原文件已读入，正在随本次修订保存。";
      })().catch(error => { attachmentMessage.textContent = error.message; })
        .finally(() => { attachmentRead = null; fileInput.disabled = false; fileInput.value = ""; });
    });
    for (const input of [...fields.values(), ...comments, reflection]) input.addEventListener("input", changed);
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
          for (const attachment of record.payload.attachments || []) renderAttachment(attachment, item);
          for (const row of record.payload.self_assessment || []) {
            item.append(create("p", `${snapshot.criteria[row.criterion_index]}：${labels[row.status]}`));
            if (row.comment) item.append(create("p", row.comment, "preserve-lines"));
          }
          historyList.append(item);
        }
        if (result.revisions.length === 50) historyList.append(create("p", "这里显示最近50版，完整历史包含在学习记录导出中。", "muted"));
      } catch (error) { historyList.textContent = error.message; }
    });
    options.onReady?.(editor);
    return editor;
  }
  async function flushAll() {
    await Promise.allSettled([...editors].map(editor => editor.save())); statusBanner();
    return ![...editors].some(editor => editor.unsaved());
  }
  addEventListener("online", flushAll);
  addEventListener("beforeunload", event => { if ([...editors].some(editor => editor.unsaved())) { event.preventDefault(); event.returnValue = ""; } });
  function registerActivity(editor) {
    if (!editor?.root || typeof editor.unsaved !== "function" || typeof editor.save !== "function") throw new Error("活动保存接口不完整");
    editors.add(editor); statusBanner(); return statusBanner;
  }
  return { mount, flushAll, registerActivity, hasUnsaved: () => [...editors].some(editor => editor.unsaved()) };
})();
