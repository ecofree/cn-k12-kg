"""选项重排的正确性保证（M3.1 的验收标准）。

核心保证：**打乱选项顺序后，判分结果不变。**
旧实现把字母当身份，重排就要重算答案键，方向写反一次就让 307 道题的答案失效（D19）。
现在选项带稳定 option_id，答案指向 id，重排只是重排数组——答案根本不用动。
"""
import copy
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import balance_options as BO  # noqa: E402
from tools.balance_options import balance  # noqa: E402
from cnkg.evidence import question_content_sha256, question_policy  # noqa: E402
from tools.validate import Report, check_questions  # noqa: E402
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


class BuildBalanceTest(unittest.TestCase):
    def population(self):
        rows = []
        for size, count in ((3, 17), (4, 31)):
            for i in range(count):
                q = make_question([f"值{j}" for j in range(size)], answer_id="o1")
                q["id"] = f"t{size}-q{i + 1}"
                q["choices"][0]["metadata"] = {"skill": "fixture"}
                q.update(diagnosis_eligible=False, target_alignment="component_practice_only")
                rows.append(q)
        return rows

    def test_mixed_population_is_balanced_idempotent_and_preserves_payloads(self):
        rows = self.population()
        snapshot = copy.deepcopy(rows)
        out = BO.balance_questions(rows)
        self.assertEqual(rows, snapshot)
        self.assertEqual(out, BO.balance_questions(out))
        for size, counts in BO.display_position_counts(out).items():
            self.assertLessEqual(max(counts[i] for i in range(size)) -
                                 min(counts[i] for i in range(size)), 1)
        for original, moved in zip(rows, out):
            self.assertEqual({k: v for k, v in original.items() if k != "choices"},
                             {k: v for k, v in moved.items() if k != "choices"})
            self.assertEqual({o["id"]: o for o in original["choices"]},
                             {o["id"]: o for o in moved["choices"]})
        out[0]["choices"][0]["text"] = "caller mutation"
        self.assertEqual(rows, snapshot, "transformed output must not alias source payloads")

    def test_input_permutation_and_hash_collision_have_deterministic_targets(self):
        rows = self.population()
        for rank in (None, lambda qid: 0):
            context = patch.object(BO, "hash_rank", rank) if rank else patch.object(BO, "hash_rank", BO.hash_rank)
            with context:
                first = {q["id"]: q for q in BO.balance_questions(rows)}
                second = {q["id"]: q for q in BO.balance_questions(list(reversed(rows)))}
                self.assertEqual(first, second)

    def test_malformed_choices_are_rejected_without_silent_answer_repair(self):
        q = make_question(["甲", "乙", "丙", "丁"])
        for malformed in (
            {**q, "answer": "missing"},
            {**q, "choices": [{"id": "o1"}, {"id": "o1"}]},
            {**q, "choices": ["not an option object"]},
            {**q, "choices": []},
        ):
            with self.subTest(question=malformed), self.assertRaises(BO.ChoiceOrderError):
                BO.balance_questions([malformed])
        with self.assertRaisesRegex(BO.ChoiceOrderError, "duplicate"):
            BO.balance_questions([q, copy.deepcopy(q)])

    def test_old_review_is_not_resigned_when_display_order_changes(self):
        q = make_question(["甲", "乙", "丙", "丁"], answer_id="o2")
        q["quality_review"] = {
            "status": "approved", "reviewer_type": "teacher",
            "reviewer_id": "fixture-teacher", "record_id": "fixture-review",
            "content_sha256": question_content_sha256(q),
        }
        self.assertFalse(question_policy(q)["reviewer_pending"])
        moved = balance(q, 0)
        self.assertNotEqual(question_content_sha256(moved), question_content_sha256(q))
        self.assertEqual(moved["quality_review"], q["quality_review"])
        self.assertTrue(question_policy(moved)["reviewer_pending"])
        self.assertFalse(question_policy(balance(q, 1))["reviewer_pending"])

    def test_explicit_position_references_block_transform_but_math_terms_do_not(self):
        q = make_question(["甲", "乙", "丙", "丁"])
        for field, value in (("explanation", "故选A。"), ("prompt", "第三个选项正确吗？")):
            with self.subTest(field=field), self.assertRaisesRegex(BO.ChoiceOrderError, field):
                BO.balance_questions([{**q, field: value}])
        relative = copy.deepcopy(q)
        relative["choices"][3]["text"] = "以上都正确"
        with self.assertRaisesRegex(BO.ChoiceOrderError, "choices"):
            BO.balance_questions([relative])
        for explanation in ("余弦差角中第二项为正。", "这是3σ原则中的第一个数值。",
                            "原题的「以上都可以」已被替换为完整的对象列表。"):
            self.assertEqual(BO.position_reference_fields({**q, "explanation": explanation}), [])

    def test_validator_counts_display_positions_instead_of_answer_ids(self):
        balanced, skewed = [], []
        for i in range(16):
            q = make_question(["甲", "乙", "丙", "丁"])
            q.update(id=f"m1a.1.1-q{i + 1}", topicId="m1a.1.1", prompt=f"不同题干{i}")
            balanced.append(balance(q, i % 4))  # all answers have ID o1
            varied_id = {**q, "answer": f"o{i % 4 + 1}"}
            skewed.append(balance(varied_id, 0))  # answer IDs look balanced; position is always A
        for rows, expect_skew in ((balanced, False), (skewed, True)):
            report = Report()
            check_questions(rows, {"m1a.1.1"}, report, {"m1a.1.1"})
            self.assertEqual(any("显示位置偏斜" in error for error in report.errors), expect_skew)


if __name__ == "__main__":
    unittest.main()
