"""A complete focus cannot hide a missing goal outside it."""
from pathlib import Path
import json
import tempfile
import unittest

from cnkg.alignment import build_alignment_report
from tools.gap_worklist import build_worklist


class FullScopeTests(unittest.TestCase):
    def test_outside_focus_goal_remains_a_release_blocker(self):
        with tempfile.TemporaryDirectory() as name:
            data = Path(name); (data / "teach").mkdir(); (data / "learning_units").mkdir()
            (data / "alignment_targets.json").write_text(json.dumps({"schema": 1, "topic_ids": ["t1"], "unit_ids": ["u"]}))
            rows = [{"goal_id": "s1", "resource_kind": "topic", "resource_id": "t1", "criterion_index": 0},
                    {"goal_id": "s2", "resource_kind": "topic", "resource_id": "t2", "criterion_index": 0},
                    {"goal_id": "s3", "resource_kind": "unit", "resource_id": "u", "objective_id": "u:o"}]
            for row in rows:
                row.update(title="目标", statement="解释数学关系", stage="primary", role="core", status="materials_ready")
            (data / "teach/t1.json").write_text(json.dumps({"objective_coverage": [{"criterion_index": 0,
                "difficulty_support": {"discriminating_prompt": "两个部分是否重叠？", "hint": "一一对应。", "revision_prompt": "另摆两组。"},
                "observation_note": "合并的解释仍需另一种排列。"}]}))
            (data / "learning_units/u.json").write_text(json.dumps({"instructional_support": {"supports": [{"objective_id": "u:o",
                "diagnostic": {"id": "d"}, "guided_task": {"id": "g"}, "hints": ["逐个对应"], "observation_note": "支持后的表现。"}]}}))
            tasks = {sid: {"id": sid, "skill_ids": [sid], "source_kind": "self_check" if sid == "s1" else "unit",
                           "family_id": "count.combine", "family_status": "declared", "representation": "verbal", "type": "rubric",
                           "availability": "active"} for sid in ("s1", "s3")}
            report = build_alignment_report(data, catalog={"tasks": tasks, "skills": {}}, scope_report={"goals": rows})
            self.assertTrue(report["focus_requirements_met"])
            self.assertFalse(report["all_goal_requirements_met"])
            self.assertTrue(all("s2" in error for error in report["all_goal_errors"]))
            worklist = build_worklist(report)
            self.assertEqual(worklist["summary"]["goals_in_scope"], 3)
            self.assertEqual(worklist["summary"]["goals_with_required_gaps"], 1)
            self.assertEqual(worklist["goals"][0]["goal_id"], "s2")
            self.assertTrue(worklist["goals"][0]["required_gaps"])
            self.assertTrue(next(row for row in worklist["goals"] if row["goal_id"] == "s1")["additional_observation_opportunities"])


if __name__ == "__main__":
    unittest.main()
