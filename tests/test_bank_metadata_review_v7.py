"""Bind the V7 bank review to actual authored tasks and limited observations.

These regressions preserve reviewed versions, reject stale answers and prevent
unsubmitted work or changed mathematical structures from gaining evidence.
Accepting the author's own answer is only an answer-contract check, not an
independent mathematical audit or evidence of teaching effectiveness.
"""
from collections import Counter
from fractions import Fraction
import hashlib
import json
from math import isqrt
from pathlib import Path
import unittest

from cnkg.answers import grade_answer
from cnkg.task_families import (
    canonical_family, exposure_family, historical_question_ids, task_contract,
)


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'docs/reviews/v7-bank-metadata-review.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def graded_task(contract):
    task = {key: value for key, value in contract.items()
            if key != 'bound_answer_spec' and value is not None}
    task['answer_spec'] = contract.get('answer_spec') or contract['bound_answer_spec']
    return task


class BankMetadataReviewV7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review_bytes = REVIEW.read_bytes()
        cls.review = json.loads(cls.review_bytes)
        cls.rows = {row['id']: row for row in cls.review['tasks']}
        cls.sources, cls.questions, cls.topics = {}, {}, {}
        for folder in ('data/content', 'data/question_sources'):
            for path in sorted((ROOT / folder).glob('*.json')):
                source = json.loads(path.read_text())
                cls.sources[path.relative_to(ROOT).as_posix()] = source
                cls.topics.update(source.get('topics', {}))
                for question in source['questions']:
                    if question['id'] in cls.questions:
                        raise AssertionError('Duplicate source question: ' + question['id'])
                    cls.questions[question['id']] = question
        cls.bindings = json.loads((ROOT / 'data/skills/catalog-bindings.json').read_text())['tasks']
        cls.registry = json.loads((ROOT / 'data/skills/registry.json').read_text())
        cls.admissions = json.loads((ROOT / 'data/task_admissions.json').read_text())['tasks']
        cls.history = json.loads((ROOT / 'data/task_family_history.json').read_text())['aliases']

    def test_reviewed_sources_contracts_and_mapping_are_the_actual_current_versions(self):
        self.assertEqual(908, len(self.rows))
        self.assertEqual(len(self.rows), len(self.review['tasks']))
        self.assertEqual({'primary': 205, 'junior': 256, 'senior': 447},
                         dict(Counter(row['stage'] for row in self.rows.values())))
        for identity, row in self.rows.items():
            with self.subTest(task=identity):
                question = self.questions[identity]
                binding = self.bindings['bank:' + identity]
                self.assertIn(question, self.sources[row['source']]['questions'])
                self.assertEqual(digest(question), row['current_source_task_sha256'])
                self.assertEqual(digest(binding), row['current_source_bank_binding_sha256'])
                self.assertEqual(task_contract(question, binding), row['current_task_contract'])
                for version in ('baseline', 'current'):
                    self.assertEqual(digest(row[version + '_task_contract']),
                                     row[version + '_task_contract_sha256'])
                self.assertEqual('ai', row['reviewer_kind'])
                self.assertIs(False, row['independent_teacher_review'])
                self.assertTrue(row['mathematical_reason'].strip())
                self.assertTrue(row['observation_boundary'].strip())
                for field in ('family_id', 'representation', 'cognitive_demand'):
                    actual = question.get(field) or binding.get(field)
                    self.assertEqual(actual, row['current_metadata'][field])
                    self.assertNotIn(actual, (None, '', 'unknown', 'unclassified'))
                    self.assertFalse(actual.startswith('unclassified:'))
                goals = row['current_goal_mapping']
                self.assertEqual(question['tested_criterion_indices'],
                                 [goal['criterion_index'] for goal in goals])
                placement = self.registry['placements'][row['topic_id']]['criterion_skill_ids']
                for goal in goals:
                    self.assertEqual(placement[goal['criterion_index']], goal['skill_id'])
                    self.assertEqual(self.topics[row['topic_id']]['mastery_criteria'][goal['criterion_index']],
                                     goal['statement'])
                current = graded_task(row['current_task_contract'])
                self.assertEqual('correct', grade_answer(current, str(current['answer']))['status'])

    def test_new_limited_goals_append_without_replacing_old_criteria_or_claiming_refinement(self):
        preserved = self.review['topic_criteria_preservation']
        self.assertEqual(642, sum(row['appended_count'] for row in preserved))
        for row in preserved:
            with self.subTest(topic=row['topic_id']):
                actual = self.topics[row['topic_id']]['mastery_criteria']
                previous = row['baseline_criteria']
                self.assertEqual(previous, actual[:len(previous)])
                self.assertEqual(row['current_criteria'], actual)
        fine = [row for row in self.rows.values() if 'fine_goal_integration' in row]
        self.assertEqual(760, len(fine))
        skills = {row['fine_goal_integration']['skill_id'] for row in fine}
        self.assertEqual(642, len(skills))
        for skill in skills:
            with self.subTest(skill=skill):
                self.assertEqual([], self.registry['skills'][skill].get('refines_skill_ids', []))
        self.assertEqual(148, sum(row['mapping_resolution'] == 'existing_goal_at_observed_scope'
                                  for row in self.rows.values()))

    def test_changed_answer_contracts_reject_stale_answers_and_keep_old_version_grades(self):
        # Independent calculations from the stated quantities. No authored
        # verify expression or answer is used to construct these expectations.
        expected = {
            'm5a.6.4-q102': 6**2 - Fraction(6*3, 2),
            'm7b.8.3-q102': isqrt(9) * isqrt(4),
            'm7b.9.2.1-q101': 300 + 400,
            'm7b.9.2.1-q102': (abs(2-(-2)) + abs(4-1)) * 50,
            'm8b.22.2-q102': 10 + (8-3)*Fraction(24, 10) - 3,
            # A half turn negates both sine and cosine, preserving their ratio.
            'mh1.5.4.3-q102': Fraction(-2, -1),
            # The symmetric difference of x² equals its derivative for any h.
            'mh4.5.1.2-q1': Fraction((4+1)**2 - (4-1)**2, 2),
        }
        actual_changes = {identity for identity, row in self.rows.items()
                          if row['baseline_task_contract']['answer'] != row['current_task_contract']['answer']}
        self.assertEqual(set(expected), actual_changes)
        for identity, answer in expected.items():
            with self.subTest(task=identity):
                row = self.rows[identity]
                old = graded_task(row['baseline_task_contract'])
                current = graded_task(row['current_task_contract'])
                self.assertEqual(Fraction(answer), Fraction(current['answer']))
                self.assertEqual('correct', grade_answer(current, str(answer))['status'])
                self.assertEqual('wrong', grade_answer(current, str(old['answer']))['status'])
                self.assertEqual('correct', grade_answer(old, str(old['answer']))['status'])
                self.assertEqual('wrong', grade_answer(old, str(answer))['status'])
                self.assertIs(False, row['answer_change_regression']['history_regraded'])
        derivative = graded_task(self.rows['mh4.5.1.2-q1']['current_task_contract'])
        self.assertEqual('wrong', grade_answer(derivative, str(4**2))['status'])
        self.assertEqual('correct', grade_answer(derivative, '8.0')['status'])

    def test_numeric_results_do_not_acquire_unsubmitted_model_or_interval_goals(self):
        observed = {
            'm9a.26.3-q102': '能由二次函数解析式求与y轴交点的纵坐标',
            'mh4.5.3.1-q2': '能求已说明会变号的一次导函数的零点',
            'mh4.5.1.2-q1': '能求简单幂函数在指定点的导数值',
            'mh2.10.3.1-q101': '能由试验记录计算某结果的实际频率',
        }
        for identity, statement in observed.items():
            with self.subTest(task=identity):
                row = self.rows[identity]
                self.assertEqual([statement], [goal['statement'] for goal in row['current_goal_mapping']])
                self.assertEqual(1, len(self.questions[identity]['tested_criterion_indices']))
        # The derivative sign change is already stated and the response is a
        # single zero. The task still cannot be used to diagnose monotonicity.
        self.assertIs(False, self.questions['mh4.5.3.1-q2']['diagnosis_eligible'])
        self.assertIs(False, self.questions['mh4.5.3.1-q2']['for_diagnosis'])

    def test_history_only_contains_explicit_same_structure_decisions(self):
        allowed = {identity for identity, row in self.rows.items()
                   if row['history_exposure_review']['decision'] == 'allow_same_structure'}
        excluded = {identity for identity, row in self.rows.items()
                    if row['history_exposure_review']['decision'] == 'exclude_changed_structure'}
        self.assertEqual({
            'm5a.6.4-q102', 'm7b.8.3-q102', 'm7b.9.2.1-q101', 'm7b.9.2.1-q102',
            'm8a.15.2-q101', 'm8a.15.2-q102', 'm9a.28.1-q101',
            'mh1.5.4.3-q102', 'mh2.8.5.2-q101',
        }, excluded)
        self.assertEqual(624, len(allowed))
        self.assertEqual(allowed, {entry['task_id'] for entry in self.history})
        self.assertEqual(624, len(self.history))
        self.assertEqual(535, sum(not self.rows[identity]['changed_task_contract_fields']
                                 for identity in allowed))
        self.assertEqual(89, sum(bool(self.rows[identity]['changed_task_contract_fields'])
                                for identity in allowed))
        evidence_hash = hashlib.sha256(self.review_bytes).hexdigest()
        for entry in self.history:
            with self.subTest(task=entry['task_id']):
                row = self.rows[entry['task_id']]
                self.assertEqual('exposure_only', entry['use'])
                self.assertEqual(evidence_hash, entry['review_evidence_sha256'])
                for name in ('baseline_source_task_sha256', 'current_source_task_sha256',
                             'baseline_task_contract_sha256', 'current_task_contract_sha256'):
                    self.assertEqual(row[name], entry[name])
                self.assertEqual(entry['alias'], canonical_family(entry['alias']))
                self.assertEqual(canonical_family(entry['canonical']), exposure_family(entry['alias']))
                self.assertIn(entry['task_id'], historical_question_ids([entry['canonical']]))
        for identity in excluded:
            self.assertEqual('unclassified:' + identity, exposure_family('unclassified:' + identity))

    def test_quarantine_and_prior_use_limits_are_preserved(self):
        quarantined = self.review['quarantine_source_preservation']
        self.assertEqual(493, len(quarantined))
        self.assertFalse({row['id'] for row in quarantined} & set(self.rows))
        for row in quarantined:
            with self.subTest(quarantined_task=row['id']):
                self.assertEqual(row['baseline_source_task_sha256'], row['current_source_task_sha256'])
                self.assertEqual(digest(self.questions[row['id']]), row['current_source_task_sha256'])
                self.assertIs(False, self.admissions[row['id']]['policy']['practice_eligible'])
        for identity, row in self.rows.items():
            with self.subTest(task=identity):
                policy = self.admissions[identity]['policy']
                for name, previous in row['baseline_use_flags'].items():
                    self.assertEqual(previous, policy[name])
                for name, versions in row['source_eligibility_flags_preserved'].items():
                    self.assertEqual(versions['baseline'], versions['current'])
                    self.assertEqual(versions['current'], self.questions[identity].get(name))

    def test_conservative_group_reuse_does_not_declare_replaced_methods_equivalent(self):
        coarse = {identity: row['family_classification_review'] for identity, row in self.rows.items()
                  if 'family_classification_review' in row}
        self.assertEqual({'m7a.6.1.1-q101', 'mh2.8.1-q101', 'm9a.29.3-q101', 'm9a.30.3-q101'},
                         set(coarse))
        for identity, decision in coarse.items():
            with self.subTest(task=identity):
                self.assertIs(False, decision['global_equivalence_claim'])
                self.assertEqual(decision['selected_existing_family'],
                                 self.rows[identity]['current_metadata']['family_id'])
        for identity in ('m1a.1.1-q1', 'm8b.22.2-q102'):
            with self.subTest(replaced_method=identity):
                row = self.rows[identity]
                self.assertNotEqual(canonical_family(row['baseline_metadata']['family_id']),
                                    canonical_family(row['current_metadata']['family_id']))
        aliases = self.review['declared_family_equivalence_review']
        self.assertEqual(19, len(aliases))
        for entry in aliases:
            with self.subTest(alias=entry['alias']):
                self.assertFalse(entry['alias'].startswith('unclassified:'))
                self.assertEqual(canonical_family(entry['canonical']), canonical_family(entry['alias']))
                self.assertTrue(entry['reason'].strip())


if __name__ == '__main__':
    unittest.main()
