"""Counterexamples for exact, bounded equation feedback and durable activity records."""
from concurrent.futures import ThreadPoolExecutor
import copy
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg import step_practice as steps
from cnkg.learning_store import LearningStore, ConflictError
from cnkg.profile_recovery import ProfileRecovery

VERSION = 'a' * 64
OWNER = 'b' * 64
OTHER = 'c' * 64


class LinearStepMathematicsTests(unittest.TestCase):
    def test_two_different_legal_routes_preserve_the_same_unique_solution(self):
        paths = [
            [('3(x-2)=12', '3x-6=12', 'simplify', ''), ('3x-6=12', '3x=18', 'add', '6'), ('3x=18', 'x=6', 'divide', '3')],
            [('3(x-2)=12', 'x-2=4', 'divide', '3'), ('x-2=4', 'x=6', 'add', '2')],
        ]
        for path in paths:
            for before, after, operation, operand in path:
                result = steps.check_step(before, after, operation, operand)
                self.assertTrue(result['accepted'], result)
                self.assertEqual(result['operation_status'], 'verified')
                self.assertEqual(steps.solution(steps.equation(after)), ('unique', Fraction(6)))
                self.assertFalse(result['may_confirm_mastery'])

    def test_equivalent_equation_is_distinct_from_a_false_claim_of_simplification(self):
        result = steps.check_step('3(x-2)=12', 'x=6', 'simplify')
        self.assertEqual(result['equivalence'], 'same')
        self.assertEqual(result['status'], 'reason_mismatch')
        self.assertFalse(result['accepted'])
        result = steps.check_step('3(x-2)=12', 'x=6', 'equivalent')
        self.assertTrue(result['accepted'])
        self.assertEqual(result['operation_status'], 'unexplained')
        self.assertIn('理由仍未说明', result['feedback'])

    def test_multiply_zero_loses_a_unique_constraint_and_uses_a_real_counterexample(self):
        result = steps.check_step('x=3', '0=0', 'multiply', '0')
        self.assertEqual(result['status'], 'non_equivalent')
        self.assertEqual(result['equivalence'], 'different')
        self.assertIn('x=0', result['feedback'])
        self.assertFalse(result['accepted'])
        # When both sides were already identical, the solution sets really are equal.
        identity = steps.check_step('2x+6=2x+6', '0=0', 'multiply', '0')
        self.assertEqual(identity['equivalence'], 'same')
        self.assertEqual(identity['operation_status'], 'unsafe')
        self.assertIn('本例新旧恰好都是恒等式', identity['feedback'])

    def test_zero_coefficient_distinguishes_no_solution_from_identity(self):
        self.assertEqual(steps.solution(steps.equation('2(x+3)=2x+7')), ('none', None))
        self.assertEqual(steps.solution(steps.equation('2(x+3)=2x+6')), ('all', None))
        self.assertTrue(steps.check_step('2x+6=2x+7', '0=1', 'subtract', '2x+6')['accepted'])
        self.assertTrue(steps.check_step('2x+6=2x+6', '0=0', 'subtract', '2x+6')['accepted'])
        self.assertEqual(steps.check_conclusion('2(x+3)=2x+7', 'none', '')['status'], 'conclusion_correct')
        self.assertEqual(steps.check_conclusion('2(x+3)=2x+6', 'all', '')['status'], 'conclusion_correct')
        self.assertEqual(steps.check_conclusion('2(x+3)=2x+6', 'unique', '0')['status'], 'conclusion_incorrect')

    def test_nonlinear_or_unsafe_syntax_is_unsupported_even_when_it_cancels(self):
        cases = ['x*x=36', 'x^2-x^2+x=6', '1/x=1/6', 'x^0=1', 'y=6',
                 '__import__("os").system("false")=0', 'sin(x)=0', 'x=6=6', 'x/0=0']
        for raw in cases:
            with self.subTest(raw=raw):
                result = steps.check_step('x=6', raw)
                self.assertEqual(result['status'], 'unsupported')
                self.assertEqual(result['equivalence'], 'not_checked')
                self.assertFalse(result['accepted'])
        for operand in ['x', 'x-6']:
            result = steps.check_step('2x=12', '2=12/x', 'divide', operand)
            self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(steps.check_step('2x=12', 'x=6', 'divide', 'x')['operation_status'], 'outside_scope')

    def test_zero_division_and_missed_distribution_get_specific_feedback(self):
        bad = steps.check_step('3(x-2)=12', '3x-2=12', 'simplify')
        self.assertEqual(bad['equivalence'], 'different')
        self.assertIn('每一项', bad['feedback'])
        self.assertIn('(3)x−6', bad['feedback'])
        zero = steps.check_step('0=0', '0=0', 'divide', '0')
        self.assertIn('不能除以0', zero['feedback'])
        self.assertFalse(zero['accepted'])

    def test_exact_rational_numbers_do_not_accept_close_decimals(self):
        self.assertTrue(steps.check_step('3x=1', 'x=1/3', 'divide', '3')['accepted'])
        self.assertEqual(steps.check_step('3x=1', 'x=0.333333333')['equivalence'], 'different')
        self.assertTrue(steps.check_step('0.1x=0.3', 'x=3', 'divide', '0.1')['accepted'])
        self.assertTrue(steps.check_step('x/2+1=3', 'x+2=6', 'multiply', '2')['accepted'])

    def test_swap_and_same_linear_addend_are_checked_on_both_sides(self):
        self.assertTrue(steps.check_step('2x+1=x+4', 'x+4=2x+1', 'swap')['accepted'])
        self.assertTrue(steps.check_step('2x+1=x+4', 'x+1=4', 'subtract', 'x')['accepted'])
        self.assertFalse(steps.check_step('2x+1=x+4', 'x+1=x+4', 'subtract', 'x')['accepted'])


class StepActivityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'original.sqlite'
        self.store = LearningStore(self.path)
        self.start_values = {'unit_id': steps.UNIT_ID, 'case_id': 'one_solution', 'expected_resource_version': VERSION, 'request_id': 'start-1'}
        self.state = steps.start(self.store, OWNER, self.start_values, VERSION)

    def request(self, **changes):
        return {'activity_id': self.state['id'], 'expected_revision': self.state['revision'], 'request_id': 'input-' + str(self.state['revision']),
                'kind': 'equation', 'equation': '3x-6=12', 'operation': 'simplify', 'operand': '', 'used_help': False, **changes}

    def draft_request(self, **changes):
        return {'activity_id': self.state['id'], 'expected_revision': self.state['revision'],
                'request_id': 'draft-' + str(self.state['revision']), 'draft': {**steps.DRAFT_DEFAULTS, **changes}}

    def test_incomplete_draft_is_literal_data_without_math_judgment_or_attempt(self):
        request = self.draft_request(equation=' 3(x− ', operand='(', value='1/')
        with patch.object(steps, 'check_step', side_effect=AssertionError('a draft must not be graded')), \
             patch.object(steps, 'check_conclusion', side_effect=AssertionError('a draft must not be graded')):
            self.state = steps.save_draft(self.store, OWNER, request, VERSION)
        self.assertEqual(self.state['draft'], request['draft'])
        self.assertEqual(self.state['current_equation'], '3(x−2)=12')
        self.assertEqual(self.state['timeline'], [])
        self.assertNotIn('feedback', self.state)
        self.assertFalse(self.state['completed'])
        self.assertEqual(steps.listing(self.store, OWNER, steps.UNIT_ID, VERSION)['activities'][0]['attempt_count'], 0)
        restarted = LearningStore(self.path)
        self.assertEqual(steps.load(restarted, OWNER, self.state['id'], VERSION)['draft'], request['draft'])

    def test_draft_owner_revision_idempotence_and_real_edit_time(self):
        request = self.draft_request(equation='x+')
        with self.assertRaises(KeyError):
            steps.save_draft(self.store, OTHER, request, VERSION)
        self.state = steps.save_draft(self.store, OWNER, request, VERSION, now='2026-10-29T14:00:00+00:00')
        before = self.store.events(OWNER)
        self.assertEqual(steps.save_draft(self.store, OWNER, request, VERSION), self.state)
        self.assertEqual(self.store.events(OWNER), before)
        with self.assertRaises(ConflictError):
            steps.save_draft(self.store, OWNER, {**request, 'draft': {**request['draft'], 'equation': 'x-'}}, VERSION)
        with self.assertRaises(ConflictError):
            steps.save_draft(self.store, OWNER, {**request, 'request_id': 'stale'}, VERSION)
        self.assertEqual(self.store.latest_scope_activity(OWNER, ['unit:' + steps.UNIT_ID])['occurred_at'], '2026-10-29T14:00:00+00:00')
        self.assertFalse({e['event_type'] for e in before} & {'answer_submitted', 'learning_need_resolved', 'step_practice_answered'})
        invalid = self.draft_request(equation='x' * 257)
        with self.assertRaises(ValueError):
            steps.save_draft(self.store, OWNER, invalid, VERSION)
        with patch.object(steps, 'rules_version', return_value='f' * 64), self.assertRaises(ConflictError):
            steps.save_draft(self.store, OWNER, self.draft_request(equation='x-'), VERSION)

    def test_bad_step_does_not_replace_last_valid_line_and_raw_input_is_preserved(self):
        raw = ' 3x−2 = 12 '
        self.state = steps.submit(self.store, OWNER, self.request(equation=raw), VERSION)
        self.assertEqual(self.state['timeline'][0]['equation'], raw)
        self.assertEqual(self.state['current_equation'], '3(x−2)=12')
        self.assertEqual(self.state['timeline'][0]['feedback']['status'], 'non_equivalent')
        self.state = steps.submit(self.store, OWNER, self.request(), VERSION)
        self.assertEqual(self.state['current_equation'], '3x-6=12')
        self.assertTrue(self.state['timeline'][1]['help_used'])
        self.assertEqual(len(self.state['timeline']), 2)
        self.assertEqual(steps.load(LearningStore(self.path), OWNER, self.state['id'], VERSION)['timeline'], self.state['timeline'])

    def test_idempotent_retry_and_stale_revision_never_duplicate_or_overwrite(self):
        req = self.request()
        self.state = steps.submit(self.store, OWNER, req, VERSION)
        repeated = steps.submit(self.store, OWNER, req, VERSION)
        self.assertEqual(repeated['revision'], self.state['revision'])
        self.assertEqual(repeated['timeline'], self.state['timeline'])
        with self.assertRaises(ConflictError):
            steps.submit(self.store, OWNER, {**req, 'equation': 'x=6'}, VERSION)
        with self.assertRaises(ConflictError):
            steps.submit(self.store, OWNER, {**req, 'request_id': 'new-stale'}, VERSION)
        self.assertEqual(steps.start(self.store, OWNER, self.start_values, VERSION)['id'], self.state['id'])
        with self.assertRaises(ConflictError):
            steps.start(self.store, OWNER, {**self.start_values, 'case_id': 'identity'}, VERSION)

    def test_two_concurrent_different_requests_cannot_share_the_same_revision(self):
        req = self.request()
        def send(i):
            try:
                steps.submit(self.store, OWNER, {**req, 'request_id': 'concurrent-' + str(i)}, VERSION)
                return 'saved'
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(send, (1, 2))), ['saved', 'conflict'])
        self.assertEqual(len(steps.load(self.store, OWNER, self.state['id'], VERSION)['timeline']), 1)

    def test_owner_isolation_and_history_pagination(self):
        with self.assertRaises(KeyError):
            steps.load(self.store, OTHER, self.state['id'], VERSION)
        with self.assertRaises(KeyError):
            steps.submit(self.store, OTHER, self.request(), VERSION)
        other = steps.start(self.store, OTHER, self.start_values, VERSION)
        self.assertNotEqual(other['id'], self.state['id'])
        steps.start(self.store, OWNER, {**self.start_values, 'request_id': 'start-2'}, VERSION)
        first = steps.listing(self.store, OWNER, steps.UNIT_ID, VERSION, limit=1)
        second = steps.listing(self.store, OWNER, steps.UNIT_ID, VERSION, offset=1, limit=1)
        self.assertEqual(first['total'], 2)
        self.assertNotEqual(first['activities'][0]['id'], second['activities'][0]['id'])

    def test_encrypted_backup_to_new_environment_preserves_inputs_hints_feedback_and_rules(self):
        self.state = steps.submit(self.store, OWNER, self.request(equation='3x-2=12'), VERSION)
        self.state = steps.submit(self.store, OWNER, {'activity_id': self.state['id'], 'expected_revision': self.state['revision'], 'request_id': 'hint-1', 'level': 2}, VERSION, hint=True)
        self.state = steps.submit(self.store, OWNER, self.request(), VERSION)
        self.state = steps.save_draft(self.store, OWNER, self.draft_request(equation=' 3x+ ', operation='add', operand='6'), VERSION)
        frozen = steps.load(self.store, OWNER, self.state['id'], VERSION)
        profile = ProfileRecovery(self.store)
        credential = profile.enable(OWNER, '分步原档案')['recovery_code']
        envelope = profile.backup(OWNER, credential)
        restored_store = LearningStore(Path(self.tmp.name) / 'new-environment.sqlite')
        ProfileRecovery(restored_store).restore(envelope, credential)
        restored = steps.load(restored_store, OWNER, self.state['id'], VERSION)
        self.assertEqual(restored, frozen)
        self.assertEqual(restored['timeline'][0]['equation'], '3x-2=12')
        self.assertEqual(restored['timeline'][1]['kind'], 'hint')
        self.assertEqual(restored['draft']['equation'], ' 3x+ ')
        self.assertEqual(restored['rules_snapshot']['parser_sha256'], frozen['rules_snapshot']['parser_sha256'])

    def test_content_and_rule_updates_archive_without_recomputing_old_feedback(self):
        self.state = steps.submit(self.store, OWNER, self.request(equation='x/0=1'), VERSION)
        before = steps.load(self.store, OWNER, self.state['id'], VERSION)
        with patch.object(steps, 'check_step', side_effect=AssertionError('must not regrade history')):
            changed_content = steps.load(self.store, OWNER, self.state['id'], 'd' * 64)
            self.assertTrue(changed_content['read_only'])
            self.assertEqual(changed_content['timeline'], before['timeline'])
            with patch.object(steps, 'rules_version', return_value='f' * 64):
                changed_rules = steps.load(self.store, OWNER, self.state['id'], VERSION)
                self.assertTrue(changed_rules['read_only'])
                with self.assertRaises(ConflictError):
                    steps.submit(self.store, OWNER, self.request(), VERSION)
        self.assertEqual(steps.load(self.store, OWNER, self.state['id'], VERSION)['timeline'], before['timeline'])

    def test_reopening_feedback_updates_the_real_unit_activity_but_never_emits_mastery(self):
        self.state = steps.submit(self.store, OWNER, self.request(), VERSION, now='2026-10-27T10:00:00+00:00')
        with patch.object(steps, '_now', return_value='2026-10-29T12:00:00+00:00'):
            steps.load(self.store, OWNER, self.state['id'], VERSION, expose=True)
        latest = self.store.latest_scope_activity(OWNER, ['unit:' + steps.UNIT_ID])
        self.assertEqual(latest['occurred_at'], '2026-10-29T12:00:00+00:00')
        self.assertEqual(latest['event_type'], 'reference_opened')
        types = {e['event_type'] for e in self.store.events(OWNER)}
        self.assertFalse(types & {'answer_submitted', 'learning_need_resolved', 'session_completed'})
        self.assertEqual(self.store.count(OWNER), 0)
        self.assertFalse(self.state['independent_evidence'])

    def test_actual_error_method_feedback_exposes_its_family_across_history_reopening(self):
        self.state = steps.submit(self.store, OWNER, self.request(equation='0=0', operation='multiply', operand='0'), VERSION)
        self.assertIn('equation.multiply-zero-error', self.state['timeline'][0]['reference_family_ids'])
        exposed = self.store.exposures(OWNER, family_ids=['equation.multiply-zero-error'])['families']
        self.assertEqual(exposed['equation.multiply-zero-error']['reference_count'], 1)
        self.state = steps.submit(self.store, OWNER, self.request(equation='x=6', operation='divide', operand='x'), VERSION)
        self.assertIn('equation.divide-variable-zero', self.state['timeline'][1]['reference_family_ids'])
        steps.load(self.store, OWNER, self.state['id'], VERSION, expose=True)
        exposed = self.store.exposures(OWNER, family_ids=['equation.multiply-zero-error', 'equation.divide-variable-zero'])['families']
        self.assertEqual(exposed['equation.multiply-zero-error']['reference_count'], 3)
        self.assertEqual(exposed['equation.divide-variable-zero']['reference_count'], 2)

    def test_replaying_an_old_request_reexposes_feedback_without_duplicating_attempts(self):
        request = self.request()
        result = steps.submit(self.store, OWNER, request, VERSION, now='2026-10-27T10:00:00+00:00')
        repeated = steps.submit(self.store, OWNER, request, VERSION, now='2026-10-30T10:00:00+00:00')
        self.assertEqual(repeated['timeline'], result['timeline'])
        self.assertEqual(repeated['revision'], result['revision'])
        latest = self.store.latest_scope_activity(OWNER, ['unit:' + steps.UNIT_ID])
        self.assertEqual(latest['occurred_at'], '2026-10-30T10:00:00+00:00')
        repeated_start = steps.start(self.store, OWNER, self.start_values, VERSION, now='2026-10-31T10:00:00+00:00')
        self.assertEqual(repeated_start['timeline'], result['timeline'])
        self.assertEqual(self.store.latest_scope_activity(OWNER, ['unit:' + steps.UNIT_ID])['occurred_at'], '2026-10-31T10:00:00+00:00')

    def test_invalid_api_fields_and_values_are_rejected_before_persistence(self):
        before = self.store.events(OWNER)
        for extra in [{'owner_id': OTHER}, {'feedback': {'accepted': True}}, {'used_help': 'false'}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                steps.submit(self.store, OWNER, self.request(**extra), VERSION)
        self.assertEqual(self.store.events(OWNER), before)
        with self.assertRaises(ValueError):
            steps.listing(self.store, OWNER, steps.UNIT_ID, VERSION, offset=2**64)
        with self.assertRaises(KeyError):
            steps.start(self.store, OWNER, {**self.start_values, 'case_id': {}}, VERSION)


if __name__ == '__main__':
    unittest.main()
