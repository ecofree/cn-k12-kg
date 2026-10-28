"""Missing or stale reviews must not disappear behind a complete plan count."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cnkg.assessment_review import check_reviews, digest


class AssessmentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_name = "data/assessments/fixture.json"
        self.old = self.task("old", "numeric", "fixture.sum")
        self.new = self.task("new", "numeric", "fixture.compare")
        self.process = self.task("process", "rubric", "fixture.record")
        self.source = {"tasks": [self.old, self.new, self.process]}
        self.catalog = {"skills": {"s": {"id": "s", "stage": "primary", "statement": "比较和说明数量关系",
                                            "granularity": "legacy_composite"}},
                        "placements": {"t": {"criterion_skill_ids": ["s"]}}, "units": {}, "projects": {},
                        "assessment_plans": {"s": {"skill_id": "s", "source_kind": "topic", "source_id": "t",
                          "assessment_mode": "mixed", "task_ids": ["old", "new", "process"],
                          "components": [{"id": t["component_ids"][0], "task_ids": [t["id"]]}
                                         for t in self.source["tasks"]],
                          "family_distinctions": [], "unobserved_aspects": ["尚无真实学生原始表现。"]}}}
        self.before = {"source_kind": "topic", "source_id": "t", "task_ids": ["old"],
                       "component_ids": ["c-old"], "objective_family_ids": ["fixture.sum"],
                       "canonical_family_ids": ["fixture.sum"], "objective_breadth_gap": True}
        self.baseline = {"schema": 1, "commit": "a" * 40, "skills_sha256": digest(self.catalog["skills"]),
                         "family_aliases_sha256": digest({}),
                         "previous_review_evidence": [], "goals": {"s": self.before},
                         "tasks": {"old": {"source": self.source_name, "source_task_sha256": digest(self.old)}}}
        self.author = self.report(tasks=[self.review(self.new), self.review(self.process)])
        self.cross = self.report(tasks=[{"task_id": "new", "source_task_sha256": digest(self.new),
            "mathematical_reason": "比较的相差量来自同一计量单位，不能把总量直接作为差量。",
            "decision": "accept_with_limits"}])
        self.goals = self.report(goals=[{"skill_id": "s", "baseline_task_ids": ["old"],
            "current_plan_sha256": digest(self.catalog["assessment_plans"]["s"]),
            "baseline_objective_family_ids": ["fixture.sum"], "current_task_ids": ["old", "new", "process"],
            "current_objective_family_ids": ["fixture.compare", "fixture.sum"], "objective_breadth_gap": False,
            "mathematical_reason": "本夹具分别表示合并量和比较量；实际内容必须另有逐题语义判断。",
            "independent_mastery_evidence": False}])
        self.index = self.report(schema=1, baseline_commit="a" * 40,
                                 expected_counts={"new_tasks": 2, "new_objective_tasks": 1, "baseline_breadth_gaps": 1})
        self.write("data/task_family_aliases.json", {})
        self.flush()

    @staticmethod
    def task(identity, kind, family):
        return {"id": identity, "type": kind, "family_id": family, "family_status": "declared",
                "representation": "symbolic", "cognitive_demand": "execute", "skill_ids": ["s"],
                "objective_ids": ["s"], "component_ids": ["c-" + identity], "answer": "5",
                "prompt": "本测试夹具不作为发布题目。", "phase": "practice"}

    @staticmethod
    def report(**values):
        return {"reviewer_kind": "ai", "independent_teacher_review": False, "student_validation": False, **values}

    def review(self, task):
        return {"task_id": task["id"], "source": self.source_name, "source_task_sha256": digest(task),
                "mathematical_reason": "本夹具检查审读绑定，数学任务另有完整审读。",
                "observation_boundary": "仅记录具体提交，不自动认定整个目标。",
                "family_reason": "比较关系和原合并关系分别记录；过程记录不计入客观族。",
                "compared_family_ids": ["fixture.sum"], "decision": "accept_with_limits"}

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def flush(self):
        self.write(self.source_name, self.source)
        self.catalog["tasks"] = {t["id"]: {**t, "source_kind": "assessment", "availability": "active",
            "policy": {"independent_evidence": False, "may_confirm_mastery": False, "may_judge_weak": False}}
            for t in self.source["tasks"]}
        self.write("data/catalog.json", self.catalog)
        self.index["baseline"] = self.write("docs/reviews/baseline.json", self.baseline)
        for key, name, report, field in (("task_reviews", "author", self.author, "tasks"),
              ("objective_cross_reviews", "cross", self.cross, "tasks"), ("goal_reviews", "goals", self.goals, "goals")):
            self.index[key] = [{**self.write(f"docs/reviews/{name}.json", report), "count": len(report[field])}]
        self.write("docs/reviews/v9-assessment-review-index.json", self.index)

    def errors(self):
        return "\n".join(check_reviews(self.root)["errors"])

    def test_exact_continuation_counts_old_tasks_without_claiming_new_review(self):
        result = check_reviews(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["summary"], {"assessment_tasks": 3, "baseline_tasks": 1, "new_tasks": 2,
            "new_tasks_reviewed": 2, "new_objective_tasks": 1, "new_objective_cross_reviewed": 1,
            "baseline_breadth_gaps": 1, "gap_goals_reviewed": 1, "current_breadth_gaps": 0})

    def test_old_task_cannot_silently_inherit_review_after_edit(self):
        self.old["answer"] = "6"
        self.flush()
        self.assertIn("旧审读不能自动继承", self.errors())

    def test_changed_metadata_invalidates_author_and_cross_reviews(self):
        self.new["phase"] = "transfer"
        self.flush()
        errors = self.errors()
        self.assertIn("完整任务对象不一致", errors)
        self.assertIn("交叉审读没有绑定当前题目", errors)

    def test_missing_or_duplicate_review_is_not_covered_by_matching_total(self):
        self.author["tasks"] = [self.author["tasks"][0], copy.deepcopy(self.author["tasks"][0])]
        self.flush()
        errors = self.errors()
        self.assertIn("未知或重复", errors)
        self.assertIn("process: 新观察任务没有", errors)

    def test_process_task_cannot_replace_missing_objective_cross_review(self):
        self.cross["tasks"][0].update(task_id="process", source_task_sha256=digest(self.process))
        self.flush()
        errors = self.errors()
        self.assertIn("客观题交叉审读未知", errors)
        self.assertIn("new: 新客观题缺少", errors)

    def test_gap_row_cannot_be_removed(self):
        self.goals["goals"] = []
        self.flush()
        self.assertIn("原不足目标未逐项复查", self.errors())
    def test_process_family_cannot_be_counted_as_objective(self):
        self.goals["goals"][0]["current_objective_family_ids"].append("fixture.record")
        self.flush()
        self.assertIn("objective_family_ids", self.errors())

    def test_changed_family_aliases_require_new_structure_review(self):
        self.write("data/task_family_aliases.json", {"fixture.sum": "fixture.compare"})
        self.assertIn("规范题族别名发生变化", self.errors())

    def test_goal_identity_original_entry_and_component_are_preserved(self):
        self.catalog["skills"]["s"]["statement"] = "缩小后的目标"
        self.catalog["assessment_plans"]["s"]["task_ids"].remove("old")
        self.catalog["assessment_plans"]["s"]["components"].pop(0)
        self.flush()
        errors = self.errors()
        for message in ("稳定目标对象", "原目标任务入口被删除", "原过程分项身份被删除"):
            self.assertIn(message, errors)

    def test_changed_process_requirements_cannot_keep_old_goal_review(self):
        self.catalog["assessment_plans"]["s"]["components"][1]["submission_requirements"] = ["新的原始图稿要求"]
        self.flush()
        self.assertIn("当前完整分项计划不一致", self.errors())

    def test_prior_family_comparison_and_honest_reviewer_scope_are_required(self):
        self.author["tasks"][0]["compared_family_ids"] = []
        self.flush()
        self.assertIn("遗漏本目标既有规范题族", self.errors())
        self.author["independent_teacher_review"] = True
        self.flush()
        self.assertIn("教师独立审核", self.errors())

    def test_tampered_report_and_automatic_mastery_promotion_fail(self):
        (self.root / "docs/reviews/author.json").write_text("{}")
        self.assertIn("证据指纹已经改变", self.errors())
        self.flush()
        self.catalog["tasks"]["new"]["policy"]["may_confirm_mastery"] = True
        self.write("data/catalog.json", self.catalog)
        self.assertIn("自动升级为独立掌握", self.errors())

    def test_row_cannot_override_ai_scope_in_any_review_category(self):
        for report, field in ((self.author, "tasks"), (self.cross, "tasks"), (self.goals, "goals")):
            for key, value in (("reviewer_kind", "teacher"), ("independent_teacher_review", True),
                               ("student_validation", True)):
                with self.subTest(field=field, key=key):
                    report[field][0][key] = value
                    self.flush()
                    self.assertIn("AI 审读必须", self.errors())
                    del report[field][0][key]

    def test_cross_review_needs_a_nonblank_text_reason(self):
        for value in (" \n\t", ["a list is not a mathematical judgment"]):
            with self.subTest(value=value):
                self.cross["tasks"][0]["mathematical_reason"] = value
                self.flush()
                self.assertIn("交叉数学判断未完成", self.errors())

    def test_same_objective_report_cannot_be_its_own_cross_review(self):
        common = self.report(tasks=[self.author["tasks"][0]])
        process = self.report(tasks=[self.author["tasks"][1]])
        common_ref = {**self.write("docs/reviews/common.json", common), "count": 1}
        self.index["task_reviews"] = [common_ref,
            {**self.write("docs/reviews/process.json", process), "count": 1}]
        self.index["objective_cross_reviews"] = [common_ref]
        self.write("docs/reviews/v9-assessment-review-index.json", self.index)
        self.assertIn("同一报告不能兼任", self.errors())

    def bind_diagram(self, task):
        name = "web/static/diagrams/fixture.svg"
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>5</text></svg>')
        task["diagram"] = {"src": "/static/diagrams/fixture.svg", "alt": "测试用实际图示。"}
        hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest()}
        if task["id"] in self.baseline["tasks"]:
            self.baseline["tasks"][task["id"]].update(source_task_sha256=digest(task), asset_sha256=hashes)
        for report in (self.author, self.cross):
            for row in report["tasks"]:
                if row["task_id"] == task["id"]:
                    row.update(source_task_sha256=digest(task), asset_sha256=hashes)
        self.flush()
        self.assertTrue(check_reviews(self.root)["ok"], self.errors())
        return path

    def test_svg_replacement_invalidates_author_and_cross_without_url_change(self):
        path = self.bind_diagram(self.new)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>999</text></svg>')
        errors = self.errors()
        self.assertIn("逐题审读没有绑定当前实际图示字节", errors)
        self.assertIn("交叉审读没有绑定当前实际图示字节", errors)

    def test_original_svg_cannot_inherit_review_after_replacement(self):
        path = self.bind_diagram(self.old)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>999</text></svg>')
        self.assertIn("原题图示字节发生变化", self.errors())


if __name__ == "__main__":
    unittest.main()
