"""Counterexamples to whole-topic conclusions from incomplete task observations."""
import copy
import unittest

from cnkg import engine
from cnkg.admissions import fingerprint
from cnkg.graph import Graph


def graph_for(tasks, criteria=None):
    topics = [{"id": "target", "name": "目标", "mastery_criteria": criteria or ["能加减计算"],
               "grade": 2, "volume": "二上", "chapter": "合成观察边界"},
              {"id": "parent", "name": "未作答的基础", "mastery_criteria": ["能数物体"],
               "grade": 1, "volume": "一上", "chapter": "合成观察边界"}]
    policy = {"practice_eligible": True, "diagnosis_eligible": True, "may_confirm_mastery": True,
              "may_judge_weak": True, "reviewer_pending": True, "confidence": "provisional",
              "evidence_level": "verified", "reason": "synthetic_observation_contract"}
    questions = []
    for index, patch in enumerate(tasks, 1):
        q = {"id": f"target-q{index}", "topicId": "target", "type": "numeric", "difficulty": "basic",
             "prompt": "计算2+3", "answer": "5", "verify": "2+3", "representation": "symbolic",
             "tested_criterion_indices": [0], "family_id": "addition.combine", **patch}
        q["_admission"] = {"fingerprint_version": 2, "content_sha256": fingerprint(q), "policy": copy.deepcopy(policy)}
        questions.append(q)
    return Graph(topics, [{"topicId": "target", "prerequisiteId": "parent", "strength": "hard"}], questions)


def answer_two(graph):
    session = engine.create_session(graph, ["target"])
    for _ in range(2):
        engine.next_item(graph, session)
        engine.apply_answer(graph, session, "correct")
    return session


class TopicObservationTests(unittest.TestCase):
    def test_same_structure_with_changed_numbers_is_not_two_independent_observations(self):
        session = answer_two(graph_for([{}, {"prompt": "计算3+5", "answer": "8", "verify": "3+5"}]))
        self.assertEqual(session["state"]["target"], "insufficient_evidence")
        self.assertIn("insufficient_structural_variety", session["evidence_gaps"]["target"])
        self.assertNotIn("parent", session["state"])

    def test_distinct_tasks_still_cannot_cover_an_unobserved_goal(self):
        session = answer_two(graph_for([{}, {"family_id": "addition.missing-part", "prompt": "2+□=5", "answer": "3", "verify": "5-2"}],
                                      ["能加减计算", "能解释进位的位值依据"]))
        self.assertIn("topic_goals_not_fully_observed", session["evidence_gaps"]["target"])
        self.assertEqual(session["state"]["target"], "insufficient_evidence")

    def test_unknown_goal_or_representation_does_not_acquire_scope_from_a_topic_id(self):
        for change, reason in [({"tested_criterion_indices": []}, "task_goal_alignment_unknown"),
                               ({"representation": "unknown"}, "task_representation_unknown"),
                               ({"family_status": "unclassified"}, "task_structure_unknown")]:
            with self.subTest(change=change):
                session = answer_two(graph_for([change, {"family_id": "addition.missing-part"}]))
                self.assertEqual(session["state"]["target"], "insufficient_evidence")
                self.assertIn(reason, session["evidence_gaps"]["target"])

    def test_bounded_success_cannot_manufacture_evidence_for_unattempted_prerequisites(self):
        graph = graph_for([{}, {"family_id": "addition.missing-part", "prompt": "2+□=5", "answer": "3", "verify": "5-2"}])
        session = answer_two(graph)
        self.assertEqual(session["state"]["target"], "mastered")  # legacy bounded state
        self.assertNotIn("parent", session["state"])
        summary = engine.summarize(graph, session)
        self.assertFalse(summary["independent_mastery_evidence"])
        self.assertFalse(summary["unobserved_prerequisites_inferred"])
        self.assertEqual(summary["observation_scope"], "presented_tasks_only")

    def test_confirmation_prefers_different_structure_and_unobserved_goal(self):
        graph = graph_for([{}, {}, {"family_id": "subtraction.remove", "prompt": "5-2", "answer": "3", "verify": "5-2", "tested_criterion_indices": [1]}],
                          ["能用加法合并数量", "能用减法求剩余"])
        session = engine.create_session(graph, ["target"])
        engine.next_item(graph, session); engine.apply_answer(graph, session, "correct")
        self.assertEqual(engine.next_item(graph, session)["question"]["id"], "target-q3")

    def test_single_screening_answer_keeps_the_rest_of_the_topic_pending(self):
        graph = graph_for([{}, {"family_id": "subtraction.remove"}])
        session = engine.create_session(graph, ["target"], mode="screen")
        engine.next_item(graph, session); engine.apply_answer(graph, session, "correct")
        self.assertEqual(session["state"]["target"], "insufficient_evidence")
        self.assertEqual(session["log"][0]["verdict"], "correct")

    def test_two_wrong_answers_of_one_structure_cannot_mark_the_topic_weak_or_descend(self):
        graph = graph_for([{}, {"prompt": "计算3+5", "answer": "8", "verify": "3+5"}])
        session = engine.create_session(graph, ["target"])
        for _ in range(2):
            engine.next_item(graph, session)
            engine.apply_answer(graph, session, "wrong")
        self.assertEqual(session["state"]["target"], "insufficient_evidence")
        self.assertIn("insufficient_structural_variety", session["evidence_gaps"]["target"])
        self.assertEqual(session["queue"], [])
        self.assertEqual(engine.summarize(graph, session)["weak"], [])

    def test_invalid_criterion_positions_do_not_count_as_observed_goals(self):
        for indices in ([True], [-1], [1], "0", None):
            with self.subTest(indices=indices):
                graph = graph_for([{"tested_criterion_indices": indices},
                                   {"family_id": "addition.missing-part"}])
                session = answer_two(graph)
                self.assertEqual(session["state"]["target"], "insufficient_evidence")
                self.assertIn("task_goal_alignment_unknown", session["evidence_gaps"]["target"])

    def test_secondary_topic_membership_does_not_reinterpret_primary_criterion_indices(self):
        graph = graph_for([{}, {"family_id": "addition.missing-part", "topicId": "parent",
                               "topicIds": ["target", "parent"]}])
        session = answer_two(graph)
        self.assertEqual(session["state"]["target"], "insufficient_evidence")
        self.assertIn("task_goal_alignment_unknown", session["evidence_gaps"]["target"])
        self.assertEqual(session["log"][1]["observed_criterion_indices"], [])

    def test_placeholder_structure_or_representation_is_never_qualified_by_a_label(self):
        cases = [({"family_id": value}, "task_structure_unknown")
                 for value in ("unknown", "unclassified:topic", "unspecified", "未分类")]
        cases += [({"representation": value}, "task_representation_unknown")
                  for value in ("unspecified", "未知", "未分类")]
        for patch, reason in cases:
            with self.subTest(patch=patch):
                session = answer_two(graph_for([{**patch, "family_status": "declared"},
                                               {"family_id": "addition.missing-part"}]))
                self.assertEqual(session["state"]["target"], "insufficient_evidence")
                self.assertIn(reason, session["evidence_gaps"]["target"])


if __name__ == "__main__":
    unittest.main()
