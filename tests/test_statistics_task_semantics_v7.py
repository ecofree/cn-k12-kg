"""Reproducible mathematical review of the 80 V7 mh2/mh5 source tasks.

Oracles are independent enumerations, exact data calculations, definitions and
counterexamples. They never evaluate a question's ``verify`` or use its answer
as the expected result. Synthetic checks do not establish teaching effectiveness.
"""
import copy
from fractions import Fraction as F
import hashlib
from itertools import combinations, permutations, product
import json
from math import comb
from pathlib import Path
from statistics import median
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'docs/reviews/v7-statistics-task-mathematics-review.json'
MATH_FIELDS = ('type', 'prompt', 'choices', 'answer', 'explanation', 'accept', 'diagram')


def mathematical_contract(question):
    """Exclude goal, family, source policy and authoring workflow metadata."""
    return {field: question.get(field) for field in MATH_FIELDS}


def mathematical_digest(question):
    return hashlib.sha256(json.dumps(mathematical_contract(question), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_questions():
    questions = {}
    for folder in ('data/content', 'data/question_sources'):
        for path in sorted((ROOT / folder).glob('*.json')):
            value = json.loads(path.read_text())
            for question in value['questions'] if isinstance(value, dict) else value:
                identity = question['id']
                if identity in questions:
                    raise AssertionError('Duplicate actual source question: ' + identity)
                questions[identity] = question
    return questions


def pairwise_variance(values):
    """Descriptive variance from pairwise differences, without an authored mean."""
    values = list(map(F, values))
    n = len(values)
    return sum((a-b)**2 for a in values for b in values) / (2*n*n)


def binomial_mass(n, k, probability):
    p = F(probability)
    return comb(n, k) * p**k * (1-p)**(n-k)


def distribution_mean(n, probability):
    # Sum the complete distribution, independently of the np shortcut.
    return sum(k*binomial_mass(n, k, probability) for k in range(n+1))


def mh2_results():
    return {
        'mh2.9.1.3-q101':F(200,2000)*100,
        'mh2.9.1.3-q102':50*F(600,600+400),
        'mh2.9.2.1-q1':1,
        'mh2.9.2.1-q101':sum([F(1,5)]*50),
        'mh2.9.2.1-q102':F(4,100)*5,
        'mh2.9.2.2-q2':'中位数', 'mh2.9.2.2-q4':50,
        'mh2.9.2.2-q5':75, 'mh2.9.2.2-q7':'从小到大',
        'mh2.9.2.2-q102':sum([F(20,100)]*500),
        'mh2.9.2.3-q101':F(sum([70,80,80,90,100]),5),
        'mh2.9.2.3-q102':sum([83]*6)-sum([82]*5),
        'mh2.9.2.4-q101':pairwise_variance([2,4,6,8]),
        'mh2.9.2.4-q102':pairwise_variance([4,8,12,16])/pairwise_variance([2,4,6,8]),
        'mh2.9.3-q2':'平均数与方差',
        'mh2.9.3-q3':F(sum(x+3 for x in [2,4,9]),3),
        'mh2.9.3-q101':F(64)/F(16,10)**2,
        'mh2.9.3-q102':24*F(15,10)**2,
        'mh2.10.1.1-q2':'1',
        'mh2.10.1.1-q101':len(list(product('正反',repeat=2))),
        'mh2.10.1.1-q102':sum(a+b==7 for a,b in product(range(1,7),repeat=2)),
        'mh2.10.1.2-q1':'A 与 B 不能同时发生',
        'mh2.10.1.2-q2':'互斥且必有一个发生',
        'mh2.10.1.2-q3':1,
        'mh2.10.1.2-q101':F(3,10)+F(4,10),
        'mh2.10.1.2-q102':F(6,10)+F(5,10)-F(8,10),
        'mh2.10.1.4-q2':'P(A)+P(B)',
        'mh2.10.2-q1':'P(AB)=P(A)P(B)',
        'mh2.10.2-q2':F(len(list(product(range(5),range(4)))),100),
        'mh2.10.2-q101':F(len(list(product(range(8),range(7)))),100),
        'mh2.10.2-q201':F(len(list(product(range(3),range(6)))),100),
        'mh2.10.2-q102':F(sum(a<8 or b<7 for a,b in product(range(10),repeat=2)),100),
        'mh2.10.2-q202':F(len({2,4,6}&{5,6}),6),
        'mh2.10.3.1-q101':F(520,1000),
        'mh2.10.3.1-q102':sum([F(170,200)]*50),
        'mh2.10.3.2-q1':'估计概率',
        'mh2.10.3.2-q101':distribution_mean(100,F(1,4)),
        'mh2.10.3.2-q102':distribution_mean(200,F(3,10)),
    }


def posterior_from_explicit_population():
    # 7,500 A items and 2,500 B items; one in each 50-item block is defective.
    # This realizes the supplied 75%/25% shares and equal 2% defect rates.
    population=[('A' if item<7500 else 'B', item%50==0) for item in range(10000)]
    defective=[source for source,is_defective in population if is_defective]
    return F(defective.count('A'),len(defective))


def mh5_results():
    pairs=list(combinations(range(5),2))
    return {
        'mh5.6.2.1-q2':len(list(permutations(range(3),2))),
        'mh5.6.2.1-q101':len(list(permutations(range(5)))),
        'mh5.6.2.1-q201':len({10*a+b for a,b in permutations([2,4,6],2)}),
        'mh5.6.2.1-q204':len(list(permutations(['甲','乙','丙']))),
        'mh5.6.2.1-q102':sum(p[0]!=0 and p[-1]!=0 for p in permutations(range(5))),
        'mh5.6.2.2-q2':'n!',
        'mh5.6.2.3-q101':len(list(combinations(range(5),2))),
        'mh5.6.2.3-q102':sum(bool(set(p)&{5,6,7}) for p in combinations(range(8),3)),
        'mh5.6.2.4-q102':next(n for n in range(2,50) if comb(n,2)==28),
        'mh5.6.3.1-q1':len({sum(p) for p in product([0,1],repeat=3)}),
        'mh5.6.3.2-q204':'C₈⁴',
        'mh5.7.1.2-q102':posterior_from_explicit_population(),
        'mh5.7.2-q1':1, 'mh5.7.2-q2':'可以一一列出',
        'mh5.7.2-q101':len({sum(p) for p in product([0,1],repeat=3)}),
        'mh5.7.2-q102':1-F(2,10)-F(5,10),
        'mh5.7.2-q204':sum([F(5,10),F(3,10)]),
        'mh5.7.4.1-q101':distribution_mean(10,F(3,10)),
        'mh5.7.4.1-q102':F(sum(sum(p)==2 for p in product([0,1],repeat=4)),2**4),
        'mh5.7.4.1-q204':F(sum(sum(p)==2 for p in product([0,1],repeat=3)),2**3),
        'mh5.7.4.2-q101':F(sum(len(set(p)&{0,1})==1 for p in pairs),len(pairs)),
        'mh5.7.4.2-q102':F(sum(bool(set(p)&{0}) for p in pairs),len(pairs)),
        'mh5.7.5-q101':F(1,2),
        'mh5.7.5-q102':int(1000*F(6827,10000)+F(1,2)),
        'mh5.8.1.1-q101':F(-9,10)**2*100,
        'mh5.8.1.1-q102':F(8,10)**2*100,
        'mh5.8.1.1-q202':'可能有非线性关系',
        'mh5.8.1.1-q203':'样本有相关趋势，可能有气温等共同因素',
        'mh5.8.1.2-q2':'[-1,1]',
        'mh5.8.1.2-q101':F(9,10)**2,
        'mh5.8.1.2-q102':F(5,10)**2*100,
        'mh5.8.1.2-q203':'分母为0，r未定义',
        'mh5.8.2.1-q1':'斜率',
        'mh5.8.2.1-q2':'同一x处的观测值减去拟合预测值',
        'mh5.8.2.1-q3':'预测值',
        'mh5.8.2.1-q101':2*5+3,
        'mh5.8.2.2-q2':'样本中心点',
        'mh5.8.2.2-q3':'中心点',
        'mh5.8.2.2-q102':9+2*(6-4),
        'mh5.8.3.1-q1':len(list(product(['row1','row2'],['col1','col2']))),
        'mh5.8.3.1-q2':'两个分类变量的关系',
        'mh5.8.3.2-q101':40*30-10*20,
    }


def centered_sums(xs,ys):
    xs,ys=list(map(F,xs)),list(map(F,ys))
    xb,yb=sum(xs)/len(xs),sum(ys)/len(ys)
    return xb,yb,sum((x-xb)**2 for x in xs),sum((y-yb)**2 for y in ys),sum((x-xb)*(y-yb) for x,y in zip(xs,ys))


class StatisticsTaskSemanticsV7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions=source_questions()
        cls.mh2=mh2_results();cls.mh5=mh5_results()
        cls.expected={**cls.mh2,**cls.mh5}
        cls.review=json.loads(REVIEW.read_text())

    def test_all_eighty_actual_tasks_match_independent_results(self):
        self.assertEqual(38,len(self.mh2));self.assertEqual(42,len(self.mh5))
        self.assertEqual(80,len(self.expected))
        for identity,expected in self.expected.items():
            with self.subTest(task=identity):
                question=self.questions[identity]
                if question.get('choices'):
                    matches=[c['text'] for c in question['choices'] if c['id']==question['answer']]
                    self.assertEqual(1,len(matches))
                    actual=matches[0]
                else:
                    actual=question['answer']
                if isinstance(expected,str):
                    self.assertEqual(expected,actual)
                else:
                    self.assertEqual(F(expected),F(actual))

    def test_review_binds_only_mathematical_content_and_all_eighty_tasks(self):
        review=self.review
        self.assertEqual('ai',review['reviewer_kind'])
        self.assertFalse(review['independent_teacher_review'])
        self.assertFalse(review['student_effectiveness_evidence'])
        self.assertEqual(list(MATH_FIELDS),review['mathematical_content_fields'])
        self.assertEqual(67,len(review['group_reviews']))
        checked={}
        for group in review['group_reviews']:
            self.assertEqual({'explanation','discrimination','hint','criterion'},set(group['four_fields']))
            self.assertEqual('ai',group['reviewer_kind'])
            self.assertFalse(group['independent_teacher_review'])
            for check in group['task_checks']:
                self.assertNotIn(check['task_id'],checked)
                checked[check['task_id']]=check
        self.assertEqual(set(self.expected),set(checked))
        for identity,check in checked.items():
            self.assertEqual(check['mathematical_content_sha256'],mathematical_digest(self.questions[identity]),identity)
        for asset in review['asset_reviews']:
            self.assertEqual(asset['sha256'],hashlib.sha256((ROOT/asset['path']).read_bytes()).hexdigest())

    def test_goal_metadata_does_not_invalidate_a_mathematical_review(self):
        original=self.questions['mh5.8.1.2-q101']
        metadata=copy.deepcopy(original)
        metadata.update(tested_criterion_indices=[999],target_alignment='new_goal_mapping',
                        family_id='classification-only',diagnosis_eligible=False)
        self.assertEqual(mathematical_digest(original),mathematical_digest(metadata))
        changed=copy.deepcopy(original);changed['answer']='0.9'
        self.assertNotEqual(mathematical_digest(original),mathematical_digest(changed))

    def test_density_area_frequencies_and_counts_are_distinct(self):
        density,width=F(4,100),5
        frequency=density*width
        self.assertEqual(F(1,5),frequency)
        self.assertNotEqual(density,frequency)
        self.assertEqual(10,50*frequency)
        # Equal heights do not imply equal frequencies when widths differ.
        self.assertNotEqual(F(1,10)*2,F(1,10)*3)
        bins=[(F(1,10),2),(F(1,10),3),(F(1,10),5)]
        self.assertEqual(1,sum(h*w for h,w in bins))

    def test_percentile_median_ties_do_not_imply_half_strictly_below(self):
        tied=[4,4,4,4]
        middle=median(tied)
        self.assertEqual(4,middle)
        self.assertEqual(0,F(sum(x<middle for x in tied),len(tied)))
        self.assertNotEqual(F(1,2),F(sum(x<middle for x in tied),len(tied)))
        self.assertEqual(F(5),F(median([2,4,6,8])))

    def test_mean_addition_and_variance_are_verified_from_complete_data(self):
        marks=[70,80,80,90,100]
        self.assertEqual(84,F(sum(marks),len(marks)))
        new_value=self.mh2['mh2.9.2.3-q102']
        self.assertEqual(83,F(sum([82]*5)+new_value,6))
        original=[2,4,6,8];doubled=[2*x for x in original]
        self.assertEqual(5,pairwise_variance(original))
        self.assertEqual(4,pairwise_variance(doubled)/pairwise_variance(original))
        self.assertEqual(pairwise_variance(original),pairwise_variance([x+17 for x in original]))

    def test_disjoint_complement_and_independence_conditions_are_not_conflated(self):
        omega={1,2,3,4};a={1};b={2}
        self.assertFalse(a&b);self.assertNotEqual(omega,a|b)
        self.assertNotEqual(F(len(a&b),4),F(len(a),4)*F(len(b),4))
        self.assertEqual(omega,a|(omega-a))
        # The actual dice intersection task supplies a valid independence example,
        # although its numeric-only submission observes only the intersection.
        a={2,4,6};b={5,6}
        self.assertEqual({6},a&b)
        self.assertEqual(F(len(a&b),6),F(len(a),6)*F(len(b),6))

    def test_frequency_estimated_mean_and_theoretical_mean_have_correct_boundaries(self):
        empirical=F(520,1000)
        self.assertEqual(F(13,25),empirical)
        estimated_rate=F(170,200)
        estimated_count=sum([estimated_rate]*50)
        self.assertEqual(F(85,2),estimated_count)
        self.assertNotEqual(1,estimated_count.denominator)
        # One compatible stable independent model permits 50 successes; the mean
        # 42.5 cannot be asserted as an actual count or guaranteed [42,43] range.
        self.assertGreater(estimated_rate**50,0)
        self.assertGreater(50,43)
        self.assertEqual(25,distribution_mean(100,F(1,4)))
        self.assertEqual(60,distribution_mean(200,F(3,10)))

    def test_ordered_arrangements_and_unordered_groups_are_enumerated(self):
        ordered=set(permutations(range(5),2))
        unordered={frozenset(pair) for pair in ordered}
        self.assertEqual(2*len(unordered),len(ordered))
        self.assertEqual(10,len(unordered))
        arrangements=list(permutations(range(5)))
        internal=[p for p in arrangements if p[0]!=0 and p[-1]!=0]
        self.assertEqual(72,len(internal))
        groups=list(combinations(range(8),3))
        with_girl=[g for g in groups if set(g)&{5,6,7}]
        without=[g for g in groups if not set(g)&{5,6,7}]
        self.assertEqual(len(groups),len(with_girl)+len(without))
        self.assertEqual((46,10),(len(with_girl),len(without)))

    def test_binomial_coefficients_support_and_point_masses_are_enumerated(self):
        coefficients=[comb(8,r) for r in range(9)]
        self.assertEqual(coefficients,list(reversed(coefficients)))
        self.assertEqual([4],[r for r,c in enumerate(coefficients) if c==max(coefficients)])
        self.assertEqual(70,max(coefficients))
        three=list(product([0,1],repeat=3))
        self.assertEqual(8,len(three));self.assertEqual({0,1,2,3},{sum(p) for p in three})
        self.assertEqual(F(3,8),F(sum(sum(p)==2 for p in three),len(three)))
        four=list(product([0,1],repeat=4))
        self.assertEqual(F(3,8),F(sum(sum(p)==2 for p in four),len(four)))
        self.assertEqual(3,distribution_mean(10,F(3,10)))
        self.assertEqual(1,sum(binomial_mass(10,k,F(3,10)) for k in range(11)))

    def test_without_replacement_event_sets_and_posterior_conditioning(self):
        pairs=list(combinations(range(5),2))
        self.assertEqual(10,len(pairs))
        self.assertEqual(6,sum(len(set(p)&{0,1})==1 for p in pairs))
        self.assertEqual(4,sum(bool(set(p)&{0}) for p in pairs))
        # When only one defective item exists, at least one equals exactly one.
        self.assertEqual([p for p in pairs if set(p)&{0}],
                         [p for p in pairs if len(set(p)&{0})==1])
        posterior=posterior_from_explicit_population()
        self.assertEqual(F(3,4),posterior)
        self.assertNotEqual(F(75,100)*F(2,100),posterior)

    def test_actual_scatter_asset_has_curvature_despite_zero_linear_covariance(self):
        path=ROOT/'web/static/diagrams/senior-v6-scatter-assessment.svg'
        svg=ET.fromstring(path.read_text())
        points=[((F(c.attrib['cx'])-740)/35,(315-F(c.attrib['cy']))/43)
                for c in svg.findall('.//{http://www.w3.org/2000/svg}circle') if F(c.attrib['cx'])>635]
        self.assertEqual([(-2,4),(-1,1),(0,0),(1,1),(2,4)],points)
        self.assertEqual([-3,-1,1,3],[points[i+1][1]-points[i][1] for i in range(4)])
        self.assertTrue(all(y==x*x for x,y in points))
        _,_,sxx,syy,sxy=centered_sums(*zip(*points))
        self.assertGreater(sxx*syy,0);self.assertEqual(0,sxy)

    def test_ols_centre_r_squared_units_and_undefined_correlation_conditions(self):
        xs=[1,2,3];ys=[2,2,5]
        xb,yb,sxx,syy,sxy=centered_sums(xs,ys)
        slope=sxy/sxx;intercept=yb-slope*xb
        fitted=[intercept+slope*x for x in xs]
        residuals=[F(y)-prediction for y,prediction in zip(ys,fitted)]
        sse=sum(e*e for e in residuals)
        self.assertEqual(yb,intercept+slope*xb)
        self.assertEqual(0,sum(residuals))
        self.assertEqual(1-sse/syy,sxy*sxy/(sxx*syy))
        self.assertGreater(syy,sse)  # The horizontal centre line is not optimal here.
        _,_,scaled_xx,scaled_yy,scaled_xy=centered_sums([F(x,100) for x in xs],ys)
        self.assertEqual(sxy*sxy/(sxx*syy),scaled_xy**2/(scaled_xx*scaled_yy))
        self.assertEqual(0,centered_sums([3,3,3],ys)[2])
        # A through-origin least-squares fit need not pass through the centre.
        origin_slope=F(1,5)  # x=(1,2), y=(1,0): sum(x*y)/sum(x*x).
        self.assertNotEqual(F(1,2),origin_slope*F(3,2))

    def test_cross_product_difference_is_not_the_full_chi_square_statistic(self):
        cells=[[40,10],[20,30]]
        rows=[sum(row) for row in cells]
        columns=[sum(cells[r][c] for r in range(2)) for c in range(2)]
        total=sum(rows)
        expected=[[F(rows[r]*columns[c],total) for c in range(2)] for r in range(2)]
        pearson=sum((cells[r][c]-expected[r][c])**2/expected[r][c] for r,c in product(range(2),repeat=2))
        cross=cells[0][0]*cells[1][1]-cells[0][1]*cells[1][0]
        self.assertEqual(1000,cross)
        self.assertEqual(F(50,3),pearson)
        self.assertEqual(pearson,F(total*cross*cross,rows[0]*rows[1]*columns[0]*columns[1]))
        self.assertNotEqual(cross,pearson)
        rule=next(g for g in self.review['group_reviews'] if g['proposal_key']=='chi-square.cross-product-intermediate')['four_fields']
        self.assertIn('由四格计算样本量及行列合计',rule['explanation'])
        self.assertNotIn('还缺样本量',rule['explanation'])


if __name__=='__main__':
    unittest.main()
