"""Semantic counterexamples for the V5 statistics learning chain.

Check the mathematical boundary and the actual diagram geometry, not only
whether an alignment field exists. These are content regressions, not evidence
of teacher review or classroom effectiveness.
"""
from __future__ import annotations

from fractions import Fraction
import json
from pathlib import Path
import unittest
from xml.etree import ElementTree

from cnkg.answers import grade_answer
from cnkg.contracts import validate_resource
from tools.rebuild_data import build_data
from tools.teach_cards import check_card


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
TOPICS = ('m3b.5.1', 'm4a.6.1', 'm4b.6.1', 'm5b.5.1', 'm6b.2.1',
          'm7b.12.1.1', 'm7b.12.1.2', 'm8b.24.1', 'm8b.24.2',
          'mh5.8.1.1', 'mh5.8.1.2', 'mh5.8.2.1', 'mh5.8.2.2')
NS = {'s': 'http://www.w3.org/2000/svg'}

# These tasks still cannot observe the two original, broader topic criteria.
# V7 gives their actual component results explicit, separately reviewed goals.
COMPONENT_GOALS = {
    'm7b.12.1.1-q2': (2, 'statistics.census-error-boundary', '能识别全面调查仍可能存在非抽样误差'),
    'mh5.8.1.2-q203': (5, 'statistics.correlation-sign-and-domain', '能判断一个样本变量没有变异时相关系数未定义'),
    'm5b.5.1-q101': (3, 'statistics.mean-from-values', '能对已给的一组数值求算术平均数'),
    'm8b.24.2-q1': (2, 'statistics.mean-from-values', '能计算已给数据的算术平均数'),
    'mh5.8.1.1-q101': (2, 'statistics.square-given-correlation', '能计算已给样本相关系数的平方及其百分制数值'),
    'mh5.8.1.1-q102': (2, 'statistics.square-given-correlation', '能计算已给样本相关系数的平方及其百分制数值'),
    'mh5.8.1.1-q202': (3, 'statistics.scatter-nonlinearity', '能由实际散点图识别明显的弯曲趋势'),
    'mh5.8.1.1-q203': (4, 'statistics.observational-causality', '能判断仅有观察性相关证据不足以推出因果结论'),
    'mh5.8.1.2-q101': (3, 'statistics.square-given-correlation', '能计算已给样本相关系数的平方'),
    'mh5.8.1.2-q102': (4, 'statistics.r-squared-interpretation', '能在给定含截距一元最小二乘条件下由样本相关系数求解释变异比例'),
    'mh5.8.2.1-q101': (4, 'statistics.prediction-substitution', '能代入已给回归预测式求样本范围内指定输入的预测值'),
    'mh5.8.2.2-q102': (3, 'statistics.ols-centre-and-prediction', '能由已给回归斜率和样本中心计算范围内的预测值'),
}


def svg(name):
    return ElementTree.parse(ROOT / 'web/static/diagrams' / name)


def mean(values):
    return sum(map(Fraction, values)) / len(values)


def variance(values):
    centre = mean(values)
    return sum((Fraction(x) - centre) ** 2 for x in values) / len(values)


