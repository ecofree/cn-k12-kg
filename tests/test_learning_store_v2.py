"""Evidence persistence risks: migrations, concurrency, replay and owner boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from cnkg.learning_store import ConflictError, LearningStore, SCHEMA_VERSION


NOW = "2026-10-26T00:00:00+00:00"


def record(identity="s1", owner="alice", unit="u1", mode="practice", completed=None):
    return {"id": identity, "owner_id": owner, "kind": "unit", "unit_id": unit, "mode": mode,
            "created_at": NOW, "content_sha256": "a" * 64, "completed_at": completed,
            "answers": [], "presentations": {}, "unit_snapshot": {"tasks": [
                {"id": "q1", "family_id": "family-1", "answer": "secret-reference"}]}}


def payload():
    return {"kind": "session_task", "source_ref": {"session_id": "s1", "question_id": "q1"},
            "resource_version": "a" * 64, "resource_snapshot": {"id": "q1", "prompt": "说明你的方法", "points": 1},
            "draft": "第一稿", "reflection": "", "self_assessment": {}}


class LearningStoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "learning.sqlite"
        self.store = LearningStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _legacy_database(self):
        path = Path(self.tmp.name) / "legacy.sqlite"
        original = record(completed=NOW)
        original["presentations"] = {"q1": {"presented_at": NOW, "content_sha256": "b" * 64},
                                     "q2": {"presented_at": NOW}}
        original["answers"] = [{"question_id": "q1", "status": "correct", "answered_at": NOW}]
        original["feedback_exposed"] = ["q1"]
        original_json = json.dumps(original)
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT, created_at TEXT,
                    updated_at TEXT, revision INTEGER, record_json TEXT);
                CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT,
                    session_id TEXT, event_type TEXT, occurred_at TEXT, resource_id TEXT,
                    question_id TEXT, content_sha256 TEXT, payload_json TEXT);
                PRAGMA user_version=1;
            """)
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", ("s1", "alice", NOW, NOW, 3, original_json))
            for kind in ("question_presented", "answer_submitted", "feedback_released"):
                db.execute("INSERT INTO events VALUES (NULL,?,?,?,?,?,?,?,?)",
                           ("alice", "s1", kind, NOW, "u1", "q1", "b" * 64, "{}"))
        return path, original_json

    def test_v1_migration_preserves_original_evidence_and_backfills_once(self):
        path, original_json = self._legacy_database()
        upgraded = LearningStore(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(db.execute("SELECT record_json FROM sessions").fetchone()[0], original_json)
        q1 = upgraded.exposures("alice")["questions"]["q1"]
        self.assertEqual((q1["presentation_count"], q1["answer_count"], q1["feedback_count"]), (1, 1, 1))
        self.assertEqual(upgraded.exposures("alice")["families"]["family-1"]["presentation_count"], 1)
        self.assertEqual(upgraded.exposures("alice")["questions"]["q2"]["presentation_count"], 1)
        reopened = LearningStore(path)
        self.assertEqual(reopened.exposures("alice"), upgraded.exposures("alice"))
        self.assertEqual(len(reopened.events("alice")), 3)
        self.assertEqual(reopened.load("s1", "alice")["_revision"], 3)

    def test_migration_failure_and_future_version_do_not_partially_mutate_database(self):
        path, original_json = self._legacy_database()
        with mock.patch.object(LearningStore, "_index_exposure", side_effect=RuntimeError("stop migration")):
            with self.assertRaisesRegex(RuntimeError, "stop migration"):
                LearningStore(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT record_json FROM sessions").fetchone()[0], original_json)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='works'").fetchone())
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(ValueError, "版本高于"):
            LearningStore(path)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 1)

    def test_revision_history_and_request_replay_return_original_result(self):
        first = payload()
        first["resource_snapshot"]["stage"] = "primary"
        saved = self.store.save_work("alice", "w1", first, 0, "request-1")
        second = copy.deepcopy(first)
        second.update(draft="已补充说明", reflection="我遗漏了单位", self_assessment={"reason": "已补充"})
        self.store.save_work("alice", "w1", second, 1, "request-2")
        replay = self.store.save_work("alice", "w1", first, 0, "request-1")
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(replay["payload"]["draft"], "第一稿")
        self.assertEqual(replay["created_at"], saved["created_at"])
        self.assertEqual(self.store.load_work("alice", "w1")["payload"]["draft"], "已补充说明")
        self.assertEqual([x["revision"] for x in self.store.work_history("alice", "w1")], [2, 1])
        self.assertEqual(len(self.store.events("alice")), 2)
        self.assertEqual(self.store.works("alice")[0]["stage"], "primary")
        with self.assertRaises(ConflictError):
            self.store.save_work("alice", "w1", second, 0, "request-1")
        with self.assertRaises(ConflictError):
            self.store.save_work("alice", "w1", first, 1, "stale-request")

    def test_content_binding_is_immutable_but_old_version_can_keep_revising(self):
        original = payload()
        self.store.save_work("alice", "task@old", original, 0, "first")
        for field, value in (("resource_version", "b" * 64), ("source_ref", "other"), ("kind", "unit"),
                             ("resource_snapshot", {**original["resource_snapshot"], "points": True})):
            altered = copy.deepcopy(original)
            altered[field] = value
            with self.assertRaises(ConflictError):
                self.store.save_work("alice", "task@old", altered, 1, "change-" + field)
        newer = copy.deepcopy(original)
        newer["resource_version"] = "b" * 64
        self.store.save_work("alice", "task@new", newer, 0, "new-version")
        original["draft"] = "继续改旧作品"
        self.store.save_work("alice", "task@old", original, 1, "revise-old")
        self.assertEqual(self.store.load_work("alice", "task@old")["resource_version"], "a" * 64)
        self.assertEqual(self.store.work_history("alice", "task@old", limit=1, offset=1)[0]["payload"]["draft"], "第一稿")

    def test_concurrent_identical_retry_creates_one_revision_and_distinct_writes_conflict(self):
        barrier = threading.Barrier(2)
        def same_request(_):
            barrier.wait()
            return self.store.save_work("alice", "w1", payload(), 0, "same")
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(same_request, range(2)))
        self.assertEqual(sorted(value["duplicate"] for value in values), [False, True])
        self.assertEqual(len(self.store.work_history("alice", "w1")), 1)
        barrier = threading.Barrier(2)
        def competing_request(number):
            value = payload()
            value["draft"] = f"writer {number}"
            barrier.wait()
            try:
                return self.store.save_work("alice", "w1", value, 1, f"writer-{number}")["revision"]
            except ConflictError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(competing_request, range(2)))
        self.assertCountEqual(values, [2, "conflict"])
        self.assertEqual(len(self.store.events("alice")), 2)

    def test_owner_boundaries_include_history_request_ids_and_exposures(self):
        self.store.save_work("alice", "w1", payload(), 0, "request")
        other = payload()
        other["draft"] = "Bob's work"
        self.store.save_work("bob", "w1", other, 0, "request")
        self.assertEqual(self.store.load_work("alice", "w1")["payload"]["draft"], "第一稿")
        self.assertEqual(self.store.load_work("bob", "w1")["payload"]["draft"], "Bob's work")
        self.assertIsNone(self.store.load_work("charlie", "w1", revision=1))
        self.assertEqual(self.store.work_history("charlie", "w1"), [])
        self.assertEqual(self.store.count_works("charlie"), 0)
        session = record()
        self.store.save(session, [{"event_type": "question_presented", "occurred_at": NOW, "question_id": "q1"}])
        self.assertIn("family-1", self.store.exposures("alice")["families"])
        self.assertEqual(self.store.exposures("bob"), {"questions": {}, "families": {}})
        self.assertEqual(self.store.latest_scope_records("bob"), [])

    def test_work_and_session_failures_roll_back_state_events_and_indexes(self):
        with mock.patch.object(LearningStore, "_insert_events", side_effect=RuntimeError("event failure")):
            with self.assertRaises(RuntimeError):
                self.store.save_work("alice", "w1", payload(), 0, "request")
        self.assertIsNone(self.store.load_work("alice", "w1"))
        self.assertEqual(self.store.work_history("alice", "w1"), [])
        self.store.save_work("alice", "w1", payload(), 0, "request")
        session = record()
        self.store.save(session)
        session["answers"] = [{"question_id": "q1", "status": "correct"}]
        with self.assertRaises(KeyError):
            self.store.save(session, [{"event_type": "question_presented", "occurred_at": NOW, "question_id": "q1"},
                                      {"occurred_at": NOW}])
        self.assertEqual(self.store.load("s1", "alice")["answers"], [])
        self.assertEqual(self.store.exposures("alice")["questions"], {})
        self.assertEqual(len(self.store.events("alice")), 1)

    def test_scope_summaries_keep_older_units_and_paginated_reads_avoid_full_snapshots(self):
        old = record(identity="old", unit="old-unit", completed=NOW)
        old["created_at"] = "2026-10-01T00:00:00+00:00"
        old["completed_at"] = "2026-10-01T01:00:00+00:00"
        old["answers"] = [{"question_id": "q1", "status": "correct"}]
        self.store.save(old)
        for number in range(105):
            current = record(identity=f"s{number:03}", unit="new-unit", mode="delayed", completed=NOW)
            current["answers"] = [{"question_id": "q1", "status": "correct"}]
            self.store.save(current)
        self.assertEqual(self.store.count("alice"), 106)
        self.assertEqual(len(self.store.records("alice", limit=100)), 100)
        self.assertEqual(len(self.store.records("alice", limit=10, offset=100)), 6)
        summaries = self.store.latest_scope_records("alice")
        self.assertEqual({entry["unit_id"] for entry in summaries}, {"old-unit", "new-unit"})
        self.assertEqual(next(entry for entry in summaries if entry["unit_id"] == "new-unit")["phase_completion_count"], 105)
        self.assertNotIn("secret-reference", json.dumps(summaries))
        self.assertNotIn("unit_snapshot", summaries[0])
        self.assertEqual(self.store.latest_scope_records("alice", ["old-unit"])[0]["id"], "old")
        empty = record(identity="empty-newer", unit="old-unit", completed=NOW)
        self.store.save(empty)
        self.assertEqual(self.store.latest_scope_records("alice", ["old-unit"])[0]["id"], "old")

    def test_exposure_filters_and_repeated_events_have_explicit_counts(self):
        for kind in ("question_presented", "question_presented", "answer_submitted", "feedback_released"):
            self.store.add_event("alice", {"event_type": kind, "question_id": "q1", "family_id": "f1", "occurred_at": NOW})
        evidence = self.store.exposures("alice", question_ids=["q1", "absent"], family_ids=[])
        self.assertEqual(evidence["families"], {})
        self.assertEqual(evidence["questions"]["q1"]["presentation_count"], 2)
        self.assertEqual(self.store.events("alice")[0]["payload"]["family_id"], "f1")
        self.assertEqual(self.store.exposures("alice", question_ids=[], family_ids=["f1"])["families"]["f1"]["answer_count"], 1)

    def test_scope_versions_prefer_compiled_identity_and_preserve_legacy_fallback(self):
        for identity, version in (("legacy", None), ("compiled-old", "compiled-v1"), ("compiled-new", "compiled-v2")):
            value = record(identity=identity, completed=NOW)
            value["answers"] = [{"question_id": "q1", "status": "correct"}]
            if version:
                value["resource_version"] = version
            self.store.save(value)
        summaries = self.store.latest_scope_records("alice")
        self.assertEqual(len(summaries), 3)
        self.assertEqual({row.get("resource_version") for row in summaries}, {None, "compiled-v1", "compiled-v2"})
        self.assertEqual({row["phase_completion_count"] for row in summaries}, {1})

    def test_material_and_reference_lists_are_indexed_once_without_inventing_unknown_families(self):
        self.store.add_event("alice", {"event_type": "material_provided", "occurred_at": NOW,
            "payload": {"task_ids": ["q1", "q1", "q2"], "family_ids": ["f1", "f1"]}})
        self.store.add_event("alice", {"event_type": "reference_opened", "occurred_at": NOW,
            "payload": {"task_ids": ["q1"], "family_ids": ["f1"]}})
        self.store.add_event("alice", {"event_type": "question_presented", "occurred_at": NOW,
            "question_id": "unknown-q", "family_id": "unknown-family", "family_status": "unknown"})
        self.store.add_event("alice", {"event_type": "question_presented", "occurred_at": NOW,
            "question_id": "unclassified-q", "family_id": "unclassified-family", "family_status": "unclassified"})
        indexed = self.store.exposures("alice")
        self.assertEqual(indexed["questions"]["q1"]["material_count"], 1)
        self.assertEqual(indexed["questions"]["q1"]["reference_count"], 1)
        self.assertEqual(indexed["questions"]["q1"]["last_reference_at"], NOW)
        self.assertEqual(indexed["families"]["f1"]["material_count"], 1)
        self.assertIsNone(indexed["questions"]["q2"]["last_reference_at"])
        self.assertNotIn("unknown-family", indexed["families"])
        self.assertNotIn("unclassified-family", indexed["families"])
        self.assertIn("unknown-q", indexed["questions"])

    def test_invalid_limits_nonfinite_json_and_bool_revisions_are_rejected(self):
        for limit, offset in ((0, 0), (-1, 0), (True, 0), (1, -1)):
            with self.assertRaises(ValueError):
                self.store.records("alice", limit, offset)
        with self.assertRaises(ValueError):
            self.store.save_work("alice", "w1", payload(), True, "req")
        bad = payload()
        bad["self_assessment"] = float("nan")
        with self.assertRaises(ValueError):
            self.store.save_work("alice", "w1", bad, 0, "req")
        self.assertEqual(self.store.count_works("alice"), 0)


if __name__ == "__main__":
    unittest.main()
