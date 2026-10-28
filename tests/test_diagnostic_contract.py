"""不依赖任何外部题库的诊断契约回归。

小图用来隔离状态机语义；真实库例子保留审计中发现的学生可见问题。
"""
import json
from pathlib import Path
import tempfile
import unittest

from cnkg import engine, evidence
from cnkg.graph import Graph, load_graph


def make_graph(edges=(), level="verified", count=2):
    # These state-machine fixtures explicitly observe their one declared goal
    # through two different quantity structures. Missing mappings and repeated
    # structures have separate counterexample tests in test_goal_observation_v6.
    topics = [{"id": t, "name": t, "grade": 7, "volume": "七上", "chapter": "测试",
               "mastery_criteria": ["能用十以内加减法解决一步合并或求剩余问题。"]}
              for t in ("A", "B", "C")]
    questions = [{"id": f"{t}-q{i}", "topicId": t, "type": "numeric",
                  "difficulty": "basic", "answer": "2", "representation": "verbal",
                  "tested_criterion_indices": [0],
                  "prompt": "一组有1个，另一组有1个，合起来有几个？" if i % 2 else "原有5个，取走3个，还剩几个？",
                  "verify": "1+1" if i % 2 else "5-3",
                  "family_id": "quantity.combine" if i % 2 else "quantity.remove",
                  "evidence_level": level, "source": "external-fixture"}
                 for t in ("A", "B", "C") for i in range(1, count + 1)]
    return Graph(topics, list(edges), questions)


def answer(graph, session, verdict):
    item = engine.next_item(graph, session)
    if item is None:
        raise AssertionError("预期当前应该有可答题目")
    engine.apply_answer(graph, session, verdict, text="2" if verdict == "correct" else "3")


