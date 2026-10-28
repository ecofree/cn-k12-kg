"""公式资源契约，以及真实渲染函数对已保存数学内容的路由。

轻量元素替身只核对实际app函数使用的数据与富文本入口，不声明浏览器排版通过。
真正的公式渲染仍由 tools/check_formulas.js 用 KaTeX 本体验证。
"""
import copy
import json
import pathlib
import re
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 核心资源和页面契约不依赖外部题库，必须在 fresh clone 中执行。

STATIC = ROOT / "web" / "static"
sys.path.insert(0, str(ROOT))


class KatexVendorTest(unittest.TestCase):
    def test_vendor_files_present(self):
        for name in ("katex.min.js", "katex.min.css", "auto-render.min.js"):
            self.assertTrue((STATIC / "vendor" / "katex" / name).is_file(), name)

    def test_fonts_present(self):
        # KaTeX 共 20 个字体族，每族 woff2/woff/ttf 各一份，所以 woff2 就是 20 个
        fonts = list((STATIC / "vendor" / "katex" / "fonts").glob("*.woff2"))
        self.assertGreaterEqual(len(fonts), 20, "KaTeX 字体不全，公式会显示成方框")
        # 正体是公式里最常见的字形，缺了整页都会退化成方块
        self.assertTrue((STATIC / "vendor" / "katex" / "fonts" / "KaTeX_Main-Regular.woff2").is_file())


class PageWiringTest(unittest.TestCase):
    def test_index_loads_katex_and_mathjs(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn("vendor/katex/katex.min.css", html)
        self.assertIn("vendor/katex/katex.min.js", html)
        self.assertIn("auto-render.min.js", html)
        self.assertIn("math.js", html)
        # 顺序很重要：math.js 依赖 katex 与 auto-render
        self.assertLess(html.index("katex.min.js"), html.index("math.js"))

    def test_app_uses_rich_text(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('window.setRichText($("quiz-prompt"), item.question.prompt)', js)
        # Only the fixed notice for an unavailable historical prompt is plain
        # text. An actual task prompt must still enter the math-aware renderer.
        plain_assignments = re.findall(r'\$\("quiz-prompt"\)\.textContent\s*=\s*([^;]+);', js)
        self.assertEqual(['"这份记录没有可恢复的当前题面。"'], plain_assignments)

    @unittest.skipUnless(shutil.which('node'), 'Node.js 未安装；静态富文本契约仍执行')
    def test_real_render_functions_use_saved_answers_and_saved_summary_data(self):
        from web import server
        answers = [{'question_id': 'old-math', 'status': 'correct',
                    'prompt': '旧题：$\\frac{1}{2}+\\frac{1}{2}$', 'raw_answer': '1',
                    'answer': '1', 'explanation': '旧解析：$\\frac{2}{2}=1$',
                    'answered_at': '2026-01-01T00:00:00+00:00'}]
        saved = {'kind': 'topic', 'title': '保存时的总结', 'weak': [], 'shaky': [], 'roots': [],
                 'answers': copy.deepcopy(answers), 'context': {}}
        record = {'id': 'old-session', 'kind': 'topic', 'mode': 'practice',
                  'created_at': '2026-01-01T00:00:00+00:00',
                  'completed_at': '2026-01-01T00:01:00+00:00',
                  'content_sha256': 'old-version', 'answers': answers,
                  'summary_snapshot': saved, 'verdict_snapshot': '当时保存的文字结论',
                  'engine': {'state': {'old-topic': 'weak'}}, 'context': {}}
        original = copy.deepcopy(record)
        # Both completed-history paths must use saved data, even if today's
        # interpretation would fail or produce a different result.
        with patch.object(server, '_build_session_summary', side_effect=AssertionError('must not recompute history')):
            stored_summary = server.session_summary(record)
            missing_summary = server.session_summary({key: value for key, value in record.items()
                                                      if key != 'summary_snapshot'})
        self.assertEqual(saved, stored_summary)
        self.assertEqual('summary_not_saved', missing_summary['history_status'])
        self.assertEqual(answers, missing_summary['answers'])
        self.assertEqual(original, record)
        payload = {'current_prompt': '新题：$x^2=9$', 'saved_answers': answers,
                   'saved_summary': stored_summary, 'missing_summary': missing_summary,
                   'saved_verdict': record['verdict_snapshot']}
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const elements = new Map(), richCalls = [], promptCalls = [];
class Element {
  constructor(id) { this.id = id; this.children = []; this.textContent = ''; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  querySelectorAll() { return []; }
  focus() {}
}
function element(id) { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); }
function node(tag, text='') { const value = new Element(tag); value.textContent = text; return value; }
function rich(tag, value) { richCalls.push(value); return node(tag, value); }
const state = {session: {id: 'old-session', kind: 'topic', mode: 'practice', answered_count: 1,
  targets: [], context: {}, feedback_policy: 'immediate', resume_blocked: false},
  item: {topicName: '当前任务', question: {id: 'new-math', type: 'numeric', prompt: input.current_prompt}},
  savedAnswers: [], verdictText: input.saved_verdict};
const context = vm.createContext({state, $: element, node, rich, performance: {now: () => 0},
  PHASE: {practice: '练习'}, STATUS: {correct: '答对了'}, formatTime: value => value,
  renderSessionContentUpdate: () => {}, renderRubric: () => {}, show: () => {},
  contextText: () => '保存的上下文', nameOf: value => value,
  window: {setRichText: (target, value) => promptCalls.push({id: target.id, value})}});
function loadFunction(name, nextMarker) {
  const start = source.indexOf('function ' + name + '(');
  const end = source.indexOf(nextMarker, start);
  assert.ok(start >= 0 && end > start, name);
  vm.runInContext(source.slice(start, end), context);
}
loadFunction('renderItem', '\nfunction renderRubric(');
loadFunction('showResult', '\n$("search").addEventListener(');
vm.runInContext('renderItem()', context);
assert.deepEqual(promptCalls, [{id: 'quiz-prompt', value: input.current_prompt}]);
state.item = null; state.session.resume_blocked = true; state.savedAnswers = input.saved_answers;
richCalls.length = 0; vm.runInContext('renderItem()', context);
assert.ok(richCalls.includes(input.saved_answers[0].prompt));
assert.ok(richCalls.includes('你的答案：' + input.saved_answers[0].raw_answer));
assert.equal(element('quiz-prompt').textContent, '这份记录没有可恢复的当前题面。');
assert.equal(element('submit').disabled, true);
assert.equal(element('skip').disabled, true);
// A current task exists in state again, but both history result paths must
// render the saved answers rather than substituting that current task.
state.item = {question: {prompt: input.current_prompt}};
state.summary = input.saved_summary; richCalls.length = 0;
vm.runInContext('showResult()', context);
assert.equal(element('result-title').textContent, input.saved_summary.title);
assert.ok(richCalls.includes(input.saved_answers[0].prompt));
assert.ok(richCalls.includes(input.saved_answers[0].explanation));
assert.ok(!richCalls.includes(input.current_prompt));
state.summary = input.missing_summary; richCalls.length = 0;
vm.runInContext('showResult()', context);
assert.equal(element('diagnostic-result').hidden, true);
assert.equal(element('objective-result').hidden, true);
assert.ok(element('verdict').textContent.includes(input.missing_summary.history_notice));
assert.ok(element('verdict').textContent.includes(input.saved_verdict));
assert.ok(richCalls.includes(input.saved_answers[0].prompt));
assert.ok(!richCalls.includes(input.current_prompt));
process.stdout.write(JSON.stringify({current_prompt_rich: true, saved_answers_rich: true,
  saved_summary_used: true, missing_summary_not_reconstructed: true}));
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(STATIC / 'app.js')],
                                input=json.dumps(payload, ensure_ascii=False), text=True,
                                capture_output=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'current_prompt_rich': True, 'saved_answers_rich': True,
                          'saved_summary_used': True, 'missing_summary_not_reconstructed': True},
                         json.loads(result.stdout))


