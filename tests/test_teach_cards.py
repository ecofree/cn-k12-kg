"""讲解卡的守门测试。

讲解卡是本项目第一次有"教"的内容。它的错误比题目错误更难被发现：
题目错，学生会做错；讲解错，学生会**照着错的学会**。
所以这里把三条底线固化成测试：

1. 练习题必须真实存在，且确实挂在这个知识点上（否则卡片把人导向没学的题）。
2. 例题的 verify 必须能算出 answer（讲解里的例题自己算错，是最坏的情况）。
3. 练习题不能全是选择题（"会选"不等于"会算"）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.evidence import safe_eval  # noqa: E402
from tools.teach_cards import check_brief, check_card  # noqa: E402

DATA = ROOT / "data"


class TeachCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = {t["id"]: t for t in json.loads(
            (DATA / "topics.json").read_text(encoding="utf-8"))}
        cls.bank = {q["id"]: q for q in json.loads(
            (DATA / "questions.json").read_text(encoding="utf-8"))}
        cls.cards = []
        for path in sorted((DATA / "teach").glob("*.json")):
            cls.cards.append((path.name, json.loads(path.read_text(encoding="utf-8"))))

    def test_cards_exist(self):
        self.assertGreaterEqual(len(self.cards), 8, "第一批讲解卡丢了")

    def test_every_card_passes_check(self):
        for name, card in self.cards:
            with self.subTest(card=name):
                self.assertEqual(check_card(card, self.topics, self.bank), [])

    def test_card_topic_matches_filename(self):
        for name, card in self.cards:
            with self.subTest(card=name):
                self.assertEqual(f"{card['topicId']}.json", name)

    def test_worked_example_is_self_consistent(self):
        """例题的算式必须等于例题的答案。

        这条单独再测一遍，因为 check_card 允许"没给 verify"，
        而讲解卡里的例题**应该**给 verify——不给就没法防手滑。
        """
        for name, card in self.cards:
            ex = card["worked_example"]
            with self.subTest(card=name):
                self.assertTrue(ex.get("verify"), "例题缺 verify，无法自校验")
                got = safe_eval(ex["verify"])
                want = float(str(ex["answer"]).strip())
                self.assertAlmostEqual(got, want, places=6)

    def test_practice_points_to_own_questions(self):
        for name, card in self.cards:
            tid = card["topicId"]
            for ref in card["practice"]:
                q = self.bank[ref["id"]]
                with self.subTest(card=name, question=ref["id"]):
                    self.assertIn(tid, q.get("topicIds") or [q.get("topicId")])

    def test_practice_has_a_non_choice_question(self):
        for name, card in self.cards:
            types = {self.bank[r["id"]].get("type") for r in card["practice"]}
            with self.subTest(card=name):
                self.assertTrue(types - {"choice"}, "练习题全是选择题")


class BriefCardTest(unittest.TestCase):
    """速查卡：工具点的合并卡。

    工具点不逐点写四步讲解，但也不能没有卡——一年级 157 个基础点
    一张都没有，学生学「9 加几」时看到的还是空的。合并卡是这两者之间的解。
    """

    @classmethod
    def setUpClass(cls):
        cls.topics = {t["id"]: t for t in json.loads(
            (DATA / "topics.json").read_text(encoding="utf-8"))}
        cls.tiers = json.loads((DATA / "topic_tiers.json").read_text(encoding="utf-8"))
        cls.bank = {q["id"]: q for q in json.loads(
            (DATA / "questions.json").read_text(encoding="utf-8"))}
        cls.dir = DATA / "teach" / "clusters"
        cls.cards = []
        if cls.dir.exists():
            for path in sorted(cls.dir.glob("*.json")):
                cls.cards.append((path.name, json.loads(path.read_text(encoding="utf-8"))))

    def test_every_brief_passes_check(self):
        for name, card in self.cards:
            with self.subTest(card=name):
                self.assertEqual(check_brief(card, self.topics, self.tiers,
                                             self.bank), [])

    def test_all_tool_points_are_covered(self):
        """每个工具点必须正好被一张速查卡覆盖。

        漏一个点，这个点在系统里就既没有完整卡、也不在任何速查卡里，
        等于这个知识点根本没讲——这比「卡片不够精致」严重得多。
        重复覆盖则会出现两套说法，学生不知道信哪套。
        """
        seen: dict[str, str] = {}
        for name, card in self.cards:
            for tid in card["covers"]:
                with self.subTest(card=name, topic=tid):
                    self.assertNotIn(tid, seen,
                                     f"{tid} 同时被 {seen.get(tid)} 和 {name} 覆盖")
                    seen[tid] = name
        tools = {tid for tid, info in self.tiers["topics"].items()
                 if info["tier"] == "tool"}
        self.assertEqual(sorted(tools - set(seen)), [], "有工具点没有任何速查卡")
        self.assertEqual(sorted(set(seen) - tools), [], "速查卡覆盖了非工具点")

    def test_one_card_per_group(self):
        """一张卡 = 一个「簇 × 册次」组，不多不少。"""
        groups = {}
        for tid, info in self.tiers["topics"].items():
            if info["tier"] == "tool":
                groups.setdefault(info["brief_group"], set()).add(tid)
        for name, card in self.cards:
            key = f"{card['cluster']}@{card['volume']}"
            with self.subTest(card=name):
                self.assertIn(key, groups)
                self.assertEqual(set(card["covers"]), groups[key])

    def test_filename_matches_group(self):
        for name, card in self.cards:
            with self.subTest(card=name):
                self.assertEqual(f"{card['cluster']}@{card['volume']}.json", name)

    def test_covers_only_tool_topics(self):
        for name, card in self.cards:
            for tid in card["covers"]:
                with self.subTest(card=name, topic=tid):
                    self.assertEqual(self.tiers["topics"][tid]["tier"], "tool")


if __name__ == "__main__":
    unittest.main()
