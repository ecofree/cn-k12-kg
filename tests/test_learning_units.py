"""教学单元的交付边界：可审查目标、保密题面、有限判分及版本绑定。"""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg.learning import (LearningUnitError, check_unit, grade_task, load_units,
                          public_unit, unit_content_sha256)


def example_unit():
    feedback = {"correct": "结果正确，请继续解释理由。", "incorrect": "回看平均分的含义再试一次。"}
    return {
        "id": "u-fractions", "title": "分数的表示", "stage": "primary",
        "topic_ids": ["A"], "standard_ids": ["S"], "prerequisites": [],
        "review_status": "draft", "author_type": "ai", "teacher_review": {"status": "pending"},
        "objectives": [{"id": "u:o1", "statement": "用等份解释分数的等价表示"}],
        "explanation": {"intuition": "同一个整体可以有不同的等分方式。",
                        "reasoning": "把每一份再等分，分子分母同时改变但总量不变。",
                        "conditions": ["整体相同且每次平均分"], "counterexample": "不同整体的一半未必同样大。"},
        "worked_examples": [{"prompt": "计算1/4+1/6。", "steps": ["通分为十二分之三与十二分之二。", "相加得十二分之五。"],
                             "answer": "5/12", "verify": "1/4+1/6"}],
        "tasks": [
            {"id": "u:p1", "objective_ids": ["u:o1"], "phase": "practice", "type": "numeric",
             "prompt": "四份中的两份占整体的几分之几？", "answer": "0.5", "verify": "2/4",
             "explanation": "2/4与1/2表示相同的量。", "feedback": dict(feedback)},
            {"id": "u:t1", "objective_ids": ["u:o1"], "phase": "transfer", "type": "choice",
             "prompt": "六份中的三份与哪个分数等价？",
             "choices": [{"id": "left", "text": "1/2"}, {"id": "right", "text": "1/3"}],
             "answer": "left", "explanation": "分子分母同时除以3得到1/2。", "feedback": dict(feedback)},
            {"id": "u:d1", "objective_ids": ["u:o1"], "phase": "delayed", "type": "rubric",
             "prompt": "解释为什么两个不同整体的一半未必相等。", "explanation": "先比较整体，再比较份数。",
             "feedback": dict(feedback), "rubric": [
                 {"criterion": "指出单位一不同", "points": 2, "example": "大饼的一半可以比小饼的一半大。"}]},
        ],
    }


def write_fixture(base, units):
    (base / "learning_units").mkdir()
    (base / "topics.json").write_text(json.dumps([{"id": "A"}]))
    (base / "standards.json").write_text(json.dumps([{"id": "S"}]))
    for index, unit in enumerate(units):
        (base / "learning_units" / f"{index}.json").write_text(json.dumps(unit))


