"""改题必须同时落到 questions.json 和内容源，否则重放旧册会把修复冲掉。"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import apply_fixes as AF  # noqa: E402
from tools import rebuild_data as RD  # noqa: E402


class SyncTest(unittest.TestCase):
    """用一份临时副本验证同步逻辑，不碰真实数据。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "data" / "content").mkdir(parents=True)
        q = {"id": "t-q1", "topicId": "t1", "type": "choice", "prompt": "原题干",
             "choices": [{"key": "A", "text": "x"}], "answer": "A", "explanation": "原解析"}
        q2 = {**q, "id": "t-q2", "prompt": "第二题"}
        (self.tmp / "data" / "questions.json").write_text(
            json.dumps([q, q2], ensure_ascii=False), encoding="utf-8")
        (self.tmp / "data" / "content" / "vol.json").write_text(json.dumps({
            "volume": "T", "topics": {}, "dependencies": [],
            "questions": [dict(q), dict(q2)],
        }, ensure_ascii=False), encoding="utf-8")
        self.data = AF.DATA
        AF.DATA = self.tmp / "data"

    def tearDown(self):
        AF.DATA = self.data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_apply(self, fixes, *, dry_run=False):
        path = self.tmp / "fixes.json"
        path.write_text(json.dumps(fixes, ensure_ascii=False), encoding="utf-8")
        args = ["--fixes", str(path)] + (["--dry-run"] if dry_run else [])
        with redirect_stdout(StringIO()):
            AF.main(args)

    def test_fix_lands_in_both_places(self):
        self.run_apply({"t-q1": {"prompt": "新题干"}})
        q = json.loads((self.tmp / "data" / "questions.json").read_text(encoding="utf-8"))[0]
        c = json.loads((self.tmp / "data" / "content" / "vol.json").read_text(encoding="utf-8"))["questions"][0]
        self.assertEqual(q["prompt"], "新题干")
        self.assertEqual(c["prompt"], "新题干", "内容源没同步，重放旧册会把修复冲掉")

    def test_each_question_gets_its_own_patch(self):
        self.run_apply({"t-q1": {"answer": "B"}, "t-q2": {"answer": "C"}})
        for path in [self.tmp / "data" / "questions.json", self.tmp / "data" / "content" / "vol.json"]:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else data["questions"]
            self.assertEqual({q["id"]: q["answer"] for q in rows}, {"t-q1": "B", "t-q2": "C"})

    def test_dry_run_does_not_write_either_location(self):
        paths = [self.tmp / "data" / "questions.json", self.tmp / "data" / "content" / "vol.json"]
        before = [p.read_bytes() for p in paths]
        self.run_apply({"t-q1": {"answer": "B"}}, dry_run=True)
        self.assertEqual([p.read_bytes() for p in paths], before)

    def test_registered_sources_exclude_retired_patches(self):
        data = self.tmp / "data"
        retired = data / "content" / "old_patch.json"
        shutil.copyfile(data / "content" / "vol.json", retired)
        original = retired.read_bytes()
        RD.write_json(data / RD.REGISTRY, {
            "schema_version": 1, "content_sources": [{"path": "content/vol.json", "volume": "T"}],
            "retired_sources": [{"path": "content/old_patch.json", "sha256_lf": RD.sha256_lf(retired)}],
        })
        self.run_apply({"t-q1": {"answer": "B"}})
        self.assertEqual(retired.read_bytes(), original)
        self.assertEqual(RD.read_json(data / "content" / "vol.json")["questions"][0]["answer"], "B")

    def test_generated_questions_must_be_fixed_in_generator(self):
        path = self.tmp / "data" / "questions.json"
        questions = RD.read_json(path)
        questions[0]["source"] = RD.APPLICATION_SOURCE
        RD.write_json(path, questions)
        before = path.read_bytes()
        with self.assertRaisesRegex(SystemExit, "app_items"):
            self.run_apply({"t-q1": {"answer": "B"}})
        self.assertEqual(path.read_bytes(), before)

    def test_dump_content_keeps_structure(self):
        path = self.tmp / "data" / "content" / "vol.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        AF.dump_content(path, payload)
        back = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(back["volume"], "T")
        self.assertEqual(len(back["questions"]), 2)
        self.assertIn("topics", back)


class RealDataConsistencyTest(unittest.TestCase):
    """真实数据：questions.json 与内容源里的同一道题必须一致。"""

    def test_questions_match_content_sources(self):
        built, _ = RD.build_data()
        questions = RD.read_json(ROOT / "data" / "questions.json")
        delta = RD.differences({"questions.json": built["questions.json"]}, {"questions.json": questions})
        self.assertFalse(delta, f"登记来源（包括生成题）与主库字段不一致：{str(delta)[:1500]}")


if __name__ == "__main__":
    unittest.main()
