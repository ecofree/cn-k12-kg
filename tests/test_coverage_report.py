"""覆盖计数不能把辅助参考、局部单元和演示事件升级成教育证据。"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from tools.coverage_report import build_report, check_version_registry

DATA = Path(__file__).resolve().parents[1] / 'data'


class CoverageReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report()
        cls.standards = {s['id']: s for s in json.loads((DATA / 'standards.json').read_text())}
        cls.registry = json.loads((DATA / 'curriculum_versions.json').read_text())

        # Use actual complete core cards, then remove only their isolated copies.
        # Neither a learning-unit association nor a reference brief may hide the loss.
        core_rows = [r for r in cls.report['topic_worklist']
                     if r['tier'] == 'core' and r['legacy_full_card']]
        cls.unit_row = next((r for r in core_rows if r['learning_unit_ids']), None)
        cls.brief_row = next((r for r in core_rows if r['reference_brief']), None)
        if cls.unit_row is None or cls.brief_row is None:
            raise AssertionError('回归样本需要各一张关联学习单或速查资料的真实完整核心卡')
        cls.removed_rows = {r['id']: r for r in (cls.unit_row, cls.brief_row)}
        fixture = tempfile.TemporaryDirectory(prefix='cnkg-coverage-')
        cls.addClassCleanup(fixture.cleanup)
        cls.fixture_data = Path(fixture.name) / 'data'
        cls.fixture_data.mkdir()
        for name in ('topics.json', 'standards.json', 'outline.json', 'topic_tiers.json',
                     'questions.json', 'curriculum_versions.json', 'coverage_backlog.json'):
            shutil.copy2(DATA / name, cls.fixture_data / name)
        for name in ('teach', 'learning_units', 'projects', 'curriculum_comparisons'):
            shutil.copytree(DATA / name, cls.fixture_data / name)
        for tid in cls.removed_rows:
            (cls.fixture_data / 'teach' / f'{tid}.json').unlink()
        cls.missing_card_report = build_report(cls.fixture_data)

    def test_current_materials_are_complete_without_claiming_independent_evidence(self):
        report = self.report
        self.assertTrue(report['ok'], report['errors'])
        self.assertTrue(report['completion']['content_and_engineering_complete'])
        self.assertTrue(all(n == 0 for n in report['completion']['gaps'].values()),
                        report['completion']['gaps'])
        self.assertEqual(report['explanations']['core_with_full_card'],
                         report['explanations']['core_denominator'])
        self.assertEqual(report['registered_objectives']['core_with_materials'],
                         report['registered_objectives']['core_denominator'])
        self.assertEqual(report['activities']['pending_development'], 0)
        self.assertEqual(report['application_task_backlog']['open_count'], 0)
        self.assertEqual(set(report['completion']['deferred_by_user']),
                         {'independent_teacher_review', 'real_student_effect_validation'})

    def test_actual_gaps_are_reported_without_failing_structure(self):
        report = self.missing_card_report
        self.assertTrue(report['ok'], report['errors'])
        gaps = [r for r in report['topic_worklist'] if r['core_full_card_gap']]
        self.assertEqual(len(gaps), report['explanations']['core_full_card_gaps'])
        self.assertEqual({r['id'] for r in gaps}, set(self.removed_rows))
        missing_objectives = sum(r['registered_objective_count']
                                 for r in self.removed_rows.values())
        self.assertEqual(report['completion']['gaps']['core_full_cards'], len(self.removed_rows))
        self.assertEqual(report['completion']['gaps']['core_objective_materials'], missing_objectives)
        self.assertEqual(report['registered_objectives']['core_denominator'],
                         self.report['registered_objectives']['core_denominator'])
        self.assertEqual(report['registered_objectives']['core_with_materials'],
                         self.report['registered_objectives']['core_with_materials'] - missing_objectives)
        self.assertFalse(report['completion']['content_and_engineering_complete'])
        for tid in self.removed_rows:
            self.assertTrue((DATA / 'teach' / f'{tid}.json').exists())

    def test_unit_association_does_not_close_whole_topic_gap(self):
        row = next(r for r in self.missing_card_report['topic_worklist']
                   if r['id'] == self.unit_row['id'])
        self.assertEqual(row['learning_unit_ids'], self.unit_row['learning_unit_ids'])
        self.assertTrue(row['learning_unit_ids'])
        self.assertFalse(row['legacy_full_card'])
        self.assertTrue(row['core_full_card_gap'])
        self.assertEqual(row['full_objective_coverage'], 'not_complete')
        self.assertEqual(row['objectives_with_materials'], 0)

    def test_brief_reference_is_not_full_explanation(self):
        row = next(r for r in self.missing_card_report['topic_worklist']
                   if r['id'] == self.brief_row['id'])
        self.assertTrue(row['reference_brief'])
        self.assertFalse(row['legacy_full_card'])
        self.assertTrue(row['core_full_card_gap'])
        self.assertEqual(row['full_objective_coverage'], 'not_complete')
        self.assertEqual(row['objectives_with_materials'], 0)
        self.assertFalse(self.missing_card_report['explanations']['brief_reference_is_full_coverage'])

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
        self.assertTrue(all(row['comparison_status'] == 'complete'
                            for row in self.registry['clause_worklist']))
        self.assertEqual(self.report['curriculum_versions']['pending_clause_comparisons'], 0)
        self.assertEqual(self.report['curriculum_versions']['independent_review_pending'], len(self.standards))
        self.assertEqual(self.report['curriculum_versions']['status'], 'compared_pending_independent_review')

    def test_changed_baseline_excerpt_requires_new_comparison(self):
        standards = copy.deepcopy(self.standards)
        next(iter(standards.values()))['verbatim_text'] += '变更'
        self.assertTrue(any('基线原文' in e for e in check_version_registry(self.registry, standards)))

    def test_claiming_new_version_complete_without_full_text_is_rejected(self):
        registry = copy.deepcopy(self.registry)
        revision = next(v for v in registry['versions'] if v['role'] == 'discovered_revision')
        revision['comparison_status'] = 'complete'
        revision['full_text_status'] = 'pending_source'
        self.assertTrue(any('未取得全文' in e for e in check_version_registry(registry, self.standards)))

    def test_completed_comparison_requires_specific_evidence(self):
        registry = copy.deepcopy(self.registry)
        registry['clause_worklist'][0]['comparison']['summary'] = ''
        self.assertTrue(any('实际比较记录' in e for e in check_version_registry(registry, self.standards)))

    def test_ai_comparison_cannot_be_promoted_to_independent_teacher_review(self):
        registry = copy.deepcopy(self.registry)
        registry['clause_worklist'][0]['status'] = 'reviewed'
        registry['clause_worklist'][0]['review'] = {'reviewer_type': 'ai', 'reviewer_id': 'model', 'record_id': 'test'}
        self.assertTrue(any('独立审核记录' in e for e in check_version_registry(registry, self.standards)))


if __name__ == '__main__':
    unittest.main()
