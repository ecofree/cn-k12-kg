"""审核与试点报告不能把模板、AI记录、重复导出或不同版本混成有效证据。"""
import copy
import unittest
from tools.pilot_report import report
from tools.review_packet import packet, check_packet, CHECKS


class ReviewPacketTest(unittest.TestCase):
    def setUp(self):
        self.current = {"unit:u": {"kind": "unit", "id": "u", "title": "样例",
                                   "content_sha256": "hash-v1", "content": {}}}
        self.document = packet(self.current)

    def approve(self):
        self.document["records"][0]["review"].update(
            status="approved", reviewer_type="teacher", reviewer_id="fixture-teacher",
            record_id="fixture-review", reviewed_at="2026-10-26",
            checks={key: True for key in CHECKS})

    def test_generated_template_is_pending_not_reviewed(self):
        result = check_packet(self.document, self.current)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["pending"], 1)

    def test_ai_and_incomplete_checklist_cannot_approve(self):
        self.approve()
        self.document["records"][0]["review"]["reviewer_type"] = "ai"
        self.assertTrue(check_packet(self.document, self.current)["errors"])
        self.document["records"][0]["review"]["reviewer_type"] = "teacher"
        self.document["records"][0]["review"]["checks"][CHECKS[0]] = None
        self.assertTrue(check_packet(self.document, self.current)["errors"])

    def test_changed_content_invalidates_old_approval(self):
        self.approve()
        self.assertEqual(check_packet(self.document, self.current)["accepted"], 1)
        self.current["unit:u"]["content_sha256"] = "hash-v2"
        result = check_packet(self.document, self.current)
        self.assertEqual(result["accepted"], 0)
        self.assertTrue(result["errors"])


class PilotReportTest(unittest.TestCase):
    @staticmethod
    def session(sid="s1", version="v1", grader="g1", mode="transfer", context=None):
        return {"id": sid, "kind": "unit", "unit_id": "u", "mode": mode,
                "content_sha256": version, "grader_sha256": grader,
                "completed_at": "2026-10-26T00:00:00Z", "context": context or {},
                "answers": [{"question_id": "q1", "content_sha256": version, "status": "correct"},
                            {"question_id": "q2", "content_sha256": version, "status": "needs_review"}]}

    def test_empty_report_is_pending_and_never_fabricates_students(self):
        result = report([])
        self.assertEqual(result["status"], "pending_data")
        self.assertIsNone(result["verified_students"])
        self.assertEqual(result["educational_effect"], "pending")

    def test_duplicate_exports_do_not_double_count_or_score_open_tasks(self):
        document = {"schema": 1, "sessions": [self.session()]}
        result = report([document, copy.deepcopy(document)])
        self.assertEqual(result["unique_sessions"], 1)
        items = result["groups"][0]["items"]
        self.assertEqual(items[0]["scored_attempts"], 1)
        self.assertIsNone(items[1]["observed_correct_rate"])

    def test_versions_exposure_and_feedback_withholding_are_separate(self):
        sessions = [self.session(), self.session("s2", version="v2"), self.session("s3", grader="g2"),
                    self.session("s4", context={"material_requested": True}),
                    self.session("s5", mode="delayed", context={"actual_delay_seconds": 20}),
                    self.session("s6", mode="delayed", context={"actual_delay_seconds": 90000}),
                    self.session("s7", context={"repeated_question_ids": ["q1"]})]
        sessions[0]["answers"][0].pop("status")
        result = report([{"schema": 1, "sessions": sessions}])
        self.assertEqual(len(result["groups"]), 7)
        self.assertEqual(result["groups"][0]["answers_by_status"]["feedback_withheld"], 1)
        self.assertIsNone(result["groups"][0]["items"][0]["observed_correct_rate"])


if __name__ == "__main__":
    unittest.main()
