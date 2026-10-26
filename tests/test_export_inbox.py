"""守住交接包的完整性。

背景：第一次给外部复核时，`review.csv` 只导出了题干/答案/解析，没有选项，
44 道选择题和 57 道短答因此无法审核，对方只能标 skip（占 160 题里的 68 题）。
导出方有责任给全信息，不能要求复核方去猜。
"""
import csv
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"


class ExportInboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sidecar = json.loads((INBOX / "review_sidecar.json").read_text(encoding="utf-8"))
        with open(INBOX / "review.csv", encoding="utf-8-sig", newline="") as fh:
            cls.rows = list(csv.DictReader(fh))

    def test_csv_keeps_nine_columns(self):
        self.assertEqual(list(self.rows[0].keys()),
                         ["question_id", "topic_id", "type", "difficulty", "prompt",
                          "answer", "explanation", "verdict", "note"])

    def test_sidecar_covers_every_review_row(self):
        csv_ids = {r["question_id"] for r in self.rows}
        side_ids = {q["question_id"] for q in self.sidecar["questions"]}
        self.assertEqual(csv_ids, side_ids, "sidecar 必须与 review.csv 一一对应")

    def test_choice_has_full_options(self):
        for q in self.sidecar["questions"]:
            if q["type"] != "choice":
                continue
            self.assertGreaterEqual(len(q.get("choices") or []), 2, q["question_id"])
            self.assertEqual(len(q.get("correct_option_ids") or []), 1, q["question_id"])

    def test_short_has_accept(self):
        for q in self.sidecar["questions"]:
            if q["type"] == "short":
                self.assertIn("accept", q, q["question_id"])

    def test_every_row_has_hash(self):
        missing = [q["question_id"] for q in self.sidecar["questions"]
                   if not q.get("full_question_sha256")]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
