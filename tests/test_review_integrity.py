"""Regression scenarios for false intervals, incomplete review and legacy state."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cnkg.learning_store import LearningStore
from cnkg.planner import plan
from cnkg.review_timing import TIMING_POLICY, interval_context, qualified_review

NOW = "2026-10-26T00:00:00+00:00"
OLD = "2026-10-20T00:00:00+00:00"


def session(identity="old", completed=OLD, mode="transfer", version="v1"):
    return {"id": identity, "owner_id": "alice", "kind": "unit", "unit_id": "u1", "mode": mode,
            "created_at": completed, "completed_at": completed, "resource_version": version,
            "content_sha256": version, "answers": [{"question_id": "p1", "status": "correct"}],
            "context": {}}


def work(assessment=()):
    return {"kind": "session_task", "source_ref": {"session_id": "s1", "question_id": "p1"},
            "resource_version": "a" * 64,
            "resource_snapshot": {"title": "过程题", "fields": [{"id": "response"}],
                                  "criteria": ["说明条件", "说明推理"], "stage": "primary"},
            "draft": {"response": "原始思路"}, "reflection": "", "self_assessment": list(assessment)}


def catalog():
    return {"units": {"u1": {"id": "u1", "title": "u1", "stage": "primary", "resource_version": "v1",
        "tasks": [{"id": "p1", "phase": "practice"}, {"id": "t1", "phase": "transfer"},
                  {"id": "d1", "phase": "delayed"}]}}}


class ReviewTimingTests(unittest.TestCase):
    def setUp(self):
        self.current = session("current", NOW, "delayed")
        self.current.pop("completed_at")

    def test_stale_client_link_cannot_hide_a_recent_completion(self):
        old = session()
        recent = session("recent", "2026-10-25T23:59:00+00:00")
        context = interval_context(self.current, [old, recent], requested_previous=old)
        self.assertEqual(context["requested_previous_session_id"], "old")
        self.assertEqual(context["previous_session_id"], "recent")
        self.assertEqual(context["actual_delay_seconds"], 60)
        self.assertFalse(context["delay_window_met"])
        self.assertEqual(context["interval_reason"], "completion_interval_short")

    def test_recent_reference_resets_quiet_window_without_changing_completion_time(self):
        context = interval_context(self.current, [session()], latest_activity={
            "occurred_at": "2026-10-25T23:59:00+00:00", "event_type": "reference_opened"})
        self.assertEqual(context["actual_delay_seconds"], 6 * 86400)
        self.assertEqual(context["actual_quiet_seconds"], 60)
        self.assertFalse(context["review_interval_qualified"])
        self.assertEqual(context["interval_reason"], "recent_material_or_reference")
        self.assertIn("参考", context["interval_notice"])

    def test_unfinished_task_exposure_also_resets_quiet_window(self):
        context = interval_context(self.current, [session()], latest_activity={
            "occurred_at": "2026-10-25T23:59:00+00:00", "event_type": "question_presented"})
        self.assertFalse(context["delay_window_met"])
        self.assertEqual(context["interval_reason"], "recent_task_activity")

    def test_exact_24_hours_same_version_qualifies_but_never_claims_independence(self):
        previous = session(completed="2026-10-25T08:00:00+08:00")
        context = interval_context(self.current, [previous])
        self.assertEqual(context["actual_quiet_seconds"], 86400)
        self.assertTrue(context["review_interval_qualified"])
        self.assertFalse(context["independent_evidence"])
        changed = interval_context(self.current, [session(version="older-version")])
        self.assertTrue(changed["delay_window_met"])
        self.assertFalse(changed["review_interval_qualified"])
        self.assertEqual(changed["interval_reason"], "content_version_changed")

    def test_missing_future_or_unzoned_completion_does_not_establish_interval(self):
        for previous in (session(completed=None), session(completed="2026-10-27T00:00:00+00:00"),
                         session(completed="2026-10-20T00:00:00")):
            context = interval_context(self.current, [previous])
            self.assertFalse(context["review_interval_qualified"])
        empty = session()
        empty["answers"] = []
        self.assertEqual(interval_context(self.current, [empty])["interval_reason"], "no_completed_same_scope")
        for when in ("2026-10-26T00:00:00", "2026-10-27T00:00:00+00:00"):
            context = interval_context(self.current, [session()], latest_activity={
                "occurred_at": when, "event_type": "reference_opened"})
            self.assertFalse(context["review_interval_qualified"])
            self.assertEqual(context["interval_reason"], "invalid_or_future_timestamp")

    def test_legacy_or_all_skipped_review_does_not_advance_interval_ladder(self):
        legacy = session(mode="delayed")
        legacy["context"] = {"delay_window_met": True, "review_interval_qualified": True}
        self.assertFalse(qualified_review(legacy))
        current = copy.deepcopy(legacy)
        current["context"].update(timing_policy=TIMING_POLICY, same_learning_scope=True,
                                  same_content_version=True, actual_quiet_seconds=86400)
        current["answers"] = [{"question_id": "d1", "status": "not_attempted", "skipped": True}]
        self.assertFalse(qualified_review(current))
        current["answers"] = [{"question_id": "d1", "status": "wrong"}]
        self.assertTrue(qualified_review(current))  # An observation, not a success count.

    def test_immediate_repeats_do_not_jump_to_seven_days(self):
        repeated = session("repeat", NOW, "delayed")
        repeated["phase_completion_count"] = 8
        repeated["context"] = {"delay_window_met": False, "timing_policy": TIMING_POLICY,
                               "review_interval_qualified": False}
        action = plan(catalog(), [repeated], now=NOW)["next_steps"][0]
        self.assertEqual(action["interval_days"], 1)
        self.assertEqual(action["qualified_review_count"], 0)

    def test_one_rated_criterion_does_not_clear_whole_process_review(self):
        record = session("s1", mode="practice")
        record["answers"][0]["status"] = "needs_review"
        partial = {"resource_id": "session_task:s1:p1", "kind": "session_task",
                   "self_assessment_reported": True, "self_assessment_completed": False,
                   "assessed_criterion_count": 1, "criterion_count": 2}
        steps = plan(catalog(), [record], [partial], now=NOW)["next_steps"]
        self.assertTrue(any(step.get("continuation") == "reflect_and_revise" for step in steps))
        partial["self_assessment_completed"] = True
        steps = plan(catalog(), [record], [partial], now=NOW)["next_steps"]
        self.assertFalse(any(step.get("continuation") == "reflect_and_revise" for step in steps))
        self.assertEqual(record["answers"][0]["status"], "needs_review")


class ReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "learning.sqlite"
        self.store = LearningStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_checked_comment_and_partial_review_keep_remaining_criteria(self):
        original = work([{"criterion_index": 0, "status": "not_checked", "comment": "还没有检查"}])
        self.store.save_work("alice", "w1", original, 0, "request-1")
        summary = self.store.works("alice")[0]
        self.assertFalse(summary["self_assessment_reported"])
        self.assertFalse(summary["self_assessment_completed"])
        self.assertEqual(summary["assessed_criterion_count"], 0)
        partial = work([{"criterion_index": 0, "status": "partial"}])
        self.store.save_work("alice", "w1", partial, 1, "request-2")
        summary = self.store.works("alice")[0]
        self.assertTrue(summary["self_assessment_reported"])
        self.assertFalse(summary["self_assessment_completed"])
        finished = work([{"criterion_index": 0, "status": "partial"}, {"criterion_index": 1, "status": "revisit"}])
        self.store.save_work("alice", "w1", finished, 2, "request-3")
        self.assertTrue(self.store.works("alice")[0]["self_assessment_completed"])
        self.assertEqual(self.store.load_work("alice", "w1", 1)["payload"], original)

    def test_scope_activity_ignores_unrelated_owner_and_scope_but_tracks_shared_family(self):
        self.store.add_event("alice", {"event_type": "material_requested", "occurred_at": OLD,
            "resource_id": "unit:u1"})
        self.store.add_event("bob", {"event_type": "material_requested", "occurred_at": NOW,
            "resource_id": "unit:u1"})
        self.store.add_event("alice", {"event_type": "material_requested", "occurred_at": NOW,
            "resource_id": "unit:u2"})
        self.assertEqual(self.store.latest_scope_activity("alice", ["unit:u1"])["occurred_at"], OLD)
        self.store.add_event("alice", {"event_type": "reference_opened", "occurred_at": NOW,
            "resource_id": "other-material", "payload": {"family_ids": ["shared-family"]}})
        activity = self.store.latest_scope_activity("alice", ["unit:u1"], family_ids=["shared-family"])
        self.assertEqual(activity["occurred_at"], NOW)
        self.assertEqual(activity["event_type"], "reference_opened")
        self.assertIsNone(self.store.latest_scope_activity("stranger", ["unit:u1"], family_ids=["shared-family"]))

    def test_latest_unzoned_activity_is_not_silently_discarded(self):
        self.store.add_event("alice", {"event_type": "material_requested", "occurred_at": OLD,
            "resource_id": "unit:u1"})
        unzoned = "2026-10-25T23:59:00"
        self.store.add_event("alice", {"event_type": "reference_opened", "occurred_at": unzoned,
            "resource_id": "unit:u1"})
        activity = self.store.latest_scope_activity("alice", ["unit:u1"])
        self.assertEqual(activity["occurred_at"], unzoned)
        context = interval_context(session("current", NOW, "delayed"), [session()], latest_activity=activity)
        self.assertFalse(context["review_interval_qualified"])

    def test_qualified_aggregate_survives_immediate_repeats_and_version_boundaries(self):
        first = session("first", OLD, "delayed")
        first["context"] = {"timing_policy": TIMING_POLICY, "review_interval_qualified": True,
                            "same_learning_scope": True, "same_content_version": True,
                            "delay_window_met": True, "actual_quiet_seconds": 86400}
        self.store.save(first)
        for index in range(3):
            self.store.save(session(f"immediate-{index}", NOW, "delayed"))
        newer = session("other-version", NOW, "delayed", version="v2")
        self.store.save(newer)
        summaries = self.store.latest_scope_records("alice")
        old = next(item for item in summaries if item["resource_version"] == "v1")
        self.assertEqual(old["phase_completion_count"], 4)
        self.assertEqual(old["qualified_review_count"], 1)
        self.assertEqual(plan(catalog(), summaries, now=NOW)["next_steps"][0]["interval_days"], 3)
        self.assertEqual(next(item for item in summaries if item["resource_version"] == "v2")["qualified_review_count"], 0)

    def test_scope_summary_uses_actual_time_across_timezone_offsets(self):
        self.store.save(session("older-utc", "2026-10-26T01:00:00+08:00"))
        self.store.save(session("latest-utc", "2026-10-25T23:59:00+00:00"))
        self.assertEqual(self.store.latest_scope_records("alice")[0]["id"], "latest-utc")

    def _as_v2_database(self):
        legacy = session("legacy", OLD, "delayed")
        legacy["context"] = {"delay_window_met": True}
        self.store.save(legacy)
        self.store.save_work("alice", "w1", work([{"criterion_index": 0, "status": "not_checked", "comment": "未检查"}]), 0, "request-1")
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE sessions DROP COLUMN review_interval_qualified")
            db.execute("UPDATE works SET summary_json=?", ('{"self_assessment_reported":true}',))
            db.execute("PRAGMA user_version=2")
        return legacy

    def test_v2_migration_changes_only_derived_indexes_and_can_reopen(self):
        self._as_v2_database()
        with sqlite3.connect(self.path) as db:
            before = {table: db.execute(f"SELECT * FROM {table}").fetchall()
                      for table in ("events", "work_revisions", "work_requests")}
            sessions = db.execute("SELECT id, revision, record_json FROM sessions").fetchall()
            works = db.execute("SELECT owner_id, resource_id, revision, record_json FROM works").fetchall()
        upgraded = LearningStore(self.path)
        with sqlite3.connect(self.path) as db:
            for table, values in before.items():
                self.assertEqual(db.execute(f"SELECT * FROM {table}").fetchall(), values)
            self.assertEqual(db.execute("SELECT id, revision, record_json FROM sessions").fetchall(), sessions)
            self.assertEqual(db.execute("SELECT owner_id, resource_id, revision, record_json FROM works").fetchall(), works)
        self.assertEqual(upgraded.latest_scope_records("alice")[0]["qualified_review_count"], 0)
        self.assertFalse(upgraded.works("alice")[0]["self_assessment_completed"])
        self.assertFalse(upgraded.works("alice")[0]["self_assessment_reported"])
        self.assertEqual(LearningStore(self.path).works("alice"), upgraded.works("alice"))

    def test_failed_v3_migration_rolls_back_ddl_and_summary_updates(self):
        self._as_v2_database()
        with patch("cnkg.learning_store._work_summary", side_effect=RuntimeError("migration interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                LearningStore(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertNotIn("review_interval_qualified", {row[1] for row in db.execute("PRAGMA table_info(sessions)")})
            self.assertEqual(json.loads(db.execute("SELECT summary_json FROM works").fetchone()[0]),
                             {"self_assessment_reported": True})


if __name__ == "__main__":
    unittest.main()
