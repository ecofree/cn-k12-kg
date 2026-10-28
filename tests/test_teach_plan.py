"""Offline planning and reference cards must not inflate full teaching coverage."""
import pathlib
import tempfile
import unittest

from cnkg import tiers
from tools.rebuild_data import write_json
from tools.teach_plan import build_plan


class TeachPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = pathlib.Path(self.temp.name)
        self.topic = {"id": "m4b.3.1", "name": "三角形", "grade": 4, "volume": "四下",
                      "type": "conceptual", "mastery_criteria": ["能用内角和解决问题"]}
        write_json(self.data / "topics.json", [self.topic])
        self.question = {"id": "m4b.3.1-q1", "topicId": "m4b.3.1", "type": "numeric",
                         "prompt": "三角形两角是60度、50度，第三角多少度？",
                         "answer": "70", "verify": "180-60-50", "difficulty": "advanced"}
        write_json(self.data / "questions.json", [self.question])
        write_json(self.data / "topic_tiers.json", tiers.build([self.topic]))
        write_json(self.data / "teach/clusters/p-shape-recog@四下.json",
                   {"kind": "brief", "reference_only": True, "covers": ["m4b.3.1"]})

    def test_optional_external_file_is_not_required(self):
        plan = build_plan(self.data)
        self.assertFalse(plan["external_available"])
        self.assertEqual(plan["external_included_questions"], 0)
        self.assertEqual(plan["core"][0]["questions"], 1)

    def test_brief_reference_does_not_fill_full_card_gap(self):
        row = build_plan(self.data)["core"][0]
        self.assertTrue(row["has_reference_card"])
        self.assertFalse(row["has_card"])
        self.assertFalse(row["ready"])

    def test_difficulty_label_alone_is_not_application_evidence(self):
        row = build_plan(self.data)["core"][0]
        self.assertIn("advanced", row["levels"])
        self.assertFalse(row["has_application"])

    def test_external_data_requires_explicit_inclusion(self):
        external = {**self.question, "id": "external-q1", "source": "fixture", "evidence_level": "bare"}
        write_json(self.data / "questions_ext.json", [external])
        default = build_plan(self.data)
        self.assertTrue(default["external_available"])
        self.assertEqual(default["core"][0]["questions"], 1)
        included = build_plan(self.data, include_external=True)
        self.assertEqual(included["external_included_questions"], 1)
        self.assertEqual(included["core"][0]["questions"], 2)


if __name__ == "__main__":
    unittest.main()
