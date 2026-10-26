"""分层数据的守门测试。

分层不是为了好看，它决定三件事：写不写完整讲解卡、配几道题、答对后要不要升档。
任何一条判错，都会让学生的时间花错地方——所以这里锁住判定规则本身。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import tiers  # noqa: E402

DATA = ROOT / "data"


class TierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = json.loads((DATA / "topics.json").read_text(encoding="utf-8"))
        cls.data = tiers.load()
        cls.info = cls.data["topics"]

    def test_every_topic_is_classified(self):
        self.assertEqual(len(self.info), len(self.topics))
        for t in self.topics:
            with self.subTest(topic=t["id"]):
                self.assertIn(self.info[t["id"]]["tier"], ("core", "tool"))

    def test_cluster_is_known(self):
        for tid, i in self.info.items():
            with self.subTest(topic=tid):
                self.assertIn(i["cluster"], tiers.CLUSTER_NAMES)

    def test_low_grade_application_and_reasoning_need_independent_units(self):
        for t in self.topics:
            if (t.get("grade") or 0) <= 2 and t.get("type") in ("application", "reasoning", "modeling"):
                with self.subTest(topic=t["id"]):
                    self.assertEqual(self.info[t["id"]]["tier"], "core")
                    self.assertTrue(self.info[t["id"]]["understanding"])

    def test_fluency_and_understanding_are_independent_of_resource_grouping(self):
        # 加法含义与口算可以在一个簇中教学，仍然同时需要理解与熟练。
        arithmetic = self.info["m1a.1.5"]
        self.assertEqual(arithmetic["tier"], "tool")
        self.assertTrue(arithmetic["fluency"])
        self.assertTrue(arithmetic["understanding"])
        self.assertEqual(self.info["m1a.2.5"]["tier"], "core")
        for info in self.info.values():
            self.assertIsInstance(info["fluency"], bool)
            self.assertIsInstance(info["understanding"], bool)
            self.assertEqual(info["review_status"], "draft")

    def test_application_reasoning_modeling_plan_independent_units(self):
        for t in self.topics:
            if t.get("type") in ("application", "modeling", "reasoning"):
                with self.subTest(topic=t["id"]):
                    self.assertEqual(self.info[t["id"]]["tier"], "core")

    def test_tool_topic_has_brief_group(self):
        for tid, i in self.info.items():
            if i["tier"] == "tool":
                with self.subTest(topic=tid):
                    self.assertTrue(i.get("brief_group"), "工具点必须有速查卡分组")

    def test_core_needs_more_practice(self):
        for tid, i in self.info.items():
            if i["tier"] == "core":
                with self.subTest(topic=tid):
                    self.assertGreaterEqual(i["practice_target"], 4)

    def test_overrides_take_effect(self):
        by_name = {(t["name"], t.get("grade")): t["id"] for t in self.topics}
        cases = [("三角形的面积", 5, "core"), ("三角形", 4, "core"),
                 ("长方体和正方体", 5, "core"), ("观察简单组合体", 5, "core"),
                 ("用圆规比较线段的长短", 3, "tool")]
        for name, grade, want in cases:
            tid = by_name[(name, grade)]
            with self.subTest(topic=name):
                self.assertEqual(self.info[tid]["tier"], want)

    def test_saved_tiers_match_current_rules_and_objectives(self):
        self.assertEqual(self.data, tiers.build(self.topics))

    def test_promoted_topics_retain_reference_without_claiming_full_card(self):
        for tid in ("m4b.3.1", "m5b.4.1", "m1a.2.5"):
            info = self.info[tid]
            self.assertEqual(info["teach_mode"], "full")
            self.assertTrue(info["reference_group"])
            self.assertNotIn("brief_group", info)


if __name__ == "__main__":
    unittest.main()
