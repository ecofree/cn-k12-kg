"""模拟器真值定义的测试：根因是"连续缺口链的顶端"，不是"最高的未掌握祖先"。

这个定义只用于假定先修图下的敏感性分析，不能据此证明真实学生的因果关系。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import simulate  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


def hard_parents(graph, tid):
    return [e["prerequisiteId"] for e in graph.prereqs.get(tid, [])
            if e["strength"] == "hard"]


class RootsForTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_graph()
        # 找一条至少两级的 hard 链：target <- mid <- grand
        cls.chain = None
        for topic in cls.graph.topics:
            mids = hard_parents(cls.graph, topic["id"])
            for mid in mids:
                grands = hard_parents(cls.graph, mid)
                if grands:
                    cls.chain = (topic["id"], mid, grands[0])
                    break
            if cls.chain:
                break

    def test_chain_exists(self):
        self.assertIsNotNone(self.chain, "图谱里没有两级 hard 链，测试前提不成立")

    def test_stops_at_mastered_parent(self):
        """中间节点已掌握时，更远的祖先不算根因。"""
        target, mid, grand = self.chain
        state = {target: False, mid: True, grand: False}
        self.assertEqual(simulate.roots_for(self.graph, state, target), {target})

    def test_walks_through_unmastered_chain(self):
        """整条链都没掌握时，根因是最上游那个。"""
        target, mid, grand = self.chain
        state = {target: False, mid: False, grand: False}
        roots = simulate.roots_for(self.graph, state, target)
        self.assertIn(grand, roots)
        self.assertNotIn(target, roots)

    def test_mastered_target_has_no_root(self):
        target = self.chain[0]
        self.assertEqual(simulate.roots_for(self.graph, {target: True}, target), set())


class EffectiveStateTest(unittest.TestCase):
    def test_passthrough_when_not_irt(self):
        simulate.IRT = False
        state = {"a": True, "b": False}
        self.assertEqual(simulate.effective_state(state, -5), state)

    def test_low_ability_is_treated_as_not_mastered(self):
        simulate.IRT = True
        try:
            eff = simulate.effective_state({"a": True}, -2.0)
            self.assertFalse(eff["a"], "能力过低时，学过也不能算能演示")
            eff = simulate.effective_state({"a": True}, 0.5)
            self.assertTrue(eff["a"])
        finally:
            simulate.IRT = False


class RootMetricTest(unittest.TestCase):
    def test_extra_and_missing_roots_are_both_counted(self):
        self.assertEqual(simulate.root_set_counts({"a", "b"}, {"a", "c"}),
                         {"tp": 1, "fp": 1, "fn": 1})

    def test_no_report_is_a_false_negative(self):
        self.assertEqual(simulate.root_set_counts({"a"}, []),
                         {"tp": 0, "fp": 0, "fn": 1})

    def test_evaluation_includes_no_report_in_recall_denominator(self):
        from unittest.mock import patch
        from collections import Counter
        from tests.test_diagnostic_contract import make_graph
        g = make_graph()
        summary = {"mastered": [], "weak": ["A"], "shaky": [], "assumed": [],
                   "roots": [], "root_candidates": ["A"], "incomplete": True}
        with patch.object(simulate, "mastery_of", return_value={"A": False}), \
                patch.object(simulate, "run_one", return_value=(summary, 2, 0.0, Counter(numeric=2))):
            result = simulate.evaluate(g, ["A"], 2, 0, 0, 0, seed=1)
        self.assertEqual(result["root_hit_rate"], 0)
        self.assertEqual(result["root_recall"], 0)
        self.assertEqual(result["root_counts"]["fn"], 2)
        self.assertEqual(result["candidate_or_supported_hit_rate"], 1)
        self.assertFalse(result["interpretation"]["empirical_student_validation"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
