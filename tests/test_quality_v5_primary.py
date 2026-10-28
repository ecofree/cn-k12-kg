"""Semantic regressions for the 13 primary alignment topics, including counterexamples."""
from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import unittest
from xml.etree import ElementTree

from cnkg.answers import grade_answer
from cnkg.contracts import validate_resource
from tools.rebuild_data import build_data
from tools.teach_cards import check_card

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
TOPICS = 'm1a.1.1 m1a.2.9 m1a.2.10 m1a.4.1 m1a.4.2 m1a.5.1 m1a.5.2 m5b.1.1 m5b.2.1 m5b.3.1 m6b.1.1 m6a.1.1 m6a.1.2'.split()
NS = {'s': 'http://www.w3.org/2000/svg'}


class QualityV5PrimaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, _ = build_data(DATA)
        cls.bank = {q['id']: q for q in rows['questions.json']}
        cls.topics = {t['id']: t for t in rows['topics.json']}
        cls.bindings = json.loads((DATA / 'skills/catalog-bindings.json').read_text())['tasks']
        cls.cards = {t: json.loads((DATA / 'teach' / f'{t}.json').read_text()) for t in TOPICS}

    def task(self, qid):
        q = self.bank[qid]
        return {**q, 'answer_spec': q.get('answer_spec') or self.bindings['bank:' + qid]['answer_spec']}

    def test_off_target_generated_additions_are_replaced_without_new_identities(self):
        # Previously 17+3, 11-4, 17+9, 13+9, and 14-5 were under narrower goals.
        expected = {'m1a.2.10-q90': ('10', '20'), 'm1a.2.10-q91': ('6', '7'),
                    'm1a.5.1-q93': ('15', '26'), 'm1a.5.2-q91': ('16', '22'),
                    'm1a.5.2-q93': ('12', '9')}
        for qid, (correct, old) in expected.items():
            task = self.task(qid)
            self.assertEqual(grade_answer(task, correct)['status'], 'correct', qid)
            self.assertEqual(grade_answer(task, old)['status'], 'wrong', qid)
            self.assertFalse(task['diagnosis_eligible'])
        # A new result still cannot establish that the child actually used make-ten.
        self.assertIn('不能', self.bank['m1a.5.1-q93']['alignment_note'])

    def test_numeric_answers_do_not_claim_reading_writing_or_drawn_locations(self):
        expected = {
            'm1a.1.1-q1': (0, '能一个一个地数出 5 以内物体的个数'),
            'm1a.1.1-q2': (5, '能确定1至4各数在1至5数序中的后继数'),
            'm6a.1.1-q3': (3, '能求同列两个给定格点之间的单位间隔数'),
            'm6a.1.2-q101': (2, '能按已给比例尺把图上距离换算为实际距离'),
        }
        for qid, (index, statement) in expected.items():
            q = self.bank[qid]
            self.assertEqual(q['tested_criterion_indices'], [index])
            self.assertEqual(self.topics[q['topicId']]['mastery_criteria'][index], statement)
            self.assertEqual(q['target_alignment'], 'component_practice_only')
            self.assertFalse(q['diagnosis_eligible'])
        # The repaired first question now actually presents three discrete
        # marks; neither that result nor the successor observes speech/writing.
        self.assertEqual(self.bank['m1a.1.1-q1']['prompt'].count('●'), 3)
        for qid in ('m1a.1.1-q1', 'm1a.1.1-q2'):
            self.assertTrue({1, 3, 4}.isdisjoint(self.bank[qid]['tested_criterion_indices']))
        self.assertEqual(self.bank['m1a.1.1-q301']['tested_criterion_indices'], [0, 2])
        self.assertNotIn(3, self.bank['m1a.1.1-q301']['tested_criterion_indices'])
        self.assertNotIn(4, self.bank['m1a.1.1-q301']['tested_criterion_indices'])
        self.assertEqual(len(self.cards['m1a.1.1']['objective_coverage']), 6)

    def test_numerical_variants_share_the_actual_structure(self):
        units = json.loads((DATA / 'learning_units/ten_frame_place_value.json').read_text())
        tasks = {t['id']: t for t in units['tasks']}
        fid = tasks['ten_frame_place_value:practice-4']['family_id']
        for qid in ['m1a.5.1-q1', 'm1a.5.1-q90', 'm1a.5.1-q93', 'm1a.5.2-q1', 'm1a.5.2-q93']:
            self.assertEqual(self.bank[qid]['family_id'], fid)
        self.assertEqual(self.cards['m1a.5.1']['worked_example']['family_id'], fid)
        self.assertEqual(fid, self.cards['m1a.5.1']['objective_coverage'][1]['self_check']['family_id'])
        self.assertEqual(fid, tasks['ten_frame_place_value:delayed-2']['family_id'])
        self.assertEqual(tasks['ten_frame_place_value:practice-1']['family_id'], self.bank['m1a.1.1-q301']['family_id'])
        fraction = json.loads((DATA / 'learning_units/fraction_equivalence.json').read_text())
        remainder = next(q for q in fraction['tasks'] if q['id'] == 'fraction_equivalence:transfer-2')
        self.assertEqual(remainder['family_id'], self.bank['m5b.3.1-q102']['family_id'])
        self.assertEqual(self.bank['m5b.3.1-q90']['family_id'], self.bank['m5b.3.1-q93']['family_id'])

    def test_a_word_problem_mentioning_a_figure_does_not_claim_an_actually_shown_figure(self):
        expected = {'ten_frame_place_value': ['practice-2', 'practice-3', 'transfer-1'],
                    'position_reference_frames': ['practice-4'],
                    'fraction_equivalence': ['practice-1', 'practice-4', 'delayed-3']}
        for uid, suffixes in expected.items():
            unit = json.loads((DATA / 'learning_units' / f'{uid}.json').read_text())
            for suffix in suffixes:
                task = next(q for q in unit['tasks'] if q['id'] == f'{uid}:{suffix}')
                self.assertNotIn('diagram', task)
                self.assertIn(task['type'], ['choice', 'numeric'])
                self.assertEqual(task['representation'], 'verbal')

    def test_make_ten_conditions_exclude_impossible_split(self):
        self.assertIn('9+0', self.cards['m1a.5.1']['definition'])
        self.assertIn('6+2', self.cards['m1a.5.2']['definition'])
        # 0 cannot donate 1; 2 cannot donate the 4 needed by 6.
        self.assertLess(0, 10 - 9)
        self.assertLess(2, 10 - 6)
        svg = ElementTree.parse(ROOT / 'web/static/diagrams/primary-make-ten-v5.svg')
        circles = svg.findall('.//s:circle', NS)
        self.assertEqual(sum(c.get('fill') == '#317b9a' for c in circles), 9)
        self.assertEqual(sum(c.get('fill') == '#be6c27' for c in circles), 6)
        frame = ElementTree.parse(ROOT / 'web/static/diagrams/primary-ten-frame-blank-v5.svg')
        cells = [r for r in frame.findall('.//s:rect', NS) if r.get('width') == '54']
        self.assertEqual(len(cells), 10)
        self.assertEqual(len({r.get('y') for r in cells}), 2)

    def test_factor_questions_require_actual_factors_not_copying_a_given_product(self):
        expected = {'m5b.1.1-q90': '6', 'm5b.1.1-q91': '4',
                    'm5b.1.1-q92': '8', 'm5b.1.1-q93': '9'}
        self.assertEqual([d for d in range(1, 19) if 18 % d == 0 and 3 < d < 9], [6])
        self.assertEqual(len([d for d in range(1, 15) if 14 % d == 0]), 4)
        self.assertEqual(48 // 6, 8)
        self.assertEqual([n for n in range(9, 12) if n % 3 == 0], [9])
        for qid, answer in expected.items():
            self.assertEqual(grade_answer(self.task(qid), answer)['status'], 'correct')
            self.assertNotIn('所以', self.bank[qid]['prompt'])

    def test_fraction_numerator_is_defined_by_a_canonical_denominator_choice(self):
        q = self.task('m5b.3.1-q1')
        self.assertIn('最简分数', q['prompt'])
        self.assertEqual(Fraction(1, 2) + Fraction(1, 3), Fraction(5, 6))
        self.assertEqual(grade_answer(q, '5')['status'], 'correct')
        self.assertEqual(grade_answer(q, '10')['status'], 'wrong')
        # 10/12 is equal but is not the requested simplest numerator.
        self.assertEqual(Fraction(10, 12), Fraction(5, 6))

    def test_two_fractional_uses_explicitly_keep_the_same_original_whole(self):
        q = self.task('m5b.3.1-q101')
        self.assertGreaterEqual(q['prompt'].count('原来整瓶'), 2)
        self.assertEqual(grade_answer(q, '0.5')['status'], 'correct')
        self.assertEqual(grade_answer(q, '0.4375')['status'], 'wrong')
        sequential = Fraction(1, 4) + (1 - Fraction(1, 4)) * Fraction(1, 4)
        self.assertEqual(sequential, Fraction(7, 16))
        q = self.task('m5b.3.1-q102')
        self.assertGreaterEqual(q['prompt'].count('原全长'), 2)
        self.assertEqual(grade_answer(q, '0.4')['status'], 'correct')
        self.assertEqual(grade_answer(q, '0.48')['status'], 'wrong')
        self.assertEqual((1 - Fraction(2, 5)) * (1 - Fraction(1, 5)), Fraction(12, 25))

    def test_fraction_strip_refinement_keeps_actual_shaded_length_equal(self):
        svg = ElementTree.parse(ROOT / 'web/static/diagrams/primary-fraction-refinement-v5.svg')
        shaded = [r for r in svg.findall('.//s:rect', NS) if r.get('fill') == '#82bdd0']
        width = lambda y: sum(float(r.get('width')) for r in shaded if r.get('y') == str(y))
        self.assertEqual(width(65), 240)
        self.assertEqual(width(145), 240)
        self.assertEqual(Fraction(2, 3), Fraction(4, 6))
        self.assertNotEqual(Fraction(2, 3), Fraction(4, 5))
        # The simplification goal contains a reducible intermediate, not only an already simplest result.
        check = self.cards['m5b.3.1']['objective_coverage'][1]['self_check']
        self.assertIn('3/6', check['reference_answer'])
        self.assertEqual(Fraction(5, 6) - Fraction(1, 3), Fraction(1, 2))
        self.assertNotEqual(Fraction(3 - 2, 6 - 2), Fraction(3, 6))

    def test_bearing_figure_uses_north_as_angle_origin_and_independent_construction_stimulus(self):
        svg = ElementTree.parse(ROOT / 'web/static/diagrams/primary-bearing-read-v5.svg')
        ray = next(l for l in svg.findall('.//s:line', NS) if l.get('id') == 'bearing-ray')
        dx = float(ray.get('x2')) - float(ray.get('x1'))
        north = float(ray.get('y1')) - float(ray.get('y2'))
        self.assertAlmostEqual(math.degrees(math.atan2(dx, north)), 30, places=6)
        self.assertAlmostEqual(math.hypot(dx, north), 500 * 0.4, places=6)
        check = self.cards['m6a.1.2']['objective_coverage'][1]['self_check']
        self.assertIn('北偏西40', check['prompt'])
        self.assertIn('300', check['prompt'])
        self.assertIn('3厘米', check['reference_answer'])
        self.assertIn('blank', check['diagram']['src'])
        condition = self.task('m6a.1.2-q1')
        answer_text = next(c['text'] for c in condition['choices'] if c['id'] == condition['answer'])
        self.assertIn('观测点', answer_text)
        self.assertIn('距离', answer_text)

    def test_ratio_modelling_support_does_not_claim_narrower_card_objectives(self):
        for qid, index in [('m6b.1.1-q101', 2), ('m6b.1.1-q102', 2), ('m6b.1.1-q103', 3)]:
            q = self.bank[qid]
            self.assertEqual(q['tested_criterion_indices'], [index])
            self.assertTrue({0, 1}.isdisjoint(q['tested_criterion_indices']))
            self.assertFalse(q['diagnosis_eligible'])
            self.assertEqual(q['target_alignment'], 'component_practice_only')
        self.assertEqual(self.topics['m6b.1.1']['mastery_criteria'][2:], [
            '能将给定总量按已给的两项或多项比确定某部分量',
            '能统一长度单位后按给定比例尺求图上距离'])
        check = self.cards['m6b.1.1']['objective_coverage'][0]['self_check']
        # The current reference also reduces 18/24 to 3/4. Its actual method
        # must remain exposed, alongside the quotient interpretation.
        self.assertEqual(set(check['reference_family_ids']), {'ratio.value-direct', 'fraction.reduce-direct'})
        self.assertEqual(check['family_id'], 'ratio.reduce-direct')

    def test_all_cards_and_actual_source_tasks_satisfy_contracts_without_inflated_goals(self):
        tasks = [q for q in self.bank.values() if q['topicId'] in TOPICS and not q['id'].endswith('-q9')]
        self.assertEqual(len(tasks), 69)
        self.assertEqual(sum(len(c['objective_coverage']) for c in self.cards.values()), 36)
        for t, card in self.cards.items():
            self.assertEqual(validate_resource('teaching-card', card), [], t)
            self.assertEqual(check_card(card, self.topics, self.bank), [], t)
            for entry in card['objective_coverage']:
                self.assertTrue(entry['observation_note'])
                self.assertTrue(entry['difficulty_support']['discriminating_prompt'])
                self.assertNotEqual(entry['self_check']['representation'], 'unknown')
        for q in tasks:
            self.assertEqual(validate_resource('question', q), [], q['id'])
            self.assertFalse(q['diagnosis_eligible'])
            self.assertEqual(grade_answer(self.task(q['id']), q['answer'])['status'], 'correct', q['id'])


if __name__ == '__main__':
    unittest.main()
