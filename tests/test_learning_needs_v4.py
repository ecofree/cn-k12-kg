"""Needs survive new phases, versions and restores without rewriting evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cnkg.learning_needs import derive_needs, task_options
from cnkg.learning_store import LearningStore, SCHEMA_VERSION, _work_summary
from cnkg.planner import plan


NOW = "2026-10-26T00:00:00+00:00"
OLD = "2026-10-24T00:00:00+00:00"


def catalog():
    return {"units": {"u": {"id": "u", "title": "读图与比较", "stage": "primary", "resource_version": "v1",
        "skill_ids": ["read", "compare"], "objectives": [{"id": "o1", "skill_id": "read"},
                                                               {"id": "o2", "skill_id": "compare"}],
        "tasks": [{"id": "p", "phase": "practice"}, {"id": "t", "phase": "transfer"},
                  {"id": "d", "phase": "delayed"}]}},
        "placements": {"topic": {"stage": "primary"}},
        "skills": {"read": {"statement": "读出数据", "prerequisite_skill_ids": []},
                   "compare": {"statement": "比较数据", "prerequisite_skill_ids": ["read"]}},
        "tasks": {"repeat": {"id": "repeat", "skill_ids": ["compare"], "family_id": "f1", "family_status": "declared",
                               "representation": "bar", "resource_version": "task-v1"},
                  "new": {"id": "new", "skill_ids": ["compare"], "family_id": "f2", "family_status": "declared",
                            "representation": "table", "resource_version": "task-v1"}}}


def session(identity="old", status="wrong", *, question="p", completed=OLD, version="v1", kind="unit", mode="practice"):
    return {"id": identity, "owner_id": "alice", "kind": kind, "unit_id": "u" if kind == "unit" else None,
            "title": "读图与比较", "targets": ["topic"] if kind == "topic" else [], "stage": "primary",
            "mode": mode, "created_at": completed or OLD, "completed_at": completed,
            "resource_version": version, "content_sha256": version,
            "answers": [{"question_id": question, "status": status, "answered_at": completed or OLD,
                         "skill_ids": ["compare"], "objective_ids": ["o2"],
                         "family_id": "f1", "family_status": "declared", "representation": "bar"}]}


def work_payload(statuses, *, kind="session_task"):
    return {"kind": kind, "source_ref": {"session_id": "old", "question_id": "p"} if kind == "session_task" else "project",
            "resource_version": "question-v1", "resource_snapshot": {"title": "比较过程", "stage": "primary",
                "criteria": ["读数正确", "说明比较依据"]}, "draft": {"process": "先读再比"}, "status": "submitted",
            "self_assessment": [{"criterion_index": index, "status": status} for index, status in enumerate(statuses)]}


def work_summary(statuses, *, kind="session_task"):
    return _work_summary({"resource_id": "session_task:old:p" if kind == "session_task" else "project:one",
                          "resource_version": "question-v1", "revision": 2, "created_at": OLD,
                          "updated_at": NOW, "evidence_type": "learner_reported", "payload": work_payload(statuses, kind=kind)})


class LearningNeedsTests(unittest.TestCase):
    def test_completed_wrong_topic_remains_actionable_after_new_session(self):
        old = session(kind="topic")
        newer = session("newer", "correct", question="different", completed=NOW, kind="topic")
        result = plan(catalog(), [old, newer], now=NOW)
        step = next(item for item in result["next_steps"] if item.get("kind") == "topic")
        self.assertEqual(step["action"], "practice")
        self.assertEqual(step["targets"], ["topic"])
        self.assertEqual(step["source_refs"], [{"session_id": "old", "question_id": "p"}])
        self.assertEqual(result["learning_needs"][0]["status"], "open")

    def test_old_process_need_survives_new_phase_and_content_version(self):
        old = session(status="needs_review")
        later = session("later", "correct", question="t", completed=NOW, mode="transfer", version="v2")
        changed = catalog()
        changed["units"]["u"]["resource_version"] = "v2"
        result = plan(changed, [old, later], [work_summary(["met", "partial"])], now=NOW)
        revise = next(item for item in result["next_steps"] if item.get("continuation") == "reflect_and_revise")
        self.assertEqual(revise["session_id"], "old")
        self.assertEqual(revise["resource_version"], "v1")
        self.assertEqual(revise["criterion_outcomes"][0]["outcomes"][1]["status"], "partial")
        self.assertEqual(result["learning_needs"][0]["unresolved_criterion_indices"], [1])

    def test_partial_revisit_and_met_have_different_followups_without_regrading(self):
        original = session(status="needs_review")
        before = copy.deepcopy(original)
        ids = []
        for status in ("partial", "revisit", "met"):
            summary = work_summary(["met", status])
            self.assertTrue(summary["self_assessment_completed"])
            result = plan(catalog(), [original], [summary], now=NOW)
            need = result["learning_needs"][0]
            ids.append(need["need_id"])
            self.assertEqual(need["status"], "resolved" if status == "met" else "open")
            self.assertEqual(any(step.get("continuation") == "reflect_and_revise" for step in result["next_steps"]), status != "met")
            if status == "met":
                self.assertEqual(need["resolution"]["kind"], "learner_reported_revision")
                self.assertFalse(need["resolution"]["independent_evidence"])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(original, before)

    def test_older_wrong_answer_needs_matching_task_and_frozen_version_to_close(self):
        old = session()
        later = session("later", "correct", completed=NOW, version="v2")
        need = derive_needs(catalog(), [old, later])[0]
        self.assertEqual(need["status"], "open")
        later["resource_version"] = later["content_sha256"] = "v1"
        resolved = derive_needs(catalog(), [old, later])[0]
        self.assertEqual(resolved["need_id"], need["need_id"])
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["resolution"]["source_ref"]["session_id"], "later")
        self.assertFalse(resolved["resolution"]["independent_evidence"])
        self.assertEqual(old["answers"][0]["status"], "wrong")

    def test_support_only_resolves_explicit_terminal_confirmation_with_original_version(self):
        old = session()
        need_id = derive_needs(catalog(), [old])[0]["need_id"]
        support = session("help", "correct", question="confirm", kind="support", mode="support", version="support-v3", completed=NOW)
        support["source_origin"] = {"need_ids": [need_id], "resource_version": "v1"}
        answer = support["answers"][0]
        answer["help_used"] = True
        self.assertEqual(derive_needs(catalog(), [old, support])[0]["status"], "open")
        answer["resolution_for_need_ids"] = [need_id]
        result = derive_needs(catalog(), [old, support])[0]
        self.assertEqual(result["resolution"]["kind"], "supported_confirmation")
        self.assertTrue(result["resolution"]["help_used"])
        self.assertFalse(result["resolution"]["independent_evidence"])
        support["source_origin"]["resource_version"] = "v2"
        self.assertEqual(derive_needs(catalog(), [old, support])[0]["status"], "open")
        support["source_origin"]["resource_version"] = "v1"
        answer["skill_ids"] = ["unrelated"]
        self.assertEqual(derive_needs(catalog(), [old, support])[0]["status"], "open")

    def test_explicit_intra_unit_skill_prerequisites_drive_support_routes(self):
        result = plan(catalog(), [session()], now=NOW)
        action = result["next_steps"][0]
        self.assertEqual(action["skill_ids"], ["compare"])
        self.assertEqual(action["prerequisite_skill_ids"], ["read"])
        route = next(route for route in action["support_routes"] if route["role"] == "prerequisite")
        self.assertEqual(route["unit_id"], "u")
        self.assertTrue(route["within_current_unit"])
        self.assertEqual(route["objective_ids"], ["o1"])
        unstarted = plan(catalog(), [], now=NOW)["next_steps"][0]
        self.assertEqual(unstarted["prerequisite_skill_ids"], ["read"])
        self.assertEqual(unstarted["prerequisite_unit_ids"], [])
        self.assertEqual(result["skill_evidence"][0]["status_counts"], {"wrong": 1})

    def test_guided_errors_attach_to_original_need_without_duplicate_orphans(self):
        old = session()
        need_id = derive_needs(catalog(), [old])[0]["need_id"]
        support = session("help", "wrong", question="guided", kind="support", mode="support", completed=NOW)
        support["source_origin"] = {"need_ids": [need_id], "resource_version": "v1", "unit_id": "u"}
        support["answers"].append({**support["answers"][0], "question_id": "guided-2"})
        needs = derive_needs(catalog(), [old, support])
        self.assertEqual(len(needs), 1)
        self.assertEqual(len(needs[0]["followup_observations"]), 2)
        self.assertEqual(needs[0]["status"], "open")

    def test_unlinked_support_retains_one_resume_need_until_terminal_confirmation(self):
        support = session("help", "wrong", question="guided", kind="support", mode="support", completed=NOW)
        support["source_origin"] = {"need_ids": [], "unit_id": "u"}
        support["answers"].append({**support["answers"][0], "question_id": "guided-2"})
        result = plan(catalog(), [support], now=NOW)
        self.assertEqual(len(result["learning_needs"]), 1)
        self.assertEqual(result["next_steps"][0]["continuation"], "resume_support")
        self.assertEqual(result["next_steps"][0]["session_id"], "help")
        support["answers"].append({"question_id": "confirm", "status": "correct", "support_kind": "confirmation",
                                   "answered_at": "2026-10-26T00:01:00+00:00", "help_used": True})
        after = plan(catalog(), [support], now="2026-10-26T00:02:00+00:00")
        self.assertEqual(after["learning_needs"][0]["status"], "resolved")
        self.assertFalse(any(step.get("continuation") == "resume_support" for step in after["next_steps"]))

    def test_task_choice_accounts_for_known_family_representation_and_reference_help(self):
        seen = session(question="repeat")
        options = task_options(catalog(), ["compare"], [seen])
        self.assertEqual(options[0]["task_id"], "new")
        self.assertTrue(options[0]["new_representation"])
        self.assertTrue(options[1]["repeated_family"])
        exposed = task_options(catalog(), ["compare"], [seen], {"families": {"f2": {"reference_count": 1}}})
        new = next(row for row in exposed if row["task_id"] == "new")
        self.assertTrue(new["help_exposed"])
        self.assertTrue(new["repeated_family"])
        self.assertFalse(new["independent_evidence"])

    def test_submitted_work_partial_still_needs_revision_and_all_met_keeps_resolution(self):
        partial = work_summary(["met", "revisit"], kind="project")
        initial = plan(catalog(), [], [partial], now=NOW)
        need = initial["learning_needs"][0]
        self.assertEqual(need["status"], "open")
        self.assertTrue(any(step.get("work_resource_id") == "project:one" for step in initial["next_steps"]))
        completed = work_summary(["met", "met"], kind="project")
        after = plan(catalog(), [], [completed], now=NOW)
        self.assertEqual(after["learning_needs"][0]["need_id"], need["need_id"])
        self.assertEqual(after["learning_needs"][0]["status"], "resolved")


class LearningNeedsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "learning.sqlite"
        self.store = LearningStore(self.path)

    def test_planning_read_has_no_thousand_record_cap_or_latest_phase_loss(self):
        first = session()
        self.store.save(first)
        for index in range(1001):
            self.store.save(session(f"later-{index:04}", "correct", question="different", completed=NOW))
        other = session("other", "wrong")
        other["owner_id"] = "bob"
        self.store.save(other)
        records = self.store.planning_records("alice")
        self.assertEqual(len(records), 1002)
        self.assertEqual(len(self.store.latest_scope_records("alice")), 1)
        result = plan(catalog(), records, now=NOW)
        self.assertEqual(result["need_counts"]["open"], 1)
        self.assertEqual(result["learning_needs"][0]["source_ref"]["session_id"], "old")
        self.assertNotIn("owner_id", records[0])

    def test_defer_is_owned_auditable_restorable_and_distinct_from_resolution(self):
        self.store.save(session())
        need_id = derive_needs(catalog(), self.store.planning_records("alice"))[0]["need_id"]
        with self.assertRaises(KeyError):
            self.store.set_need_state("bob", need_id, "defer", "稍后继续")
        with self.assertRaises(ValueError):
            self.store.set_need_state("alice", need_id, "resolve", "自认掌握")
        state = self.store.set_need_state("alice", need_id, "defer", "先完成读数基础")
        self.assertEqual(state["status"], "deferred")
        restored = LearningStore(self.path)
        result = plan(catalog(), restored.planning_records("alice"), need_states=restored.need_states("alice"), now=NOW)
        self.assertEqual(result["need_counts"], {"open": 0, "deferred": 1, "resolved": 0})
        self.assertIsNone(result["learning_needs"][0]["resolution"])
        self.assertEqual(restored.need_states("bob"), [])
        restored.set_need_state("alice", need_id, "reopen", "现在继续")
        self.assertEqual(restored.need_states("alice")[0]["status"], "open")
        self.assertEqual(restored.load("old", "alice")["answers"][0]["status"], "wrong")
        events = restored.events("alice")
        self.assertEqual([event["event_type"] for event in events], ["learning_need_deferred", "learning_need_reopened"])

    def test_v3_migration_rebuilds_only_derived_summaries(self):
        record = session(status="needs_review")
        record["task_metadata"] = {"p": {"representation": "number_line", "skill_ids": ["read"]}}
        record["answers"][0].pop("representation")
        self.store.save(record)
        self.store.save_work("alice", "session_task:old:p", work_payload(["met", "partial"]),
                             expected_revision=0, request_id="first")
        with sqlite3.connect(self.path) as db:
            originals = {table: db.execute(f"SELECT record_json FROM {table}").fetchall()
                         for table in ("sessions", "works", "work_revisions")}
            events = db.execute("SELECT * FROM events").fetchall()
            db.execute("UPDATE sessions SET summary_json='{}'")
            db.execute("UPDATE works SET summary_json='{}'")
            db.execute("PRAGMA user_version=3")
        migrated = LearningStore(self.path)
        summary = migrated.works("alice", limit=None)[0]
        self.assertEqual(summary["criterion_outcomes"][1]["status"], "partial")
        self.assertTrue(summary["revision_needed"])
        frozen = migrated.planning_records("alice")[0]["answers"][0]
        self.assertEqual(frozen["representation"], "number_line")
        self.assertEqual(frozen["skill_ids"], ["compare"])  # Original answer wins over metadata.
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            for table, rows in originals.items():
                self.assertEqual(db.execute(f"SELECT record_json FROM {table}").fetchall(), rows)
            self.assertEqual(db.execute("SELECT * FROM events").fetchall(), events)

    def test_failed_v4_summary_migration_rolls_back_completely(self):
        self.store.save(session())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE sessions SET summary_json='{}'")
            db.execute("PRAGMA user_version=3")
        with patch("cnkg.learning_store._session_summary", side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                LearningStore(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT summary_json FROM sessions").fetchone()[0], "{}")


if __name__ == "__main__":
    unittest.main()
