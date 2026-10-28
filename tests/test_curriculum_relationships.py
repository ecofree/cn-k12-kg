"""Regression cases for reviewed curriculum relationship corrections."""
import json
from pathlib import Path
import unittest


DATA = Path(__file__).resolve().parents[1] / "data"


class CurriculumRelationshipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}
        cls.edges = {(e["topicId"], e["prerequisiteId"]): e
                     for e in json.loads((DATA / "dependencies.json").read_text(encoding="utf-8"))}

    def test_cylinder_cone_can_trace_missing_circle_measurement_and_volume(self):
        for prerequisite in ("m6a.5.3", "m6a.5.2", "m5b.4.1"):
            self.assertEqual(self.edges[("m6b.3.1", prerequisite)]["strength"], "hard")
        self.assertNotIn(("m6b.3.1", "m5a.6.4"), self.edges)

    def test_threshold_uses_statistical_context_not_composite_area(self):
        self.assertNotIn(("m6a.6.3", "m5a.6.4"), self.edges)
        self.assertEqual(self.edges[("m6a.6.3", "m4b.6.1")]["strength"], "soft")

    def test_negative_numbers_bridge_primary_experience(self):
        self.assertIn(("m7a.1.1", "m6a.4.2"), self.edges)
        topic = self.topics["m7a.1.1"]
        self.assertNotIn("root_reason", topic)
        self.assertIn("小学六上", topic["transition_note"])

    def test_rectangular_prisms_are_geometry_and_remain_draft(self):
        topic = self.topics["m5b.4.1"]
        self.assertEqual(topic["domain"], "图形与几何")
        self.assertEqual(topic["review_status"], "draft")


if __name__ == "__main__":
    unittest.main()
