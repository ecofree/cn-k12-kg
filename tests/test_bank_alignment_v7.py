"""An out-of-focus active task must not bypass the full-bank authoring gate."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cnkg.alignment import build_alignment_report
from tools import check_alignment


class FullBankAlignmentTests(unittest.TestCase):
    def report(self, task):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / 'alignment_targets.json').write_text(json.dumps({
                'schema': 1, 'topic_ids': ['focus-topic'], 'unit_ids': ['focus-unit']}))
            return build_alignment_report(data,
                catalog={'tasks': {task['id']: task}, 'skills': {'specific-skill': {}}},
                scope_report={'goals': []})

    def setUp(self):
        self.task = {'id': 'outside-task', 'source_kind': 'bank', 'availability': 'active',
                     'topic_ids': ['outside-topic'], 'skill_ids': ['specific-skill'],
                     'type': 'numeric', 'family_id': 'numberline.distance-between-values',
                     'family_status': 'declared', 'representation': 'verbal',
                     'cognitive_demand': 'execute',
                     'alignment_note': '观察文字给定的两个端点之间的距离值，不声称已画出数轴。'}

    def test_every_active_bank_task_requires_specific_mapping_and_known_authored_dimensions(self):
        report = self.report(self.task)
        self.assertTrue(report['all_bank_requirements_met'])
        self.assertEqual([], report['all_bank_errors'])
        # These rows are outside the named focus; they still fail the bank gate.
        changes = [({'skill_ids': []}, 'explicit_goals'),
                   ({'family_status': 'unclassified'}, 'known_family'),
                   ({'representation': 'unknown'}, 'known_representation'),
                   ({'cognitive_demand': 'unknown'}, 'known_cognitive_demand'),
                   ({'cognitive_demand': 'invented-easy'}, 'known_cognitive_demand'),
                   ({'alignment_note': '  '}, 'authored_alignment_note')]
        for fields, requirement in changes:
            with self.subTest(fields=fields):
                changed = {**self.task, **fields}
                report = self.report(changed)
                self.assertFalse(report['all_bank_requirements_met'])
                self.assertEqual([f'outside-task: 全范围活跃题缺 {requirement}'], report['all_bank_errors'])
                self.assertEqual(1, report['task_inventory']['bank']['tasks'])

    def test_quarantine_does_not_become_active_or_receive_fabricated_metadata(self):
        task = copy.deepcopy(self.task)
        task.update(availability='quarantined', skill_ids=[], family_status='unclassified',
                    representation='unknown', cognitive_demand='unknown', alignment_note='')
        original = copy.deepcopy(task)
        report = self.report(task)
        self.assertTrue(report['all_bank_requirements_met'])
        self.assertNotIn('bank', report['task_inventory'])
        self.assertEqual(original, task)

    def test_declared_status_cannot_turn_placeholder_identity_into_known_structure(self):
        for identity in ("unknown", "unclassified", "unclassified:outside-task", "", "   ", None, ["family"]):
            with self.subTest(family_id=identity):
                changed = {**self.task, 'family_id': identity, 'family_status': 'declared'}
                original = copy.deepcopy(changed)
                report = self.report(changed)
                self.assertFalse(report['all_bank_requirements_met'])
                self.assertEqual(['outside-task: 全范围活跃题缺 known_family'], report['all_bank_errors'])
                self.assertEqual(1, report['task_inventory']['bank']['unknown_family'])
                self.assertEqual(original, changed)

    def test_cli_check_fails_for_a_bank_gap_even_when_goal_and_focus_checks_pass(self):
        report = self.report({**self.task, 'skill_ids': []})
        # Isolate the newly required bank dimension. The actual outside-task
        # gap must affect exit status even when no other gate reports an error.
        report['all_goal_errors'] = []
        report['focus_errors'] = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'alignment.json'
            with patch.object(check_alignment, 'build_alignment_report', return_value=report), redirect_stdout(io.StringIO()):
                self.assertEqual(1, check_alignment.main(['--check', '--output', str(output)]))
                self.assertEqual(0, check_alignment.main(['--output', str(output)]))
            written = json.loads(output.read_text())
            self.assertEqual(['outside-task: 全范围活跃题缺 explicit_goals'], written['all_bank_errors'])


if __name__ == '__main__':
    unittest.main()