class ServerAllowlistTest(unittest.TestCase):
    def test_static_allowlist_covers_new_assets(self):
        src = (ROOT / "web" / "server.py").read_text(encoding="utf-8")
        for name in ("math.js", "ext.html", "ext-data.json"):
            self.assertIn(name, src)
        self.assertIn("vendor/", src)
        self.assertIn("woff2", src)


@unittest.skipUnless((ROOT / "data" / "questions_ext.json").exists(),
                     "外部题库未下载，见 data/README.md")
class ExtPageTest(unittest.TestCase):
    def test_page_generated_and_has_questions(self):
        """预览页必须覆盖全部外部题。

        题量涨到 14344 后，页面改成「HTML 壳 + ext-data.json 数据文件」
        的结构——全部题干内联进 HTML 会得到 18MB 单文件。
        所以这里检查两处：HTML 壳里有渲染入口，数据文件里的题数与
        questions_ext.json 一致。
        """
        path = STATIC / "ext.html"
        if not path.exists():
            self.skipTest("尚未生成外部题预览页")
        html = path.read_text(encoding="utf-8")
        self.assertIn("math.js", html)
        self.assertIn("data-rich", html)

        data_path = STATIC / "ext-data.json"
        self.assertTrue(data_path.exists(), "缺 ext-data.json")
        data = json.loads(data_path.read_text(encoding="utf-8"))
        ext = json.loads((ROOT / "data" / "questions_ext.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["questions"]), len(ext),
                         "预览页题数与 questions_ext.json 不一致")

    def test_compound_questions_shown(self):
        """复合题要在预览页上体现出来（列出多个知识点）。"""
        data_path = STATIC / "ext-data.json"
        if not data_path.exists():
            self.skipTest("尚未生成外部题预览页")
        data = json.loads(data_path.read_text(encoding="utf-8"))
        multi = [q for q in data["questions"] if len(q.get("t") or []) > 1]
        self.assertGreater(len(multi), 100,
                           f"复合题太少：{len(multi)}，多标签映射可能没生效")

    def test_answers_not_leaked_to_diagnosis_page(self):
        """外部题页不该把答案混进诊断集——两边是分开的。"""
        ext = json.loads((ROOT / "data" / "questions_ext.json").read_text(encoding="utf-8"))
        self.assertTrue(ext, "外部题表为空")
        for q in ext:
            level = q.get("admission_level")
            if level in {"verified", "cross-checked"}:
                self.assertTrue(q.get("usable_for_diagnosis"),
                                f"{q['id']} 等级 {level} 应可进诊断")
            elif level == "compound-reference":
                self.assertFalse(q.get("usable_for_diagnosis"),
                                 f"{q['id']} 是复合型训练题，不该进诊断")
                self.assertFalse((q.get("answer") or "").strip(),
                                 f"{q['id']} 复合型训练题不该带答案")


if __name__ == "__main__":
    unittest.main()
