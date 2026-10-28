"""Semantic counterexamples for authored spatial and triangle material."""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree

from cnkg.answers import grade_answer
from cnkg.contracts import validate_resource
from tools.rebuild_data import build_data
from tools.teach_cards import check_card

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
TOPICS = ('mh2.8.2', 'm9b.33.2.2', 'mh2.8.3.1', 'm8a.13.1', 'm8a.13.2', 'm8a.13.3')
NS = {'s': 'http://www.w3.org/2000/svg'}


def points(path):
    values = [float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', path.attrib['d'])]
    return list(zip(values[::2], values[1::2]))


def polygon_area(p):
    return abs(sum(a[0] * b[1] - a[1] * b[0] for a, b in zip(p, p[1:] + p[:1]))) / 2


def line_points(line):
    return [(float(line.attrib[x]), float(line.attrib[y])) for x, y in [('x1', 'y1'), ('x2', 'y2')]]


class QualityV5GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows, _ = build_data(DATA)
        cls.bank = {q['id']: q for q in rows['questions.json']}
        cls.topics = {t['id']: t for t in rows['topics.json']}
        cls.bindings = json.loads((DATA / 'skills/catalog-bindings.json').read_text())['tasks']
        cls.cards = {tid: json.loads((DATA / f'teach/{tid}.json').read_text()) for tid in TOPICS}

    def task(self, qid):
        q = self.bank[qid]
        return {**q, 'answer_spec': q.get('answer_spec') or self.bindings['bank:' + qid]['answer_spec']}

    def test_y_axis_shortening_cannot_be_replaced_by_z_axis_shortening(self):
        q = self.task('mh2.8.2-q101')
        self.assertIn('水平', q['prompt'])
        self.assertIn('y轴', q['prompt'])
        self.assertNotIn('竖直方向的边长', q['prompt'])
        self.assertIn('z方向', q['explanation'])
        self.assertEqual(grade_answer(q, '2')['status'], 'correct')
        self.assertEqual(grade_answer(q, '4')['status'], 'wrong')
        # A numeric projection component does not observe the full drawing.
        self.assertEqual(q['tested_criterion_indices'], [1])

    def test_oblique_area_and_the_actual_svg_have_the_same_nonhalf_scale(self):
        q = self.task('mh2.8.2-q102')
        self.assertNotIn('同上', q['prompt'])
        self.assertEqual(grade_answer(q, '5.656')['status'], 'correct')
        self.assertEqual(grade_answer(q, '8')['status'], 'wrong')
        svg = ElementTree.parse(ROOT / 'web/static/diagrams/oblique-projection-v5.svg')
        polygons = [points(p) for p in svg.findall('.//s:path', NS) if p.attrib.get('fill') == '#dceff2']
        self.assertEqual(len(polygons), 2)
        original, drawn = polygons
        self.assertAlmostEqual(math.dist(original[0], original[1]), 240)
        self.assertAlmostEqual(math.dist(drawn[0], drawn[1]), 240)
        self.assertAlmostEqual(math.dist(drawn[0], drawn[3]), 80, places=2)
        self.assertAlmostEqual(polygon_area(drawn) / polygon_area(original), math.sqrt(2) / 4, places=5)
        self.assertEqual(q['tested_criterion_indices'], [2])
        self.assertEqual(self.topics['mh2.8.2']['mastery_criteria'][2],
                         '能按给定斜二测轴向变换规则计算水平正方形直观图的面积估值')
        self.assertNotIn(0, q['tested_criterion_indices'])  # Full drawing is unobserved.

    def test_multiview_counterexamples_actually_have_identical_projections(self):
        def projections(flat):
            a, b, c, d = flat
            return (max(a, c), max(b, d)), (max(a, b), max(c, d))
        self.assertEqual(projections([2, 1, 1, 2]), projections([2, 2, 2, 2]))
        self.assertNotEqual(sum([2, 1, 1, 2]), sum([2, 2, 2, 2]))
        totals = {sum(v) for v in itertools.product(range(1, 4), repeat=4)
                  if projections(v) == ((3, 3), (3, 3))}
        self.assertEqual(totals, {8, 9, 10, 11, 12})
        self.assertIn('不唯一', self.cards['m9b.33.2.2']['objective_coverage'][1]['self_check']['reference_answer'])
        svg = ElementTree.parse(ROOT / 'web/static/diagrams/three-views-stacks-v5.svg')
        cube_tops = [points(p) for p in svg.findall('.//s:path', NS) if p.attrib.get('fill') == '#e2f0f7']
        self.assertEqual(sum(sum(x for x, _ in p) / len(p) < 480 for p in cube_tops), 6)
        self.assertEqual(sum(sum(x for x, _ in p) / len(p) > 480 for p in cube_tops), 8)

    def test_known_cylinder_volume_does_not_claim_shape_identification(self):
        q = self.task('m9b.33.2.2-q102')
        self.assertIn('轴竖直', q['prompt'])
        self.assertIn('直径', q['prompt'])
        self.assertEqual(grade_answer(q, '24')['status'], 'correct')
        self.assertEqual(grade_answer(q, '96')['status'], 'wrong')
        self.assertEqual(q['tested_criterion_indices'], [3])
        self.assertEqual(self.topics['m9b.33.2.2']['mastery_criteria'][3],
                         '能由文字给定的直立圆柱主视尺寸求体积中π的系数')
        self.assertTrue({0, 1}.isdisjoint(q['tested_criterion_indices']))
        q = self.bank['m9b.33.2.2-q4']
        self.assertEqual({x['text'] for x in q['choices']}, {'球', '圆柱', '圆锥', '正方体'})

    def test_complex_perimeter_or_area_tasks_are_not_forced_into_recognition_goals(self):
        expected = {
            'm8a.13.1-q101': (2, '能由等腰三角形腰长与底边长计算周长'),
            'm8a.13.1-q102': (3, '能由三角形周长与三边比求指定边长'),
            'm8a.13.2-q102': (5, '能由中线分成的一半面积求原三角形面积'),
            'm9b.33.2.2-q101': (2, '能由文字给定的长方体视图尺寸计算体积'),
            'm9b.33.2.2-q102': (3, '能由文字给定的直立圆柱主视尺寸求体积中π的系数'),
            'mh2.8.2-q102': (2, '能按给定斜二测轴向变换规则计算水平正方形直观图的面积估值'),
        }
        for qid, (index, statement) in expected.items():
            q = self.bank[qid]
            self.assertEqual(q['tested_criterion_indices'], [index])
            self.assertEqual(self.topics[q['topicId']]['mastery_criteria'][index], statement)
            self.assertFalse(q['diagnosis_eligible'])
            self.assertEqual(q['target_alignment'], 'component_practice_only')
            self.assertTrue(q['alignment_note'])

    def test_the_special_line_diagram_distinguishes_three_different_definitions(self):
        svg = ElementTree.parse(ROOT / 'web/static/diagrams/triangle-special-lines-v5.svg')
        triangles = [points(p) for p in svg.findall('.//s:path', NS) if p.attrib.get('fill') == '#e6f0f7']
        self.assertEqual(len(triangles), 4)
        lines = svg.findall('.//s:line', NS)
        # The longest green segment is the median; short green strokes mark
        # equal base halves. Test the drawn coordinates, not just its label.
        median = max((line_points(l) for l in lines if l.attrib.get('stroke') == '#378469'),
                     key=lambda p: math.dist(*p))
        a, b, c = triangles[1]
        self.assertEqual(median[0], a)
        self.assertEqual(median[1], ((b[0] + c[0]) / 2, b[1]))
        self.assertNotEqual(a[0], median[1][0])  # Not an altitude in this scalene example.
        bisector = next(line_points(l) for l in lines if l.attrib.get('stroke') == '#ba7031')
        a, b, c = triangles[2]
        self.assertAlmostEqual(math.dist(b, bisector[1]) / math.dist(bisector[1], c),
                               math.dist(a, b) / math.dist(a, c), places=5)
        a, b, c = triangles[3]
        outer = [line_points(l) for l in lines if l.attrib.get('stroke') == '#177aa1'][-1]
        self.assertEqual(outer[0], a)
        self.assertEqual(outer[0][0], outer[1][0])
        self.assertEqual(outer[1][1], b[1])
        self.assertGreater(outer[1][0], max(b[0], c[0]))

    def test_midpoint_number_does_not_claim_all_three_constructions(self):
        q = self.task('m8a.13.2-q202')
        self.assertEqual(q['tested_criterion_indices'], [3])
        self.assertFalse(q['diagnosis_eligible'])
        self.assertIn('未实际连接顶点作中线', q['alignment_note'])
        c = self.cards['m8a.13.2']
        self.assertEqual(len(c['objective_coverage']), 6)
        self.assertEqual(c['objective_coverage'][0]['title'], '能画出三角形的高、中线与角平分线')
        self.assertIn('外部高', c['objective_coverage'][2]['title'])

    def test_identical_structures_share_families_across_card_and_unit(self):
        unit = json.loads((DATA / 'learning_units/triangle_conditions.json').read_text())
        families = {q['id']: q['family_id'] for q in unit['tasks']}
        self.assertEqual(self.bank['m8a.13.2-q101']['family_id'], families['triangle_conditions:delayed-1'])
        self.assertEqual(self.bank['m8a.13.3-q102']['family_id'], families['triangle_conditions:transfer-2'])
        self.assertEqual(self.bank['m8a.13.3-q202']['family_id'], families['triangle_conditions:practice-2'])
        self.assertEqual(self.bank['mh2.8.3.1-q1']['family_id'], self.bank['mh2.8.3.1-q101']['family_id'])
        for tid in TOPICS:
            c = self.cards[tid]
            self.assertTrue(c['worked_example']['family_id'])
            for row in c['objective_coverage']:
                self.assertNotEqual(row['self_check']['representation'], 'unknown')
                self.assertTrue(row['self_check']['family_id'])

    def test_all_six_cards_and_their_actual_source_tasks_satisfy_the_contracts(self):
        for tid, c in self.cards.items():
            self.assertEqual(validate_resource('teaching-card', c), [])
            self.assertEqual(check_card(c, self.topics, self.bank), [])
            for row in c['objective_coverage']:
                self.assertTrue(row['observation_note'])
                self.assertTrue(row['difficulty_support']['discriminating_prompt'])
        tasks = [q for q in self.bank.values() if q['topicId'] in TOPICS and not q['id'].endswith('-q9')]
        self.assertEqual(len(tasks), 42)
        for q in tasks:
            self.assertEqual(validate_resource('question', q), [], q['id'])
            self.assertFalse(q['diagnosis_eligible'])
            resolved = self.task(q['id'])
            self.assertEqual(grade_answer(resolved, q['answer'])['status'], 'correct', q['id'])


if __name__ == '__main__':
    unittest.main()
