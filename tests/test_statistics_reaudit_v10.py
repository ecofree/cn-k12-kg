"""Bind the bounded fresh review and independently recalculate its numerical cases."""
from __future__ import annotations

from fractions import Fraction as F
import json
from pathlib import Path
import unittest

from cnkg.bank_review import digest
from cnkg.answers import grade_answer

ROOT = Path(__file__).resolve().parents[1]


class StatisticsReauditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ROOT / 'docs/reviews/v10-statistics-bank-reaudit.json').read_text())
        cls.catalog = json.loads((ROOT / 'data/catalog.json').read_text())
        cls.bindings = json.loads((ROOT / 'data/skills/catalog-bindings.json').read_text())['tasks']
        cls.tasks = {row['id']: row for row in cls.report['tasks']}

    def test_actual_reviewed_sources_goals_contracts_and_limited_scope_are_bound(self):
        self.assertEqual(len(self.tasks), 34)
        self.assertEqual(self.report['bank_questions_not_freshly_reviewed_in_this_batch'], 3550)
        for row in self.report['tasks']:
            with self.subTest(question=row['id']):
                payload = json.loads((ROOT / row['source']).read_text())
                qs = payload.get('questions', []) if isinstance(payload, dict) else payload
                question = next(q for q in qs if q['id'] == row['id'])
                task = self.catalog['tasks'][row['id']]
                self.assertEqual(row['source_task_sha256'], digest(question))
                self.assertEqual(row['compiled_task_sha256'], digest(task))
                self.assertEqual(row['binding_sha256'], digest(self.bindings['bank:' + row['id']]))
                self.assertEqual(row['skill_sha256'], {s: digest(self.catalog['skills'][s]) for s in task['skill_ids']})
                self.assertEqual(row['expected_answer'], question['answer'])
                self.assertEqual(task['availability'], 'active')
                self.assertFalse(row['independent_teacher_review'])
                self.assertFalse(row['student_validation'])
                self.assertGreater(len(row['mathematical_reason']), 25)
                self.assertTrue(row['observation_boundary'].strip())

    def test_fraction_recalculations_of_actual_numeric_cases(self):
        expected = {
            'mh5.8.1.2-q1': 1 if abs(F('-0.7')) > abs(F('0.4')) else 2,
            'mh5.8.1.2-q101': F('0.9') ** 2,
            'mh5.8.1.2-q102': F('0.5') ** 2 * 100,
            'mh5.8.1.2-q201': max(abs(F('-0.92')), abs(F('0.46'))),
            'mh5.8.2.1-q101': 2 * 5 + 3,
            'mh5.8.2.1-q102': F('0.8') * 10,
            'mh5.8.2.1-q201': 12 - (4 + 2 * 3),
            'mh5.8.2.1-q202': 11 - (10 + F('0.5') * 8),
            'mh5.8.2.2-q101': 5 - 2 * 3,
            'mh5.8.2.2-q102': 9 + 2 * (6 - 4),
            'mh5.8.2.2-q201': 9 - F('1.5') * 4,
            'mh5.8.2.2-q202': sum(e * e for e in (1, -2, 1)),
            'mh5.8.3.1-q1': 2 * 2,
            'mh5.8.3.2-q101': 40 * 30 - 10 * 20,
            'mh5.8.3.2-q102': F(100 * (30 * 30 - 20 * 20) ** 2, 50 ** 4),
            'mh5.8.3.2-q201': sum(F((obs - 15) ** 2, 15) for obs in (20, 10, 10, 20)),
        }
        for identity, answer in expected.items():
            with self.subTest(question=identity):
                self.assertEqual(F(self.tasks[identity]['expected_answer']), answer)

    def test_zero_correlation_counterexample_has_nonzero_denominator(self):
        points = [(-2, 4), (0, 0), (2, 4)]
        xmean = sum(F(x) for x, y in points) / 3
        ymean = sum(F(y) for x, y in points) / 3
        self.assertGreater(sum((x - xmean) ** 2 for x, y in points), 0)
        self.assertGreater(sum((y - ymean) ** 2 for x, y in points), 0)
        self.assertEqual(sum((x - xmean) * (y - ymean) for x, y in points), 0)
        self.assertTrue(all(y == x * x for x, y in points))
        self.assertEqual(self.tasks['mh5.8.1.2-q204']['expected_answer'], '0')

    def test_choice_identity_is_not_an_option_position(self):
        for row in self.report['tasks']:
            task = self.catalog['tasks'][row['id']]
            if task['type'] != 'choice':
                continue
            self.assertIn(row['expected_answer'], [option['id'] for option in task['choices']])
            # The discriminating regression is the exact stable-ID contract;
            # option order and presentation letters are not an answer key.
            self.assertEqual(task['answer'], row['expected_answer'])
            for option in task['choices']:
                expected = 'correct' if option['id'] == row['expected_answer'] else 'wrong'
                self.assertEqual(grade_answer(task, option['id'])['status'], expected)


if __name__ == '__main__':
    unittest.main()
