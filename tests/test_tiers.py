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

    def test_low_grade_is_tool(self):
        """一二年级默认工具点。

        这不是贬低：20 以内加减、乘法口诀的教学目标是「不用想就能算」，
        不是「能建模」。给它们写四步讲解+七道题，挤掉的是高年级应用题的时间。
        """
        for t in self.topics:
            if (t.get("grade") or 0) <= 2:
                with self.subTest(topic=t["id"]):
                    self.assertEqual(self.info[t["id"]]["tier"], "tool")

    def test_application_is_core_unless_overridden(self):
        for t in self.topics:
            if t.get("type") in ("application", "modeling") and t.get("grade", 0) >= 3:
                if tiers.override_of(t):
                    continue  # 人工覆盖优先，理由写在 OVERRIDES 里
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
        cases = [("三角形的面积", 5, "core"), ("三角形", 4, "tool"),
                 ("观察简单组合体", 5, "tool")]
        for name, grade, want in cases:
            tid = by_name[(name, grade)]
            with self.subTest(topic=name):
                self.assertEqual(self.info[tid]["tier"], want)

    def test_workload_much_smaller_than_flat_plan(self):
        """分层之后教学单元必须显著少于逐点平摊，否则分层没有意义。"""
        clusters = self.data["clusters"]
        units = sum(c["full_cards_needed"] + c["brief_cards_needed"]
                    for c in clusters.values())
        self.assertLess(units, len(self.topics) * 0.8,
                        f"教学单元 {units} 与点数 {len(self.topics)} 差别太小")


if __name__ == "__main__":
    unittest.main()