class LearningUnitValidationTest(unittest.TestCase):
    def test_repository_units_pass_reference_and_coverage_gate(self):
        units = load_units()
        self.assertTrue(units, "教学单元目录为空，不能宣称已交付教学闭环")

    def test_valid_unit_and_fraction_worked_example(self):
        self.assertEqual(check_unit(example_unit(), {"A"}, {"S"}), [])

    def test_unknown_curriculum_references_are_reported(self):
        unit = example_unit()
        unit["prerequisite_topic_ids"] = ["missing"]
        errors = check_unit(unit, {"A"}, set())
        self.assertTrue(any("standard_ids" in e and "S" in e for e in errors))
        self.assertTrue(any("prerequisite_topic_ids" in e and "missing" in e for e in errors))

    def test_prerequisites_are_readable_descriptions_not_topic_references(self):
        unit = example_unit()
        unit["prerequisites"] = ["知道平均分表示每一份同样多。"]
        unit["prerequisite_topic_ids"] = ["A"]
        self.assertEqual(check_unit(unit, {"A"}, {"S"}), [])
        shown = public_unit(unit)
        self.assertEqual(shown["prerequisites"], unit["prerequisites"])
        self.assertEqual(shown["prerequisite_topic_ids"], ["A"])

    def test_curriculum_supplement_can_stand_outside_textbook_topics(self):
        unit = example_unit()
        unit.update(topic_ids=[], coverage_kind="curriculum_supplement",
                    navigation_note="按课标独立导航；当前教材目录尚无对应节点。")
        self.assertEqual(check_unit(unit, set(), {"S"}), [])
        self.assertEqual(public_unit(unit)["coverage_kind"], "curriculum_supplement")
        self.assertEqual(public_unit(unit)["navigation_note"], unit["navigation_note"])
        unit["standard_ids"] = []
        self.assertTrue(any("standard_ids" in e for e in check_unit(unit)))

    def test_empty_topic_list_requires_explicit_supplement_navigation(self):
        unit = example_unit()
        unit["topic_ids"] = []
        self.assertTrue(any("topic_ids" in e for e in check_unit(unit)))
        unit["coverage_kind"] = "curriculum_supplement"
        self.assertTrue(any("navigation_note" in e for e in check_unit(unit)))

    def test_objective_needs_practice_and_independent_task(self):
        unit = example_unit()
        unit["objectives"].append({"id": "u:o2", "statement": "辨认不同整体"})
        unit["tasks"][0]["objective_ids"].append("u:o2")
        errors = check_unit(unit)
        self.assertTrue(any("u:o2" in e and "迁移" in e for e in errors))
        unit["tasks"][2]["objective_ids"].append("u:o2")
        self.assertEqual(check_unit(unit), [])

    def test_unknown_objective_and_duplicate_task_are_rejected(self):
        unit = example_unit()
        unit["tasks"][1]["objective_ids"] = ["u:missing"]
        unit["tasks"][1]["id"] = unit["tasks"][0]["id"]
        errors = check_unit(unit)
        self.assertTrue(any("未定义目标" in e for e in errors))
        self.assertTrue(any("任务 ID 重复" in e for e in errors))

    def test_missing_phase_is_not_complete_learning_cycle(self):
        unit = example_unit()
        unit["tasks"] = unit["tasks"][:2]
        self.assertTrue(any("delayed" in e for e in check_unit(unit)))

    def test_numeric_answer_verify_mismatch_is_rejected(self):
        unit = example_unit()
        unit["tasks"][0]["answer"] = "0.75"
        self.assertTrue(any("verify" in e for e in check_unit(unit)))

    def test_broken_choice_and_invalid_rubric_points_are_rejected(self):
        unit = example_unit()
        unit["tasks"][1]["choices"][1]["text"] = "1/2"
        unit["tasks"][2]["rubric"][0]["points"] = True
        errors = check_unit(unit)
        self.assertTrue(any("选项正文" in e for e in errors))
        self.assertTrue(any("points" in e for e in errors))

    def test_wrong_field_types_return_errors_not_traceback(self):
        unit = example_unit()
        unit["stage"] = []
        unit["review_status"] = {}
        unit["teacher_review"]["status"] = []
        unit["tasks"][0]["phase"] = {}
        unit["tasks"][0]["type"] = []
        self.assertGreaterEqual(len(check_unit(unit)), 5)

    def test_loader_checks_cross_file_task_ids(self):
        first, second = example_unit(), example_unit()
        second["id"] = "u-other"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_fixture(base, [first, second])
            with self.assertRaisesRegex(LearningUnitError, "跨单元任务 ID 重复"):
                load_units(base)

    def test_loader_reads_units_and_does_not_mutate_them(self):
        unit = example_unit()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_fixture(base, [unit])
            self.assertEqual(load_units(base), {unit["id"]: unit})

    def test_invalid_json_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_fixture(base, [example_unit()])
            (base / "learning_units" / "bad.json").write_text('{"id":')
            with self.assertRaisesRegex(LearningUnitError, "bad.json"):
                load_units(base)


class PublicLessonTest(unittest.TestCase):
    def test_task_secrets_are_absent_but_teaching_examples_remain(self):
        unit = example_unit()
        unit["tasks"][0]["accept"] = ["secret-alias"]
        unit["tasks"][0]["private_hint"] = "secret-private-hint"
        unit["tasks"][1]["choices"][0]["is_correct"] = True
        shown = public_unit(unit)
        for task in shown["tasks"]:
            for key in ("answer", "verify", "accept", "explanation", "feedback", "private_hint"):
                self.assertNotIn(key, task)
        self.assertEqual(shown["worked_examples"][0]["answer"], "5/12")
        self.assertEqual(shown["tasks"][2]["rubric"], [{"criterion": "指出单位一不同", "points": 2}])
        self.assertNotIn("is_correct", shown["tasks"][1]["choices"][0])
        self.assertEqual(shown["teacher_review"], {"status": "pending"})

    def test_student_payload_cannot_mutate_canonical_unit(self):
        unit = example_unit()
        shown = public_unit(unit)
        shown["tasks"][0]["objective_ids"].append("other")
        shown["explanation"]["conditions"].append("mutated")
        self.assertEqual(unit, example_unit())