class DiagnosticContractTest(unittest.TestCase):
    def test_explained_wrong_cannot_mark_weak_or_descend(self):
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "hard"}],
                       level="explained")
        s = engine.create_session(g, ["A"])
        answer(g, s, "wrong")
        # 即使调用方篡改 current 的证据标记，也必须以图中的真实题目策略为准。
        engine.next_item(g, s)
        s["current"]["evidence"]["may_judge_weak"] = True
        engine.apply_answer(g, s, "wrong")
        self.assertIsNone(engine.next_item(g, s))
        self.assertEqual(s["state"]["A"], "insufficient_evidence")
        self.assertNotIn("B", s["asked"])
        result = engine.summarize(g, s)
        self.assertEqual(result["weak"], [])
        self.assertEqual(result["roots"], [])

    def test_explained_correct_is_provisional_not_teacher_review(self):
        g = make_graph(level="explained")
        s = engine.create_session(g, ["A"])
        answer(g, s, "correct")
        answer(g, s, "correct")
        result = engine.summarize(g, s)
        self.assertEqual(result["mastered"], ["A"])
        self.assertTrue(result["reviewer_pending"])
        self.assertEqual(result["confidence"], "provisional")
        self.assertFalse(result["causal_claim"])

    def test_soft_related_topic_is_neither_assumed_nor_required(self):
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "soft"}])
        self.assertEqual(g.ancestors("A"), {"B"})
        self.assertEqual(g.ancestors("A", hard_only=True), set())
        # 两种缓存不能互相污染。
        self.assertEqual(g.ancestors("A"), {"B"})
        s = engine.create_session(g, ["A"])
        answer(g, s, "correct")
        answer(g, s, "correct")
        self.assertNotIn("B", s["state"])
        self.assertNotIn("B", engine.summarize(g, s)["untested"])

    def test_real_approximation_does_not_assume_scientific_notation(self):
        g = load_graph()
        s = engine.create_session(g, ["m7a.2.3.3"])
        answer(g, s, "correct")
        answer(g, s, "correct")
        self.assertNotEqual(s["state"].get("m7a.2.3.2"), "assumed")

    def test_shaky_is_consolidation_and_only_candidate(self):
        g = make_graph()
        s = engine.create_session(g, ["A"])
        answer(g, s, "correct")
        answer(g, s, "wrong")
        r = engine.summarize(g, s)
        self.assertEqual(r["roots"], [])
        self.assertEqual(r["root_candidates"], ["A"])
        self.assertEqual(r["path"], ["A"])
        self.assertEqual(r["support_tasks"][0]["action"], "consolidate")

    def test_limit_preserves_candidate_and_does_not_prescribe_all_ancestors(self):
        # Synthetic complete evidence isolates the limit contract from current
        # production tasks' deliberately narrower observation permissions.
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "hard"},
                        {"topicId": "B", "prerequisiteId": "C", "strength": "hard"}])
        self.assertTrue(all(policy["may_judge_weak"] for policy in g.question_policies.values()))
        s = engine.create_session(g, ["A"], limit=2)
        answer(g, s, "wrong")
        answer(g, s, "wrong")
        self.assertIsNone(engine.next_item(g, s))
        r = engine.summarize(g, s)
        self.assertTrue(r["incomplete"])
        self.assertEqual(r["stop_reason"], "limit")
        self.assertEqual(r["roots"], [])
        self.assertEqual(r["root_candidates"], ["A"])
        self.assertEqual(r["root_evidence"]["A"]["missing_prerequisites"], ["B"])
        self.assertEqual(r["path"], ["B", "A"])
        self.assertNotIn("C", r["path"], "未观察的深层祖先不能整串列为必须补学")
        self.assertNotIn("已判定", engine.verdict_label(r))

    def test_mastered_bridge_keeps_independent_gap(self):
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "hard"},
                        {"topicId": "B", "prerequisiteId": "C", "strength": "hard"}])
        s = engine.create_session(g, ["C", "A"])
        for verdict in ("wrong", "wrong", "wrong", "wrong", "correct", "correct"):
            answer(g, s, verdict)
        r = engine.summarize(g, s)
        self.assertEqual(set(r["roots"]), {"A", "C"})
        self.assertEqual(r["root_evidence"]["A"]["status"], "supported_within_model")
        self.assertFalse(r["root_evidence"]["A"]["causal_claim"])

    def test_skip_is_missing_evidence_not_failure(self):
        g = make_graph()
        s = engine.create_session(g, ["A"])
        answer(g, s, "skip")
        answer(g, s, "skip")
        self.assertEqual(s["state"]["A"], "insufficient_evidence")
        self.assertEqual(engine.summarize(g, s)["weak"], [])

    def test_practice_wrong_gives_teaching_feedback_not_diagnosis(self):
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "hard"}])
        s = engine.create_session(g, ["A"], mode="practice")
        answer(g, s, "wrong")
        answer(g, s, "wrong")
        self.assertEqual(s["state"], {"A": "practice_review"})
        r = engine.summarize(g, s)
        for key in ("weak", "shaky", "mastered", "roots", "root_candidates"):
            self.assertEqual(r[key], [])
        self.assertEqual(r["practice_results"]["A"]["correct_count"], 0)

    def test_practice_success_does_not_assume_ancestors(self):
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "hard"}])
        s = engine.create_session(g, ["A"], mode="practice")
        answer(g, s, "correct")
        answer(g, s, "correct")
        self.assertEqual(s["state"], {"A": "practice_passed"})

    def test_no_items_remains_visible_with_a_confirmation_task(self):
        g = make_graph(count=0)
        s = engine.create_session(g, ["A"])
        self.assertIsNone(engine.next_item(g, s))
        r = engine.summarize(g, s)
        self.assertTrue(r["incomplete"])
        self.assertEqual(r["untested"], ["A"])
        self.assertEqual(r["support_tasks"][0]["action"], "confirm")

    def test_final_answer_at_limit_is_complete(self):
        g = make_graph()
        s = engine.create_session(g, ["A"], limit=2)
        answer(g, s, "correct")
        answer(g, s, "correct")
        self.assertIsNone(engine.next_item(g, s))
        self.assertEqual(engine.summarize(g, s)["stop_reason"], "completed")

    def test_explicit_target_cannot_be_skipped_by_ancestor_inference(self):
        g = make_graph([{"topicId": "A", "prerequisiteId": "B", "strength": "hard"}])
        s = engine.create_session(g, ["A", "B"])
        answer(g, s, "correct")
        answer(g, s, "correct")
        self.assertEqual(engine.next_item(g, s)["topicId"], "B")

    def test_graph_data_directory_does_not_leak_between_loads(self):
        g = make_graph()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, data in (("topics.json", g.topics), ("dependencies.json", []),
                               ("questions.json", g.questions)):
                (base / name).write_text(json.dumps(data))
            custom = load_graph(base)
            self.assertEqual(len(custom.topics), 3)
        self.assertEqual(len(load_graph().topics), 477)


if __name__ == "__main__":
    unittest.main()
