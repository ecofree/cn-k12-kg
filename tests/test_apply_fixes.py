"""改题必须同时落到 questions.json 和内容源，否则重放旧册会把修复冲掉。"""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import apply_fixes as AF  # noqa: E402


class SyncTest(unittest.TestCase):
    """用一份临时副本验证同步逻辑，不碰真实数据。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "data" / "content").mkdir(parents=True)
        q = {"id": "t-q1", "topicId": "t1", "type": "choice", "prompt": "原题干",
             "choices": [{"key": "A", "text": "x"}], "answer": "A", "explanation": "原解析"}
        (self.tmp / "data" / "questions.json").write_text(
            json.dumps([q], ensure_ascii=False), encoding="utf-8")
        (self.tmp / "data" / "content" / "vol.json").write_text(json.dumps({
            "volume": "T", "topics": {}, "dependencies": [],
            "questions": [dict(q)],
        }, ensure_ascii=False), encoding="utf-8")
        self.data = AF.DATA
        AF.DATA = self.tmp / "data"

    def tearDown(self):
        AF.DATA = self.data
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_apply(self, fixes):
        AF.DATA = self.tmp / "data"
        questions = json.loads((self.tmp / "data" / "questions.json").read_text(encoding="utf-8"))
        by_id = {q["id"]: q for q in questions}
        for qid, patch in fixes.items():
            by_id[qid].update(patch)
        AF.dump(self.tmp / "data" / "questions.json", questions)
        path = self.tmp / "data" / "content" / "vol.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for q in payload["questions"]:
            if q["id"] in fixes:
                q.update(fixes[qid])
        AF.dump_content(path, payload)

    def test_fix_lands_in_both_places(self):
        self.run_apply({"t-q1": {"prompt": "新题干"}})
        q = json.loads((self.tmp / "data" / "questions.json").read_text(encoding="utf-8"))[0]
        c = json.loads((self.tmp / "data" / "content" / "vol.json").read_text(encoding="utf-8"))["questions"][0]
        self.assertEqual(q["prompt"], "新题干")
        self.assertEqual(c["prompt"], "新题干", "内容源没同步，重放旧册会把修复冲掉")

    def test_dump_content_keeps_structure(self):
        path = self.tmp / "data" / "content" / "vol.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        AF.dump_content(path, payload)
        back = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(back["volume"], "T")
        self.assertEqual(len(back["questions"]), 1)
        self.assertIn("topics", back)


class RealDataConsistencyTest(unittest.TestCase):
    """真实数据：questions.json 与内容源里的同一道题必须一致。"""

    def test_questions_match_content_sources(self):
        content_dir = ROOT / "data" / "content"
        if not content_dir.exists():
            self.skipTest("无内容源")
        by_id = {}
        for path in content_dir.glob("*.json"):
            for q in json.loads(path.read_text(encoding="utf-8")).get("questions", []):
                by_id.setdefault(q["id"], q)
        if not by_id:
            self.skipTest("内容源为空")
        questions = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
        mismatched = []
        for q in questions:
            src = by_id.get(q["id"])
            if not src:
                continue
            for key in ("prompt", "answer", "explanation"):
                if (src.get(key) or "") != (q.get(key) or ""):
                    mismatched.append((q["id"], key))
        self.assertEqual(mismatched, [], f"内容源与题库不一致：{mismatched[:5]}")


if __name__ == "__main__":
    unittest.main()
