"""Semantic change reports use real base objects and never approve themselves."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cnkg.change_review import review_changes, validate_review_binding


class ChangeReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.card = {"topicId": "t", "definition": "数的含义", "conditions": ["每份等大"],
            "worked_example": {"prompt": "一加一", "answer": "2", "family_id": "f"},
            "objective_coverage": [{"criterion_index": 0, "title": "解释数量", "explanation": "把两部分合起来",
                                    "self_check": {"prompt": "说明一加一", "reference_answer": "2", "criteria": ["两部分合成总数"]}}],
            "diagram": {"src": "/static/diagrams/test.svg", "alt": "两点"}}
        registry = {"skills": {"s1": {"prerequisite_skill_ids": []}, "s2": {"prerequisite_skill_ids": ["s1"]}},
                    "placements": {"t": {"criterion_skill_ids": ["s1"]}}, "objective_skill_ids": {"u:o": "s2"}}
        self.catalog = {"skills": registry["skills"], "tasks": {
            "q": {"id": "q", "source_ref": {"path": "data/teach/t.json"}, "skill_ids": ["s1"], "material_id": "topic:t"},
            "uq": {"id": "uq", "source_ref": {"path": "data/learning_units/u.json"}, "skill_ids": ["s2"], "material_id": "unit:u"}},
            "units": {"u": {"id": "u", "skill_ids": ["s2"], "task_ids": ["uq"]}},
            "source_index": {"self_check:t:0": "q"}}
        self.write_json("data/teach/t.json", self.card)
        self.write_json("data/skills/registry.json", registry)
        self.write_json("data/catalog.json", self.catalog)
        self.write("web/static/diagrams/test.svg", '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="1" cy="1" r="1"/></svg>')
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True)

    def write(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")

    def write_json(self, path, value):
        self.write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def completed_review(self, references):
        self.card["conditions"].append("正整数")
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        completed = copy.deepcopy(report["review_template"])
        completed["status"] = "reviewed"
        for item in completed["items"]:
            item.update(review_status="reviewed", reviewer_kind="ai", reviewer="test reviewer",
                        findings=["核对条件和观察边界"], decision="accept_with_limits",
                        evidence_refs=copy.deepcopy(references))
        return report, completed

    def test_changed_answer_and_conditions_are_classified_and_propagate_explicit_dependencies(self):
        self.card["worked_example"]["answer"] = "3"
        self.card["conditions"] = ["允许每份不等大"]
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        row = report["files"][0]
        self.assertIn("answers_and_criteria", row["categories"])
        self.assertIn("conditions", row["categories"])
        answer = next(item for item in row["field_changes"] if item["pointer"] == "/worked_example/answer")
        self.assertEqual((answer["before"], answer["after"]), ("2", "3"))
        self.assertIn("s1", row["impact"]["skills"])
        self.assertIn("q", row["impact"]["tasks"])
        self.assertEqual(report["impact"]["downstream_skill_ids"], ["s2"])
        self.assertTrue(any(row["path"] == "/?unit=u" for row in report["preview_tasks"]))
        self.assertFalse(report["automatic_semantic_approval"])
        self.assertEqual(report["review_status"], "pending")

    def test_json_formatting_change_is_not_mislabelled_as_semantic_change(self):
        self.write("data/teach/t.json", json.dumps(self.card, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        report = review_changes(self.root, self.base)
        self.assertTrue(report["files"][0]["format_only"])
        self.assertFalse(report["files"][0]["semantic_review_required"])
        self.assertEqual(report["files"][0]["field_changes"], [])
        self.assertEqual(report["summary"]["review_items"], 0)

    def test_nested_json_type_changes_are_not_hidden_by_python_numeric_equality(self):
        for before, after in ((True, 1), (1, 1.0)):
            with self.subTest(before=type(before).__name__, after=type(after).__name__):
                self.write_json("data/typed.json", {"nested": [{"value": before}]})
                self.git("add", "data/typed.json")
                self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "typed baseline")
                base = self.git("rev-parse", "HEAD").strip()
                self.write_json("data/typed.json", {"nested": [{"value": after}]})
                report = review_changes(self.root, base)
                row = next(row for row in report["files"] if row["path"] == "data/typed.json")
                self.assertFalse(row["format_only"])
                self.assertTrue(row["semantic_review_required"])
                self.assertEqual(row["field_changes"][0]["pointer"], "/nested/0/value")

    def test_untracked_new_author_file_is_included_with_semantic_fields(self):
        self.write_json("data/teach/new.json", {**self.card, "topicId": "new"})
        report = review_changes(self.root, self.base)
        row = next(row for row in report["files"] if row["path"] == "data/teach/new.json")
        self.assertTrue(row["untracked_new_source"])
        self.assertEqual(row["operation"], "added")
        self.assertIn("answers_and_criteria", row["categories"])
        self.assertIn("topic:new", row["impact"]["materials"])
        self.assertTrue(report["unresolved_registrations"])

    def test_deleted_objective_is_visible_and_keeps_base_skill_impact(self):
        self.card["objective_coverage"].clear()
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        row = report["files"][0]
        removal = next(item for item in row["field_changes"] if item["operation"] == "remove")
        self.assertEqual(removal["before"]["title"], "解释数量")
        self.assertIn("s1", row["impact"]["skills"])
        self.assertIn("原始评分", report["historical_record_policy"]["sessions"])
        self.assertIn("不得清除旧事项", report["historical_record_policy"]["unresolved_needs"])

    def test_diagram_geometry_change_maps_back_to_actual_learning_page(self):
        self.write("web/static/diagrams/test.svg", '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="1" r="1"/></svg>')
        report = review_changes(self.root, self.base)
        row = report["files"][0]
        self.assertIn("representations", row["categories"])
        self.assertIn("topic:t", row["impact"]["materials"])
        self.assertTrue(any(page["url"] == "http://127.0.0.1:8765/?teach=t" for page in report["preview_tasks"]))
        self.assertTrue(all(page["status"] == "pending" for page in report["preview_tasks"]))

    def test_runtime_and_schema_changes_require_runtime_previews(self):
        self.write("cnkg/answers.py", "def grade(value):\n    return 'correct'\n")
        self.write_json("schema/example.schema.json", {"type": "object", "additionalProperties": False})
        report = review_changes(self.root, self.base)
        self.assertTrue(any("schema_contract" in row["categories"] for row in report["files"]))
        self.assertTrue(report["impact"]["runtime_scope"])
        paths = {row["path"] for row in report["preview_tasks"]}
        self.assertTrue({"/?scope=1", "/?needs=1", "/?profile=1"} <= paths)

    def test_assessment_figure_only_change_requires_the_exact_goal_preview(self):
        source = "data/assessments/primary.json"
        self.catalog["tasks"]["observe"] = {"id": "observe", "source_ref": {"path": source},
            "skill_ids": ["s1"], "material_id": "assessment:s1"}
        self.catalog["assessment_plans"] = {"s1": {"skill_id": "s1", "source_path": source,
            "task_ids": ["observe"]}}
        self.catalog["materials"] = {"assessment:s1": {"topic_ids": ["t"], "kind": "assessment"}}
        self.write_json(source, {"goals": [{"skill_id": "s1"}], "tasks": [{"id": "observe",
            "diagram": {"src": "/static/diagrams/observation.svg", "alt": "任务原图"}}]})
        self.write_json("data/catalog.json", self.catalog)
        self.write("web/static/diagrams/observation.svg", '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="1" cy="1" r="1"/></svg>')
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "registered observation figure")
        base = self.git("rev-parse", "HEAD").strip()
        self.write("web/static/diagrams/observation.svg", '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="1" r="1"/></svg>')
        report = review_changes(self.root, base)
        self.assertEqual([row["path"] for row in report["files"]], ["web/static/diagrams/observation.svg"])
        self.assertIn("assessment:s1", report["files"][0]["impact"]["materials"])
        self.assertIn("/?goal=s1", {row["path"] for row in report["preview_tasks"]})

    def test_changed_example_also_impacts_tasks_with_declared_shared_family(self):
        self.catalog["tasks"]["uq"].update(family_id="f", family_status="declared")
        self.write_json("data/catalog.json", self.catalog)
        self.git("add", "data/catalog.json")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "register shared family")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.card["worked_example"]["answer"] = "3"
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        row = report["files"][0]
        self.assertIn("f", row["related_family_ids"])
        self.assertIn("uq", row["impact"]["tasks"])
        self.assertIn("unit:u", row["impact"]["materials"])

    def test_pending_and_stale_review_templates_cannot_pass_binding_check(self):
        self.card["conditions"].append("正整数")
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        self.assertTrue(validate_review_binding(report, report["review_template"]))
        completed = copy.deepcopy(report["review_template"])
        completed["status"] = "reviewed"
        for item in completed["items"]:
            item.update(review_status="reviewed", reviewer_kind="ai", reviewer="test reviewer",
                        findings=["核对基线条件变化并保留适用范围"], decision="accept_with_limits", evidence_refs=["test:explicit-review"])
        self.assertEqual(validate_review_binding(report, completed), [])
        self.card["conditions"].append("第二次变化")
        self.write_json("data/teach/t.json", self.card)
        changed = review_changes(self.root, self.base)
        self.assertNotEqual(report["content_fingerprint"], changed["content_fingerprint"])
        self.assertTrue(validate_review_binding(changed, completed))

    def test_repository_gate_requires_existing_evidence_with_matching_raw_hash(self):
        self.write("docs/reviews/evidence.json", '{"checks":["条件与答案已核对"]}\n')
        evidence = self.root / "docs/reviews/evidence.json"
        reference = {"path": "docs/reviews/evidence.json",
                     "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
        report, review = self.completed_review([reference])
        self.assertEqual(validate_review_binding(report, review, root=self.root), [])
        evidence.write_text('{"checks":["后来修改的核对记录"]}\n')
        self.assertTrue(any("SHA256" in error for error in
                            validate_review_binding(report, review, root=self.root)))

    def test_repository_gate_rejects_placeholder_missing_and_outside_references(self):
        self.write("docs/reviews/real.json", "{}\n")
        report, review = self.completed_review(["placeholder"])
        # Compatibility checks on an in-memory record deliberately do not
        # establish file provenance; real CLI gates must always provide root.
        self.assertEqual(validate_review_binding(report, review), [])
        for reference in ("placeholder", {"path": "docs/missing.json", "sha256": "a" * 64},
                          {"path": "../outside.json", "sha256": "a" * 64},
                          {"path": str(self.root / "docs/reviews/real.json"), "sha256": "a" * 64},
                          {"path": "docs/reviews", "sha256": "a" * 64},
                          {"path": "docs/reviews/real.json", "sha256": "not-a-hash"}):
            with self.subTest(reference=reference):
                for row in review["items"]:
                    row["evidence_refs"] = [reference]
                self.assertTrue(validate_review_binding(report, review, root=self.root))

    def test_repository_gate_rejects_evidence_symlink_and_linked_ancestor(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "evidence.json"
            external.write_text("{}\n")
            (self.root / "docs/reviews").mkdir(parents=True)
            (self.root / "docs/reviews/link.json").symlink_to(external)
            (self.root / "docs/linked").symlink_to(external.parent, target_is_directory=True)
            report, review = self.completed_review([])
            for name in ("docs/reviews/link.json", "docs/linked/evidence.json"):
                with self.subTest(path=name):
                    reference = {"path": name, "sha256": hashlib.sha256(external.read_bytes()).hexdigest()}
                    for row in review["items"]:
                        row["evidence_refs"] = [reference]
                    errors = validate_review_binding(report, review, root=self.root)
                    self.assertTrue(any("符号链接" in error for error in errors))

    def test_both_formal_cli_entrypoints_validate_files_under_actual_root(self):
        from tools import check_change_review, review_changes as review_cli

        _, review = self.completed_review(["not-an-evidence-file"])
        self.write_json("docs/reviews/record.json", review)
        self.write_json("docs/reviews/current.json", {
            "base_commit": self.base, "review_record": "docs/reviews/record.json"})
        with patch.object(check_change_review, "ROOT", self.root), redirect_stdout(StringIO()):
            self.assertEqual(check_change_review.main(), 1)
        arguments = ["review_changes.py", "--base", self.base, "--root", str(self.root),
                     "--check-review", "docs/reviews/record.json"]
        with patch.object(sys, "argv", arguments), redirect_stdout(StringIO()):
            self.assertEqual(review_cli.main(), 1)

    def test_browser_preview_accepts_current_real_git_impact_inventory(self):
        from tools.browser_check import require_current_preview

        self.card["conditions"].append("正整数")
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        self.write_json("build/content-review.json", report)
        self.assertGreaterEqual(len(report["preview_tasks"]), 2)
        self.assertEqual(require_current_preview(self.root), report["content_fingerprint"])

    def test_browser_preview_rejects_inventory_after_content_changes(self):
        from tools.browser_check import require_current_preview

        self.card["conditions"].append("正整数")
        self.write_json("data/teach/t.json", self.card)
        self.write_json("build/content-review.json", review_changes(self.root, self.base))
        self.card["worked_example"]["answer"] = "3"
        self.write_json("data/teach/t.json", self.card)
        with self.assertRaisesRegex(ValueError, "过期"):
            require_current_preview(self.root)

    def test_browser_preview_rejects_dropped_impacted_page_even_with_current_fingerprint(self):
        from tools.browser_check import require_current_preview

        self.card["conditions"].append("正整数")
        self.write_json("data/teach/t.json", self.card)
        report = review_changes(self.root, self.base)
        self.assertGreaterEqual(len(report["preview_tasks"]), 2)
        report["preview_tasks"].pop()
        self.write_json("build/content-review.json", report)
        with self.assertRaisesRegex(ValueError, "缺页"):
            require_current_preview(self.root)


if __name__ == "__main__":
    unittest.main()
