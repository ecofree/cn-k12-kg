"""综合实践：真目录引用、目标评价闭环、学生版和审核版本隔离。"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from cnkg.projects import (load_projects, check_project, project_content_sha256,
                           public_project, project_summary, ProjectError)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


class ProjectsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projects = load_projects()
        cls.outline = json.loads((DATA / 'outline.json').read_text())
        cls.standards = json.loads((DATA / 'standards.json').read_text())

    def sample(self):
        return copy.deepcopy(self.projects['water_use_inquiry'])

    def check(self, project):
        return check_project(project, self.outline, self.standards)

    def test_real_resources_have_actual_outline_and_evaluation_links(self):
        self.assertGreaterEqual(len(self.projects), 3)
        for project in self.projects.values():
            self.assertEqual(self.check(project), [])
            self.assertEqual(project['teacher_review']['status'], 'pending')
            self.assertEqual(project['review_status'], 'draft')

    def test_unknown_activity_or_wrong_stage_cannot_fake_coverage(self):
        project = self.sample()
        project['outline_refs'][0]['title'] = '目录中不存在的活动'
        self.assertTrue(self.check(project))
        project = self.sample()
        project['stage'] = 'senior'
        self.assertTrue(self.check(project))

    def test_each_goal_requires_both_task_and_rubric_evidence(self):
        for key in ('steps', 'rubric'):
            project = self.sample()
            missing = project['objectives'][0]['id']
            for row in project[key]:
                row['objective_ids'] = [oid for oid in row['objective_ids'] if oid != missing]
            self.assertTrue(any(missing in e for e in self.check(project)))

    def test_student_view_keeps_criteria_and_hides_teacher_answers(self):
        project = self.sample()
        project['answer'] = 'unpublished extra field'
        shown = public_project(project)
        self.assertNotIn('teacher_notes', shown)
        self.assertNotIn('answer', shown)
        for row in shown['rubric']:
            self.assertEqual(set(row), {'criterion', 'points', 'objective_ids'})
        shown['steps'][0]['instructions'] = 'client edit'
        self.assertNotEqual(shown['steps'][0], project['steps'][0])
        self.assertEqual(project_summary(project)['objective_count'], len(project['objectives']))

    def test_ai_cannot_approve_as_teacher(self):
        project = self.sample()
        project['teacher_review'] = {'status': 'approved', 'reviewer_type': 'ai',
                                      'reviewer_id': 'model', 'record_id': 'test',
                                      'content_sha256': project_content_sha256(project)}
        self.assertTrue(self.check(project))

    def test_teacher_approval_binds_current_content(self):
        project = self.sample()
        project['teacher_review'] = {'status': 'approved', 'reviewer_type': 'teacher',
                                      'reviewer_id': 'test-reviewer', 'record_id': 'test-only',
                                      'content_sha256': project_content_sha256(project)}
        project['review_status'] = 'reviewed'
        self.assertEqual(self.check(project), [])
        project['steps'][0]['instructions'] += '变更条件。'
        self.assertTrue(any('哈希' in e for e in self.check(project)))

    def test_status_alone_is_not_approval(self):
        project = self.sample()
        project['review_status'] = 'reviewed'
        self.assertTrue(self.check(project))

    def test_invalid_points_and_malformed_fields_are_errors(self):
        for value in (True, float('inf'), 0, -2, '2'):
            project = self.sample()
            project['rubric'][0]['points'] = value
            self.assertTrue(self.check(project))
        for key, value in (('standard_ids', [{}]), ('objectives', ['goal']),
                           ('outline_refs', ['missing']), ('rubric', [None])):
            project = self.sample()
            project[key] = value
            self.assertTrue(self.check(project))
        self.assertTrue(check_project([]))

    def test_duplicate_ids_across_files_fail_load(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / 'projects').mkdir()
            (data / 'outline.json').write_text(json.dumps(self.outline))
            (data / 'standards.json').write_text(json.dumps(self.standards))
            for name in ('a', 'b'):
                (data / 'projects' / f'{name}.json').write_text(json.dumps(self.sample()))
            with self.assertRaises(ProjectError):
                load_projects(data)

    def test_hash_changes_for_content_but_not_review_metadata(self):
        project = self.sample()
        before = project_content_sha256(project)
        project['teacher_review']['note'] = 'metadata'
        self.assertEqual(before, project_content_sha256(project))
        project['rubric'][0]['example'] += '新条件'
        self.assertNotEqual(before, project_content_sha256(project))


if __name__ == '__main__':
    unittest.main()
