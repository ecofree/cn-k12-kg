"""学生闭环的 HTTP 契约：服务端会话、反馈隔离、复测与过程题。"""
from __future__ import annotations
import copy
import json
from http.cookiejar import CookieJar
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from web import server
from cnkg.learning_store import LearningStore
from cnkg.graph import Graph


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
        self.store = server._STORE
        self.tmp = tempfile.TemporaryDirectory()
        server._STORE = LearningStore(Path(self.tmp.name) / 'var' / 'learning.sqlite')
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
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
                     'prompt': '求1/2的值，可用小数或分数。', 'answer': '0.5', 'verify': '1/2', 'explanation': '把1分成2等份。',
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
        server._STORE = self.store
        self.tmp.cleanup()

    def request(self, path, payload=None):
        req = urllib.request.Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=5) as res:
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

    def test_restart_restores_session_and_original_unit_snapshot(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        sid = response['session']['id']
        # 模拟另一进程/重启，只依赖 SQLite；新发布内容不能改变已呈现题的答案。
        server._STORE = LearningStore(server._STORE.path)
        server._UNIT_CACHE['contract']['tasks'][0]['answer'] = '0.75'
        server._UNIT_CACHE['contract']['tasks'][0]['verify'] = '3/4'
        code, restored = self.request('/api/session/' + sid)
        self.assertEqual(code, 200)
        self.assertEqual(restored['item']['question']['prompt'], '求1/2的值，可用小数或分数。')
        code, done = self.answer(restored, '1/2')
        self.assertEqual(code, 200)
        self.assertEqual(done['summary']['answers'][0]['status'], 'correct')

    def test_foreign_cookie_cannot_read_answer_link_or_export_session(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        sid = response['session']['id']
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.assertEqual(self.request('/api/session/' + sid)[0], 404)
        self.assertEqual(self.answer(response)[0], 404)
        self.assertEqual(self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice',
                                                       'previous_session_id': sid})[0], 404)
        self.assertEqual(self.request('/api/history')[1]['total'], 0)
        self.assertEqual(self.request('/api/export')[1]['sessions'], [])

    def test_duplicate_submission_is_idempotent_and_cannot_overwrite(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        _, done = self.answer(response, '1/2', elapsed_ms=1200)
        _, retry = self.answer(response, '1/2', elapsed_ms=9999)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['session']['answered_count'], 1)
        self.assertEqual(self.answer(response, '3/4')[0], 409)
        export = self.request('/api/export')[1]
        submitted = [e for e in export['events'] if e['event_type'] == 'answer_submitted']
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]['payload']['client_elapsed_ms'], 1200)
        entry = done['summary']['answers'][0]
        self.assertEqual(entry['normalized_answer'], '1/2')
        self.assertEqual(entry['normalized_answer_format'], 'rational_v1')
        self.assertGreaterEqual(entry['duration_ms'], 0)
        self.assertIn('presented_at', entry)
        self.assertIn('feedback_available_at', entry)
        self.assertEqual(len(entry['content_sha256']), 64)

    def test_export_does_not_leak_deferred_feedback_or_unseen_answers(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'transfer'})
        _, second = self.answer(response, '1/2')
        export = self.request('/api/export')[1]
        record = export['sessions'][0]
        self.assertNotIn('summary', record)
        self.assertNotIn('unit_snapshot', record)
        self.assertNotIn('question_snapshots', record)
        self.assertNotIn('status', record['answers'][0])
        self.assertNotIn('answer', record['answers'][0])
        self.assertNotIn('explanation', record['answers'][0])
        self.assertEqual(record['answers'][0]['raw_answer'], '1/2')
        self.assertFalse(any(e['event_type'] == 'feedback_released' for e in export['events']))
        self.assertNotIn('把同一张纸均分', json.dumps(export, ensure_ascii=False))
        self.answer(second, '同一整体的两种等分。')
        export = self.request('/api/export')[1]
        self.assertEqual(export['sessions'][0]['answers'][0]['status'], 'wrong')
        self.assertEqual(len([e for e in export['events'] if e['event_type'] == 'feedback_released']), 2)

    def test_requested_material_is_not_claimed_reading_or_delayed_evidence(self):
        _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice',
                                                    'instruction_seen': True})
        ctx = response['session']['context']
        self.assertTrue(ctx['reported_instruction_seen'])
        self.assertFalse(ctx['material_requested'])
        _, done = self.answer(response)
        self.request('/api/learning/units/contract')
        _, delayed = self.request('/api/session', {'unit_id': 'contract', 'mode': 'delayed',
            'previous_session_id': done['session']['id']})
        ctx = delayed['session']['context']
        self.assertTrue(ctx['material_requested'])
        self.assertEqual(ctx['material_exposure_evidence'], 'http_request_only_not_completion')
        self.assertGreaterEqual(ctx['actual_delay_seconds'], 0)
        self.assertFalse(ctx['delay_window_met'])
        self.assertFalse(ctx['independent_evidence'])
        self.answer(delayed, '2/5')
        _, repeated = self.request('/api/session', {'unit_id': 'contract', 'mode': 'delayed'})
        self.assertEqual(repeated['session']['context']['repeated_question_ids'], ['d1'])

    def test_teaching_page_keeps_worked_examples_but_no_assessment_prompts(self):
        code, detail = self.request('/api/learning/units/contract')
        self.assertEqual(code, 200)
        self.assertEqual(detail['worked_examples'][0]['answer'], '2/4')
        self.assertNotIn('tasks', detail)
        self.assertEqual(detail['task_counts'], {'practice': 1, 'transfer': 2, 'delayed': 1})
        self.assertNotIn('用小数表示3/4', json.dumps(detail, ensure_ascii=False))
        self.assertNotIn('解释为什么2/4和1/2相等', json.dumps(detail, ensure_ascii=False))

    def test_topic_revision_change_blocks_scoring_but_preserves_history(self):
        _, response = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose'})
        old_graph = server.GRAPH
        try:
            server.GRAPH = copy.deepcopy(old_graph)
            server.GRAPH._web_content_version = 'new-revision'
            code, restored = self.request('/api/session/' + response['session']['id'])
            self.assertEqual(code, 200)
            self.assertTrue(restored['session']['resume_blocked'])
            self.assertEqual(self.answer(response, '1')[0], 409)
            self.assertEqual(self.request('/api/history')[1]['total'], 1)
            self.assertEqual(self.request('/api/export')[1]['sessions'][0]['answers'], [])
        finally:
            server.GRAPH = old_graph

    def test_completed_topic_result_is_frozen_when_graph_and_rules_change(self):
        _, response = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose', 'limit': 2})
        sid = response['session']['id']
        while not response['done']:
            qid = response['item']['question']['id']
            _, response = self.answer(response, str(server.GRAPH.questions_by_id[qid]['answer']))
        original_summary = copy.deepcopy(response['summary'])
        original_verdict = response['verdictText']
        # 模拟发布删除原节点、改写题库，且推断/结论文案的代码也已变化。
        with patch.object(server, 'GRAPH', Graph([], [], [])), \
             patch.object(server.engine, 'summarize', side_effect=AssertionError('不得重算历史')), \
             patch.object(server.engine, 'verdict_label', side_effect=AssertionError('不得重写历史结论')):
            server._STORE = LearningStore(server._STORE.path)
            code, restored = self.request('/api/session/' + sid)
            self.assertEqual(code, 200)
            self.assertEqual(restored['summary'], original_summary)
            self.assertEqual(restored['verdictText'], original_verdict)
            exported = self.request('/api/export')[1]['sessions'][0]
            self.assertEqual(exported['summary'], original_summary)
            self.assertEqual(exported['verdictText'], original_verdict)

    def test_blocked_incomplete_topic_keeps_raw_observations_without_new_graph(self):
        _, response = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose', 'limit': 2})
        sid = response['session']['id']
        qid = response['item']['question']['id']
        raw = str(server.GRAPH.questions_by_id[qid]['answer'])
        _, response = self.answer(response, raw)
        self.assertFalse(response['done'])
        with patch.object(server, 'GRAPH', Graph([], [], [])), \
             patch.object(server.engine, 'summarize', side_effect=AssertionError('旧会话不重算')):
            code, restored = self.request('/api/session/' + sid)
            self.assertEqual(code, 200)
            self.assertTrue(restored['session']['resume_blocked'])
            self.assertEqual(restored['saved_answers'][0]['question_id'], qid)
            self.assertEqual(restored['saved_answers'][0]['raw_answer'], raw)
            self.assertNotIn('status', restored['saved_answers'][0])
            self.assertNotIn('answer', restored['saved_answers'][0])
            self.assertEqual(self.answer(restored, '1')[0], 409)

    def test_diagram_revision_change_blocks_old_task(self):
        old_dir = server.learning.DIAGRAM_DIR
        diagrams = Path(self.tmp.name) / 'diagrams'
        diagrams.mkdir()
        svg = diagrams / 'contract.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>1/2</text></svg>')
        try:
            server.learning.DIAGRAM_DIR = diagrams
            server._UNIT_CACHE['contract']['diagram'] = {'src': '/static/diagrams/contract.svg', 'alt': '半份'}
            _, response = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>3/4</text></svg>')
            self.assertEqual(self.answer(response)[0], 409)
            _, restored = self.request('/api/session/' + response['session']['id'])
            self.assertTrue(restored['session']['resume_blocked'])
            self.assertEqual(restored['session']['answered_count'], 0)
        finally:
            server.learning.DIAGRAM_DIR = old_dir

    def test_unrelated_previous_session_does_not_create_post_instruction_claim(self):
        _, practice = self.request('/api/session', {'unit_id': 'contract', 'mode': 'practice'})
        _, done = self.answer(practice)
        _, topic = self.request('/api/session', {'targets': ['m7a.5.2'], 'mode': 'diagnose',
                                                'previous_session_id': done['session']['id']})
        ctx = topic['session']['context']
        self.assertFalse(ctx['same_learning_scope'])
        self.assertIsNone(ctx['actual_delay_seconds'])
        self.assertFalse(ctx['delay_window_met'])
        self.assertNotIn('post_instruction', ctx)

    def test_project_routes_only_return_public_resources(self):
        code, index = self.request('/api/projects')
        self.assertEqual(code, 200)
        self.assertGreater(len(index['projects']), 0)
        pid = index['projects'][0]['id']
        code, detail = self.request('/api/projects/' + pid)
        self.assertEqual(code, 200)
        self.assertNotIn('teacher_notes', detail)
        self.assertTrue(all('example' not in row for row in detail['rubric']))
        self.assertEqual(self.request('/api/projects/missing-project')[0], 404)

if __name__ == '__main__':
    unittest.main()
