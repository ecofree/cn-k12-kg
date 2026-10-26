"""第二轮回传接入的守门逻辑：只读字段被改时必须中止，不能"将错就错"并进去。"""
import csv
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import ingest_round2 as R  # noqa: E402


class ReadOnlyGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "inbox").mkdir()
        q = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))[0]
        self.q = q
        self.write_review(prompt=q["prompt"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_review(self, **override):
        path = self.tmp / "inbox" / "review.csv"
        row = {
            "question_id": self.q["id"],
            "topic_id": self.q["topicId"],
            "type": self.q["type"],
            "difficulty": self.q.get("difficulty", ""),
            "prompt": self.q["prompt"],
            "answer": str(self.q["answer"]),
            "explanation": self.q.get("explanation", ""),
            "verdict": "pass",
            "note": "ok",
        }
        row.update(override)
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            w.writeheader()
            w.writerow(row)

    def test_unchanged_row_passes(self):
        out = R.ingest_reviews(self.tmp, dry=True)
        self.assertEqual(out["rows"], 1)

    def test_prompt_tamper_aborts(self):
        self.write_review(prompt="被改过的题干")
        with self.assertRaises(SystemExit):
            R.ingest_reviews(self.tmp, dry=True)

    def test_explanation_tamper_aborts(self):
        self.write_review(explanation="被改过的解析")
        with self.assertRaises(SystemExit):
            R.ingest_reviews(self.tmp, dry=True)

    def test_answer_tamper_aborts(self):
        self.write_review(answer="Z")
        with self.assertRaises(SystemExit):
            R.ingest_reviews(self.tmp, dry=True)


class ParamsTest(unittest.TestCase):
    def test_correct_option_ids_parsed(self):
        # fail 的 note 约定以 correct_option_ids= 开头，解析成列表供后续改题用
        import re
        note = "correct_option_ids=none; 两个选项都成立。"
        m = re.match(r"correct_option_ids\s*=\s*([^;]*);", note)
        self.assertEqual([x.strip() for x in m.group(1).split("+") if x.strip()], ["none"])


if __name__ == "__main__":
    unittest.main()
