"""A corrected task family carries its own exposure, never its whole old family."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg import task_families
from cnkg.bank_review import digest
from cnkg.learning_store import LearningStore
from cnkg.learning_needs import skill_evidence, task_options
from tests.test_quality_v5_decisions import catalog, helped_history, later_observation, followup


class TaskFamilyTransitionTests(unittest.TestCase):
    def test_only_reviewed_task_blocks_novelty_without_reclassifying_original_evidence(self):
        mapping = {"p": {"historical_family_id": "old-family", "canonical": "new-family"}}
        history = helped_history()
        history[0]["answers"][0]["family_id"] = "old-family"
        later = later_observation(history)
        records = [*history, later]
        saved, evidence = copy.deepcopy(records), skill_evidence(catalog(), records, [])
        self.assertEqual("resolved", followup(records)["status"])
        with patch.dict(task_families.TASK_TRANSITIONS, mapping, clear=True):
            self.assertEqual("old-family", task_families.exposure_family("old-family"))
            self.assertEqual("old-family", task_families.exposure_family("old-family", question_id="different-task"))
            self.assertEqual("new-family", task_families.exposure_family("old-family", question_id="p"))
            self.assertEqual("old-family", task_families.canonical_family("old-family"))
            self.assertEqual("open", followup(records)["status"])
            self.assertEqual(evidence, skill_evidence(catalog(), records, []))
            with tempfile.TemporaryDirectory() as directory:
                store = LearningStore(Path(directory) / "learning.sqlite")
                event = {"event_type": "reference_opened", "occurred_at": "2026-10-28T00:00:00+00:00",
                         "question_id": "p", "family_id": "old-family", "family_status": "declared",
                         "resource_id": "original", "payload": {}}
                store.add_event("owner", event)
                before = copy.deepcopy(store.exposures("owner"))
                options = task_options(catalog(), ["compare"], [], before, allowed_task_ids={"d"})
                self.assertTrue(options[0]["repeated_family"])
                self.assertTrue(options[0]["help_exposed"])
                self.assertFalse(options[0]["eligible_for_followup"])
                self.assertEqual(event["occurred_at"], store.latest_scope_activity(
                    "owner", [], question_ids=[], family_ids=["new-family"])["occurred_at"])
                self.assertEqual(before, store.exposures("owner"))
        self.assertEqual(saved, records)

    def test_transition_binds_both_authored_versions_and_current_grading_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data/content/example.json"
            binding_path = root / "data/skills/catalog-bindings.json"
            source.parent.mkdir(parents=True); binding_path.parent.mkdir(parents=True)
            old = {"id": "p", "type": "numeric", "prompt": "总数12，其中5，另一部分多少？",
                   "answer": "7", "family_id": "old-family"}
            current = {**old, "family_id": "new-family", "prompt": "两类互不重叠且包含全部12个，一类5个，另一类多少？"}
            binding = {"answer_spec": {"version": 1, "kind": "number"}}
            source.write_text(json.dumps({"questions": [current]}))
            binding_path.write_text(json.dumps({"tasks": {"bank:p": binding}}))
            row = {"task_id": "p", "source": "data/content/example.json", "baseline_task": old,
                   "current_task": current, "baseline_commit": "a" * 40,
                   "baseline_source_task_sha256": digest(old), "current_source_task_sha256": digest(current),
                   "current_task_contract_sha256": digest(task_families.task_contract(current, binding)),
                   "historical_family_id": "old-family", "canonical": "new-family", "use": "exposure_only",
                   "decision": "allow_same_structure", "reason": "两版都是已知总量与一部分求缺项，后版明确完整分类条件。",
                   "reviewer_kind": "ai", "independent_teacher_review": False}
            path = root / "transitions.json"
            path.write_text(json.dumps({"schema": 1, "tasks": [row]}))
            self.assertEqual({"p": {"historical_family_id": "old-family", "canonical": "new-family"}},
                             task_families.load_task_transitions(path, root=root))
            for change in ({"current_task_contract_sha256": "0" * 64}, {"historical_family_id": "different"},
                           {"baseline_source_task_sha256": "0" * 64}, {"decision": "unreviewed"},
                           {"use": "mastery"}, {"source": "data/content/../../secret.json"}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    path.write_text(json.dumps({"schema": 1, "tasks": [{**row, **change}]}))
                    task_families.load_task_transitions(path, root=root)


if __name__ == "__main__":
    unittest.main()
