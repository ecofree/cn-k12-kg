"""Public schema contracts are executed against active authoring resources."""
from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from cnkg.contracts import (ROOT, SCHEMA_BASE, clear_schema_cache, load_schema,
                            validate_repository, validate_resource)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = json.loads((ROOT / "data/topics.json").read_text())
        cls.questions = json.loads((ROOT / "data/questions.json").read_text())
        cls.unit = json.loads((ROOT / "data/learning_units/fraction_equivalence.json").read_text())
        cls.project = json.loads((ROOT / "data/projects/junior_track_design.json").read_text())

    def test_every_active_resource_matches_the_executed_public_contract(self):
        self.assertEqual(validate_repository(), [])

    def test_legacy_choice_keys_and_undeclared_fields_are_rejected(self):
        question = copy.deepcopy(next(q for q in self.questions if q["type"] == "choice"))
        question["choices"][0]["key"] = question["choices"][0].pop("id")
        errors = validate_resource("question", question)
        self.assertTrue(any("id" in error for error in errors), errors)
        self.assertTrue(any("key" in error for error in errors), errors)
        topic = copy.deepcopy(self.topics[0])
        topic["mastry_criteria"] = topic["mastery_criteria"]
        self.assertTrue(validate_resource("topic", topic))

    def test_known_legacy_version_is_explicitly_supported_unknown_is_not(self):
        topic = copy.deepcopy(self.topics[0])
        self.assertNotIn("schema_version", topic)
        self.assertEqual(validate_resource("topic", topic), [])
        topic["schema_version"] = 1
        self.assertEqual(validate_resource("topic", topic), [])
        for invalid in (2, "1", True):
            topic["schema_version"] = invalid
            self.assertTrue(validate_resource("topic", topic), invalid)
        unit = copy.deepcopy(self.unit)
        unit["schema"] = 2
        self.assertTrue(validate_resource("learning-unit", unit))

    def test_one_observable_topic_goal_is_valid_not_mechanically_two(self):
        topic = copy.deepcopy(self.topics[0])
        topic["mastery_criteria"] = ["能在给定条件下完成一项可观察任务"]
        self.assertEqual(validate_resource("topic", topic), [])
        topic["mastery_criteria"] = []
        self.assertTrue(validate_resource("topic", topic))

    def test_source_observation_cannot_regress_into_exam_scope(self):
        topic = copy.deepcopy(self.topics[0])
        topic["exam_scope"] = "textbook_only"
        self.assertTrue(validate_resource("topic", topic))
        topic["exam_scope"] = "included"
        topic["exam_scope_evidence"] = {"method": "explicit_scope", "reason": "测试范围"}
        self.assertTrue(validate_resource("topic", topic))
        topic["exam_scope_evidence"].update(
            source_url="https://example.edu/scope", scope_reference="明确条款定位")
        self.assertEqual(validate_resource("topic", topic), [])

    def test_answer_specs_validate_kind_precision_and_known_fields(self):
        from cnkg.answers import validate_answer_spec
        valid = [
            {"kind": "number"},
            {"version": 1, "kind": "number", "comparison": "rounded", "decimal_places": 2},
            {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "0.01"},
            {"kind": "number", "comparison": "tolerance", "relative_tolerance": "1e-100"},
            {"kind": "coordinate", "dimensions": 2},
            {"kind": "polynomial", "variables": ["x", "y"], "representation": "factored"},
        ]
        for spec in valid:
            self.assertEqual(validate_resource("answer-spec", spec), [], spec)
            self.assertEqual(validate_answer_spec(spec), [], spec)
        invalid = [
            {"kind": "number", "comparison": "rounded"},
            {"kind": "number", "comparison": "tolerance"},
            {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "-1"},
            {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "1e999"},
            {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "0." + "0" * 128},
            {"kind": "number", "comparison": "tolerance", "absolute_tolerance": "0.1\n"},
            {"kind": "number", "decimal_places": 2},
            {"kind": "number", "rounding": "half_up"},
            {"kind": "number", "absolute_tolerance": "0.01"},
            {"kind": "coordinate", "comparison": "rounded", "decimal_places": 2},
            {"kind": "coordinate", "representation": "any"},
            {"kind": "polynomial", "variables": ["x", "x"]},
            {"kind": "text", "representation": "factored"},
            {"kind": "text", "representation": "any"},
            {"kind": "number", "typo_precision": 2},
        ]
        for spec in invalid:
            self.assertTrue(validate_resource("answer-spec", spec), spec)
            self.assertTrue(validate_answer_spec(spec), spec)

    def test_answer_kind_must_match_interaction_type(self):
        numeric = copy.deepcopy(next(q for q in self.questions if q["type"] == "numeric"))
        numeric["answer_spec"] = {"kind": "text"}
        self.assertTrue(validate_resource("question", numeric))
        short = copy.deepcopy(next(q for q in self.questions if q["type"] == "short"))
        short["answer_spec"] = {"kind": "rubric"}
        self.assertTrue(validate_resource("question", short))
        unit = copy.deepcopy(self.unit)
        task = next(t for t in unit["tasks"] if t["type"] == "rubric")
        task["answer_spec"] = {"kind": "number"}
        self.assertTrue(validate_resource("learning-unit", unit))

    def test_nested_contracts_reject_missing_rubric_and_unknown_fields(self):
        unit = copy.deepcopy(self.unit)
        task = next(t for t in unit["tasks"] if t["type"] == "rubric")
        task.pop("rubric")
        self.assertTrue(validate_resource("learning-unit", unit))
        project = copy.deepcopy(self.project)
        project["rubric"][0]["objective_id"] = "a_typo"
        self.assertTrue(validate_resource("project", project))

    def test_invalid_review_cannot_claim_approval_without_identity(self):
        unit = copy.deepcopy(self.unit)
        unit["teacher_review"] = {"status": "approved"}
        self.assertTrue(validate_resource("learning-unit", unit))
        unit["teacher_review"].update(reviewer_type="ai", reviewer_id="model",
                                      record_id="test", content_sha256="0" * 64)
        self.assertTrue(validate_resource("learning-unit", unit))

    def test_non_finite_values_are_not_valid_json_resources(self):
        project = copy.deepcopy(self.project)
        project["rubric"][0]["points"] = float("nan")
        self.assertTrue(validate_resource("project", project))

    def _minimal_repository(self, directory):
        data = Path(directory)
        unit = copy.deepcopy(self.unit)
        needed = set(unit["topic_ids"] + unit.get("prerequisite_topic_ids", []))
        question = copy.deepcopy(next(q for q in self.questions if q["topicId"] in needed))
        needed.update(question.get("topicIds", [question["topicId"]]))
        topics = [copy.deepcopy(t) for t in self.topics if t["id"] in needed]
        for filename, value in (("topics.json", topics), ("dependencies.json", []),
                                ("questions.json", [question])):
            (data / filename).write_text(json.dumps(value, ensure_ascii=False))
        shutil.copyfile(ROOT / "data/standards.json", data / "standards.json")
        (data / "learning_units").mkdir()
        (data / "learning_units/fraction_equivalence.json").write_text(json.dumps(unit, ensure_ascii=False))
        return data, unit

    def test_task_goal_reference_typo_is_rejected_beyond_schema_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            data, unit = self._minimal_repository(directory)
            self.assertEqual(validate_repository(data), [])
            unit["tasks"][0]["objective_ids"] = ["missing:goal"]
            self.assertEqual(validate_resource("learning-unit", unit), [])
            (data / "learning_units/fraction_equivalence.json").write_text(json.dumps(unit))
            errors = validate_repository(data)
            self.assertTrue(any("missing:goal" in error for error in errors), errors)

    def test_missing_question_topic_and_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data, _ = self._minimal_repository(directory)
            questions = json.loads((data / "questions.json").read_text())
            questions[0]["topicId"] = "m9b.99.99"
            questions.append(copy.deepcopy(questions[0]))
            (data / "questions.json").write_text(json.dumps(questions))
            errors = validate_repository(data)
            self.assertTrue(any("重复" in error for error in errors), errors)
            self.assertTrue(any("m9b.99.99" in error for error in errors), errors)

    def test_formal_validate_command_executes_the_contract_gate(self):
        from tools import validate
        with tempfile.TemporaryDirectory() as directory:
            data, _ = self._minimal_repository(directory)
            questions = json.loads((data / "questions.json").read_text())
            questions[0]["undeclared_release_flag"] = True
            (data / "questions.json").write_text(json.dumps(questions))
            output = io.StringIO()
            with mock.patch.object(validate, "DATA", data), \
                    mock.patch.object(sys, "argv", ["validate.py"]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(validate.main(), 1)
            self.assertIn("undeclared_release_flag", output.getvalue())

    def test_unknown_schema_references_fail_without_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            schema_dir = Path(directory)
            for source in (ROOT / "schema").glob("*.schema.json"):
                shutil.copy(source, schema_dir / source.name)
            schema = load_schema("topic", schema_dir)
            schema["properties"]["id"] = {"$ref": "https://untrusted.invalid/topic.json"}
            (schema_dir / "topic.schema.json").write_text(json.dumps(schema))
            with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
                with self.assertRaises(Exception) as context:
                    validate_resource("topic", self.topics[0], schema_dir=schema_dir)
                self.assertNotIsInstance(context.exception, AssertionError)
                self.assertIn("untrusted.invalid", str(context.exception))
        clear_schema_cache()

    def test_contract_module_import_does_not_require_build_dependencies(self):
        process = subprocess.run(
            [sys.executable, "-S", "-c", "import cnkg.contracts; print('ok')"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
