/* Objective help deliberately stays separate from formal unit assessment. */
window.CNKGSupport = (() => {
  function mount(root, initial, ui) {
    const { node, rich, button, api, diagram } = ui;
    let data = initial, busy = false;
    const drafts = new Map();
    function draw(feedback) {
      root.replaceChildren();
      const support = data.support;
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
      if (support.step > 0) {
        const hints = node("section", null, "card"); hints.append(node("h3", "按需要打开提示"));
        const opened = node("div");
        function renderHints() {
          opened.replaceChildren();
          for (const hint of support.hints_opened || []) {
            opened.append(node("strong", `提示 ${hint.level}`), rich("p", hint.text));
            if (hint.branch_text) opened.append(rich("p", hint.branch_text));
          }
        }
        renderHints();
        const actions = node("div", null, "actions");
        for (const [level, label] of [[1, "提示1 · 提醒线索"], [2, "提示2 · 分步引导"], [3, "提示3 · 查看例子"]]) {
          const control = button(label, async () => {
            if (busy) return; busy = true; ui.busy(true); control.disabled = true;
            try {
              const response = await api("/api/support/hint", { session_id: data.session.id, level });
              support.hints_opened = response.support.hints_opened; renderHints();
            } catch (error) { ui.notice(error.message); }
            finally { busy = false; ui.busy(false); control.disabled = false; }
          }); actions.append(control);
        }
        hints.append(actions, opened); root.append(hints);
      }
      if (data.done) {
        const last = data.answers.at(-1), card = node("section", null, "card");
        card.append(node("h3", last?.status === "correct" ? "已留下本次帮助后的确认记录" : "这一处还需要继续处理"),
          node("p", last?.status === "correct" ? "可以回到未解决事项查看具体变化。其他目标、过程修订和后续复测会分别保留。" : "原来的困难仍在学习事项中。可以回看记录与提示，随后继续练习或修订。"),
          button("查看未解决事项", ui.openNeeds), button("回看本单元", () => ui.openUnit(data.session.unit_id)));
        root.append(card); return;
      }
      const task = data.item, card = node("section", null, "card support-task");
      card.append(node("h3", ["先辨别一下", "跟着做一步", "换个任务再确认"][support.step]), rich("p", task.prompt));
      if (task.diagram) card.append(diagram(task.diagram));
      let selected = drafts.get(task.id) || "", control;
      if (task.type === "choice") {
        const choices = node("fieldset", null, "choices"); choices.append(node("legend", "选择一个答案", "sr-only"));
        for (const choice of task.choices) {
          const label = node("label", null, "support-choice"), input = node("input"); input.type = "radio"; input.name = "support-choice"; input.value = choice.id; input.checked = selected === choice.id;
          input.addEventListener("change", () => { selected = choice.id; drafts.set(task.id, selected); });
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
      async function submit(skipped) {
        if (busy) return;
        if (!skipped && !selected.trim()) { message.textContent = "先填写答案，或选择暂时不会。"; control?.focus(); return; }
        busy = true; ui.busy(true); submitButton.disabled = skip.disabled = true;
        try {
          const response = await api("/api/support/answer", { session_id: data.session.id, question_id: task.id, answer: selected, skipped });
          data = response; draw(response.feedback);
          const heading = root.querySelector(".support-feedback h3, h2"); heading.tabIndex = -1; heading.focus();
        } catch (error) { message.textContent = error.message; }
        finally { busy = false; ui.busy(false); submitButton.disabled = skip.disabled = false; }
      }
      const submitButton = button("记录这一步", () => submit(false), "primary"), skip = button("暂时不会", () => submit(true));
      card.append(submitButton, skip, message); root.append(card);
      if (data.session.resume_blocked) { submitButton.disabled = skip.disabled = true; message.textContent = "保存的内容校验未通过，原记录保留；请回本单元重新开始。"; }
    }
    draw();
  }
  return { mount };
})();
