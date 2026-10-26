"""完成课标比较须绑定实际条款记录，不能只修改完成标志。"""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.curriculum_compare import check_comparison_records, synchronize

DATA = Path(__file__).resolve().parents[1] / 'data'


class CurriculumComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        shutil.copytree(DATA / 'curriculum_comparisons', self.data / 'curriculum_comparisons')
        for name in ('curriculum_versions.json', 'standards.json'):
            shutil.copy2(DATA / name, self.data / name)
        self.registry = json.loads((self.data / 'curriculum_versions.json').read_text())
        self.standards = {s['id']: s for s in json.loads((self.data / 'standards.json').read_text())}

    def check(self):
        return check_comparison_records(self.data, self.registry, self.standards)

    def change_capsule(self, change):
        row = self.registry['clause_worklist'][0]
        path = self.data / 'curriculum_comparisons' / Path(row['comparison']['record_path']).name
        capsule = json.loads(path.read_text())
        record = next(r for r in capsule['clauses'] if r['standard_id'] == row['standard_id'])
        change(capsule, record)
        path.write_text(json.dumps(capsule, ensure_ascii=False))

    def test_all_registered_clauses_have_bound_records(self):
        self.assertEqual(self.check(), [])
        self.assertEqual(len(self.registry['clause_worklist']), len(self.standards))
        self.assertTrue(all(r['comparison_status'] == 'complete' for r in self.registry['clause_worklist']))

    def test_missing_comparison_file_cannot_keep_complete(self):
        shutil.rmtree(self.data / 'curriculum_comparisons')
        self.assertTrue(any('无法读取' in e for e in self.check()))

    def test_change_conclusion_without_record_is_rejected(self):
        self.registry['clause_worklist'][0]['comparison']['summary'] = '未经记录的修改'
        self.assertTrue(any('结论' in e for e in self.check()))

    def test_stale_baseline_hash_cannot_be_reused(self):
        sid = self.registry['clause_worklist'][0]['standard_id']
        self.standards[sid]['verbatim_text'] += '新增要求'
        self.assertTrue(any('基线摘录哈希' in e for e in self.check()))

    def test_wrong_source_pdf_is_rejected(self):
        self.change_capsule(lambda capsule, record: capsule['source_document'].update(file_sha256='0' * 64))
        self.assertTrue(any('新版文件哈希' in e for e in self.check()))

    def test_nonexistent_page_is_rejected_even_if_registry_matches(self):
        self.change_capsule(lambda capsule, record: record['target_locator'][0].update(pdf_pages_one_based=[99999]))
        row = self.registry['clause_worklist'][0]
        row['target_locator'][0]['pdf_pages_one_based'] = [99999]
        self.assertTrue(any('页码' in e for e in self.check()))

    def test_record_path_cannot_escape_comparison_directory(self):
        self.registry['clause_worklist'][0]['comparison']['record_path'] = '../../unrelated.json'
        self.assertTrue(any('路径' in e for e in self.check()))

    def test_sync_is_repeatable_and_does_not_upgrade_ai_to_teacher(self):
        synchronize(self.data)
        first = (self.data / 'curriculum_versions.json').read_bytes()
        synchronize(self.data)
        self.assertEqual(first, (self.data / 'curriculum_versions.json').read_bytes())
        registry = json.loads(first)
        self.assertTrue(all(r['status'] == 'pending_review' for r in registry['clause_worklist']))
        self.assertTrue(all(r['comparison']['author_type'] == 'ai' for r in registry['clause_worklist']))

    def test_sync_will_not_overwrite_existing_independent_review(self):
        registry = copy.deepcopy(self.registry)
        registry['clause_worklist'][0]['status'] = 'reviewed'
        (self.data / 'curriculum_versions.json').write_text(json.dumps(registry))
        with self.assertRaisesRegex(ValueError, '已有独立审核'):
            synchronize(self.data)


if __name__ == '__main__':
    unittest.main()
