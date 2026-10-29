"""Submitting self-ratings is not evidence that required work was supplied."""
from __future__ import annotations

import copy
import unittest

from cnkg.learning_store import _work_summary
from cnkg.learning_needs import derive_needs, work_is_reviewed


def result(draft=None, *, status="submitted", fields=None, components=None):
    snapshot = {"title": "分项作图与解释", "criteria": ["检查图稿", "检查解释"],
                "fields": fields if fields is not None else [
                    {"id": "response", "label": "原作答"},
                    {"id": "diagram", "label": "图稿", "phase": "component"},
                    {"id": "reason", "label": "解释", "phase": "component"}],
                "component_ids": ["diagram", "reason"] if components is None else components}
    return {"resource_id": "session_task:s:q", "resource_version": "a" * 64,
            "revision": 1, "created_at": "2026-10-29T01:00:00Z", "updated_at": "2026-10-29T01:00:00Z",
            "evidence_type": "learner_work",
            "payload": {"kind": "session_task", "source_ref": {"session_id": "s", "question_id": "q"},
                "resource_version": "a" * 64, "resource_snapshot": snapshot,
                "draft": draft or {}, "reflection": "已检查", "status": status,
                "self_assessment": [{"criterion_index": 0, "status": "met"},
                                    {"criterion_index": 1, "status": "met"}]}}


class SubmissionEvidenceTests(unittest.TestCase):
    def test_blank_work_marked_met_keeps_process_need_open(self):
        summary = _work_summary(result())
        self.assertFalse(work_is_reviewed(summary))
        records = [{"id": "s", "kind": "learning_unit", "resource_version": "a" * 64,
                    "completed_at": "2026-10-29T00:59:00Z",
                    "answers": [{"question_id": "q", "status": "needs_review"}]}]
        needs = derive_needs({}, records, [summary])
        need = next(row for row in needs if row["kind"] == "process_revision")
        self.assertEqual(need["status"], "open")
        self.assertIsNone(need["resolution"])
        self.assertEqual(need["submission_evidence"]["missing_field_ids"], ["diagram", "reason"])

    def test_one_component_cannot_complete_another_component(self):
        summary = _work_summary(result({"response": "已经做了", "diagram": "我的图稿说明"}))
        self.assertFalse(work_is_reviewed(summary))
        self.assertEqual(summary["submission_evidence"]["missing_field_ids"], ["reason"])

    def test_nonempty_draft_with_all_met_still_needs_explicit_submission(self):
        summary = _work_summary(result({"diagram": "图稿过程", "reason": "解释过程"}, status="draft"))
        self.assertFalse(work_is_reviewed(summary))

    def test_complete_submitted_self_check_closes_only_its_bounded_need(self):
        original = result({"diagram": "图稿过程", "reason": "解释过程"})
        before = copy.deepcopy(original)
        summary = _work_summary(original)
        self.assertTrue(work_is_reviewed(summary))
        self.assertTrue(summary["submission_evidence"]["complete"])
        self.assertEqual(summary["submission_evidence"]["evidence_basis"], "submission_presence_only_not_quality_or_mastery")
        self.assertEqual(original, before)

    def test_optional_support_drafts_and_reflection_do_not_replace_response(self):
        fields = [{"id": "response", "label": "原作答"},
                  {"id": "revision", "label": "指导后修订", "phase": "revision"}]
        original = result({"revision": "指导后照着写"}, fields=fields, components=[])
        original["payload"]["kind"] = "self_check"
        summary = _work_summary(original)
        self.assertFalse(work_is_reviewed(summary))
        self.assertEqual(summary["submission_evidence"]["missing_field_ids"], ["response"])

    def test_old_compact_self_assessment_flags_without_work_are_not_evidence(self):
        self.assertFalse(work_is_reviewed({"self_assessment_completed": True, "status": "submitted"}))
        self.assertFalse(work_is_reviewed({"criterion_outcomes": [{"status": "met"}], "status": "submitted"}))

    def test_legacy_payload_without_field_schema_requires_actual_nonblank_work(self):
        original = result()
        original["payload"]["resource_snapshot"] = {"title": "旧作品", "criteria": ["检查图稿", "检查解释"]}
        self.assertFalse(work_is_reviewed(_work_summary(original)))
        original["payload"]["draft"] = {"response": "保留的旧作答"}
        self.assertTrue(work_is_reviewed(_work_summary(original)))


class SubmissionMigrationTests(unittest.TestCase):
    def test_v4_migration_reopens_missing_work_without_rewriting_any_raw_record(self):
        import json
        from pathlib import Path
        import tempfile
        from cnkg.learning_store import LearningStore, SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite"
            store = LearningStore(path)
            payload = result()["payload"]
            saved = store.save_work("owner", "session_task:s:q", payload, 0, "original")
            with store._connect() as db:
                original = [tuple(row) for row in db.execute("SELECT record_json FROM works")]
                history = [tuple(row) for row in db.execute("SELECT * FROM work_revisions")]
                stale = _work_summary(saved)
                stale.pop("submission_evidence")
                db.execute("UPDATE works SET summary_json=?", (json.dumps(stale),))
                db.execute("PRAGMA user_version=4")
            reopened = LearningStore(path)
            self.assertFalse(work_is_reviewed(reopened.works("owner")[0]))
            with reopened._connect() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual([tuple(row) for row in db.execute("SELECT record_json FROM works")], original)
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM work_revisions")], history)
            again = LearningStore(path)
            self.assertEqual(again.load_work("owner", "session_task:s:q"), saved)

    def test_failed_v4_reindex_rolls_back_version_and_all_summary_changes(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from cnkg.learning_store import LearningStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite"
            store = LearningStore(path)
            store.save_work("owner", "session_task:s:q", result()["payload"], 0, "original")
            with store._connect() as db:
                db.execute("PRAGMA user_version=4")
                original = [tuple(row) for row in db.execute("SELECT * FROM works")]
            with patch("cnkg.learning_store._work_summary", side_effect=RuntimeError("reindex stopped")):
                with self.assertRaisesRegex(RuntimeError, "reindex stopped"):
                    LearningStore(path)
            with store._connect() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM works")], original)

    def test_attachments_only_satisfy_their_own_component_and_removal_reopens(self):
        from pathlib import Path
        import tempfile
        from cnkg.learning_store import LearningStore
        from test_assessments_v8 import attachment
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "work.sqlite")
            payload = result({"reason": "推导文字"})["payload"]
            payload["attachments"] = [attachment("response")]
            store.save_work("owner", "session_task:s:q", payload, 0, "wrong-field")
            self.assertFalse(work_is_reviewed(store.works("owner")[0]))
            payload["attachments"] = [attachment("diagram")]
            complete = store.save_work("owner", "session_task:s:q", payload, 1, "right-field")
            self.assertTrue(work_is_reviewed(store.works("owner")[0]))
            payload["attachments"] = []
            store.save_work("owner", "session_task:s:q", payload, 2, "remove-file")
            self.assertFalse(work_is_reviewed(store.works("owner")[0]))
            self.assertEqual(store.load_work("owner", "session_task:s:q", 2), complete)
            self.assertIsNone(store.load_work("foreign", "session_task:s:q"))

    def test_forged_attachment_metadata_does_not_count_as_submitted_content(self):
        original = result({"reason": "解释过程"})
        original["payload"]["attachments"] = [{"field_id": "diagram", "filename": "not-an-image.png", "byte_size": 1}]
        self.assertFalse(_work_summary(original)["submission_evidence"]["complete"])


if __name__ == "__main__":
    unittest.main()
