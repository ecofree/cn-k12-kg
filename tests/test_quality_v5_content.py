"""Content counterexamples: topic alignment must reflect what a task observes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from xml.etree import ElementTree

from cnkg.answers import grade_answer
from cnkg.catalog import Catalog, CatalogError, compile_catalog
from tools.rebuild_data import build_data
from tools.register_catalog import register_new_sources
from tools.teach_cards import check_card

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
TOPICS = {'m1a.1.1', 'mh2.8.3.1'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class QualityV5ContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.data = cls.root / 'data'
        # Build actual authoring sources in memory, so a stale derived catalog
        # cannot accidentally let this regression pass.
        rows, _ = build_data(DATA)
        topics = [t for t in rows['topics.json'] if t['id'] in TOPICS]
        questions = [q for q in rows['questions.json'] if q['topicId'] in TOPICS]
        qids = {q['id'] for q in questions}
        registry = read(DATA / 'skills/registry.json')
        registry['placements'] = {tid: p for tid, p in registry['placements'].items() if tid in TOPICS}
        ids = {sid for p in registry['placements'].values() for sid in p['criterion_skill_ids']}
        registry['skills'] = {sid: s for sid, s in registry['skills'].items() if sid in ids}
        registry['objective_skill_ids'] = {}
        bindings = read(DATA / 'skills/catalog-bindings.json')
        bindings['tasks'] = {k: v for k, v in bindings['tasks'].items()
                             if (k.startswith('bank:') and v['id'] in qids)
                             or any(k.startswith('self_check:' + tid + ':') for tid in TOPICS)}
        bindings['examples'] = {k: v for k, v in bindings['examples'].items()
                                if k in {'example:topic:' + tid for tid in TOPICS}}
        admissions = read(DATA / 'task_admissions.json')
        admissions['tasks'] = {qid: v for qid, v in admissions['tasks'].items() if qid in qids}
        for name, value in [('topics.json', topics), ('questions.json', questions),
                            ('skills/registry.json', registry), ('skills/catalog-bindings.json', bindings),
                            ('task_admissions.json', admissions)]:
            write(cls.data / name, value)
        for tid in TOPICS:
            write(cls.data / f'teach/{tid}.json', read(DATA / f'teach/{tid}.json'))
        shutil.copytree(ROOT / 'web/static/diagrams', cls.root / 'web/static/diagrams')
        cls.catalog = Catalog(compile_catalog(cls.data))
        cls.topics = {t['id']: t for t in topics}
        cls.bank = {q['id']: q for q in questions}

    def test_frustum_is_not_evidence_for_prism_pyramid_legacy_goal(self):
        p = self.catalog.placement('mh2.8.3.1')['criterion_skill_ids']
        q = self.catalog.task('mh2.8.3.1-q204')
        self.assertEqual(q['skill_ids'], [p[5]])
        self.assertNotEqual(p[0], p[5])
        self.assertNotIn(p[0], self.catalog.skill(p[5])['refines_skill_ids'])
        self.assertIn('平行', q['prompt'])
        self.assertEqual(grade_answer(q, '28')['status'], 'correct')
        # Averaging the base areas gives 30, a plausible but wrong strategy.
        self.assertEqual(grade_answer(q, '30')['status'], 'wrong')
        # Independently reconstruct the two similar pyramids: areas 4/16,
        # height ratio 1/2, height difference 3 -> heights 3 and 6.
        self.assertEqual(16 * 6 / 3 - 4 * 3 / 3, 28)

    def test_existing_item_meanings_are_not_guessed_from_question_numbers(self):
        p = self.catalog.placement('mh2.8.3.1')['criterion_skill_ids']
        # These are independent semantic expectations from the actual prompts,
        # not from whichever mapping the source currently declares.
        expectations = {
            'q201': ('棱锥', [p[4]], 'solid.pyramid.base-height-volume', '60'),
            'q202': ('长方体', [p[1]], 'solid.cuboid.surface-area', '62'),
            'q203': ('正四棱锥', [p[1]], 'solid.regular-pyramid.surface-area', '96'),
            'q204': ('棱台', [p[5]], 'solid.frustum.parallel-cut-volume', '28'),
            'q305': ('斜棱柱', sorted([p[2], p[3]]), 'solid.prism.base-height-volume', '126'),
        }
        for suffix, (shape, skills, family, answer) in expectations.items():
            q = self.catalog.task('mh2.8.3.1-' + suffix)
            self.assertIn(shape, q['prompt'])
            self.assertEqual(q['skill_ids'], skills)
            self.assertEqual(q['family_id'], family)
            self.assertEqual(grade_answer(q, answer)['status'], 'correct')
        self.assertEqual(2 * (5 * 3 + 5 * 2 + 3 * 2), 62)
        self.assertEqual(30 * 6 / 3, 60)
        self.assertEqual(18 * 7, 126)

    def test_old_ids_retained_and_child_goals_do_not_replace_parents(self):
        p = self.catalog.placement('m1a.1.1')['criterion_skill_ids']
        self.assertEqual(p[:2], ['sk_8d5fd54e49d34c4b8e33', 'sk_5f4308dc9f044e51b71e'])
        self.assertEqual(self.catalog.skill(p[1])['statement'], '能认、读、写 1～5')
        for i in (2, 3, 4):
            self.assertEqual(self.catalog.skill(p[i])['granularity'], 'observable')
            self.assertIn(p[1], self.catalog.skill(p[i])['refines_skill_ids'])
        g = self.catalog.placement('mh2.8.3.1')['criterion_skill_ids']
        self.assertIn(g[0], self.catalog.skill(g[3])['refines_skill_ids'])
        self.assertIn(g[0], self.catalog.skill(g[4])['refines_skill_ids'])

    def test_copy_and_successor_items_cannot_confirm_composite_mastery(self):
        p = self.catalog.placement('m1a.1.1')['criterion_skill_ids']
        for qid, index in [('m1a.1.1-q1', 0), ('m1a.1.1-q2', 5)]:
            q = self.catalog.task(qid)
            self.assertTrue(q['policy']['practice_eligible'])
            self.assertFalse(q['policy']['diagnosis_eligible'])
            self.assertFalse(q['policy']['may_confirm_mastery'])
            self.assertFalse(q['policy']['may_judge_weak'])
            self.assertEqual(q['skill_ids'], [p[index]])
            self.assertTrue({p[1], p[3], p[4]}.isdisjoint(q['skill_ids']))
            history = read(self.data / 'task_admissions.json')['tasks'][qid]['previous_decisions']
            self.assertTrue(history[0]['policy']['diagnosis_eligible'])
        self.assertEqual(self.catalog.task('m1a.1.1-q1')['prompt'].count('●'), 3)
        self.assertEqual(self.catalog.skill(p[5])['statement'], '能确定1至4各数在1至5数序中的后继数')
        self.assertEqual(self.catalog.skill(p[5])['refines_skill_ids'], [])

    def test_actual_dot_assets_and_matching_option_agree_with_answers(self):
        ns = {'s': 'http://www.w3.org/2000/svg'}
        for qid in ('m1a.1.1-q301', 'm1a.1.1-q303'):
            q = self.catalog.task(qid)
            svg = ElementTree.parse(self.root / 'web' / q['diagram']['src'].lstrip('/'))
            self.assertEqual(len(svg.findall('.//s:circle', ns)), int(q['answer']))
            self.assertNotIn('5', q['prompt'])
            self.assertFalse(q['policy']['may_confirm_mastery'])
            self.assertFalse(q['policy']['independent_evidence'])
        q = self.catalog.task('m1a.1.1-q302')
        svg = ElementTree.parse(self.root / 'web' / q['diagram']['src'].lstrip('/'))
        counts = [sum(left < float(c.attrib['cx']) < right for c in svg.findall('.//s:circle', ns))
                  for left, right in ((10, 210), (230, 430), (450, 650))]
        self.assertEqual(counts, [3, 4, 5])
        self.assertEqual(next(c['text'] for c in q['choices'] if c['id'] == q['answer']), '乙组')

    def test_rearranged_dots_remain_same_family_and_never_cover_speech_or_handwriting(self):
        a, b = [self.catalog.task('m1a.1.1-' + q) for q in ('q301', 'q303')]
        self.assertEqual(a['family_id'], b['family_id'])
        p = self.catalog.placement('m1a.1.1')['criterion_skill_ids']
        for q in (a, b, self.catalog.task('m1a.1.1-q302')):
            self.assertNotIn(p[3], q['skill_ids'])
            self.assertNotIn(p[4], q['skill_ids'])
        for i in (3, 4):
            self.assertEqual(self.catalog.self_check('m1a.1.1', i)['type'], 'rubric')

    def test_registration_preserves_explicit_observable_topic_goals(self):
        before = read(self.data / 'skills/registry.json')
        registry, _, changes = register_new_sources(self.data)
        self.assertEqual(registry, before)
        self.assertFalse(any(changes.values()))
        # Reordering is still prohibited; supporting explicit granularity must
        # not turn registration into a title-based identity migration.
        topics = read(self.data / 'topics.json')
        topics[0]['mastery_criteria'][2:4] = reversed(topics[0]['mastery_criteria'][2:4])
        original = read(self.data / 'topics.json')
        try:
            write(self.data / 'topics.json', topics)
            with self.assertRaises(CatalogError):
                register_new_sources(self.data)
        finally:
            write(self.data / 'topics.json', original)

    def test_every_promised_goal_has_its_own_observation_and_revision(self):
        for tid in TOPICS:
            card = read(self.data / f'teach/{tid}.json')
            self.assertEqual(check_card(card, self.topics, self.bank), [])
            self.assertEqual(len(card['objective_coverage']), len(self.topics[tid]['mastery_criteria']))
            for goal in card['objective_coverage']:
                self.assertTrue(goal['observation_note'])
                self.assertEqual(set(goal['difficulty_support']),
                                 {'discriminating_prompt', 'hint', 'revision_prompt'})
                self.assertGreaterEqual(len(goal['self_check']['criteria']), 2)


if __name__ == '__main__':
    unittest.main()
