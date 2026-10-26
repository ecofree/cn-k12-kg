"""选项重排的正确性保证（M3.1 的验收标准）。

核心保证：**打乱选项顺序后，判分结果不变。**
旧实现把字母当身份，重排就要重算答案键，方向写反一次就让 307 道题的答案失效（D19）。
现在选项带稳定 option_id，答案指向 id，重排只是重排数组——答案根本不用动。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.balance_options import balance  # noqa: E402
from web.server import judge  # noqa: E402


def make_question(texts, answer_id="o1", n=None):
    n = n or len(texts)
    return {
        "id": "t-q1", "topicId": "t1", "type": "choice", "difficulty": "basic",
        "prompt": "题干", "choices": [{"id": f"o{i + 1}", "text": t} for i, t in enumerate(texts)],
        "answer": answer_id, "explanation": "解析",
    }


class BalanceTest(unittest.TestCase):
    def test_rotation_moves_correct_option_to_target(self):
        texts = ["甲", "乙", "丙", "丁"]
        for want in range(4):
            q = make_question(texts, answer_id="o1")
            out = balance(q, want)
            self.assertEqual(out["choices"][want]["text"], "甲")

    def test_answer_id_never_changes(self):
        texts = ["甲", "乙", "丙", "丁"]
        for want in range(4):
            out = balance(make_question(texts, answer_id="o2"), want)
            self.assertEqual(out["answer"], "o2", "重排不该动答案")

    def test_option_ids_travel_with_their_option(self):
        q = make_question(["甲", "乙", "丙", "丁"], answer_id="o3")
        out = balance(q, 0)
        by_text = {c["text"]: c["id"] for c in out["choices"]}
        self.assertEqual(by_text["甲"], "o1")
        self.assertEqual(by_text["丙"], "o3")
        self.assertEqual(out["choices"][0]["id"], "o3")

    def test_judgement_unchanged_after_rotation(self):
        """验收标准：重排后判分不变。"""
        texts = ["甲", "乙", "丙", "丁"]
        q = make_question(texts, answer_id="o2")
        for want in range(4):
            out = balance(q, want)
            self.assertEqual(judge(out, "o2"), "correct",
                             f"重排到第 {want} 位后答案应仍然判对")
            self.assertEqual(judge(out, out["choices"][want]["id"]), "correct")

    def test_three_option_question(self):
        texts = ["甲", "乙", "丙"]
        q = make_question(texts, answer_id="o3")
        out = balance(q, 1)
        self.assertEqual(out["choices"][1]["text"], "丙")
        self.assertEqual(out["answer"], "o3")
        self.assertEqual(judge(out, "o3"), "correct")

    def test_no_rotation_when_already_in_place(self):
        q = make_question(["甲", "乙", "丙", "丁"], answer_id="o2")
        out = balance(q, 1)
        self.assertEqual([c["id"] for c in out["choices"]], ["o1", "o2", "o3", "o4"])


class JudgeTest(unittest.TestCase):
    def test_option_id_judgement(self):
        q = make_question(["甲", "乙", "丙", "丁"], answer_id="o3")
        self.assertEqual(judge(q, "o3"), "correct")
        self.assertEqual(judge(q, "o1"), "wrong")

    def test_letter_still_accepted_as_fallback(self):
        """兼容旧客户端：字母按当前顺序换算成 id。"""
        q = make_question(["甲", "乙", "丙", "丁"], answer_id="o3")
        self.assertEqual(judge(q, "C"), "correct")

    def test_letter_fallback_follows_current_order(self):
        """字母是位置：重排后同一个字母指向不同选项，判分随之改变（这正是要靠 id 避免的坑）。"""
        q = make_question(["甲", "乙", "丙", "丁"], answer_id="o3")
        moved = balance(q, 0)
        self.assertEqual(judge(moved, "A"), "correct", "字母 A 现在指的是正确项")
        self.assertEqual(judge(moved, "C"), "wrong")


if __name__ == "__main__":
    unittest.main()
