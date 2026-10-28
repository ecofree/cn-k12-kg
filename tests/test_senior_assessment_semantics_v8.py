"""Independent mathematical checks for the authored senior V8 opportunities.

These checks enumerate actual outcomes, use raw pairs and geometry, and keep
conditions visible. They do not turn an answer match into proof, graph, teacher
review, or learner-effectiveness evidence. Author review covers the remaining
logical statements; this suite is deliberately not a generated mirror of prose.
"""
from collections import Counter
from fractions import Fraction as F
from itertools import combinations, permutations, product
import json
import math
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def points(text, dimension=2):
    text = text.replace('−', '-').replace('（', '(').replace('）', ')')
    pattern = r'\((' + r',\s*'.join([r'-?\d+(?:\.\d+)?'] * dimension) + r')\)'
    return [tuple(F(x.strip()) for x in item.split(',')) for item in re.findall(pattern, text)]


def centered(pairs):
    n = len(pairs)
    xb = sum(p[0] for p in pairs) / n
    yb = sum(p[1] for p in pairs) / n
    return xb, yb, sum((x-xb)**2 for x, y in pairs), sum((y-yb)**2 for x, y in pairs), sum((x-xb)*(y-yb) for x, y in pairs)


class SeniorAssessmentMathematicsV8Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((ROOT/'data/assessments/senior.json').read_text())
        cls.tasks = {t['id']: t for t in cls.source['tasks']}

    def task(self, number):
        return self.tasks[f'v8s_objective_{number:03d}']

    def chosen(self, number):
        task = self.task(number)
        return next(row['text'] for row in task['choices'] if row['id'] == task['answer'])

    def assert_scalar_choice(self, number, expected):
        """Parse all scalar alternatives, so the keyed option must be unique."""
        task = self.task(number)
        matches = []
        for choice in task['choices']:
            match = re.fullmatch(r'([−-]?\d+(?:\.\d+)?)(?:种|立方厘米)?。', choice['text'])
            self.assertIsNotNone(match, (task['id'], choice['text']))
            if F(match.group(1).replace('−', '-')) == expected:
                matches.append(choice['id'])
        self.assertEqual(matches, [task['answer']], (task['id'], expected))

    def test_complete_subsets_and_restricted_integer_permutations(self):
        expected = {frozenset(c) for size in range(3) for c in combinations('ab', size)}
        task = self.task(3)
        self.assertIn('两个不同元素', task['prompt'])
        valid = []
        for option in task['choices']:
            raw = option['text']
            tokens = re.findall(r'∅|\{[^{}]*\}', raw)
            parsed = [frozenset() if s == '∅' else frozenset(s[1:-1].split(',')) for s in tokens]
            if '{{' not in raw and len(parsed) == len(expected) and set(parsed) == expected:
                valid.append(option['id'])
        self.assertEqual(valid, [task['answer']])
        expected_numbers = {10*a+b for a, b in permutations(range(3), 2) if a != 0}
        result = {int(n) for n in re.findall(r'\d+', self.chosen(70))}
        self.assertEqual(result, expected_numbers)

    def test_adjacency_and_binomial_symmetry_by_enumeration(self):
        count = sum(abs(p.index('甲')-p.index('乙')) > 1 for p in permutations('甲乙丙丁戊'))
        self.assert_scalar_choice(71, F(count))
        coeffs = [len(list(combinations(range(8), k))) for k in range(9)]
        solutions = [k for k in range(7) if coeffs[k] == coeffs[k+2]]
        self.assertEqual(len(solutions), 1)
        self.assert_scalar_choice(74, F(solutions[0]))

    def test_conditioning_and_marginal_counts_use_different_spaces(self):
        cards = [(True, True)]*6 + [(True, False)]*4 + [(False, True)]*6 + [(False, False)]*4
        a = [c for c in cards if c[0]]
        both = [c for c in cards if all(c)]
        conditional, joint = F(len(both), len(a)), F(len(both), len(cards))
        self.assertNotEqual(conditional, joint)
        fractions = [F(s) for s in re.findall(r'\d+/\d+', self.chosen(76))]
        self.assertEqual(fractions, [conditional, joint])
        marginal_b = F(sum(c[1] for c in cards), len(cards))
        self.assertEqual(joint, F(len(a), len(cards))*marginal_b)
        self.assertIn('恰等于乘积', self.chosen(95))

    def test_independent_count_distribution_and_replacement_support(self):
        outcomes = list(product((0, 1), repeat=2))
        mass = Counter(map(sum, outcomes))
        actual = [F(mass[k], len(outcomes)) for k in range(3)]
        choice_mass = [F(s) for s in re.findall(r'\d+/\d+', self.chosen(79))]
        self.assertEqual(choice_mass, actual)
        replace_counts = Counter(sum(x == 0 for x in seq) for seq in product(range(5), repeat=3))
        nonreplace_counts = Counter(sum(x == 0 for x in seq) for seq in combinations(range(5), 3))
        self.assertGreater(replace_counts[3], 0)
        self.assertEqual(nonreplace_counts[2], 0)
        self.assertIn('X=2或3可能', self.chosen(82))

    def test_wrong_simulation_mechanism_has_a_different_exact_limit(self):
        population = ['R', 'R', 'W']
        without = list(permutations(range(3), 2))
        with_replacement = list(product(range(3), repeat=2))
        probability = lambda rows: F(sum(any(population[i] == 'W' for i in row) for row in rows), len(rows))
        self.assertEqual(probability(without), F(2, 3))
        self.assertEqual(probability(with_replacement), F(5, 9))
        self.assertIn('增大轮数不能修正', self.chosen(47))

    def test_total_probability_inverse_weight_from_population_model(self):
        # Of ten equally weighted A outcomes six are red; B has one red.
        a = [True]*6 + [False]*4
        b = [True] + [False]*9
        candidates = [F(i, 20) for i in range(21)]
        valid = [w for w in candidates if w*F(sum(a), len(a))+(1-w)*F(sum(b), len(b)) == F(35, 100)]
        self.assertEqual(len(valid), 1)
        self.assert_scalar_choice(78, valid[0])

    def test_spatial_coplanarity_checks_rank_of_the_first_pair(self):
        a, b, c = points(self.task(50)['prompt'], 3)
        cross = lambda u, v: (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        self.assertEqual(cross(a, b), (0, 0, 0))
        self.assertNotEqual(cross(a, c), (0, 0, 0))
        self.assertTrue(all(v[1] == 0 for v in (a, b, c)))
        self.assertIn('仍共面', self.chosen(50))

    def test_derived_normal_and_unoriented_line_angle(self):
        origin, a, b, p = points(self.task(52)['prompt'], 3)
        normal = (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
        for tangent in (a, b):
            self.assertEqual(sum(x*y for x, y in zip(normal, tangent)), 0)
        distance_squared = sum(x*y for x, y in zip(p, normal))**2 / sum(x*x for x in normal)
        self.assertEqual(distance_squared, F(1, 3))
        self.assertIn('1/√3', self.chosen(52))
        u, v = points(self.task(53)['prompt'], 3)
        cosine = abs(sum(x*y for x, y in zip(u, v))) / math.sqrt(float(sum(x*x for x in u)*sum(x*x for x in v)))
        angle = math.degrees(math.acos(cosine))
        self.assertAlmostEqual(angle, 45)
        self.assertTrue(self.chosen(53).startswith('45°'))

    def test_complex_conjugate_equation_and_parameter_division(self):
        z = complex(2, 1)
        self.assertEqual(z+2*z.conjugate(), complex(6, -1))
        self.assertIn('a=2，b=1', self.chosen(31))
        self.assertIn('(t+1)/(t²+1)', self.chosen(32))
        self.assertIn('(t−1)/(t²+1)', self.chosen(32))
        for t in (-4, -1, 0, 1, 3):
            result = complex(1, 1)/complex(t, 1)
            self.assertAlmostEqual(result.real, float(F(t+1, t*t+1)))
            self.assertAlmostEqual(result.imag, float(F(t-1, t*t+1)))
        reciprocal = 1/complex(3, 4)
        self.assertAlmostEqual(abs(reciprocal), 1/5)
        self.assertAlmostEqual(math.atan2(reciprocal.imag, reciprocal.real), -math.atan2(4, 3))
        self.assertIn('r>0', self.task(108)['prompt'])
        self.assertIn('模为1/r', self.chosen(108))

    def test_ssa_has_two_feasible_angles_and_ratio_signs_are_coupled(self):
        sine = math.sqrt(2)*math.sin(math.pi/6)
        first = math.asin(sine)
        feasible = [x for x in (first, math.pi-first) if math.pi/6+x < math.pi]
        self.assertEqual(len(feasible), 2)
        self.assertAlmostEqual(math.degrees(feasible[0]), 45)
        self.assertAlmostEqual(math.degrees(feasible[1]), 135)
        self.assertIn('45°或135°', self.chosen(30))
        s = F(3, 5)
        possibilities = [(F(sign*4, 5), s/F(sign*4, 5)) for sign in (1, -1)]
        actual = [F(v) for v in re.findall(r'[−-]?\d+/\d+', self.chosen(114).replace('−', '-'))]
        self.assertEqual(actual, [v for pair in possibilities for v in pair])

    def test_grouped_scatter_coordinates_and_pooled_covariance(self):
        task = self.task(83)
        data = points(task['diagram']['alt'])
        self.assertEqual(len(data), 6)
        svg = ET.parse(ROOT/'web'/task['diagram']['src'].lstrip('/')).getroot()
        ns = {'s': 'http://www.w3.org/2000/svg'}
        circle_group = next(g for g in svg.findall('s:g', ns) if g.get('fill') == '#2166ac')
        square_group = next(g for g in svg.findall('s:g', ns) if g.get('fill') == '#b45309')
        actual = [(F(c.get('cx'))-80)/55 for c in circle_group]
        self.assertEqual(actual, [p[0] for p in data[:3]])
        rendered = [((F(c.get('cx'))-80)/55, (380-F(c.get('cy')))/30) for c in circle_group]
        rendered += [((F(c.get('x'))+7-80)/55, (380-(F(c.get('y'))+7))/30) for c in square_group]
        self.assertEqual(rendered, data)
        # Pairwise identity independent of the centered-product explanation.
        sxy = sum((a[0]-b[0])*(a[1]-b[1]) for a, b in combinations(data, 2))/len(data)
        self.assertEqual(sxy, -59)
        self.assertGreater(centered(data[:3])[4], 0)
        self.assertGreater(centered(data[3:])[4], 0)
        self.assertIn('全体点总体呈下降', self.chosen(83))

    def test_zero_correlation_can_hide_conditional_scale_dependence(self):
        data = points(self.task(86)['prompt'])
        xb, yb, xx, yy, xy = centered(data)
        self.assertEqual((xb, yb, xy), (0, 0, 0))
        self.assertGreater(xx*yy, 0)
        by_x = {x: {abs(y) for a, y in data if a == x} for x, y in data}
        self.assertNotEqual(by_x[F(-1)], by_x[F(1)])
        self.assertIn('绝对值随x分组而改变', self.chosen(86))

    def test_affine_correlation_and_constant_least_squares(self):
        data = [(-1.0, -3+4/math.sqrt(3)), (0.0, -8/math.sqrt(3)), (1.0, 3+4/math.sqrt(3))]
        def correlation(rows):
            _, _, xx, yy, xy = centered(rows)
            return xy/math.sqrt(xx*yy)
        self.assertAlmostEqual(correlation(data), .6)
        self.assertAlmostEqual(correlation([(x, 100-2*y) for x, y in data]), -.6)
        self.assertIn('−0.6', self.chosen(85))
        observed = [F(1), F(2), F(6)]
        mean = sum(observed)/len(observed)
        pairwise_minimum = sum((a-b)**2 for a, b in combinations(observed, 2))/len(observed)
        self.assertEqual(sum((y-mean)**2 for y in observed), pairwise_minimum)
        self.assert_scalar_choice(88, mean)

    def test_no_intercept_ols_and_zero_margins(self):
        data = points(self.task(96)['prompt'])
        slope = sum(x*y for x, y in data)/sum(x*x for x, y in data)
        _, ymean, xx, yy, xy = centered(data)
        self.assertEqual(xy*xy, xx*yy)
        loss = sum((y-slope*x)**2 for x, y in data)
        self.assert_scalar_choice(96, 1-loss/yy)
        rows = points(self.task(92)['prompt'])
        totals = [sum(r) for r in rows]
        columns = [sum(r[j] for r in rows) for j in range(2)]
        expected = [[a*b/sum(totals) for b in columns] for a in totals]
        self.assertTrue(any(e == 0 for row in expected for e in row))
        self.assertIn('零分母', self.chosen(92))

    def test_arithmetic_recovery_rate_composition_and_piecewise_calls(self):
        # Recover a and d from prefix sums by a small exact linear solve.
        s2, s4 = F(8), F(24)
        d = (s4-2*s2)/4
        a = (s2-d)/2
        sequence = [a+k*d for k in range(6)]
        self.assertEqual(sum(sequence[:2]), s2)
        self.assertEqual(sum(sequence[:4]), s4)
        self.assert_scalar_choice(109, sum(sequence))
        self.assert_scalar_choice(110, (F(2)*3+F(1)*9)/3)
        self.assertIn('f(x)=3（x≤1）', self.task(99)['prompt'])
        def f(x):
            return F(3) if x <= 1 else x+1
        self.assert_scalar_choice(99, f(f(F(1))))

    def test_moving_sine_window_and_piecewise_rotation(self):
        for a in (0, math.pi/4, math.pi/2, 3*math.pi/4, math.pi):
            samples = [math.sin(a+(math.pi/2)*k/2000) for k in range(2001)]
            target = 1 if a <= math.pi/2 else math.sin(a)
            self.assertAlmostEqual(max(samples), target, places=6)
        self.assertIn('π/2<a≤π时M=sin a', self.chosen(103))
        # Accumulate the specified angular travel, including the first half-turn.
        angle = lambda t: math.pi*min(t, 5)/5 + math.pi*max(t-5, 0)/10
        self.assertAlmostEqual(angle(0), 0)
        self.assertAlmostEqual(angle(5), math.pi)
        self.assertAlmostEqual(angle(15), 2*math.pi)
        self.assertIn('θ=π+π(t−5)/10', self.chosen(106))
        for x in (-2, -.3, 0, .7, 2):
            self.assertAlmostEqual(math.sin(-(x-math.pi/3)), math.sin(math.pi/3-x))
        self.assertTrue(self.chosen(107).startswith('先关于y轴反射'))

    def test_solid_dimensions_and_project_reference_arithmetic(self):
        self.assert_scalar_choice(36, F(12*6))
        self.assert_scalar_choice(37, F(4*9, 2)*5/3)
        self.assertIn('24π平方厘米与12π立方厘米', self.chosen(116))
        # These are deterministic ranges, not coverage-probability intervals.
        lower = 1.45+9.8*math.tan(math.radians(44))
        upper = 1.55+10.2*math.tan(math.radians(46))
        self.assertEqual(math.floor(lower*100)/100, 10.91)
        self.assertEqual(math.ceil(upper*100)/100, 12.12)
        training = [(F(t), F(t*t+t+1)) for t in range(5)]
        xb, yb, xx, yy, xy = centered(training)
        b, a = xy/xx, yb-(xy/xx)*xb
        self.assertEqual(sum((y-a-b*x)**2 for x, y in training), 14)
        holdout = [(F(t), F(t*t+t+1)) for t in (5, 6)]
        self.assertEqual(sum((y-a-b*x)**2 for x, y in holdout), 245)


if __name__ == '__main__':
    unittest.main()
