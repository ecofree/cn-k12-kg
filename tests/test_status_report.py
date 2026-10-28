"""Missing optional bundles must remain distinguishable from measured zeros."""
from contextlib import redirect_stdout
from io import StringIO
import pathlib
import tempfile
import unittest

from tools import status_report as SR
from tools.rebuild_data import write_json


class StatusReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()

    def test_empty_checkout_prints_and_reports_missing_data(self):
        snapshot = SR.build_snapshot(self.data, self.root)
        self.assertFalse(snapshot["external_kc"]["available"])
        self.assertIsNone(snapshot["external_kc"]["mapping_rate"])
        self.assertIsNone(snapshot["external_kc"]["topic_cover_rate"])
        self.assertIsNone(snapshot["response_data"]["rows"])
        self.assertFalse(snapshot["response_data"]["available"])
        self.assertFalse(snapshot["bound_bank_review"]["available"])
        self.assertFalse(snapshot["goal_observation_resources"]["available"])
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(SR.main(["--data-dir", str(self.data), "--root-dir", str(self.root)]), 0)
        self.assertIn("未提供", out.getvalue())
        self.assertNotIn("None", out.getvalue())

    def test_provided_empty_statistics_are_zero_not_missing(self):
        write_json(self.data / "external/batch/response_stats.json", {"rows": [], "status": "empty"})
        report = SR.response_data(self.data)
        self.assertTrue(report["available"])
        self.assertEqual(report["rows"], 0)
        self.assertIsNone(report["student_events"])

    def test_summary_rows_do_not_become_student_response_count(self):
        write_json(self.data / "external/batch/response_stats.json",
                   {"rows": [{"questionId": "q1", "attempts": 200}], "status": "provided"})
        report = SR.response_data(self.data)
        self.assertEqual(report["rows"], 1)
        self.assertIsNone(report["student_events"])

    def test_ai_review_does_not_count_as_teacher_review(self):
        write_json(self.data / "questions.json", [{"id": "q1", "topicId": "t1", "type": "numeric"}])
        write_json(self.data / "reviews.json", {
            "q1": {"verdict": "pass", "reviewer_type": "ai", "teacher_reviewed": False},
            "removed": {"verdict": "pass", "reviewer_type": "teacher", "teacher_reviewed": True},
        })
        review = SR.build_snapshot(self.data, self.root)["review"]
        self.assertEqual(review["reviewed"], 1)
        self.assertEqual(review["teacher_reviewed"], 0)
        self.assertEqual(review["orphaned_records"], 1)
        self.assertIn("不表示", review["missing_record_meaning"])


if __name__ == "__main__":
    unittest.main()
