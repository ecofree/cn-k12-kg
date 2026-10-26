"""覆盖计数不能把辅助参考、局部单元和演示事件升级成教育证据。"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import unittest
from tools.coverage_report import build_report, check_version_registry

DATA = Path(__file__).resolve().parents[1] / 'data'


class CoverageReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report()
        cls.standards = {s['id']: s for s in json.loads((DATA / 'standards.json').read_text())}
        cls.registry = json.loads((DATA / 'curriculum_versions.json').read_text())

    def test_actual_gaps_are_reported_without_failing_structure(self):
        report = self.report
        self.assertTrue(report['ok'], report['errors'])
        gaps = [r for r in report['topic_worklist'] if r['core_full_card_gap']]
        self.assertEqual(len(gaps), report['explanations']['core_full_card_gaps'])
        self.assertGreater(len(gaps), 0)
        self.assertGreater(report['activities']['pending_development'], 0)
        self.assertGreater(report['application_task_backlog']['open_count'], 0)

    def test_unit_association_does_not_close_whole_topic_gap(self):
        rows = [r for r in self.report['topic_worklist']
                if r['tier'] == 'core' and r['learning_unit_ids'] and not r['legacy_full_card']]
        self.assertTrue(rows)
        self.assertTrue(all(r['core_full_card_gap'] for r in rows))
        self.assertTrue(all(r['full_objective_coverage'] == 'not_audited' for r in rows))

    def test_brief_reference_is_not_full_explanation(self):
        rows = [r for r in self.report['topic_worklist']
                if r['tier'] == 'core' and r['reference_brief'] and not r['legacy_full_card']]
        self.assertTrue(rows)
        self.assertTrue(all(r['core_full_card_gap'] for r in rows))
        self.assertFalse(self.report['explanations']['brief_reference_is_full_coverage'])

    def test_unknown_fine_skill_denominator_and_student_effect_remain_unknown(self):
        self.assertIsNone(self.report['fine_skills']['whole_curriculum_objective_denominator'])
        self.assertIsNone(self.report['fine_skills']['whole_curriculum_coverage_rate'])
        self.assertIsNone(self.report['student_evidence']['verified_participants'])
        self.assertEqual(self.report['student_evidence']['educational_effect'], 'pending')
        self.assertEqual(self.report['teacher_review']['learning_units']['verified_approved'], 0)
        self.assertEqual(self.report['teacher_review']['projects']['verified_approved'], 0)

    def test_real_registry_has_work_item_for_each_existing_clause(self):
        self.assertEqual(check_version_registry(self.registry, self.standards), [])
        self.assertEqual(len(self.registry['clause_worklist']), len(self.standards))
        self.assertEqual(self.report['curriculum_versions']['pending_clause_comparisons'], len(self.standards))

    def test_changed_baseline_excerpt_requires_new_comparison(self):
        standards = copy.deepcopy(self.standards)
        next(iter(standards.values()))['verbatim_text'] += '变更'
        self.assertTrue(any('基线原文' in e for e in check_version_registry(self.registry, standards)))

    def test_claiming_new_version_complete_without_text_or_teacher_is_rejected(self):
        registry = copy.deepcopy(self.registry)
        revision = next(v for v in registry['versions'] if v['role'] == 'discovered_revision')
        revision['comparison_status'] = 'complete'
        self.assertTrue(check_version_registry(registry, self.standards))
        registry = copy.deepcopy(self.registry)
        registry['clause_worklist'][0]['status'] = 'reviewed'
        registry['clause_worklist'][0]['review'] = {'reviewer_type': 'ai', 'reviewer_id': 'model', 'record_id': 'test'}
        self.assertTrue(check_version_registry(registry, self.standards))


if __name__ == '__main__':
    unittest.main()
