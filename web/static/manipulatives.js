window.CNKGManipulatives = (() => {
  async function mount(root, unit, ui) {
    if (!unit.manipulative) return;
    const { node, rich, button, api } = ui, config = unit.manipulative;
    const card = node("section", null, "card manipulative"), controls = node("div", null, "manip-controls"), drawing = node("div"), output = node("p");
    output.setAttribute("aria-live", "polite");
    card.append(node("h3", config.title), rich("p", config.prompt), controls, drawing, output); root.append(card);
    let state = structuredClone(config.state), prediction = null, taps = [], loaded = false, failed = false;
    let running = null, timer = null, lastSaved = "", requestId = null;
    const message = node("p", "", "muted"); message.setAttribute("role", "status");
    const explanationLabel = node("label", "看到什么变化？可以说给身边的人听，也可以写下来（可选）"), explanation = node("textarea");
    explanation.id = `manip-explanation-${unit.id}`; explanationLabel.htmlFor = explanation.id; explanation.rows = 3; explanation.maxLength = 2000;
    const ns = "http://www.w3.org/2000/svg";
    const predictionBox = node("section", null, "prediction-box"), predictionLabel = node("label"), predictionInput = node(config.kind === "fraction_strip" ? "select" : "input");
    predictionInput.id = `manip-prediction-${unit.id}`; predictionLabel.htmlFor = predictionInput.id;
    if (config.kind === "fraction_strip") for (const [value, label] of [["", "先选一个预测"], ["same", "长度不变"], ["longer", "长度变长"], ["shorter", "长度变短"]]) { const option = node("option", label); option.value = value; predictionInput.append(option); }
    else { predictionInput.type = "text"; predictionInput.inputMode = "decimal"; predictionInput.maxLength = 32; predictionInput.placeholder = config.kind === "ten_frame" ? "0到20之间的数" : "整数、小数或分数"; }
    const checkPrediction = button("保存预测，开始检验", async () => {
      if (!loaded || !predictionInput.value.trim()) { message.textContent = "先选好或填写你的预测。"; predictionInput.focus(); return; }
      setDisabled(true); ui.busy(true);
      try {
        clearTimeout(timer); if (running) await running;
        requestId ||= (globalThis.crypto?.randomUUID?.() || "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx".replace(/x/g, () => Math.floor(Math.random() * 16).toString(16))).replaceAll("-", "");
        const response = await api("/api/manipulative/predict", { unit_id: unit.id, state: structuredClone(state),
          resource_version: unit.resource_version, representation_version: config.representation_version,
          prediction_id: requestId, prediction: predictionInput.value });
        if (prediction?.prediction_id !== response.prediction.prediction_id) taps = [];
        prediction = response.prediction; failed = false; draw();
        message.textContent = "预测已记录。现在用图、表或逐一点数来检验；之后仍可修订。";
        await persist();
      } catch (error) { message.textContent = error.message; }
      finally { setDisabled(false); ui.busy(false); }
    }, "primary");
    predictionBox.append(node("h4", "先留下预测，再显示检验结果"), predictionLabel, predictionInput, checkPrediction);
    card.insertBefore(predictionBox, drawing);
    const predictionFeedback = node("p", "", "prediction-feedback"); predictionFeedback.setAttribute("aria-live", "polite");
    card.insertBefore(predictionFeedback, output);
    function signature() { return JSON.stringify({ state, prediction: prediction?.prediction_id || null, predictionDraft: predictionInput.value, taps, reflection: explanation.value }); }
    const editor = { root: card, get failed() { return failed; }, unsaved: () => loaded && signature() !== lastSaved, save: () => persist() };
    const notify = window.CNKGWork.registerActivity(editor);
    function changed() {
      failed = false; clearTimeout(timer); timer = setTimeout(() => persist(), 600); notify?.();
    }
    function resetPrediction() { prediction = null; taps = []; requestId = null; predictionInput.value = ""; checkPrediction.textContent = "保存预测，开始检验"; }
    function svgElement(name, attrs) { const e = document.createElementNS(ns, name); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; }
    function text(svg, x, y, value, size = 13) { const e = svgElement("text", { x, y, "font-size": size, fill: "#263b45" }); e.textContent = value; svg.append(e); }
    function slider(key, title, min, max, values) {
      const wrap = node("div"), label = node("label", title), input = node(values ? "select" : "input");
      input.id = `manip-${unit.id}-${key}`; label.htmlFor = input.id;
      if (values) for (const value of values) { const option = node("option", String(value)); option.value = String(value); input.append(option); }
      else { input.type = "range"; input.min = min; input.max = max; input.step = 1; }
      input.value = key.startsWith("v") ? state.values[Number(key[1])] : state[key];
      input.addEventListener("input", () => {
        if (key.startsWith("v")) state.values[Number(key[1])] = Number(input.value); else state[key] = Number(input.value);
        if (state.numerator > state.denominator) state.numerator = state.denominator;
        resetPrediction(); draw(); changed(); message.textContent = "条件改变了。原预测保留在记录中，先为这组新条件留下预测。";
      }); wrap.append(label, input); controls.append(wrap);
    }
    function draw() {
      const prompt = config.kind === "ten_frame" ? `第一部分${state.a}个，第二部分${state.b}个。你预测一共有几个？` : config.kind === "fraction_strip" ? `同一条纸带涂了${state.numerator}/${state.denominator}，每份再分成${state.factor}小份，涂色长度会怎样？` : config.kind === "data_chart" ? `四个实际数量是${state.values.join("、")}本。你预测平均每组几本？` : `y=${state.slope}x${state.intercept >= 0 ? "+" : ""}${state.intercept}。x=${state.x}时，你预测y是多少？`;
      predictionLabel.textContent = prompt;
      const numerator = controls.querySelector(`#manip-${unit.id}-numerator`); if (numerator) { numerator.max = state.denominator; numerator.value = state.numerator; }
      drawing.replaceChildren(); output.textContent = ""; predictionFeedback.textContent = "";
      if (!prediction) { drawing.append(node("p", "预测保存后，这里会显示可以检验它的图、表或计数片。", "muted")); return; }
      if (config.kind !== "ten_frame") predictionFeedback.textContent = `已记录的预测：${prediction.result.value === "same" ? "长度不变" : prediction.result.value === "longer" ? "长度变长" : prediction.result.value === "shorter" ? "长度变短" : prediction.result.value}。${prediction.result.matches_model ? "这次与图中关系一致。" : "这次与图中关系不同，可以比较后修订。"}${prediction.result.feedback}`;
      const svg = svgElement("svg", { viewBox: "0 0 480 230", role: "img", "aria-label": config.title });
      if (config.kind === "ten_frame") {
        const frames = node("div", null, "count-frames"), counted = new Set(taps), total = state.a + state.b;
        for (let i = 0; i < 20; i++) {
          if (i % 10 === 0) { const frame = node("div", null, "count-frame"); frame.setAttribute("role", "group"); frame.setAttribute("aria-label", i === 0 ? "第一个十格框" : "第二个十格框"); frames.append(frame); }
          const frame = frames.lastChild;
          if (i >= total) { const blank = node("span", "", "count-empty"); blank.setAttribute("aria-hidden", "true"); frame.append(blank); continue; }
          const token = button(counted.has(i) ? "● ✓" : "●", () => {
            if (taps.length >= 200) { message.textContent = "这一轮已保留200次点击。请保存后改变条件，开始新一轮。"; return; }
            const again = taps.includes(i); taps.push(i); draw(); changed();
            message.textContent = again ? "这一片已经数过，不会再增加数量。" : `已数过${new Set(taps).size}个不同的计数片。`;
            drawing.querySelector(`[data-token-index="${i}"]`)?.focus({ preventScroll: true });
          }, "counting-token " + (i < state.a ? "count-first" : "count-second"));
          token.dataset.tokenIndex = i; token.setAttribute("aria-pressed", counted.has(i) ? "true" : "false");
          token.setAttribute("aria-label", `${i < state.a ? "蓝色第一部分" : "橙色第二部分"}计数片${i + 1}，${counted.has(i) ? "已经数过" : "还没数过"}`); frame.append(token);
        }
        const moved = Math.min(10 - state.a, state.b), left = state.b - moved;
        drawing.append(frames, node("p", "蓝色是第一部分，橙色是第二部分。逐一点击，每片只数一次；按空格或回车也能点数。", "muted"));
        output.textContent = counted.size === total ? `${state.a}+${state.b}=${total}。第二部分取${moved}个放入第一框，还剩${left}个。${total >= 10 ? `合成${Math.floor(total / 10)}个十和${total % 10}个一。` : "还不够十个。"}` : `已经数过${counted.size}个不同的计数片。全部点过后，再比较原来的预测。`;
        if (counted.size === total) predictionFeedback.textContent = `原来的预测是${prediction.result.value}个，实际点数是${total}个。${prediction.result.matches_model ? "这次预测一致。" : "这次预测不同，可以说说你怎样改进。"}这次操作不自动判断掌握。`;
        return;
      } else if (config.kind === "fraction_strip") {
        for (const [row, factor] of [[0, 1], [1, state.factor]]) {
          const d = state.denominator * factor, n = state.numerator * factor, width = 360 / d;
          for (let i = 0; i < d; i++) svg.append(svgElement("rect", { x: 35 + i * width, y: 35 + row * 80, width, height: 42, fill: i < n ? "#7bbdbb" : "#f5f8f8", stroke: "#37545f" }));
          text(svg, 403, 62 + row * 80, `${n}/${d}`);
        }
        output.textContent = `整体都是同样长的一条。${state.numerator}/${state.denominator}=${state.numerator * state.factor}/${state.denominator * state.factor}。每一旧份再分成${state.factor}小份，本次上下两条的涂色长度相同。只改变细分倍数保持原分数；改变原分子或分母是在重新设置原分数，不保证数值不变。此工具最多取完整一条，减少分母时涂色份数也会限制到总份数以内。`;
        const n = controls.querySelector("input[type=range]"); if (n) { n.max = state.denominator; n.value = state.numerator; }
      } else if (config.kind === "data_chart") {
        const y = value => 185 - (value - state.baseline) * 10;
        for (let value = state.baseline; value <= 15; value++) {
          svg.append(svgElement("line", { x1: 40, x2: 450, y1: y(value), y2: y(value), stroke: "#e1e8eb" })); text(svg, 13, y(value) + 4, value);
        }
        state.values.forEach((value, i) => { const x = 65 + i * 95; svg.append(svgElement("rect", { x, y: y(value), width: 55, height: (value - state.baseline) * 10, fill: "#39858b" })); text(svg, x + 18, y(value) - 7, value); text(svg, x + 20, 208, ["甲", "乙", "丙", "丁"][i]); });
        text(svg, 8, 19, "本"); output.textContent = `四个实际数量：${state.values.join("、")}本；合计${state.values.reduce((a, b) => a + b, 0)}本，平均数${state.values.reduce((a, b) => a + b, 0) / 4}本。纵轴从${state.baseline}起；画出的条长${state.baseline ? "不能直接表示实际数量倍数" : "从零起点表示数量"}。`;
      } else {
        const x = value => 240 + value * 35, y = value => 110 - value * 18;
        const defs = svgElement("defs"), clip = svgElement("clipPath", { id: "linear-manip-clip" }); clip.append(svgElement("rect", { x: 65, y: 20, width: 350, height: 180 })); defs.append(clip); svg.append(defs);
        for (let value = -5; value <= 5; value++) {
          svg.append(svgElement("line", { x1: x(value), x2: x(value), y1: 20, y2: 200, stroke: "#e1e8eb" }), svgElement("line", { x1: 65, x2: 415, y1: y(value), y2: y(value), stroke: "#e1e8eb" }));
          if (value) { text(svg, x(value) - 5, 124, value, 10); text(svg, 222, y(value) + 3, value, 10); }
        }
        svg.append(svgElement("line", { x1: 65, x2: 415, y1: 110, y2: 110, stroke: "#263b45" }), svgElement("line", { x1: 240, x2: 240, y1: 20, y2: 200, stroke: "#263b45" }));
        svg.append(svgElement("line", { x1: x(-5), x2: x(5), y1: y(-5 * state.slope + state.intercept), y2: y(5 * state.slope + state.intercept), stroke: "#176b72", "stroke-width": 3, "clip-path": "url(#linear-manip-clip)" }));
        const value = state.slope * state.x + state.intercept;
        if (value >= -5 && value <= 5) svg.append(svgElement("circle", { cx: x(state.x), cy: y(value), r: 5, fill: "#bd692e" }));
        text(svg, 423, 113, "x"); text(svg, 245, 14, "y");
        output.textContent = `y=${state.slope}x${state.intercept >= 0 ? "+" : ""}${state.intercept}；x=${state.x}时，y=${value}。${state.slope === 0 ? "这是常函数。" : "k不为0，是一次函数。"}${Math.abs(value) > 5 ? "选中的点超出图示窗口，数值关系仍保留在表中。" : ""}`;
        const table = node("table"), caption = node("caption", "同一表达式得到的数值表"), header = node("tr"); header.append(node("th", "x"), node("th", "y")); table.append(caption, header);
        for (let i = -2; i <= 2; i++) { const row = node("tr"); row.append(node("td", i), node("td", state.slope * i + state.intercept)); table.append(row); } drawing.append(table);
      }
      svg.setAttribute("aria-label", output.textContent); drawing.prepend(svg);
    }
    function setup() {
      controls.replaceChildren();
      if (config.kind === "ten_frame") { slider("a", "第一部分个数", 0, 10); slider("b", "第二部分个数", 0, 10); }
      else if (config.kind === "fraction_strip") { slider("numerator", "涂色份数", 0, state.denominator); slider("denominator", "原来的等份数", 2, 12, [2, 3, 4, 6, 8, 12]); slider("factor", "每份再细分成几份", 2, 3, [2, 3]); }
      else if (config.kind === "data_chart") { state.values.forEach((_, i) => slider("v" + i, ["甲", "乙", "丙", "丁"][i] + "的本数", 5, 15)); slider("baseline", "纵轴起点", 0, 5, [0, 5]); }
      else { slider("slope", "变化率 k", -3, 3); slider("intercept", "截距 b", -4, 4); slider("x", "选定输入 x", -2, 2); }
      draw();
    }
    async function persist(force = false) {
      clearTimeout(timer);
      if (running) { await running; if (!failed && editor.unsaved()) return persist(force); return; }
      if (!loaded || (!force && !editor.unsaved())) return;
      const submitted = signature(), body = { unit_id: unit.id, resource_version: unit.resource_version,
        representation_version: config.representation_version, state: structuredClone(state), reflection: explanation.value,
        prediction_draft: predictionInput.value,
        interaction: { tap_sequence: [...taps] }, ...(prediction ? { prediction_id: prediction.prediction_id } : {}) };
      setDisabled(true);
      running = (async () => {
        try { await api("/api/manipulative", body); lastSaved = submitted; failed = false;
          message.textContent = prediction ? "预测、操作顺序和解释已保存，可随完整档案恢复；这些是学习过程记录。" : "当前条件与草稿已保存；这轮还没有预测和检验记录。";
        } catch (error) { failed = true; message.textContent = `尚未保存：${error.message}。本页内容保留，请重试后再离开。`; }
      })();
      await running; running = null; setDisabled(false); notify?.();
      if (!failed && editor.unsaved()) return persist();
    }
    const save = button("保存操作和解释", () => persist(true));
    const retry = button("重新读取已保存操作", () => loadSaved()); retry.hidden = true;
    function setDisabled(value) {
      for (const control of controls.querySelectorAll("input, select")) control.disabled = value;
      for (const control of drawing.querySelectorAll("button")) control.disabled = value;
      explanation.disabled = save.disabled = predictionInput.disabled = checkPrediction.disabled = value;
    }
    async function loadSaved() {
      setDisabled(true); retry.hidden = true; message.textContent = "正在读取已保存操作…";
      try {
        const prior = await api("/api/manipulative?unit_id=" + encodeURIComponent(unit.id));
        if (prior.saved && prior.same_version) { state = prior.saved.payload.state; explanation.value = prior.saved.payload.reflection || ""; prediction = prior.saved.payload.prediction || null; taps = prior.saved.payload.interaction?.tap_sequence || []; message.textContent = "已恢复这个内容与表征版本上次保存的预测、操作和解释。"; }
        else message.textContent = prior.saved ? "材料或表征已更新。旧版本操作和原配置保留在学习记录与完整备份中，本页从当前表征开始。" : "先留下预测，再操作检验。过程会自动保存，也可按保存按钮。";
        const pending = prior.pending_prediction;
        const pendingCurrent = pending && pending.content_sha256 === unit.resource_version && pending.payload.representation_version === config.representation_version;
        if (pendingCurrent) {
          if (JSON.stringify(state) !== JSON.stringify(pending.payload.state)) explanation.value = "";
          prediction = pending.payload; state = structuredClone(prediction.state); taps = []; message.textContent = "已恢复刚保存、尚未完成检验的预测。可以继续操作。";
        }
        predictionInput.value = pendingCurrent ? prediction.result.value : prior.same_version ? prior.saved.payload.prediction_draft ?? prediction?.result?.value ?? "" : prediction?.result?.value || "";
        requestId = prediction && predictionInput.value === prediction.result.value ? prediction.prediction_id : null;
        setup(); loaded = true; failed = false; lastSaved = signature(); notify?.();
        setDisabled(false);
      } catch (error) { message.textContent = `读取原操作失败：${error.message}。请重试后继续，避免覆盖尚未读到的原记录。`; retry.hidden = false; }
    }
    predictionInput.addEventListener("input", () => { requestId = null; if (prediction) checkPrediction.textContent = "保存修订的预测，再检验"; changed(); });
    explanation.addEventListener("input", () => { message.textContent = "解释有修改，正在等待自动保存。"; changed(); });
    card.append(explanationLabel, explanation, save, retry, message); setup(); await loadSaved();
  }
  return { mount };
})();
