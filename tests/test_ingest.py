"""交接包格式约定测试：inbox/*.csv 是外部协作的接口，字段规则不能悄悄变。

这些用例守的是"对方填错时我们会报错"，而不是"对方填对时我们能读"——
后者手一跑就知道，前者只有回归测试能保证。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ingest  # noqa: E402


def rpt():
    return ingest.Report()


class StandardsTest(unittest.TestCase):
    def test_unknown_id(self):
        r = rpt()
        ingest.check_standards([{"id": "nope"}], {"cms-2022:4.NA.01"}, r)
        self.assertTrue(any("不存在" in e for e in r.errors))

    def test_verbatim_true_without_text(self):
        r = rpt()
        ingest.check_standards([{"id": "cms-2022:4.NA.01", "verbatim": "true"}],
                               {"cms-2022:4.NA.01"}, r)
        self.assertTrue(any("没有原文" in e for e in r.errors))

    def test_counts_filled(self):
        r = rpt()
        rows = [{"id": "cms-2022:4.NA.01", "verbatim_text": "理解有理数的意义，能比较大小",
                 "page_or_ref": "第22页"}]
        self.assertEqual(ingest.check_standards(rows, {"cms-2022:4.NA.01"}, r), 1)
        self.assertEqual(r.errors, [])

    def test_missing_page_warns(self):
        r = rpt()
        rows = [{"id": "cms-2022:4.NA.01", "verbatim_text": "理解有理数的意义，能比较大小",
                 "page_or_ref": "待核"}]
        ingest.check_standards(rows, {"cms-2022:4.NA.01"}, r)
        self.assertTrue(any("页码" in w for w in r.warns))


class ReviewTest(unittest.TestCase):
    def test_verdict_enum(self):
        r = rpt()
        ingest.check_reviews([{"question_id": "q1", "verdict": "maybe"}], {"q1"}, r)
        self.assertTrue(any("verdict" in e for e in r.errors))

    def test_fail_requires_note(self):
        r = rpt()
        counts = ingest.check_reviews([{"question_id": "q1", "verdict": "fail"}], {"q1"}, r)
        self.assertEqual(counts["fail"], 1)
        self.assertTrue(any("没写原因" in w for w in r.warns))

    def test_unknown_question(self):
        r = rpt()
        ingest.check_reviews([{"question_id": "qX", "verdict": "pass"}], {"q1"}, r)
        self.assertTrue(any("题目不存在" in e for e in r.errors))


class KcMapTest(unittest.TestCase):
    def test_unknown_topic_and_duplicate(self):
        r = rpt()
        rows = [{"kc_id": "1", "topic_id": "m7a.1.2.1"},
                {"kc_id": "1", "topic_id": "m7a.1.2.1"},
                {"kc_id": "2", "topic_id": "nope"}]
        ingest.check_kc_map(rows, {"m7a.1.2.1"}, r)
        self.assertTrue(any("重复" in e for e in r.errors))
        self.assertTrue(any("不存在" in e for e in r.errors))

    def test_example_row_ignored(self):
        r = rpt()
        n, mapped = ingest.check_kc_map([{"kc_id": "<示例> 100001", "topic_id": "x"}],
                                        {"m7a.1.2.1"}, r)
        self.assertEqual((n, mapped), (0, 0))
        self.assertEqual(r.errors, [])


class StatsTest(unittest.TestCase):
    def test_rate_range(self):
        r = rpt()
        ingest.check_stats([{"question_id": "q1", "attempts": "10", "correct_rate": "1.5"}],
                           {"q1"}, r)
        self.assertTrue(any("0—1" in e for e in r.errors))

    def test_attempts_positive(self):
        r = rpt()
        ingest.check_stats([{"question_id": "q1", "attempts": "0", "correct_rate": "0.5"}],
                           {"q1"}, r)
        self.assertTrue(any("attempts" in e for e in r.errors))


class OutlineTest(unittest.TestCase):
    def test_volume_whitelist(self):
        r = rpt()
        ingest.check_outline([{"volume": "七上", "leaf_no": "1.1", "action": "keep"}], r)
        self.assertTrue(any("册次" in e for e in r.errors))

    def test_new_needs_title_and_source(self):
        r = rpt()
        ingest.check_outline([{"volume": "四下", "leaf_no": "1.2", "action": "new"}], r)
        self.assertTrue(any("leaf_title" in e for e in r.errors))

    def test_new_without_source_warns(self):
        r = rpt()
        ingest.check_outline([{"volume": "四下", "leaf_no": "1.2", "action": "new",
                               "leaf_title": "除数是整十数的口算"}], r)
        self.assertTrue(any("source" in w for w in r.warns))


if __name__ == "__main__":
    unittest.main(verbosity=2)
