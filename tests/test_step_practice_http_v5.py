"""Real HTTP, owner cookies, and encrypted installation-to-installation recovery."""
from http.cookiejar import CookieJar
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from cnkg.learning_store import LearningStore
from cnkg import step_practice as steps
from web import server


class StepPracticeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.servers = []
        for index in range(2):
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            httpd.learning_store = LearningStore(Path(self.tmp.name) / f'installation-{index}.sqlite')
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self.servers.append((httpd, thread))
        self.addCleanup(self.close)
        self.client = self.new_client()

    def close(self):
        for httpd, thread in self.servers:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)

    @staticmethod
    def new_client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, path, body=None, *, client=None, installation=0):
        request = urllib.request.Request(f'http://127.0.0.1:{self.servers[installation][0].server_port}{path}',
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with (client or self.client).open(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def start(self, case='one_solution', client=None):
        code, listing = self.request('/api/steps?unit_id=equation_equivalence', client=client)
        self.assertEqual(code, 200, listing)
        code, state = self.request('/api/steps/start', {'unit_id': 'equation_equivalence', 'case_id': case,
            'expected_resource_version': listing['resource_version'], 'request_id': 'start-' + case}, client=client)
        self.assertEqual(code, 200, state)
        return state

    def answer(self, state, equation, operation, operand='', **extra):
        return self.request('/api/steps/answer', {'activity_id': state['id'], 'expected_revision': state['revision'],
            'request_id': 'answer-' + str(state['revision']), 'kind': 'equation', 'equation': equation,
            'operation': operation, 'operand': operand, 'used_help': False, **extra})

    def draft(self, state, equation, **extra):
        return self.request('/api/steps/draft', {'activity_id': state['id'], 'expected_revision': state['revision'],
            'request_id': 'draft-' + str(state['revision']), 'draft': {**steps.DRAFT_DEFAULTS, 'equation': equation, **extra}})

    def test_unsubmitted_incomplete_draft_is_saved_without_an_answer_or_feedback(self):
        state = self.start()
        code, state = self.draft(state, ' 3(x− ', operand='(')
        self.assertEqual(code, 200, state)
        self.assertEqual(state['draft']['equation'], ' 3(x− ')
        self.assertEqual(state['timeline'], [])
        self.assertNotIn('feedback', state)
        self.assertEqual(state['current_equation'], '3(x−2)=12')
        self.assertEqual(self.request('/api/steps?activity_id=' + state['id'])[1], state)
        self.assertEqual(self.request('/api/history')[1]['total'], 0)
        self.assertEqual(self.request('/api/steps?unit_id=equation_equivalence')[1]['activities'][0]['attempt_count'], 0)

    def test_actual_endpoint_tracks_failed_then_alternative_legal_steps_and_conclusion(self):
        state = self.start()
        code, state = self.answer(state, '0=0', 'multiply', '0')
        self.assertEqual(code, 200, state)
        self.assertEqual(state['feedback']['status'], 'non_equivalent')
        self.assertEqual(state['current_equation'], '3(x−2)=12')
        code, state = self.answer(state, 'x-2=4', 'divide', '3')
        self.assertEqual(code, 200, state)
        self.assertTrue(state['feedback']['accepted'])
        code, state = self.answer(state, 'x=6', 'add', '2')
        self.assertEqual(code, 200, state)
        code, finished = self.request('/api/steps/answer', {'activity_id': state['id'], 'expected_revision': state['revision'],
            'request_id': 'conclusion', 'kind': 'conclusion', 'conclusion': 'unique', 'value': '6', 'used_help': False})
        self.assertEqual(code, 200, finished)
        self.assertTrue(finished['completed'])
        self.assertFalse(finished['may_confirm_mastery'])
        self.assertEqual([r['feedback']['accepted'] for r in finished['timeline']], [False, True, True, True])
        self.assertEqual(self.request('/api/history')[1]['total'], 0)
        self.assertEqual(self.request('/api/steps?activity_id=' + state['id'])[1]['timeline'], finished['timeline'])

    def test_other_browser_cannot_read_submit_or_replace_saved_history(self):
        state = self.start()
        stranger = self.new_client()
        self.assertEqual(self.request('/api/steps?activity_id=' + state['id'], client=stranger)[0], 404)
        code, failure = self.request('/api/steps/answer', {'activity_id': state['id'], 'expected_revision': 1,
            'request_id': 'attack', 'equation': 'x=6', 'operation': 'equivalent'}, client=stranger)
        self.assertEqual(code, 404, failure)
        code, failure = self.request('/api/steps/draft', {'activity_id': state['id'], 'expected_revision': 1,
            'request_id': 'attack-draft', 'draft': {**steps.DRAFT_DEFAULTS, 'equation': 'overwrite'}}, client=stranger)
        self.assertEqual(code, 404, failure)
        code, failure = self.answer(state, 'x=6', 'equivalent', owner_id='b' * 64)
        self.assertEqual(code, 400, failure)
        self.assertEqual(self.request('/api/steps?activity_id=' + state['id'])[1]['revision'], 1)
        self.assertEqual(self.request('/api/steps?unit_id=missing')[0], 404)

    def test_backup_restores_actual_wrong_input_hint_and_rules_in_an_empty_new_browser(self):
        state = self.start()
        _, state = self.answer(state, '3x-2=12', 'simplify')
        code, state = self.request('/api/steps/hint', {'activity_id': state['id'], 'expected_revision': state['revision'], 'request_id': 'hint-2', 'level': 2})
        self.assertEqual(code, 200, state)
        code, state = self.draft(state, ' 3x+ ', operation='add', operand='6')
        self.assertEqual(code, 200, state)
        original = self.request('/api/steps?activity_id=' + state['id'])[1]
        code, enabled = self.request('/api/profile/enable', {'label': '方程原档案'})
        self.assertEqual(code, 200, enabled)
        recovery = enabled['recovery_code']
        code, envelope = self.request('/api/profile/backup', {'recovery_code': recovery})
        self.assertEqual(code, 200, envelope)
        fresh = self.new_client()
        code, receipt = self.request('/api/profile/restore', {'envelope': envelope, 'recovery_code': recovery}, client=fresh, installation=1)
        self.assertEqual(code, 200, receipt)
        code, recovered = self.request('/api/steps?activity_id=' + state['id'], client=fresh, installation=1)
        self.assertEqual(code, 200, recovered)
        self.assertEqual(recovered, original)
        self.assertEqual(recovered['draft']['equation'], ' 3x+ ')
        # A rule change exposes an archive, rather than silently recomputing the old wrong step.
        with patch.object(steps, 'rules_version', return_value='f' * 64):
            code, archived = self.request('/api/steps?activity_id=' + state['id'], client=fresh, installation=1)
            self.assertTrue(archived['read_only'])
            self.assertEqual(archived['timeline'], original['timeline'])
            code, failure = self.request('/api/steps/answer', {'activity_id': state['id'], 'expected_revision': state['revision'],
                'request_id': 'blocked-new-rule', 'equation': 'x=6', 'operation': 'equivalent'}, client=fresh, installation=1)
            self.assertEqual(code, 409, failure)


if __name__ == '__main__':
    unittest.main()
