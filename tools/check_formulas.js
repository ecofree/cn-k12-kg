/* 用 KaTeX 本体校验公式能否渲染，输出坏题清单。
 *
 * 为什么单独跑：判「公式对不对」的唯一可信标准就是渲染器自己。
 * 用正则猜（比如查尾部反斜杠）只能覆盖已知的几种，换个符号又漏。
 * 所以这里批量跑一次，把结果交给 Python 侧的入库闸门。
 *
 * 用法：
 *   node tools/check_formulas.js <in.json> <out.json>
 *   in.json  = [{"qid": "...", "tex": "..."}, ...]
 *   out.json = {"total": N, "failed": M, "bad_qids": ["..."], "samples": [...]}
 */
const fs = require("fs");
const path = require("path");

const katex = require(path.join(__dirname, "..", "web", "static", "vendor", "katex", "katex.min.js"));

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  console.error("用法: node tools/check_formulas.js <in.json> <out.json>");
  process.exit(2);
}

const items = JSON.parse(fs.readFileSync(input, "utf8"));
const badQids = new Set();
const samples = [];
let failed = 0;

for (const item of items) {
  try {
    katex.renderToString(item.tex, { throwOnError: true, strict: false, trust: false });
  } catch (err) {
    failed += 1;
    if (!badQids.has(item.qid) && samples.length < 12) {
      samples.push({ qid: item.qid, tex: String(item.tex).slice(0, 70), error: String(err.message).slice(0, 90) });
    }
    badQids.add(item.qid);
  }
}

const report = {
  total_formulas: items.length,
  failed_formulas: failed,
  bad_questions: badQids.size,
  samples,
  bad_qids: [...badQids],
};
fs.writeFileSync(output, JSON.stringify(report, null, 1));
console.log(`公式 ${items.length} 条，失败 ${failed} 条，涉及题目 ${badQids.size} 道`);
