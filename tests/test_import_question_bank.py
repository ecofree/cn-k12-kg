"""外部题入库闸门：结构、污染、答案自相矛盾、默认不进诊断。"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 缺文件时整组跳过，而不是报失败——否则 clone 下来第一件事就是红一片。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

sys.path.insert(0, str(ROOT))

from tools.import_question_bank import check_answer, check_structure, fp  # noqa: E402


def row(**kw):
    base = {
        "prompt": "下列关于函数的说法正确的是（ ）",
        "choices": [{"id": "o1", "text": "1"}, {"id": "o2", "text": "2"},
                    {"id": "o3", "text": "3"}, {"id": "o4", "text": "4"}],
        "answer": "o2",
        "explanation": "由定义可知选 B。",
        "type": "choice",
    }
    base.update(kw)
    return base


class StructureTest(unittest.TestCase):
    def test_good_row_passes(self):
        self.assertIsNone(check_structure(row()))

    def test_too_few_options(self):
        r = row(choices=[{"id": "o1", "text": "1"}, {"id": "o2", "text": "2"}], answer="o1")
        self.assertEqual(check_structure(r), "选项少于 3 个")

    def test_duplicate_option_text(self):
        r = row(choices=[{"id": "o1", "text": "1"}, {"id": "o2", "text": "1"},
                         {"id": "o3", "text": "3"}])
        self.assertEqual(check_structure(r), "选项内容重复")

    def test_answer_not_in_options(self):
        self.assertIn("答案不在选项内", check_structure(row(answer="o9")))

    def test_solution_leaked_into_prompt(self):
        # 原数据集里确实有题干混入"故选C 分析:"的脏数据
        r = row(prompt="故选 C. 分析: 本题主要考查了基本不等式 10. 设 a>0")
        self.assertEqual(check_structure(r), "题干混入解析残留")


class AnswerCheckTest(unittest.TestCase):
    def test_consistent(self):
        self.assertEqual(check_answer(row(explanation="故选 B。"))[0], "consistent")

    def test_conflict_detected(self):
        # 解析说选 C、答案字段写 B，这种题必须拦下
        self.assertEqual(check_answer(row(explanation="故选 C。"))[0], "conflict")

    def test_no_signal(self):
        self.assertEqual(check_answer(row(explanation="两边同除以 2。"))[0], "no_signal")


class FingerprintTest(unittest.TestCase):
    def test_ignores_latex_spacing(self):
        self.assertEqual(fp("求 $a=1$ 的值"), fp("求$a = 1$的值"))


class GateTest(unittest.TestCase):
    def test_imported_questions_are_not_usable(self):
        import json
        path = ROOT / "data" / "questions_ext.json"
        if not path.exists():
            self.skipTest("尚未导入外部题")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data:
            self.skipTest("外部题表为空")
        # 注意：这条断言曾经是「所有外部题都不可用」。
        # 现在真题源（中高考）已解锁为 cross-checked，所以改为**按准入等级**断言：
        # 只有 verified / cross-checked 能进诊断，tag-mapped / ai-mapped 必须留在外面。
        # 分级规则本身由 tests/test_admission.py 守门。
        allowed = {"verified", "cross-checked"}
        for q in data:
            level = q.get("admission_level")
            if level in allowed:
                self.assertTrue(q["usable_for_diagnosis"],
                                f"{q['id']} 等级 {level} 应可进诊断")
            else:
                self.assertFalse(q["usable_for_diagnosis"],
                                 f"{q['id']} 等级 {level} 不应进诊断")
        self.assertTrue(all(q.get("mapping", {}).get("reviewed_by") for q in data))


if __name__ == "__main__":
    unittest.main()
