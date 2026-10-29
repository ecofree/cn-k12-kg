"""The fresh semantic re-audit queue must cover every bank item without changing evidence status."""
from __future__ import annotations

import unittest

from tools.prepare_bank_reaudit import ROOT, build_queue


class BankReauditQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = build_queue(ROOT)

    def test_exact_bank_coverage_and_quarantine_are_preserved(self):
        self.assertEqual(len(self.rows), 3584)
        self.assertEqual(len({row["id"] for row in self.rows}), 3584)
        self.assertEqual(sum(row["quarantined"] for row in self.rows), 493)
        self.assertEqual(sum(row["availability"] == "active" for row in self.rows), 3091)

    def test_queue_is_not_misrepresented_as_completed_review(self):
        self.assertTrue(all(row["audit"]["status"] == "not_reaudited" for row in self.rows))
        self.assertTrue(all(row["audit"]["answer_correct"] is None for row in self.rows))
        self.assertTrue(all(row["audit"]["explanation_consistent"] is None for row in self.rows))
        self.assertTrue(all(row["audit"]["skill_binding_valid"] is None for row in self.rows))
        self.assertTrue(all(row["audit"]["quarantine_decision"] == "keep_quarantined"
                            for row in self.rows if row["quarantined"]))

    def test_each_row_is_bound_to_source_and_retains_historical_scope(self):
        self.assertTrue(all(len(row["source_sha256"]) == 64 for row in self.rows))
        self.assertTrue(all(row["source"] and row["id"] for row in self.rows))
        self.assertTrue(all(row["prior_review"] for row in self.rows))
        self.assertTrue(all(row["prior_review"]["coverage_kind"] in {"previous_round", "this_round"}
                            for row in self.rows))


if __name__ == "__main__":
    unittest.main()
