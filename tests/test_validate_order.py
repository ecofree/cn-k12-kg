"""先修顺序校验的测试：前置不能指向"以后才学"的内容。

这一类错误靠人眼看 570 条边看不出来，但会让诊断把学生往还没学过的内容上引，
所以必须有机械检查守着。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import validate  # noqa: E402


def topic(tid):
    return {"id": tid, "name": tid, "volume": "", "chapter": "", "section": "",
            "grade": 1, "semester": "上", "domain": "", "theme": "", "type": "conceptual",
            "description": "", "mastery_criteria": [], "standards": [],
            "curriculum_version": "2024", "edition": "人教版", "review_status": "draft"}


class CurriculumOrderTest(unittest.TestCase):
    def test_same_volume_by_chapter(self):
        self.assertLess(validate.curriculum_key(topic("m7a.1.1")),
                        validate.curriculum_key(topic("m7a.2.1")))

    def test_volume_order(self):
        self.assertLess(validate.curriculum_key(topic("m6b.1.1")),
                        validate.curriculum_key(topic("m7a.1.1")))
        self.assertLess(validate.curriculum_key(topic("m9b.1.1")),
                        validate.curriculum_key(topic("mh1.1.1")))

    def test_subsection(self):
        self.assertLess(validate.curriculum_key(topic("m7a.6.2.1")),
                        validate.curriculum_key(topic("m7a.6.3.1")))

    def test_high_school_volumes(self):
        self.assertLess(validate.curriculum_key(topic("mh1.5.1")),
                        validate.curriculum_key(topic("mh3.1.1")))


class EdgeOrderTest(unittest.TestCase):
    def setUp(self):
        self.topics = [topic("m7a.6.2.1"), topic("m7a.6.3.1")]

    def test_hard_backwards_is_error(self):
        r = validate.Report()
        deps = [{"topicId": "m7a.6.2.1", "prerequisiteId": "m7a.6.3.1", "strength": "hard"}]
        hard, soft = validate.check_edge_order(self.topics, deps, r)
        self.assertEqual((hard, soft), (1, 0))
        self.assertTrue(any("先修晚于当前点" in e for e in r.errors))

    def test_soft_backwards_is_warning(self):
        r = validate.Report()
        deps = [{"topicId": "m7a.6.2.1", "prerequisiteId": "m7a.6.3.1", "strength": "soft"}]
        hard, soft = validate.check_edge_order(self.topics, deps, r)
        self.assertEqual((hard, soft), (0, 1))
        self.assertEqual(r.errors, [])
        self.assertTrue(r.warnings)

    def test_forward_edge_ok(self):
        r = validate.Report()
        deps = [{"topicId": "m7a.6.3.1", "prerequisiteId": "m7a.6.2.1", "strength": "hard"}]
        hard, soft = validate.check_edge_order(self.topics, deps, r)
        self.assertEqual((hard, soft), (0, 0))
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
