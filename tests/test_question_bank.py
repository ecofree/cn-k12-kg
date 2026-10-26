"""外部题库接入的两个易错点：选项标签补位、候选池与诊断数据隔离。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.fetch_question_bank import fingerprint, normalize_cmm, parse_choices  # noqa: E402


class ParseChoicesTest(unittest.TestCase):
    def test_normal_four_options(self):
        items, ok = parse_choices("A. 正数\nB. 负数\nC. 正数或零\nD. 负数或零")
        self.assertTrue(ok)
        self.assertEqual([i["key"] for i in items], ["A", "B", "C", "D"])
        self.assertEqual(items[3]["text"], "负数或零")

    def test_missing_first_letter_is_filled(self):
        # CMM-Math 里有 9% 的选择题首项丢了 "A." 前缀，必须按行序补位
        items, ok = parse_choices("2022\nB. $\\frac{1}{2022}$\nC. -2022\nD. $-\\frac{1}{2022}$")
        self.assertTrue(ok)
        self.assertEqual([i["key"] for i in items], ["A", "B", "C", "D"])
        self.assertEqual(items[0]["text"], "2022")

    def test_empty_options_is_not_choice(self):
        items, ok = parse_choices("")
        self.assertEqual(items, [])
        self.assertTrue(ok)

    def test_duplicate_letters_marked_broken(self):
        _, ok = parse_choices("A. 1\nA. 2")
        self.assertFalse(ok)


class NormalizeTest(unittest.TestCase):
    def row(self, **kw):
        base = {
            "id": "1",
            "question": "题干",
            "answer": "A",
            "options": "A. 1\nB. 2",
            "analysis": "解析",
            "solution": "null",
            "image": [],
            "level": "九年级",
            "subject": "算术",
        }
        base.update(kw)
        return base

    def test_image_question_is_dropped(self):
        # 依赖配图的题纯文本管道解不了，必须整条排除而不是留个空壳
        self.assertIsNone(normalize_cmm(self.row(image=["a.png"])))

    def test_empty_answer_is_dropped(self):
        self.assertIsNone(normalize_cmm(self.row(answer="null")))

    def test_kept_question_never_claims_a_topic(self):
        item = normalize_cmm(self.row())
        self.assertIsNone(item["topicId"])
        self.assertTrue(item["needs_kc"])
        self.assertEqual(item["review_status"], "unverified")

    def test_stage_from_level(self):
        self.assertEqual(normalize_cmm(self.row(level="九年级"))["stage"], "junior")
        self.assertEqual(normalize_cmm(self.row(level="高二"))["stage"], "senior")
        self.assertEqual(normalize_cmm(self.row(level="三年级"))["stage"], "primary")


class FingerprintTest(unittest.TestCase):
    def test_ignores_latex_spacing(self):
        a = fingerprint("若 $a=1$，求 $b$")
        b = fingerprint("若$a = 1$，求$b$")
        self.assertEqual(a, b)

    def test_distinguishes_different_questions(self):
        self.assertNotEqual(fingerprint("求 $a$"), fingerprint("求 $b$"))


if __name__ == "__main__":
    unittest.main()
