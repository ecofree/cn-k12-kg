"""Independent counterexamples, not just agreement with authored verify strings."""
import copy
import hashlib
from fractions import Fraction
import json
import math
from pathlib import Path
import unittest

from cnkg.answers import grade_answer
from cnkg.catalog import load_catalog
from cnkg.choice_order import stable_order
from cnkg.contracts import validate_resource
from cnkg.verify import check_question
from tools.balance_options import ChoiceOrderError
from tools.rebuild_data import build_data

ROOT = Path(__file__).resolve().parents[1]

def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class ContentCounterexampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = {q['id']: q for q in read('data/questions.json')}
        cls.catalog = load_catalog()

    def test_full_bank_identity_set_is_preserved_despite_quarantine(self):
        audit = read('docs/reviews/v6-corrections-ai-review.json')['identity_preservation']
        additions = set(audit['added_question_ids'])
        self.assertEqual(len(additions), 6)
        self.assertFalse(audit['deleted_question_ids'])
        restoration = read('docs/reviews/v6-bank-restoration-review.json')
        restored_additions = {row['id'] for row in restoration['tasks'] if row['new_task']}
        self.assertEqual(restored_additions, {f'm8a.18.{section}-q{number}'
                                           for section in (2, 3) for number in (301, 302, 303)})
        self.assertFalse(additions & restored_additions)
        self.assertTrue((additions | restored_additions) <= set(self.questions))
        baseline_ids = set(self.questions) - additions - restored_additions
        self.assertEqual(len(baseline_ids), audit['baseline_question_count'])
        digest = hashlib.sha256('\n'.join(sorted(baseline_ids)).encode()).hexdigest()
        self.assertEqual(digest, audit['baseline_question_id_sha256'])

    def test_conditional_dice_is_computed_from_enumerated_outcomes(self):
        possible = set(range(1, 7))
        given = {n for n in possible if n > 2}
        favourable = {n for n in given if n < 5}
        self.assertEqual(given, {3, 4, 5, 6})
        expected = Fraction(len(favourable), len(given))
        task = self.catalog.task('mh5.7.1.1-q91')
        self.assertEqual(Fraction(task['answer']), expected)
        for response in ['0.5', '1/2', '2/4']:
            self.assertEqual(grade_answer(task, response)['status'], 'correct')
        self.assertEqual(grade_answer(task, '1/4')['status'], 'wrong')

    def test_grouping_endpoint_requires_six_half_open_bins(self):
        qid = 'm8b.24.4-q101'
        q = self.questions[qid]
        intervals = [(42 + 9*i, 42 + 9*(i+1)) for i in range(6)]
        self.assertFalse(any(lo <= 87 < hi for lo, hi in intervals[:5]))
        self.assertEqual(sum(lo <= 87 < hi for lo, hi in intervals), 1)
        self.assertEqual(sum(lo <= 42 < hi for lo, hi in intervals), 1)
        for point in range(42, 88):
            self.assertEqual(sum(lo <= point < hi for lo, hi in intervals), 1)
        task = self.catalog.task(qid)
        self.assertEqual(grade_answer(task, '6')['status'], 'correct')
        self.assertEqual(grade_answer(task, '5')['status'], 'wrong')
        self.assertIn('所有组都采用左闭右开', q['prompt'])
        self.assertIn('严格大于最大值', ' '.join(read('data/teach/m8b.24.4.json')['conditions']))

    def test_ferris_wheel_uses_vertical_displacement_not_sine(self):
        # Independent coordinate rotation of the initial position (0,-R).
        angle = math.radians(30)
        x0, y0 = 0, -20
        y_after = x0 * math.sin(angle) + y0 * math.cos(angle)
        rise = y_after - y0
        self.assertAlmostEqual(rise, 20 - 10*math.sqrt(3))
        task = self.catalog.task('mh1.5.2.1-q101')
        self.assertEqual(grade_answer(task, f'{rise:.2f}')['status'], 'correct')
        self.assertEqual(grade_answer(task, '10')['status'], 'wrong')
        self.assertEqual(task['answer_spec']['decimal_places'], 2)
        self.assertIn('保留两位小数', task['prompt'])
        self.assertAlmostEqual((-20*math.cos(0)) - y0, 0)
        self.assertAlmostEqual((-20*math.cos(math.pi)) - y0, 40)

    def test_sequential_story_uses_current_state(self):
        stock = list(range(10))
        del stock[:2]
        self.assertEqual(len(stock), 8)
        stock.append('new')
        task = self.catalog.task('m1a.5.5-q95')
        self.assertEqual(grade_answer(task, str(len(stock)))['status'], 'correct')
        self.assertEqual(grade_answer(task, '11')['status'], 'wrong')
        self.assertEqual(task['skill_ids'], [])

    def test_repaired_and_new_items_never_confirm_whole_topic(self):
        ids = ['m1a.5.5-q95', 'mh5.7.1.1-q91', 'm8b.24.4-q101', 'mh1.5.2.1-q101',
               'm1a.1.6-q401', 'm1a.1.6-q402', *[f'm1a.5.5-q{i}' for i in range(401, 405)]]
        for qid in ids:
            with self.subTest(qid=qid):
                task = self.catalog.task(qid)
                self.assertIn('practice', task['purpose'])
                self.assertNotIn('check', task['purpose'])
                self.assertEqual(grade_answer(task, task['answer'])['status'], 'correct')
                q = self.questions[qid]
                if q['type'] == 'numeric':
                    self.assertEqual(check_question(q)['status'], 'pass')

    def test_out_of_grade_tasks_remain_archived_and_are_not_assigned(self):
        for i in range(90, 94):
            task = self.catalog.task(f'm1a.5.5-q{i}')
            self.assertEqual(task['purpose'], [])
            self.assertNotEqual(task['availability'], 'active')

    def test_named_inverse_addition_page_teaches_addition_and_preserves_contrast(self):
        card = read('data/teach/m1a.5.5.json')
        self.assertEqual(card['worked_example']['answer'], '14')
        self.assertIn('送出', card['worked_example']['prompt'])
        self.assertEqual(card['objective_coverage'][1]['title'], '能用减法求出原来的数量')
        self.assertIn('对照', card['objective_coverage'][1]['explanation'])
        self.assertIn('相加', card['objective_coverage'][2]['title'])
        registry = read('data/skills/registry.json')
        ids = registry['placements']['m1a.5.5']['criterion_skill_ids']
        self.assertEqual(ids[:2], ['sk_6823add8ae974742ad3c', 'sk_07cd5f98e7c94c3a8eaa'])
        self.assertEqual(len(ids), 3)
        for taskid in ['m1a.5.5-q401', 'm1a.5.5-q403', 'm1a.5.5-q404']:
            self.assertIn(ids[2], self.catalog.task(taskid)['skill_ids'])
        first, second = (self.catalog.task(i) for i in ['m1a.5.5-q401', 'm1a.5.5-q404'])
        self.assertEqual(first['family_id'], second['family_id'])

    def test_repaired_cards_obey_schema_and_target_placement(self):
        for tid in ['m1a.1.6', 'm1a.5.5', 'm8b.24.4', 'mh5.7.1.1']:
            card = read(f'data/teach/{tid}.json')
            self.assertEqual(validate_resource('teaching-card', card), [])
            placement = self.catalog.placement(tid)['criterion_skill_ids']
            for row in card['objective_coverage']:
                index = row['criterion_index']
                self.assertEqual(self.catalog.self_check(tid, index)['skill_ids'], [placement[index]])


