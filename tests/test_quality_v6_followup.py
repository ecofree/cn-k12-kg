"""Regression coverage for concrete Quality V6 follow-up corrections."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReverseAdditionLessonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "data" / "teach" / "m1a.1.6.json"
        cls.card = json.loads(path.read_text(encoding="utf-8"))

    def test_reverse_addition_is_an_explicit_objective_not_a_keyword_tip(self):
        goals = self.card["objective_coverage"]
        goal = next((g for g in goals if g.get("criterion_index") == 2), None)
        self.assertIsNotNone(goal)
        self.assertIn("加法求原来的总数", goal["title"])
        self.assertIn("已知去掉数与剩余数", goal["title"])
        self.assertIn("2+3=5", goal["self_check"]["reference_answer"])
        self.assertTrue(goal["observation_note"])
        self.assertTrue(goal["difficulty_support"]["discriminating_prompt"])
        self.assertTrue(goal["difficulty_support"]["hint"])
        self.assertTrue(goal["difficulty_support"]["revision_prompt"])

    def test_old_objectives_and_identity_are_preserved(self):
        self.assertEqual(self.card["topicId"], "m1a.1.6")
        self.assertEqual(
            [g["criterion_index"] for g in self.card["objective_coverage"]],
            [0, 1, 2],
        )
        self.assertIn("拿走", self.card["objective_coverage"][0]["title"])
        self.assertIn("口算", self.card["objective_coverage"][1]["title"])
        self.assertEqual(self.card["content_revision"]["teacher_review"], "pending")

    def test_new_objective_has_a_stable_skill_identity(self):
        registry = json.loads((ROOT / "data" / "skills" / "registry.json").read_text(encoding="utf-8"))
        skill_ids = registry["placements"]["m1a.1.6"]["criterion_skill_ids"]
        self.assertEqual(len(skill_ids), 3)
        new_skill = registry["skills"][skill_ids[2]]
        self.assertEqual(new_skill["granularity"], "observable")
        self.assertIn("加法求原来的总数", new_skill["statement"])
        self.assertIn(skill_ids[0], new_skill["refines_skill_ids"])
        source = json.loads((ROOT / "data" / "content" / "yishang.json").read_text(encoding="utf-8"))
        criteria = source["topics"]["m1a.1.6"]["mastery_criteria"]
        self.assertEqual(criteria[2], new_skill["statement"])


if __name__ == "__main__":
    unittest.main()
