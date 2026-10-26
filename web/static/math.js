/* 题干渲染：KaTeX 公式 + Markdown 表格。
 *
 * 为什么单独一层：题库来自公开数据集，题干里混着两种非纯文本内容——
 *   `$x^{2}$` 这种行内公式（入库题里 75% 有）
 *   `| 每年体检 | 每年未体检 |` 这种表格（全池约 3.5%）
 * 直接 textContent 会把这些显示成源码。
 *
 * 设计取舍：
 *   1. 只处理带定界符的公式（$...$、$$...$$、\(...\)、\[...\]）。
 *      裸 LaTeX（\frac 没有 $）不在前端兜底，改在入库时拒收——
 *      猜错边界会把正常文字当公式渲染，比显示源码更糟。
 *   2. throwOnError=false：单条公式坏了只标记这一处，不整题白屏。
 */
(function () {
  const DELIMS = [
    { left: "$$", right: "$$", display: true },
    { left: "$", right: "$", display: false },
    { left: "\\(", right: "\\)", display: false },
    { left: "\\[", right: "\\]", display: true },
  ];

  const isTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
  const isSeparator = (line) => /^\s*\|[\s:|-]+\|\s*$/.test(line);

  function buildTable(lines) {
    const table = document.createElement("table");
    table.className = "math-table";
    const cells = (line) =>
      line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((s) => s.trim());

    let start = 0;
    let header = null;
    if (lines.length > 1 && isSeparator(lines[1])) {
      header = cells(lines[0]);
      start = 2;
    } else if (lines.length && isSeparator(lines[0])) {
      start = 1;
    }
    if (header) {
      const thead = document.createElement("thead");
      const tr = document.createElement("tr");
      for (const text of header) {
        const th = document.createElement("th");
        th.textContent = text;
        tr.append(th);
      }
      thead.append(tr);
      table.append(thead);
    }
    const tbody = document.createElement("tbody");
    for (let i = start; i < lines.length; i += 1) {
      if (isSeparator(lines[i])) continue;
      const tr = document.createElement("tr");
      for (const text of cells(lines[i])) {
        const td = document.createElement("td");
        td.textContent = text;
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(tbody);
    return table;
  }

  function typeset(el) {
    if (typeof window.renderMathInElement !== "function") return;
    window.renderMathInElement(el, {
      delimiters: DELIMS,
      throwOnError: false,
      errorColor: "#c0392b",
      strict: false,
    });
  }

  /* 把一段混合文本写进元素：表格建 DOM，其余按行合并成段落，最后统一排版公式。 */
  function setRichText(el, text) {
    el.textContent = "";
    const lines = String(text == null ? "" : text).split("\n");
    let buffer = [];

    const flush = () => {
      if (!buffer.length) return;
      const p = document.createElement("div");
      p.className = "math-line";
      p.textContent = buffer.join("\n");
      el.append(p);
      buffer = [];
    };

    for (let i = 0; i < lines.length; i += 1) {
      if (isTableRow(lines[i])) {
        const block = [];
        while (i < lines.length && isTableRow(lines[i])) {
          block.push(lines[i]);
          i += 1;
        }
        i -= 1;
        if (block.length >= 2) {
          flush();
          el.append(buildTable(block));
          continue;
        }
        buffer.push(...block);
        continue;
      }
      buffer.push(lines[i]);
    }
    flush();
    typeset(el);
  }

  window.setRichText = setRichText;
})();
