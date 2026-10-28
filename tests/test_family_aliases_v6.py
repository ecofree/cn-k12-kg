"""A reviewed rename must not erase old help, timing, or unresolved follow-up."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg import task_families
from cnkg.learning_needs import task_options
from cnkg.learning_store import LearningStore
from cnkg.review_timing import indexed_scope_activity
from tests.test_goal_observation_v6 import graph_for, answer_two
from tests.test_quality_v5_decisions import catalog, record, helped_history, later_observation, followup


class FamilyAliasTests(unittest.TestCase):
    def test_old_help_is_found_by_the_new_name_without_rewriting_events_or_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite")
            event = {"event_type": "reference_opened", "occurred_at": "2026-10-26T23:00:00+00:00",
                     "resource_id": "old-lesson", "payload": {"family_ids": ["old-family"]}}
            store.add_event("learner", event)
            before = copy.deepcopy(store.events("learner"))
            with patch.dict(task_families.ALIASES, {"old-family": "new-family"}):
                observed = store.exposures("learner", question_ids=[], family_ids=["new-family"])
                self.assertIn("old-family", observed["families"])
                self.assertNotIn("new-family", observed["families"])
                option = task_options(catalog(), ["compare"], [], observed, allowed_task_ids={"d"})[0]
                self.assertTrue(option["help_exposed"])
                self.assertTrue(option["repeated_family"])
                self.assertFalse(option["eligible_for_followup"])
                latest = store.latest_scope_activity("learner", [], family_ids=["new-family"])
                self.assertEqual(latest["occurred_at"], event["occurred_at"])
                scope = {"question_ids": [], "family_ids": ["new-family"]}
                self.assertEqual(indexed_scope_activity(scope, observed)["occurred_at"], event["occurred_at"])
            self.assertEqual(store.events("learner"), before)
            self.assertEqual(set(store.exposures("learner")["families"]), {"old-family"})

    def test_alias_of_an_answered_family_is_a_repeat_without_an_exposure_index(self):
        original = record()
        saved = copy.deepcopy(original)
        with patch.dict(task_families.ALIASES, {"old-family": "new-family"}):
            option = task_options(catalog(), ["compare"], [original], allowed_task_ids={"d"})[0]
            self.assertTrue(option["repeated_family"])
            self.assertFalse(option["eligible_for_followup"])
        self.assertEqual(original, saved)

    def test_renamed_structure_does_not_count_as_a_second_observation(self):
        with patch.dict(task_families.ALIASES, {"addition.old-name": "addition.combine"}):
            session = answer_two(graph_for([{"family_id": "addition.old-name"}, {}]))
            self.assertEqual(session["state"]["target"], "insufficient_evidence")
            self.assertIn("insufficient_structural_variety", session["evidence_gaps"]["target"])

    def test_alias_cannot_close_an_old_independent_followup_need(self):
        history = helped_history()
        later = later_observation(history)
        self.assertEqual(followup([*history, later])["status"], "resolved")
        original = copy.deepcopy([*history, later])
        with patch.dict(task_families.ALIASES, {"old-family": "new-family"}):
            self.assertEqual(followup([*history, later])["status"], "open")
        self.assertEqual([*history, later], original)

    def test_duplicate_name_index_counts_are_not_added_as_independent_events(self):
        with patch.dict(task_families.ALIASES, {"old": "new"}):
            value = task_families.family_exposure({"old": {"reference_count": 1}, "new": {"reference_count": 1}}, "new")
            self.assertEqual(value["reference_count"], 1)
            self.assertEqual(value["count_basis"], "maximum_per_indexed_name")

    def test_alias_registry_rejects_cycles_and_unreviewed_relations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aliases.json"
            def row(alias, canonical):
                return {"alias": alias, "canonical": canonical, "reason": "同一数量关系，仅使用旧命名。",
                        "reviewer_kind": "ai", "independent_review": False}
            path.write_text(json.dumps({"schema": 1, "aliases": [row("old", "middle"), row("middle", "new")]}))
            self.assertEqual(task_families.load_aliases(path), {"old": "new", "middle": "new"})
            path.write_text(json.dumps({"schema": 1, "aliases": [row("old", "new"), row("new", "old")]}))
            with self.assertRaisesRegex(ValueError, "循环"):
                task_families.load_aliases(path)
            missing = row("old", "new"); missing["reason"] = ""
            path.write_text(json.dumps({"schema": 1, "aliases": [missing]}))
            with self.assertRaises(ValueError):
                task_families.load_aliases(path)


if __name__ == "__main__":
    unittest.main()