class TaskGradingTest(unittest.TestCase):
    def test_numeric_accepts_equivalent_fractions(self):
        task = example_unit()["tasks"][0]
        self.assertEqual(grade_task(task, "1/2")["status"], "correct")
        self.assertEqual(grade_task(task, "2 / 4")["status"], "correct")
        self.assertEqual(grade_task(task, "3/4")["status"], "wrong")

    def test_invalid_number_does_not_mark_wrong_or_reveal_answer(self):
        task = example_unit()["tasks"][0]
        for raw in ("1/0", "nan", "inf", "1+1", "__import__('os')", True, {"answer": 0.5}):
            result = grade_task(task, raw)
            self.assertEqual(result["status"], "invalid")
            self.assertNotIn("answer", result)
            self.assertNotIn("explanation", result)

    def test_choice_uses_option_identity_and_rejects_unknown_option(self):
        task = example_unit()["tasks"][1]
        self.assertEqual(grade_task(task, "left")["status"], "correct")
        self.assertEqual(grade_task(task, "A")["status"], "correct")
        self.assertEqual(grade_task(task, "right")["status"], "wrong")
        self.assertEqual(grade_task(task, "missing")["status"], "invalid")
        self.assertEqual(grade_task(task, "right")["answer_text"], "1/2")

    def test_short_respects_required_representation(self):
        task = {"type": "short", "answer": "5/12", "accept": ["十二分之五"]}
        self.assertEqual(grade_task(task, "5 / 12。")["status"], "correct")
        self.assertEqual(grade_task(task, "十二分之五")["status"], "correct")
        self.assertEqual(grade_task(task, "10/24")["status"], "wrong")

    def test_open_answer_never_auto_passes_even_when_matching_example(self):
        task = example_unit()["tasks"][2]
        answer = task["rubric"][0]["example"]
        result = grade_task(task, answer)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("rubric", result)
        self.assertNotIn("score", result)
        self.assertNotIn("mastered", result)
        self.assertEqual(grade_task(task, "")["status"], "invalid")

    def test_broken_question_requests_review_instead_of_penalizing_student(self):
        task = example_unit()["tasks"][0]
        task["answer"] = "3"
        result = grade_task(task, "1/2")
        self.assertEqual(result["status"], "needs_review")


class UnitVersionTest(unittest.TestCase):
    def test_canonical_hash_ignores_only_workflow_metadata(self):
        unit = example_unit()
        original = unit_content_sha256(unit)
        reordered = dict(reversed(list(unit.items())))
        self.assertEqual(unit_content_sha256(reordered), original)
        unit["review_status"] = "reviewed"
        unit["teacher_review"] = {"status": "approved", "arbitrary": "metadata"}
        self.assertEqual(unit_content_sha256(unit), original)
        unit["tasks"][0]["prompt"] += "新的条件"
        self.assertNotEqual(unit_content_sha256(unit), original)

    def test_teacher_review_requires_identity_and_matching_content_version(self):
        unit = example_unit()
        unit["review_status"] = "reviewed"
        unit["teacher_review"] = {"status": "approved", "reviewer_type": "teacher",
                                  "reviewer_id": "test-teacher", "record_id": "test-review",
                                  "content_sha256": unit_content_sha256(unit)}
        self.assertEqual(check_unit(unit), [])
        unit["tasks"][0]["feedback"]["incorrect"] = "已修改反馈"
        self.assertTrue(any("版本" in e for e in check_unit(unit)))
        unit["teacher_review"]["reviewer_type"] = "ai"
        self.assertTrue(any("独立审核" in e for e in check_unit(unit)))

    def test_diagrams_are_safe_and_asset_edits_change_unit_version(self):
        unit = example_unit()
        diagram = {"src": "/static/diagrams/test.svg", "alt": "两等份中的一份"}
        unit["diagram"] = dict(diagram)
        unit["tasks"][0]["diagram"] = dict(diagram)
        with tempfile.TemporaryDirectory() as directory, patch("cnkg.learning.DIAGRAM_DIR", Path(directory)):
            file = Path(directory) / "test.svg"
            file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>1/2</text></svg>')
            self.assertEqual(check_unit(unit), [])
            original = unit_content_sha256(unit)
            self.assertEqual(public_unit(unit)["tasks"][0]["diagram"], diagram)
            file.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>1/3</text></svg>')
            self.assertNotEqual(unit_content_sha256(unit), original)
            unit["diagram"]["src"] = "/static/diagrams/../../questions.json"
            self.assertTrue(any("diagram" in e for e in check_unit(unit)))


if __name__ == "__main__":
    unittest.main()
