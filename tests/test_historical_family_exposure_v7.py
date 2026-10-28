"""Classifying an old task cannot erase exposure or manufacture mastery evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg import engine, task_families
from cnkg.learning_needs import skill_evidence, task_options
from cnkg.learning_store import LearningStore
from cnkg.review_timing import indexed_scope_activity, unit_activity_scope
from tests.test_quality_v5_decisions import catalog, record, helped_history, later_observation, followup


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class HistoricalExposureTests(unittest.TestCase):
    def test_unknown_family_question_index_still_blocks_novelty_help_and_quiet_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / 'learning.sqlite')
            event = {'event_type': 'reference_opened', 'occurred_at': '2026-10-28T00:00:00+00:00',
                     'question_id': 'p', 'family_id': 'unclassified:p', 'family_status': 'unclassified',
                     'resource_id': 'legacy-card', 'payload': {}}
            store.add_event('owner', event)
            before = copy.deepcopy(store.events('owner'))
            original_index = copy.deepcopy(store.exposures('owner'))
            # Existing indexes intentionally omitted an unknown family. Retain
            # the original question index rather than inserting a fake family.
            self.assertEqual({}, original_index['families'])
            with patch.dict(task_families.HISTORICAL_FAMILIES, {'unclassified:p': 'new-family'}):
                option = task_options(catalog(), ['compare'], [], original_index, allowed_task_ids={'d'})[0]
                self.assertFalse(option['repeated_question'])
                self.assertTrue(option['repeated_family'])
                self.assertTrue(option['help_exposed'])
                self.assertFalse(option['eligible_for_followup'])
                scope = {'question_ids': [], 'family_ids': ['new-family']}
                self.assertEqual(event['occurred_at'], indexed_scope_activity(scope, original_index)['occurred_at'])
                self.assertEqual(event['occurred_at'], store.latest_scope_activity(
                    'owner', [], question_ids=[], family_ids=['new-family'])['occurred_at'])
                self.assertIsNone(store.latest_scope_activity('another-owner', [], family_ids=['new-family']))
                # The low-level query's [] = no questions contract is intact.
                self.assertEqual({'questions': {}, 'families': {}},
                                 store.exposures('owner', question_ids=[], family_ids=[]))
                self.assertIsNone(store.latest_scope_activity('owner', [], question_ids=[], family_ids=[]))
            self.assertEqual(before, store.events('owner'))
            self.assertEqual(original_index, store.exposures('owner'))

    def test_old_answer_blocks_repeat_but_remains_unclassified_and_unmapped(self):
        old = record(status='correct')
        old['answers'][0].update(family_id='unclassified:p', family_status='unclassified', skill_ids=[])
        before = copy.deepcopy(old)
        with patch.dict(task_families.HISTORICAL_FAMILIES, {'unclassified:p': 'new-family'}):
            option = task_options(catalog(), ['compare'], [old], allowed_task_ids={'d'})[0]
            self.assertTrue(option['repeated_family'])
            self.assertFalse(option['eligible_for_followup'])
            self.assertEqual([], skill_evidence(catalog(), [old], []))
            self.assertEqual('unclassified:p', task_families.canonical_family('unclassified:p'))
            self.assertIsNone(engine._declared_family(old['answers'][0]))
            self.assertEqual(['p'], task_families.historical_question_ids(['new-family']))
        self.assertEqual(before, old)

    def test_historical_and_current_name_counters_are_not_added(self):
        with patch.dict(task_families.HISTORICAL_FAMILIES, {'unclassified:p': 'new-family'}):
            counts = task_families.family_exposure({'new-family': {'reference_count': 1},
                                                  'unclassified:p': {'reference_count': 1}},
                                                 'new-family', questions={'p': {'reference_count': 1}})
            self.assertEqual(1, counts['reference_count'])
            self.assertEqual('maximum_per_indexed_name', counts['count_basis'])
            data = catalog()
            scope = unit_activity_scope(data['units']['u'], data)
            self.assertIn('p', scope['question_ids'])

    def test_historical_identity_cannot_close_an_old_different_family_followup(self):
        for legacy_family in ('unclassified:p', 'unknown', None):
            with self.subTest(legacy_family=legacy_family):
                history = helped_history()
                original = history[0]['answers'][0]
                original['family_status'] = 'unclassified'
                if legacy_family is None:
                    original.pop('family_id')
                else:
                    original['family_id'] = legacy_family
                # The old record already knew this target. The history ledger
                # must not supply any additional skill or positive observation.
                self.assertEqual(['compare'], original['skill_ids'])
                later = later_observation(history)
                records = [*history, later]
                saved = copy.deepcopy(records)
                evidence = skill_evidence(catalog(), records, [])
                self.assertTrue(later['answers'][0]['followup_basis']['eligible'])
                self.assertEqual('resolved', followup(records)['status'])
                with patch.dict(task_families.HISTORICAL_FAMILIES,
                                {'unclassified:p': 'new-family'}, clear=True):
                    current = followup(records)
                    self.assertEqual('open', current['status'])
                    self.assertIsNone(current['resolution'])
                    self.assertEqual(['compare'], current['remaining_skill_ids'])
                    self.assertEqual(legacy_family, current['family_id'])
                    self.assertEqual(evidence, skill_evidence(catalog(), records, []))
                    self.assertIsNone(engine._declared_family(original))
                # The exclusion applies to the reviewed same structure only.
                with patch.dict(task_families.HISTORICAL_FAMILIES,
                                {'unclassified:p': 'another-family'}, clear=True):
                    self.assertEqual('resolved', followup(records)['status'])
                self.assertEqual(saved, records)
                self.assertEqual('wrong', original['status'])

    def test_registry_binds_task_answer_contract_and_review_without_relaxing_normal_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'data/question_sources/application.json'
            evidence = root / 'docs/reviews/review.json'
            binding_path = root / 'data/skills/catalog-bindings.json'
            for path in (source, evidence, binding_path):
                path.parent.mkdir(parents=True, exist_ok=True)
            question = {'id': 'p', 'topicId': 't', 'type': 'numeric', 'prompt': '2+3',
                        'answer': '5', 'family_id': 'new-family'}
            binding = {'family_id': 'new-family', 'answer_spec': {
                'version': 1, 'kind': 'number', 'comparison': 'exact', 'representation': 'any'}}
            source.write_text(json.dumps({'questions': [question]}))
            binding_path.write_text(json.dumps({'tasks': {'bank:p': binding}}))
            baseline_question = {**question, 'family_id': 'unclassified:p'}
            row = {'alias': 'unclassified:p', 'canonical': 'new-family', 'task_id': 'p',
                   'source': source.relative_to(root).as_posix(), 'review_evidence': evidence.relative_to(root).as_posix(),
                   'baseline_commit': 'a' * 40, 'baseline_source_task_sha256': digest(baseline_question),
                   'current_source_task_sha256': digest(question),
                   'baseline_task_contract_sha256': digest(task_families.task_contract(baseline_question, binding)),
                   'current_task_contract_sha256': digest(task_families.task_contract(question, binding)),
                   'alias_kind': 'reviewed_historical_task_identity', 'use': 'exposure_only',
                   'reason': '同一两部分合计结构，仅保留历史曝光限制。',
                   'reviewer_kind': 'ai', 'independent_review': False}
            hash_keys = ('baseline_source_task_sha256', 'current_source_task_sha256',
                         'baseline_task_contract_sha256', 'current_task_contract_sha256')
            reviewed_task = {'id': 'p', 'reviewer_kind': 'ai', 'independent_teacher_review': False,
                **{key: row[key] for key in hash_keys},
                'history_exposure_review': {'decision': 'allow_same_structure',
                    'reason': '原题和当前题均把2与3两部分相加求总数，只有题族元数据变化。'}}
            review = {'baseline_commit': row['baseline_commit'], 'tasks': [reviewed_task]}
            evidence.write_text(json.dumps(review))
            row['review_evidence_sha256'] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            path = root / 'history.json'
            def write(value):
                path.write_text(json.dumps({'schema': 1, 'aliases': [value]}))
            write(row)
            self.assertEqual({'unclassified:p': 'new-family'}, task_families.load_history(path, root=root))
            with self.assertRaises(ValueError):
                task_families.load_aliases(path)
            for change in ({'use': 'mastery_evidence'}, {'task_id': 'another'}, {'reason': ''},
                           {'current_source_task_sha256': '0' * 64},
                           {'current_task_contract_sha256': '0' * 64},
                           {'review_evidence_sha256': '0' * 64}, {'canonical': 'another-family'}):
                with self.subTest(change=change):
                    write({**row, **change})
                    with self.assertRaises(ValueError):
                        task_families.load_history(path, root=root)

            # Recompute the enclosing evidence-file hash, so each rejection
            # exercises the per-task binding rather than a stale file digest.
            bad_reviews = [('baseline_commit', {**review, 'baseline_commit': 'c' * 40}),
                           ('missing_task', {**review, 'tasks': []}),
                           ('duplicate_id', {**review, 'tasks': [reviewed_task, copy.deepcopy(reviewed_task)]})]
            for change in ({'reviewer_kind': 'unreviewed'}, {'independent_teacher_review': True},
                           {'history_exposure_review': None},
                           {'history_exposure_review': {'reason': '尚未作出允许同结构比较的决定。'}},
                           {'history_exposure_review': {'decision': 'allow_same_structure', 'reason': ''}},
                           *({'history_exposure_review': {'decision': decision,
                                'reason': '逐题审查未允许同结构比较，运行时不得自行补判。'}}
                             for decision in ('exclude_changed_structure', 'not_applicable')),
                           *({key: '0' * 64} for key in hash_keys)):
                bad_reviews.append((str(change), {**review, 'tasks': [{**reviewed_task, **change}]}))
            for label, bad_review in bad_reviews:
                with self.subTest(evidence=label):
                    evidence.write_text(json.dumps(bad_review))
                    write({**row, 'review_evidence_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()})
                    with self.assertRaises(ValueError):
                        task_families.load_history(path, root=root)

            # Contract changes alone do not decide mathematical equivalence.
            # An explicit exclusion must block a correctly fingerprinted old
            # multiplication task, while a reviewed number variant may pass.
            for old_prompt, decision, reason in (
                    ('2*3', 'exclude_changed_structure', '旧题由两个因数求积，当前题由两部分求和；结构替换，排除。'),
                    ('2+4', 'allow_same_structure', '原题2+4与当前2+3均给出两部分求总数；仅第二部分数值变化，旧帮助仍限制新鲜性。')):
                with self.subTest(old_prompt=old_prompt, decision=decision):
                    old_variant = {**baseline_question, 'prompt': old_prompt, 'answer': '6'}
                    variant_row = {**row, 'baseline_source_task_sha256': digest(old_variant),
                        'baseline_task_contract_sha256': digest(task_families.task_contract(old_variant, binding))}
                    variant_review = {**review, 'tasks': [{**reviewed_task,
                        **{key: variant_row[key] for key in hash_keys},
                        'history_exposure_review': {'decision': decision, 'reason': reason}}]}
                    evidence.write_text(json.dumps(variant_review))
                    variant_row['review_evidence_sha256'] = hashlib.sha256(evidence.read_bytes()).hexdigest()
                    write(variant_row)
                    self.assertNotEqual(variant_row['baseline_task_contract_sha256'], variant_row['current_task_contract_sha256'])
                    if decision == 'allow_same_structure':
                        self.assertEqual({'unclassified:p': 'new-family'}, task_families.load_history(path, root=root))
                    else:
                        with self.assertRaisesRegex(ValueError, '明确允许'):
                            task_families.load_history(path, root=root)

            evidence.write_text(json.dumps(review))
            write(row)
            binding_path.write_text(json.dumps({'tasks': {'bank:p': {**binding, 'answer_spec': {'kind': 'text'}}}}))
            with self.assertRaisesRegex(ValueError, '判分合同'):
                task_families.load_history(path, root=root)


if __name__ == '__main__':
    unittest.main()
