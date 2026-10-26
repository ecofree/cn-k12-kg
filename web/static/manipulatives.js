window.CNKGManipulatives = (() => {
  async function mount(root, unit, ui) {
    if (!unit.manipulative) return;
    const { node, rich, button, api } = ui, config = unit.manipulative;
    const card = node("section", null, "card manipulative"), controls = node("div", null, "manip-controls"), drawing = node("div"), output = node("p");
    output.setAttribute("aria-live", "polite");
    card.append(node("h3", config.title), rich("p", config.prompt), controls, drawing, output); root.append(card);
    let state = structuredClone(config.state);
    const message = node("p", "", "muted"); message.setAttribute("role", "status");
    const explanationLabel = node("label", "写下你的预测、看到的变化和解释"), explanation = node("textarea");
    explanation.id = `manip-explanation-${unit.id}`; explanationLabel.htmlFor = explanation.id; explanation.rows = 3; explanation.maxLength = 2000;
    const ns = "http://www.w3.org/2000/svg";
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
        draw(); message.textContent = "操作有变化，可写下解释后保存。";
      }); wrap.append(label, input); controls.append(wrap);
    }
    function draw() {
      drawing.replaceChildren(); const svg = svgElement("svg", { viewBox: "0 0 480 230", role: "img", "aria-label": config.title });
      if (config.kind === "ten_frame") {
        for (let i = 0; i < 20; i++) {
          const x = 30 + (i % 5) * 42 + Math.floor(i / 10) * 230, y = 35 + Math.floor((i % 10) / 5) * 42;
          svg.append(svgElement("rect", { x, y, width: 38, height: 38, fill: "white", stroke: "#657a84" }));
          if (i < state.a + state.b) svg.append(svgElement("circle", { cx: x + 19, cy: y + 19, r: 13, fill: i < state.a ? "#176b72" : "#bd692e" }));
        }
        const moved = Math.min(10 - state.a, state.b), left = state.b - moved;
        const total = state.a + state.b;
        output.textContent = `${state.a}+${state.b}=${total}。第二部分取${moved}个放入第一框，还剩${left}个。${total >= 10 ? `合成${Math.floor(total / 10)}个十和${total % 10}个一。` : "还不够十个。"}`;
        text(svg, 30, 154, "蓝色：第一部分；橙色：第二部分");
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
    const save = button("保存操作和解释", async () => {
      setDisabled(true); ui.busy(true);
      try { await api("/api/manipulative", { unit_id: unit.id, resource_version: unit.resource_version,
        representation_version: config.representation_version, state: structuredClone(state), reflection: explanation.value }); message.textContent = "操作状态与解释已保存，可随完整档案备份恢复；这项记录不自动计分。"; }
      catch (error) { message.textContent = error.message; } finally { setDisabled(false); ui.busy(false); }
    });
    const retry = button("重新读取已保存操作", () => loadSaved()); retry.hidden = true;
    function setDisabled(value) {
      for (const control of controls.querySelectorAll("input, select")) control.disabled = value;
      explanation.disabled = save.disabled = value;
    }
    async function loadSaved() {
      setDisabled(true); retry.hidden = true; message.textContent = "正在读取已保存操作…";
      try {
        const prior = await api("/api/manipulative?unit_id=" + encodeURIComponent(unit.id));
        if (prior.saved && prior.same_version) { state = prior.saved.payload.state; explanation.value = prior.saved.payload.reflection || ""; setup(); message.textContent = "已恢复这个内容与表征版本上次保存的操作。"; }
        else message.textContent = prior.saved ? "材料或表征已更新。旧版本操作和原配置保留在学习记录与完整备份中，本页从当前表征开始。" : "先预测，再操作；写下解释后保存。";
        setDisabled(false);
      } catch (error) { message.textContent = `读取原操作失败：${error.message}。请重试后继续，避免覆盖尚未读到的原记录。`; retry.hidden = false; }
    }
    explanation.addEventListener("input", () => { message.textContent = "解释有修改，请保存操作和解释。"; });
    card.append(explanationLabel, explanation, save, retry, message); setup(); await loadSaved();
  }
  return { mount };
})();
