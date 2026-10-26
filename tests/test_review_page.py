"""复核工作台生成器的测试：页面必须自带数据、不能残留占位符、题数与样本一致。"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_review_page  # noqa: E402


class BuildPageTest(unittest.TestCase):
    def test_embeds_data_without_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "review.html"
            n = build_review_page.build(5, out)
            html = out.read_text(encoding="utf-8")

        self.assertEqual(n, 5)
        self.assertNotIn("__DATA__", html)
        self.assertNotIn("__TOTAL__", html)
        self.assertIn("const DATA = [", html)
        self.assertEqual(html.count('"topicId":'), 5)

    def test_has_export_and_keyboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "review.html"
            build_review_page.build(3, out)
            html = out.read_text(encoding="utf-8")
        for token in ("导出 review.csv", "ArrowDown", "cnkg-review-v1", "verdict"):
            self.assertIn(token, html)

    def test_csv_header_matches_inbox_schema(self):
        """页面导出的列必须和 inbox/review.csv 完全一致，否则 ingest 读不了。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "review.html"
            build_review_page.build(3, out)
            html = out.read_text(encoding="utf-8")
        expect = ('["question_id", "topic_id", "type", "difficulty", "prompt", '
                  '"answer", "explanation", "verdict", "note"]')
        self.assertIn(expect, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
