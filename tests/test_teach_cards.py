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
from cnkg.verify import to_number, compare  # noqa: E402
from tools.teach_cards import check_brief, check_card, check_objective_coverage  # noqa: E402
import copy

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

        数值例题必须复算。证明、作图与多结果例题核对真实步骤及结论，
        不能为了填verify把参考证明伪装成一个无关数字。
        """
        for name, card in self.cards:
            ex = card["worked_example"]
            with self.subTest(card=name):
                if to_number(ex.get("answer")) is not None:
                    self.assertTrue(ex.get("verify"), "数值例题缺 verify，无法自校验")
                    self.assertTrue(compare(ex["answer"], safe_eval(ex["verify"])))
                else:
                    self.assertTrue(ex.get("answer"))
                    self.assertGreaterEqual(len(ex.get("steps", [])), 2)

    def test_objective_mapping_detects_missing_duplicate_or_stale_goals(self):
        card = next(copy.deepcopy(c) for _, c in self.cards if c.get('tier') == 'core')
        topic = self.topics[card['topicId']]
        self.assertEqual(check_objective_coverage(card, topic), [])
        incomplete = copy.deepcopy(card)
        incomplete['objective_coverage'].pop()
        self.assertTrue(check_objective_coverage(incomplete, topic))
        duplicate = copy.deepcopy(card)
        duplicate['objective_coverage'].append(copy.deepcopy(duplicate['objective_coverage'][0]))
        self.assertTrue(check_objective_coverage(duplicate, topic))
        changed_topic = copy.deepcopy(topic)
        changed_topic['mastery_criteria'][0] += '（目标已改变）'
        self.assertTrue(check_objective_coverage(card, changed_topic))

    def test_self_check_needs_answer_and_observable_criteria(self):
        card = next(copy.deepcopy(c) for _, c in self.cards if c.get('tier') == 'core')
        topic = self.topics[card['topicId']]
        card['objective_coverage'][0]['self_check']['reference_answer'] = '略'
        self.assertTrue(check_objective_coverage(card, topic))
        card['objective_coverage'][0]['self_check']['reference_answer'] = '有具体解答'
        card['objective_coverage'][0]['self_check']['criteria'] = []
        self.assertTrue(check_objective_coverage(card, topic))

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
    """速查卡保留原参考范围，允许辅助 core，但不能冒充完整讲解。"""

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
        """既有工具点有可查参考，不把关联等同于全部目标已讲清。"""
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
        self.assertTrue(set(seen) - tools, "升级后的 core 参考内容不应被丢弃")

    def test_one_card_per_group(self):
        """一张卡 = 一个「簇 × 册次」组，不多不少。"""
        groups = {}
        for tid, info in self.tiers["topics"].items():
            group = info.get("reference_group") or info.get("brief_group")
            if group:
                groups.setdefault(group, set()).add(tid)
        for name, card in self.cards:
            key = f"{card['cluster']}@{card['volume']}"
            with self.subTest(card=name):
                self.assertIn(key, groups)
                self.assertEqual(set(card["covers"]), groups[key])

    def test_filename_matches_group(self):
        for name, card in self.cards:
            with self.subTest(card=name):
                self.assertEqual(f"{card['cluster']}@{card['volume']}.json", name)

    def test_core_references_are_explicitly_not_full_teaching(self):
        for name, card in self.cards:
            self.assertTrue(card["reference_only"])
            for tid in card["covers"]:
                with self.subTest(card=name, topic=tid):
                    if self.tiers["topics"][tid]["tier"] == "core":
                        self.assertEqual(self.tiers["topics"][tid]["teach_mode"], "full")
                        self.assertEqual(card["kind"], "brief")


if __name__ == "__main__":
    unittest.main()
