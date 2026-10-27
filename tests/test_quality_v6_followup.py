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


if __name__ == "__main__":
    unittest.main()