class StatisticsAlignmentV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, _ = build_data(DATA)
        cls.bank = {q['id']: q for q in rows['questions.json']}
        cls.topics = {t['id']: t for t in rows['topics.json']}
        cls.bindings = json.loads((DATA / 'skills/catalog-bindings.json').read_text())['tasks']
        cls.cards = {tid: json.loads((DATA / f'teach/{tid}.json').read_text()) for tid in TOPICS}
        cls.catalog = json.loads((DATA / 'catalog.json').read_text())
        review = json.loads((ROOT / 'docs/reviews/v7-observable-goal-review.json').read_text())
        cls.component_reviews = {qid: row for row in review['goals'] for qid in row['bank_task_ids']}

    def task(self, qid):
        q = self.bank[qid]
        return {**q, 'answer_spec': q.get('answer_spec') or self.bindings['bank:' + qid]['answer_spec']}

    def assert_only_reviewed_component(self, qid):
        index, proposal, statement = COMPONENT_GOALS[qid]
        q = self.bank[qid]
        review = self.component_reviews[qid]
        self.assertEqual((review['criterion_index'], review['proposal_key'], review['statement']),
                         (index, proposal, statement), qid)
        self.assertEqual(review['topic_id'], q['topicId'], qid)
        self.assertEqual(q['tested_criterion_indices'], [index], qid)
        self.assertEqual(self.topics[q['topicId']]['mastery_criteria'][index], statement, qid)
        registered = self.catalog['tasks'][qid]
        self.assertEqual(registered['skill_ids'], [review['skill']['id']], qid)
        broad_ids = self.catalog['placements'][q['topicId']]['criterion_skill_ids'][:2]
        self.assertTrue(set(broad_ids).isdisjoint(registered['skill_ids']), qid)
        self.assertEqual(review['skill']['refines_skill_ids'], [], qid)
        self.assertFalse(registered['policy']['may_confirm_mastery'], qid)
        self.assertFalse(registered['policy']['may_judge_weak'], qid)

    def test_bar_self_checks_use_their_own_actual_scale_and_missing_bar(self):
        card = self.cards['m4a.6.1']
        checks = [r['self_check'] for r in card['objective_coverage']]
        self.assertEqual(len({q['diagram']['src'] for q in checks}), 1)
        self.assertNotEqual(checks[0]['diagram']['src'], card['diagram']['src'])
        drawing = svg('statistics-v5-bar-self-check.svg')
        bars = drawing.findall('.//s:rect[@id]', NS)
        self.assertEqual(len(bars), 2)  # C must remain an actual construction task.
        for rect, expected in zip(bars, [8, 12]):
            self.assertAlmostEqual(float(rect.attrib['height']) * 14 / 260, expected, places=4)
            self.assertAlmostEqual(float(rect.attrib['y']) + float(rect.attrib['height']), 345)
        self.assertIn('每格代表2人', checks[0]['reference_answer'])
        self.assertIn('5格', checks[1]['reference_answer'])

    def test_line_diagram_has_the_actual_four_day_data_and_equal_intervals(self):
        drawing = svg('statistics-v5-line-self-check.svg')
        points = drawing.findall('.//s:circle[@id]', NS)
        actual = [(float(p.attrib['cx']), (345-float(p.attrib['cy']))*24/260) for p in points]
        self.assertEqual(len(actual), 4)
        for (_, value), expected in zip(actual, [18, 20, 19, 23]):
            self.assertAlmostEqual(value, expected, places=4)
        intervals = [actual[i+1][0] - actual[i][0] for i in range(3)]
        self.assertLess(max(intervals) - min(intervals), 0.002)
        self.assertEqual([b-a for a,b in zip([18,20,19], [20,19,23])], [2,-1,4])
        checks = {row['criterion_index']: row for row in self.cards['m5b.5.1']['objective_coverage']}
        self.assertEqual(checks[0]['title'], '能读懂折线统计图的变化趋势')
        self.assertEqual(checks[1]['title'], '能在折线统计图上比较增减幅度')
        for row in (checks[0], checks[1]):
            self.assertTrue(row['self_check']['diagram']['src'].endswith('line-self-check.svg'))
        self.assertIn('尺度', checks[1]['self_check']['reference_answer'])
        # The appended arithmetic-mean task is verbal, not another graph-reading observation.
        self.assert_only_reviewed_component('m5b.5.1-q101')
        self.assertNotIn('diagram', checks[3]['self_check'])
        self.assertEqual(checks[3]['self_check']['family_id'], 'statistics.mean-from-values')

    def test_sports_bar_total_requires_exclusive_and_exhaustive_categories(self):
        drawing = svg('statistics-v5-sports-bars.svg')
        bars = drawing.findall('.//s:rect[@id]', NS)
        for rect, expected in zip(bars, [12, 9, 6, 13]):
            self.assertAlmostEqual(float(rect.attrib['height']) * 14 / 260, expected, places=4)
        self.assertEqual(len(bars), 4)
        q = self.task('m4a.6.1-q102')
        self.assertIn('全体', q['prompt'])
        self.assertIn('只选一项', q['prompt'])
        self.assertEqual(grade_answer(q, str(sum([12,9,6,13])))['status'], 'correct')
        self.assertIn('多选', q['explanation'])

    def test_full_coverage_does_not_promise_error_free_measurement(self):
        q = self.bank['m7b.12.1.1-q2']
        answer = next(c['text'] for c in q['choices'] if c['id'] == q['answer'])
        self.assertIn('仍须', answer)
        self.assertIn('测量', answer)
        self.assert_only_reviewed_component(q['id'])
        self.assertFalse(q['diagnosis_eligible'])
        check = self.cards['m7b.12.1.2']['objective_coverage'][1]['self_check']
        self.assertIn('40人', check['reference_answer'])
        self.assertIn('不能直接保证', check['reference_answer'])

    def test_zero_record_and_missing_record_have_different_mean_denominators(self):
        self.assertEqual(mean([0,6,12]), 6)
        self.assertNotEqual(mean([6,12]), 6)
        self.assertEqual(mean([3,4,5,8]), 5)
        self.assertEqual(mean([3,4,5,28]), 10)
        unit = json.loads((DATA / 'learning_units/data_reading_and_mean.json').read_text())
        delayed = next(q for q in unit['tasks'] if q['id'].endswith(':delayed-2'))
        self.assertEqual(delayed['family_id'], self.bank['m4b.6.1-q1']['family_id'])
        self.assertEqual(delayed['family_id'], 'statistics.mean-from-values')

    def test_variance_uses_the_declared_n_convention_and_not_signed_cancellation(self):
        self.assertEqual(variance([2,4,6]), Fraction(8,3))
        self.assertNotEqual(sum(x-4 for x in [2,4,6]), variance([2,4,6]))
        q = self.task('m8b.24.2-q201')
        self.assertEqual(grade_answer(q, '8/3')['status'], 'correct')
        self.assertEqual(grade_answer(q, '4')['status'], 'wrong')  # n-1 gives a different statistic.
        self.assertEqual(variance([1,5,9])-variance([3,5,7]), 8)
        self.assertEqual(mean([4,5,6]), mean([1,5,9]))
        self.assertLess(variance([4,5,6]), variance([1,5,9]))

    def test_zero_correlation_has_a_nonzero_denominator_and_nonlinear_relation(self):
        xs, ys = [Fraction(-1), Fraction(0), Fraction(1)], [Fraction(1), Fraction(0), Fraction(1)]
        cross = sum((x-mean(xs))*(y-mean(ys)) for x,y in zip(xs,ys))
        self.assertEqual(cross, 0)
        self.assertGreater(variance(xs)*variance(ys), 0)
        self.assertTrue(all(y == x*x for x,y in zip(xs,ys)))
        self.assertEqual(variance([2,2,2]), 0)
        q = self.bank['mh5.8.1.2-q203']
        self.assertIn('未定义', next(c['text'] for c in q['choices'] if c['id'] == q['answer']))
        self.assert_only_reviewed_component(q['id'])
        self.assertFalse(self.bank['mh5.8.1.2-q1']['diagnosis_eligible'])
        self.assertNotIn('诊断资格', self.bank['mh5.8.1.2-q1']['explanation'])

    def test_containing_an_intercept_is_essential_to_the_ols_centre_property(self):
        xs, ys = [Fraction(1),Fraction(2)], [Fraction(2),Fraction(1)]
        through_origin_slope = sum(x*y for x,y in zip(xs,ys)) / sum(x*x for x in xs)
        self.assertEqual(through_origin_slope, Fraction(4,5))
        self.assertNotEqual(through_origin_slope*mean(xs), mean(ys))
        for qid in ['mh5.8.2.2-q2','mh5.8.2.2-q3','mh5.8.2.2-q101','mh5.8.2.2-q201']:
            self.assertIn('含截距', self.bank[qid]['prompt'], qid)
        q = self.task('mh5.8.2.2-q101')
        self.assertEqual(grade_answer(q, '-1')['status'], 'correct')
        self.assertEqual(grade_answer(q, '1')['status'], 'wrong')

    def test_ols_figure_and_residuals_match_recomputed_fit(self):
        xs, ys = [1,2,3], [2,3,7]
        slope = sum((x-mean(xs))*(y-mean(ys)) for x,y in zip(xs,ys))/sum((x-mean(xs))**2 for x in xs)
        intercept = mean(ys)-slope*mean(xs)
        self.assertEqual((intercept,slope), (Fraction(-1),Fraction(5,2)))
        self.assertEqual([y-(intercept+slope*x) for x,y in zip(xs,ys)], [Fraction(1,2),Fraction(-1),Fraction(1,2)])
        drawing = svg('statistics-v5-ols-centre.svg')
        dots = drawing.findall('.//s:circle[@id]', NS)
        for dot,x,y in zip(dots,xs,ys):
            self.assertAlmostEqual((float(dot.attrib['cx'])-115)/155, x)
            self.assertAlmostEqual((345-float(dot.attrib['cy']))/25.5, y)
        line = next(n for n in drawing.findall('.//s:line',NS) if n.attrib.get('stroke') == '#2872ad')
        for i in ['1','2']:
            x = (float(line.attrib['x'+i])-115)/155
            y = (345-float(line.attrib['y'+i]))/25.5
            self.assertAlmostEqual(y,float(intercept)+float(slope)*x)

    def test_support_calculations_are_not_silently_mapped_to_other_goals(self):
        for qid in ['m5b.5.1-q101','m8b.24.2-q1','mh5.8.1.1-q101',
                    'mh5.8.1.1-q102','mh5.8.1.1-q202','mh5.8.1.1-q203',
                    'mh5.8.1.2-q101','mh5.8.1.2-q102','mh5.8.2.1-q101',
                    'mh5.8.2.2-q102']:
            q = self.bank[qid]
            self.assert_only_reviewed_component(qid)
            self.assertFalse(q['diagnosis_eligible'],qid)
            self.assertTrue(q['alignment_note'],qid)
        # This prediction can be obtained without finding a parameter.
        self.assertEqual(9+2*(6-4), 13)
        self.assertEqual(grade_answer(self.task('mh5.8.2.2-q102'), str(9+2*(6-4)))['status'], 'correct')
        self.assertNotIn(1, self.bank['mh5.8.2.2-q102']['tested_criterion_indices'])
        self.assertIn('含截距', self.bank['mh5.8.1.2-q102']['prompt'])

    def test_compound_references_declare_the_real_component_families(self):
        example = self.cards['m8b.24.1']['worked_example']
        self.assertEqual(set(example['reference_family_ids']),
                         {'statistics.mean-from-values','statistics.median-position','statistics.mode-frequency'})
        unit = json.loads((DATA / 'learning_units/correlation_and_regression.json').read_text())
        example = next(e for e in unit['worked_examples'] if e['family_id'] == 'statistics.ols-from-paired-data')
        self.assertTrue({'statistics.ols-intercept-from-centre','statistics.signed-residual',
                         'statistics.prediction-substitution'} <= set(example['reference_family_ids']))
        self.assertEqual(self.bank['mh5.8.2.1-q201']['family_id'], self.bank['mh5.8.2.1-q202']['family_id'])

    def test_all_authored_cards_and_active_questions_have_valid_exact_metadata(self):
        for tid,c in self.cards.items():
            self.assertEqual(validate_resource('teaching-card',c),[],tid)
            self.assertEqual(check_card(c,self.topics,self.bank),[],tid)
            for row in c['objective_coverage']:
                self.assertTrue(row['observation_note'])
                for key in ['discriminating_prompt','hint','revision_prompt']:
                    self.assertTrue(row['difficulty_support'][key])
                self.assertTrue(row['self_check']['family_id'])
                self.assertNotEqual(row['self_check']['representation'],'unknown')
        active = [q for q in self.bank.values() if q['topicId'] in TOPICS and not q['id'].endswith('-q9')]
        self.assertEqual(len(active),74)
        for q in active:
            self.assertEqual(validate_resource('question',q),[],q['id'])
            self.assertTrue(q['family_id'],q['id'])
            self.assertNotEqual(q['representation'],'unknown',q['id'])
            self.assertTrue(q['alignment_note'],q['id'])
            self.assertEqual(grade_answer(self.task(q['id']),q['answer'])['status'],'correct',q['id'])


if __name__ == '__main__':
    unittest.main()
