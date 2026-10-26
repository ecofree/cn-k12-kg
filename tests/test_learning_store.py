"""学习证据必须跨重启保存，与派生内容库分离，状态和事件不可部分提交。"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from cnkg import store as content_store
from cnkg.learning_store import ConflictError, LearningStore, ROOT


class LearningStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'var' / 'learning.sqlite'
        self.store = LearningStore(self.path)
        self.record = {'id': 'session-1', 'owner_id': 'owner-1',
                       'created_at': '2026-10-26T00:00:00+00:00', 'answers': []}
        self.event = {'event_type': 'session_started',
                      'occurred_at': self.record['created_at'], 'payload': {'mode': 'practice'}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_reopen_preserves_owner_bound_state_and_events(self):
        self.store.save(self.record, [self.event])
        reopened = LearningStore(self.path)
        self.assertEqual(reopened.load('session-1', 'owner-1')['answers'], [])
        self.assertIsNone(reopened.load('session-1', 'someone-else'))
        self.assertEqual(reopened.records('someone-else'), [])
        self.assertEqual(reopened.events('someone-else'), [])
        self.assertEqual(reopened.events('owner-1')[0]['event_type'], 'session_started')

    def test_optimistic_conflict_cannot_double_write_state_or_event(self):
        self.store.save(self.record, [self.event])
        a = self.store.load('session-1', 'owner-1')
        b = self.store.load('session-1', 'owner-1')
        a['answers'].append({'raw_answer': '1/2'})
        self.store.save(a, [{'event_type': 'answer_submitted',
                             'occurred_at': a['created_at'], 'payload': {'raw_answer': '1/2'}}])
        b['answers'].append({'raw_answer': '3/4'})
        with self.assertRaises(ConflictError):
            self.store.save(b, [self.event])
        saved = self.store.load('session-1', 'owner-1')
        self.assertEqual(saved['answers'], [{'raw_answer': '1/2'}])
        self.assertEqual(saved['_revision'], 2)
        self.assertEqual(len(self.store.events('owner-1')), 2)

    def test_event_failure_rolls_back_state_in_same_transaction(self):
        self.store.save(self.record)
        record = self.store.load('session-1', 'owner-1')
        record['answers'].append({'raw_answer': '1/2'})
        with self.assertRaises(KeyError):
            self.store.save(record, [{'occurred_at': record['created_at']}])
        self.assertEqual(self.store.load('session-1', 'owner-1')['answers'], [])
        self.assertEqual(self.store.events('owner-1'), [])

    def test_real_content_database_rebuild_does_not_clear_learning_records(self):
        self.store.save(self.record, [self.event])
        content_db = Path(self.tmp.name) / 'build' / 'kg.sqlite'
        first = content_store.build(content_db, include_external=False)
        second = content_store.build(content_db, include_external=False)
        self.assertEqual(first, second)
        self.assertGreater(first['topics'], 0)
        self.assertEqual(LearningStore(self.path).load('session-1', 'owner-1')['id'], 'session-1')
        self.assertEqual(len(LearningStore(self.path).events('owner-1')), 1)

    def test_learning_database_rejects_repository_build_directory(self):
        with self.assertRaisesRegex(ValueError, 'build/'):
            LearningStore(ROOT / 'build' / 'learning.sqlite')

    def test_misdirected_content_rebuild_refuses_to_overwrite_learning_database(self):
        self.store.save(self.record, [self.event])
        with self.assertRaisesRegex(ValueError, '学习记录'):
            content_store.build(self.path, include_external=False)
        self.assertEqual(LearningStore(self.path).load('session-1', 'owner-1')['id'], 'session-1')
        self.assertEqual(len(LearningStore(self.path).events('owner-1')), 1)


if __name__ == '__main__':
    unittest.main()