class StableChoiceOrderTests(unittest.TestCase):
    def question(self, qid, answer='b'):
        return {'id':qid,'type':'choice','prompt':'选择对应数值','answer':answer,
                'choices':[{'id':'a','text':'1'}, {'id':'b','text':'2'}, {'id':'c','text':'3'}]}

    def test_population_edits_do_not_move_other_questions(self):
        rows = [self.question('old'), self.question('new')]
        snapshot = {'schema_version':1, 'orders':{'old':['c','b','a']}}
        expected = {q['id']:q for q in stable_order(rows, snapshot)}
        enlarged = stable_order([self.question(f'extra{i}') for i in range(100)] + rows, snapshot)
        self.assertEqual({q['id']:q for q in enlarged if q['id'] in expected}, expected)
        self.assertEqual(stable_order([rows[1]], snapshot)[0], expected['new'])
        self.assertEqual(stable_order(list(reversed(rows)), snapshot)[0], expected['new'])

    def test_idempotent_copy_and_answer_content_preservation(self):
        q = self.question('q')
        q['quality_review'] = {'status':'pending', 'content_sha256':'original'}
        original = copy.deepcopy(q)
        snapshot = {'schema_version':1, 'orders':{}}
        once = stable_order([q],snapshot)
        self.assertEqual(stable_order(once,snapshot), once)
        self.assertEqual(once[0]['answer'], q['answer'])
        self.assertEqual(once[0]['quality_review'], q['quality_review'])
        once[0]['choices'][0]['text'] = 'not in source'
        self.assertEqual(q, original)

    def test_frozen_option_identity_mismatch_is_not_silently_reordered(self):
        snapshot = {'schema_version':1,'orders':{'q':['a','b','c']}}
        q=self.question('q'); q['choices'].pop()
        with self.assertRaises(ChoiceOrderError):stable_order([q],snapshot)
        with self.assertRaises(ChoiceOrderError):stable_order([self.question('q')]*2,snapshot)
        with self.assertRaises(ChoiceOrderError):stable_order([self.question('q')],{'schema_version':1,'orders':{'q':['a','a']}})

    def test_published_legacy_orders_are_preserved_by_real_source_build(self):
        frozen=read('data/choice_orders.json')['orders']
        built,_=build_data(ROOT/'data')
        for q in built['questions.json']:
            if q['id'] in frozen:
                self.assertEqual([o['id'] for o in q['choices']], frozen[q['id']], q['id'])


if __name__ == '__main__':
    unittest.main()
