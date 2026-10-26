"""学生闭环的 HTTP 契约：服务端会话、反馈隔离、复测与过程题。"""
from __future__ import annotations
import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from web import server


class LearningWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.units = server._UNIT_CACHE
        server._SESSIONS.clear()
        server._SESSION_COUNTER = 0
        server._UNIT_CACHE = {
            'contract': {
                'schema': 1, 'id': 'contract', 'title': '等值分数样例', 'stage': 'primary',
                'topic_ids': ['m5b.2.1'], 'standard_ids': ['cms-2022:3.NA.01'],
                'review_status': 'draft', 'author_type': 'ai', 'teacher_review': {'status': 'pending'},
                'objectives': [{'id': 'equiv', 'statement': '能判断等值分数'}],
                'prerequisites': ['分数意义'],
                'explanation': {'intuition': '同一整体的相同部分', 'reasoning': '分子分母同乘非零数',
                                'conditions': ['分母不为零'], 'counterexample': '只改分子会改变分数值'},
                'worked_examples': [{'prompt': '1/2化为分母4的分数', 'steps': ['分母乘2', '分子同乘2'], 'answer': '2/4'}],
                'tasks': [
                    {'id': 'p1', 'objective_ids': ['equiv'], 'phase': 'practice', 'type': 'numeric',
                     'prompt': '用小数表示1/2。', 'answer': '0.5', 'verify': '1/2', 'explanation': '把1分成2等份。',
                     'feedback': {'correct': '值相同', 'incorrect': '检查分子与分母'}},
                    {'id': 't1', 'objective_ids': ['equiv'], 'phase': 'transfer', 'type': 'numeric',
                     'prompt': '用小数表示3/4。', 'answer': '0.75', 'verify': '3/4', 'explanation': '3除以4。',
                     'feedback': {'correct': '等值', 'incorrect': '重新分割单位'}},
                    {'id': 't2', 'objective_ids': ['equiv'], 'phase': 'transfer', 'type': 'rubric',
                     'prompt': '解释为什么2/4和1/2相等。', 'explanation': '说明单位整体相同且所占部分相同。',
                     'feedback': {'correct': '待复核', 'incorrect': '待复核'},
                     'rubric': [{'criterion': '说明同一个整体', 'points': 1, 'example': '把同一张纸均分'}]},
                    {'id': 'd1', 'objective_ids': ['equiv'], 'phase': 'delayed', 'type': 'numeric',
                     'prompt': '用小数表示2/5。', 'answer': '0.4', 'verify': '2/5', 'explanation': '2除以5。',
                     'feedback': {'correct': '等值', 'incorrect': '再次画图'}}]}}

    def tearDown(self):
        server._UNIT_CACHE = self.units

    def request(self, path, payload=None):
        req = urllib.request.Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, json.load(res)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def answer(self, response, answer='1/2', **extra):
        return self.request('/api/answer', {'session_id': response['session']['id'],
            'question_id': response['item']['question']['id'], 'answer': answer, **extra})

    def test_practice_shows_feedback_and_fraction_equivalence(self):
        code, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        self.assertEqual(code, 200)
        self.assertNotIn('answer', response['item']['question'])
        code, done = self.answer(response)
        self.assertEqual(code, 200)
        self.assertTrue(done['done'])
        self.assertEqual(done['feedback']['status'], 'correct')
        self.assertEqual(done['summary']['objective_results'][0]['status'], 'demonstrated')

    def test_transfer_defers_answers_and_rubric_never_auto_passes(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'transfer'})
        _, second = self.answer(response, '3/4')
        self.assertEqual(second['feedback'], {'deferred': True})
        self.assertNotIn('summary', second)
        self.assertNotIn('answer', second['item']['question'])
        self.assertNotIn('example', second['item']['question']['rubric'][0])
        _, done = self.answer(second, '这是同一个整体的相同部分。')
        self.assertTrue(done['done'])
        self.assertEqual(done['summary']['answers'][1]['status'], 'needs_review')
        self.assertEqual(done['summary']['objective_results'][0]['status'], 'needs_review')
        self.assertIn('rubric', done['summary']['answers'][1])

    def test_invalid_format_does_not_advance_or_record_wrong(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        code, _ = self.answer(response, '1/0')
        self.assertEqual(code, 422)
        _, saved = self.request('/api/session/' + response['session']['id'])
        self.assertEqual(saved['session']['answered_count'], 0)
        self.assertEqual(saved['item']['question']['id'], response['item']['question']['id'])

    def test_client_cannot_replace_state_or_self_report_verdict(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        sid = response['session']['id']
        code, _ = self.request('/api/answer', {'session_id': sid, 'question_id': 'p1', 'verdict': 'correct',
                                             'session': {'state': {'equiv': 'mastered'}}})
        self.assertEqual(code, 400)
        code, _ = self.request('/api/answer', {'session_id': sid, 'question_id': 'other', 'answer': '0.5'})
        self.assertEqual(code, 409)
        _, saved = self.request('/api/session/' + sid)
        self.assertEqual(saved['session']['answered_count'], 0)

    def test_topic_retest_excludes_previously_exposed_questions(self):
        _, response = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose', 'limit': 2})
        old_id = response['session']['id']
        seen = []
        while not response['done']:
            qid = response['item']['question']['id']; seen.append(qid)
            _, response = self.answer(response, str(server.GRAPH.questions_by_id[qid]['answer']))
        self.assertTrue(all(answer['raw_answer'] is not None for answer in response['summary']['answers']))
        _, retest = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose', 'previous_session_id': old_id})
        if retest['item']:
            self.assertNotIn(retest['item']['question']['id'], seen)
        self.assertEqual(retest['session']['feedback_policy'], 'deferred')

    def test_skip_remains_unobserved_not_an_automatic_failure(self):
        _, response = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose', 'limit': 2})
        while not response['done']:
            _, response = self.answer(response, '', skip=True)
        self.assertEqual(response['summary']['weak'], [])
        self.assertIn('m7a.5.2', response['summary']['insufficient_evidence'])

    def test_malformed_session_request_returns_400(self):
        for payload in ({'mode': []}, {'unit_id': []}, {'targets': ['m7a.5.2'], 'limit': -1}, []):
            self.assertEqual(self.request('/api/session', payload)[0], 400)

if __name__ == '__main__':
    unittest.main()
