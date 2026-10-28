/* Objective help deliberately stays separate from formal unit assessment. */
window.CNKGSupport = (() => {
  function mount(root, initial, ui) {
    const { node, rich, button, api, diagram } = ui;
    let data = initial, busy = false;
    const drafts = new Map();
    function draw(feedback) {
      root.replaceChildren();
      const support = data.support;
      let draftReady = data.done;
      const hintControls = [];
      root.append(node("h2", data.session.title), rich("p", support.observation_note), node("p", data.notice, "muted"));
      const history = node("details", null, "card"); history.append(node("summary", "本次辨别与练习记录"));
      for (const answer of data.answers || []) {
        history.append(rich("p", answer.prompt), node("p", `我的作答：${answer.raw_answer || "暂时不会"}`),
          rich("p", answer.feedback || answer.explanation));
      }
      if ((data.answers || []).length) root.append(history);
      if (feedback) {
        const box = node("section", null, "card support-feedback"); box.setAttribute("role", "status");
        const labels = { correct: "这一步答对了", wrong: "再核对这个区别", not_attempted: "可以先用提示帮助理解", needs_review: "过程已记录，仍需按量规检查", invalid: "请检查答案格式" };
        box.append(node("h3", labels[feedback.status] || "这一步的提示"), rich("p", feedback.feedback || feedback.explanation || feedback.reason));
        root.append(box);
      }
      if (support.route) {
        const route = node("section", null, "card support-route");
        route.dataset.routeId = support.route.route_id;
        route.append(node("h3", `这次的指导：${support.route.title}`),
          rich("p", support.route.selection_reason), rich("p", support.route.observation_note),
          node("p", "这是根据本次选择安排的下一步，仍需观察后面的表现；没有据此认定固定的错误原因。", "muted"));
        root.append(route);
      }
      if (support.step > 0) {
        const hints = node("section", null, "card"); hints.append(node("h3", "按需要打开提示"));
        const opened = node("div");
        opened.setAttribute("aria-live", "polite");
        const hintRegions = new Map();
        for (const level of [1, 2, 3]) {
          const region = node("div", null, "support-hint"); region.hidden = true;
          region.id = `support-hint-${data.session.id}-${level}`; region.dataset.hintLevel = String(level);
          hintRegions.set(level, region); opened.append(region);
        }
        function renderHints() {
          for (const hint of support.hints_opened || []) {
            const region = hintRegions.get(hint.level);
            if (!region) continue;
            region.replaceChildren(node("strong", `提示 ${hint.level}`), rich("p", hint.text));
            if (hint.branch_text) region.append(rich("p", hint.branch_text));
            region.hidden = false;
          }
          for (const control of hintControls) control.setAttribute("aria-expanded", String(
            (support.hints_opened || []).some(hint => String(hint.level) === control.dataset.hintLevel)));
        }
        renderHints();
        const actions = node("div", null, "actions");
        for (const [level, label] of [[1, "提示1 · 提醒线索"], [2, "提示2 · 分步引导"], [3, "提示3 · 查看例子"]]) {
          const control = button(label, async () => {
            if (busy || !draftReady) return; busy = true; ui.busy(true); control.setAttribute("aria-busy", "true");
            try {
              if (!await window.CNKGWork.flushAll()) throw new Error("当前草稿还未保存，请先重试保存或处理版本冲突，再打开提示。");
              const response = await api("/api/support/hint", { session_id: data.session.id, level });
              // A learner can continue writing while this request is pending.
              // Keep the hint closed if those later edits fail to persist.
              if (!await window.CNKGWork.flushAll()) throw new Error("当前草稿还未保存，请先重试保存或处理版本冲突，再打开提示。");
              support.hints_opened = response.support.hints_opened; renderHints();
            } catch (error) { ui.notice(error.message); }
            finally { busy = false; ui.busy(false); control.setAttribute("aria-busy", "false"); control.disabled = !draftReady || Boolean(data.session.resume_blocked); }
          });
          control.disabled = !draftReady || Boolean(data.session.resume_blocked); control.dataset.hintLevel = String(level);
          control.setAttribute("aria-controls", hintRegions.get(level).id);
          control.setAttribute("aria-expanded", String((support.hints_opened || []).some(hint => hint.level === level)));
          hintControls.push(control); actions.append(control);
        }
        hints.append(actions, opened); root.append(hints);
      }
      if (data.done) {
        const last = data.answers.at(-1), card = node("section", null, "card");
        const confirmed = last?.status === "correct" && last?.confirmation_eligible !== false;
        card.append(node("h3", confirmed ? "已留下本次帮助后的确认记录" : "这一处还需要继续处理"),
          node("p", last?.confirmation_notice || (confirmed ? "可以回到未解决事项查看具体变化。其他目标、过程修订和后续复测会分别保留。" : "原来的困难仍在学习事项中。可以回看记录与提示，随后继续练习或修订。")),
          button("查看未解决事项", ui.openNeeds), button("回看本单元", () => ui.openUnit(data.session.unit_id)));
        if (!confirmed) card.append(button("关联原事项，重新辨别并练习", async () => {
          if (busy) return;
          await ui.openSupport(data.session.unit_id, data.support.id, data.session.retry_need_ids || []);
        }));
        root.append(card); return;
      }
      const task = data.item, card = node("section", null, "card support-task");
      card.dataset.taskId = task.id;
      card.append(node("h3", support.step_kind === "guided" && support.route ? `针对这一步再练：${support.route.title}` :
        ["先辨别一下", "跟着做一步", "换个任务再确认"][support.step]), rich("p", task.prompt));
      if (task.diagram) card.append(diagram(task.diagram));
      let selected = drafts.get(task.id) || "", control;
      const choiceInputs = [];
      if (task.type === "choice") {
        control = node("input"); control.type = "hidden"; control.hidden = true; control.value = selected; card.append(control);
        const choices = node("fieldset", null, "choices"); choices.append(node("legend", "选择一个答案", "sr-only"));
        for (const choice of task.choices) {
          const label = node("label", null, "support-choice"), input = node("input"); input.type = "radio"; input.name = "support-choice"; input.value = choice.id; input.checked = selected === choice.id;
          input.addEventListener("change", () => {
            selected = choice.id; drafts.set(task.id, selected); control.value = selected;
            control.dispatchEvent(new Event("input", { bubbles: true }));
          });
          choiceInputs.push(input);
          label.append(input, rich("span", choice.text)); choices.append(label);
        } card.append(choices);
      } else {
        const label = node("label", task.type === "rubric" ? "写下过程与依据" : "你的答案");
        control = node(task.type === "rubric" ? "textarea" : "input"); control.id = "support-answer"; control.value = selected; control.maxLength = 8000;
        if (task.type === "rubric") control.rows = 5;
        label.htmlFor = control.id; control.addEventListener("input", () => { selected = control.value; drafts.set(task.id, selected); }); card.append(label, control);
        if (task.rubric) card.append(ui.list(task.rubric.map(r => r.criterion)));
      }
      const message = node("p", "", "muted"); message.setAttribute("role", "status");
      const draftRoot = node("div", null, "support-draft");
      function editing(enabled) {
        control.disabled = !enabled;
        for (const input of choiceInputs) input.disabled = !enabled;
      }
      async function submit(skipped) {
        if (busy || !draftReady) return;
        if (!skipped && !selected.trim()) { message.textContent = "先填写答案，或选择暂时不会。"; (choiceInputs[0] || control).focus(); return; }
        busy = true; ui.busy(true); submitButton.disabled = skip.disabled = true; editing(false);
        try {
          if (!await window.CNKGWork.flushAll()) throw new Error("当前草稿还未保存，请先重试保存或处理版本冲突；本题尚未提交。");
          const response = await api("/api/support/answer", { session_id: data.session.id, question_id: task.id, answer: selected, skipped });
          data = response; draw(response.feedback);
          const heading = root.querySelector(".support-feedback h3") || root.querySelector("h2"); heading.tabIndex = -1; heading.focus();
        } catch (error) { message.textContent = error.message; }
        finally {
          busy = false; ui.busy(false);
          const enabled = draftReady && !data.session.resume_blocked;
          submitButton.disabled = skip.disabled = !enabled; editing(enabled);
        }
      }
      const submitButton = button("记录这一步", () => submit(false), "primary"), skip = button("暂时不会", () => submit(true));
      submitButton.disabled = skip.disabled = true; editing(false);
      card.append(submitButton, skip, message, draftRoot); root.append(card);
      window.CNKGWork.mount(draftRoot, { kind: "session_task", session_id: data.session.id, question_id: task.id }, {
        hidePrompt: true, compact: true, externalFields: { response: control }, disabled: Boolean(data.session.resume_blocked),
        onReady(editor) {
          selected = editor.value("response"); drafts.set(task.id, selected);
          for (const input of choiceInputs) input.checked = input.value === selected;
          draftReady = true;
          const enabled = !data.session.resume_blocked;
          submitButton.disabled = skip.disabled = !enabled; editing(enabled);
          for (const hint of hintControls) hint.disabled = !enabled;
        },
      }).catch(() => {});
      if (data.session.resume_blocked) { submitButton.disabled = skip.disabled = true; message.textContent = "保存的内容校验未通过，原记录保留；请回本单元重新开始。"; }
    }
    draw();
  }
  return { mount };
})();
