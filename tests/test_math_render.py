"""公式渲染与外部题预览页的静态契约。

这里不测浏览器行为（没有 DOM），测的是"页面需要的资源都在、路径都对"。
真正的公式渲染由 tools/check_formulas.js 用 KaTeX 本体验证。
"""
import json
import pathlib
import re
import sys
import unittest

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
        self.assertIn("setRichText", js)
        # 题干如果还走 textContent，公式就会显示成源码
        self.assertNotIn('$("quiz-prompt").textContent', js)


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
