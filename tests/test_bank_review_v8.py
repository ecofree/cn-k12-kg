"""A report title or a count cannot substitute for version-bound task reviews."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cnkg.bank_review import check_reviews, digest


class BankReviewCoverageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = [{"id": "old", "type": "numeric", "prompt": "一加一", "answer": "2"},
                       {"id": "new", "type": "choice", "prompt": "两条说法中哪条缺少条件", "answer": "b"}]
        self.write("data/content/example.json", {"questions": self.source})
        self.write("data/content_sources.json", {"content_sources": [{"path": "content/example.json"}],
                                                 "question_sources": []})
        self.bindings = {"bank:" + q["id"]: {"id": q["id"], "answer_spec": {"kind": q["type"]}}
                         for q in self.source}
        self.write("data/skills/catalog-bindings.json", {"tasks": self.bindings})
        self.write("data/catalog.json", {"tasks": {q["id"]: {"source_kind": "bank", "availability": availability,
             "source_ref": {"path": "data/questions.json"}} for q, availability in zip(self.source, ("active", "quarantined"))}})
        self.index = {"schema": 1, "bank_task_count": 2, "this_round_task_count": 1,
                      "bank_answer_bindings_sha256": digest(self.bindings), "reviewer_kind": "ai",
                      "independent_teacher_review": False, "student_validation": False, "reviews": []}
        self.reviews = []
        for number, (question, kind) in enumerate(zip(self.source, ("previous_round", "this_round"))):
            review = {"reviewer_kind": "ai", "independent_teacher_review": False, "tasks": [{
                "id": question["id"], "source": "data/content/example.json", "current_sha256": digest(question),
                "mathematical_reason": "1+1=2。" if number == 0 else "题面仍缺适用条件，保留隔离。",
                "observation_boundary": "仅核对本题，不能推广为整个目标完成。"}]}
            self.reviews.append(review)
            self.index["reviews"].append({"path": f"docs/reviews/review-{number}.json", "task_count": 1,
                "coverage_kind": kind, "identity_field": "id", "source_hash_field": "current_sha256",
                "boundary_field": "observation_boundary"})
        self.flush()

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def flush(self):
        for ref, review in zip(self.index["reviews"], self.reviews):
            ref["sha256"] = self.write(ref["path"], review)
        self.write("docs/reviews/v8-bank-review-index.json", self.index)

    def test_old_review_is_not_relabelled_this_round_and_quarantine_is_retained(self):
        result = check_reviews(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["summary"], {"bank_tasks": 2, "covered_tasks": 2, "previous_round_tasks": 1,
                         "this_round_tasks": 1, "active_tasks": 1, "quarantined_tasks": 1})

    def test_changed_source_or_answer_binding_invalidates_review(self):
        self.source[0]["answer"] = "999"
        self.write("data/content/example.json", {"questions": self.source})
        result = check_reviews(self.root)
        self.assertTrue(any("偏离" in error for error in result["errors"]))
        self.bindings["bank:old"]["answer_spec"] = {"kind": "changed"}
        self.write("data/skills/catalog-bindings.json", {"tasks": self.bindings})
        self.assertTrue(any("登记契约" in error for error in check_reviews(self.root)["errors"]))

    def test_changed_report_and_claimed_count_cannot_hide_missing_entries(self):
        self.reviews[1]["tasks"] = []
        self.flush()
        result = check_reviews(self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(any("尚无" in error for error in result["errors"]))
        self.assertTrue(any("审查数" in error for error in result["errors"]))
        self.reviews[0]["tasks"][0]["mathematical_reason"] = "changed after approval"
        self.write(self.index["reviews"][0]["path"], self.reviews[0])
        self.assertTrue(any("指纹" in error for error in check_reviews(self.root)["errors"]))

    def test_duplicate_reviews_or_no_math_reason_are_not_coverage(self):
        self.reviews[1]["tasks"] = copy.deepcopy(self.reviews[0]["tasks"])
        self.flush()
        result = check_reviews(self.root)
        self.assertTrue(any("重复" in error for error in result["errors"]))
        self.reviews[1]["tasks"][0]["id"] = "new"
        self.reviews[1]["tasks"][0]["current_sha256"] = digest(self.source[1])
        self.reviews[1]["tasks"][0]["mathematical_reason"] = ""
        self.flush()
        self.assertTrue(any("数学判断" in error for error in check_reviews(self.root)["errors"]))

    def test_claiming_independent_teacher_review_is_rejected(self):
        self.reviews[1]["independent_teacher_review"] = True
        self.flush()
        self.assertFalse(check_reviews(self.root)["ok"])


if __name__ == "__main__":
    unittest.main()
