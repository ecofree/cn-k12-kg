"""Exercise every authored support's actual routes without claiming subject review."""
import copy
import json
from pathlib import Path
import unittest

from cnkg import instruction
from cnkg.catalog import load_catalog


ROOT = Path(__file__).resolve().parents[1]
OWNER = "a" * 64


def submit(record, value=None, skipped=False):
    qid = record["task_ids"][record["index"]]
    task = record["question_snapshots"][qid]
    if value is None:
        value = task.get("answer", "按原任务写下过程，留待量规检查。")
    return instruction.answer(record, qid, str(value), f"2026-02-01T00:00:0{record['index'] + 1}+00:00", skipped)


class EveryAuthoredSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()
        cls.supports = []
        for path in sorted((ROOT / "data/learning_units").glob("*.json")):
            unit = json.loads(path.read_text())
            cls.supports.extend((unit, support) for support in unit.get("instructional_support", {}).get("supports", []))

    def create(self, unit, support):
        return instruction.create(unit, support["id"], OWNER, self.catalog, {}, [], "2026-02-01T00:00:00+00:00")[0]

    def test_every_authored_wrong_choice_selects_only_its_exact_frozen_task_and_hint_families(self):
        for unit, support in self.supports:
            for branch in support.get("guided_branches", []):
                for choice in branch["choice_ids"]:
                    with self.subTest(goal=support["objective_id"], choice=choice):
                        record = self.create(unit, support)
                        snapshot = copy.deepcopy(record["support_snapshot"])
                        feedback, events = submit(record, choice)
                        self.assertEqual(feedback["status"], "wrong")
                        shown = instruction.public_session(record)
                        self.assertEqual(shown["support"]["route"]["route_id"], branch["id"])
                        self.assertEqual(shown["item"]["prompt"], branch["guided_task"]["prompt"])
                        self.assertEqual(shown["item"]["objective_ids"], [support["objective_id"]])
                        self.assertNotIn("answer", shown["item"])
                        for other in support.get("guided_branches", []):
                            if other["id"] != branch["id"]:
                                self.assertNotIn(other["guided_task"]["prompt"], json.dumps(shown, ensure_ascii=False))
                        self.assertEqual([event["question_id"] for event in events if event["event_type"] == "question_presented"],
                                         [shown["item"]["id"]])
                        for level, authored in enumerate(support["hints"], 1):
                            hint, hint_events = instruction.hint(record, level, "2026-02-01T00:00:10+00:00")
                            self.assertEqual(hint["text"], authored["text"])
                            branch_hints = support.get("branch_hints", {}).get(choice, [])
                            if "reference_family_ids" in authored and (len(branch_hints) < level or
                                    isinstance(branch_hints[level - 1], dict) and "reference_family_ids" in branch_hints[level - 1]):
                                expected = set(authored["reference_family_ids"])
                                if len(branch_hints) >= level:
                                    expected.update(branch_hints[level - 1]["reference_family_ids"])
                                self.assertEqual(set(hint_events[0]["payload"]["family_ids"]), expected)
                                self.assertIsNone(hint_events[0]["question_id"])
                        self.assertEqual(record["support_snapshot"], snapshot)
                        self.assertIsNone(instruction.route_problem(record))

    def test_a_skipped_probe_never_becomes_confirmed_just_because_later_answers_match(self):
        for unit, support in self.supports:
            with self.subTest(goal=support["objective_id"]):
                record = self.create(unit, support)
                submit(record, "", skipped=True)
                self.assertEqual(record["route_selection"]["route_id"], "skip")
                expected = support.get("skip_route", {}).get("guided_task", support["guided_task"])
                self.assertEqual(instruction.public_session(record)["item"]["prompt"], expected["prompt"])
                submit(record)
                final, _ = submit(record)
                self.assertFalse(final["confirmation_eligible"])
                self.assertEqual(final["resolution_for_need_ids"], [])
                self.assertIsNone(instruction.route_problem(record))
                self.assertTrue(instruction.public_session(record)["done"])
                self.assertFalse(record["context"]["independent_evidence"])

    def test_correct_probe_and_guide_preserve_only_bounded_confirmation(self):
        for unit, support in self.supports:
            with self.subTest(goal=support["objective_id"]):
                record = self.create(unit, support)
                diagnostic, _ = submit(record)
                guided, _ = submit(record)
                final, _ = submit(record)
                expected = all(row["status"] == "correct" for row in (diagnostic, guided, final))
                self.assertEqual(final["confirmation_eligible"], expected)
                self.assertTrue(final["help_used"])
                self.assertFalse(record["context"]["independent_evidence"])
                self.assertIsNone(instruction.route_problem(record))


if __name__ == "__main__":
    unittest.main()
