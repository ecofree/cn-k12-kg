"""Current lesson links must not expose archived or unadmitted practice."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.teach_cards import check_card, check_brief, current_practice_ids


ROOT = Path(__file__).resolve().parents[1]


class CurrentLessonPracticeTests(unittest.TestCase):
    def test_archived_source_wins_even_if_admission_is_accidentally_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "task_admissions.json").write_text(json.dumps({"schema": 1, "tasks": {
                "active": {"policy": {"practice_eligible": True}},
                "archived": {"policy": {"practice_eligible": True}},
                "screening_only": {"policy": {"diagnosis_eligible": True, "practice_eligible": False}},
            }}))
            (data / "content_sources.json").write_text(json.dumps({"question_sources": [
                {"path": "archive.json", "status": "retired"}]}))
            (data / "archive.json").write_text(json.dumps({"questions": [{"id": "archived"}]}))
            self.assertEqual(current_practice_ids(data), {"active"})

    def test_unknown_admission_cannot_be_inferred_from_question_existence(self):
        card = json.loads((ROOT / "data/teach/m3a.4.4.json").read_text())
        topics = {topic["id"]: topic for topic in json.loads((ROOT / "data/topics.json").read_text())}
        bank = {q["id"]: q for q in json.loads((ROOT / "data/questions.json").read_text())}
        archived = "m3a.4.4-q9"
        self.assertIn(archived, bank)  # Historical archive is intentionally retained.
        card["practice"].append({"id": archived, "note": "unexpected retired link"})
        errors = check_card(card, topics, bank, practice_ids=current_practice_ids(ROOT / "data"))
        self.assertTrue(any(archived in error and "已退役" in error for error in errors), errors)
        denied = card["practice"][0]["id"]
        errors = check_card(card, topics, bank, practice_ids=set())
        self.assertTrue(any(denied in error and "未获当前练习准入" in error for error in errors), errors)

    def test_brief_cards_apply_the_same_admission_rule(self):
        card = {"practice": [{"id": "archived"}], "covers": ["topic"]}
        bank = {"archived": {"id": "archived", "topicId": "topic"}}
        errors = check_brief(card, {}, {}, bank, practice_ids=set())
        self.assertTrue(any("archived" in error and "已退役" in error for error in errors), errors)

    def test_every_current_card_and_brief_has_only_live_practice_links(self):
        allowed = current_practice_ids(ROOT / "data")
        failures = []
        for path in sorted((ROOT / "data/teach").rglob("*.json")):
            card = json.loads(path.read_text())
            for ref in card.get("practice", []):
                qid = ref.get("id") if isinstance(ref, dict) else ref
                if qid not in allowed:
                    failures.append(f"{path.relative_to(ROOT)}: {qid}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
