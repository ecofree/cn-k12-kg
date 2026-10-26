r"""外部题内容底线的守门测试。

**背景**：2026-10-25 之前，23313 道外部题（占全库 91%）**完全不在校验视野里**
——validate.py 只加载 questions.json。修完 id 撞车（D35）后把外部题纳入了校验，
本测试再把「内容底线」这部分钉死，防止哪天有人把规则改松。

**为什么只对「可诊断题」提要求**：外部题是按准入规则分层的——
tag-mapped 那批是储备，本来就承认答案证据弱；但一旦进了诊断，
学生就会被问到，那就必须真能判分。对储备题提同样的要求只会让校验变成
噪声（一万条错误没人看），所以这里的门只守「可诊断」这一层。

**broken 是什么**：题目本身残缺（正确答案指向空选项、解析引用了不存在的选项）。
它是准入等级之外的第四个状态——证据（cross-checked）说的是「答案可信」，
broken 说的是「题不完整」，两件事各有各的门。
"""
from __future__ import annotations

import collections
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 缺文件时整组跳过，而不是报失败——否则 clone 下来第一件事就是红一片。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from fix_explanation_letters import PATTERNS as OPTION_LETTER_PATS  # noqa: E402


def load(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


class TestExternalContentGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ext = load("questions_ext.json")
        cls.diag = [q for q in cls.ext if q.get("usable_for_diagnosis")]

    def test_diagnosable_set_not_empty(self):
        """诊断集不能是空的——要是这条挂了，多半是准入逻辑被改坏了。"""
        self.assertGreater(len(self.diag), 8000,
                           f"可诊断题只有 {len(self.diag)} 道")

    def test_diagnosable_choice_answer_is_in_options(self):
        """可诊断选择题的答案必须指向真实存在的选项。"""
        bad = []
        for q in self.diag:
            if q.get("type") != "choice":
                continue
            opt_ids = [c.get("id") for c in (q.get("choices") or [])]
            if q.get("answer") not in opt_ids:
                bad.append((q["id"], q.get("answer"), opt_ids))
        self.assertFalse(bad, f"答案不在选项内：{bad[:3]}")

    def test_diagnosable_choice_has_no_empty_option(self):
        """可诊断选择题不能有空选项——空选项意味着源数据的选项丢了。"""
        bad = [q["id"] for q in self.diag if q.get("type") == "choice"
               and any(not str(c.get("text", "")).strip()
                       for c in (q.get("choices") or []))]
        self.assertFalse(bad, f"可诊断题有空选项：{bad[:5]}")

    def test_diagnosable_explanation_has_no_letter_reference(self):
        """可诊断题的解析不得用字母指代选项（D19：选项重排就必然指错）。

        判定口径与 tools/fix_explanation_letters.py 共用同一份正则——
        宽版正则会把「若 b 是 a、c 的比例中项」里的数学变量误判成选项字母。
        """
        bad = []
        for q in self.diag:
            expl = q.get("explanation") or ""
            if any(p.search(expl) for p in OPTION_LETTER_PATS):
                bad.append(q["id"])
        self.assertFalse(bad, f"解析还在用字母指代选项：{bad[:5]}")

    # ---------------- 准入等级与可用性的一致性 ----------------
    def test_admit_level_iff_usable(self):
        """不变式：{verified, cross-checked} ⟺ usable_for_diagnosis=True。

        历史坑：封存残缺题时只把 usable 置 False、没动 admission_level，
        这条不变式立刻就破，两条既有测试同时挂。封存必须连等级一起降级。
        """
        admit = {"verified", "cross-checked"}
        bad = []
        for q in self.ext:
            level = q.get("admission_level")
            if level in admit and not q.get("usable_for_diagnosis"):
                bad.append((q["id"], level, "应可诊断"))
            if level not in admit and q.get("usable_for_diagnosis"):
                bad.append((q["id"], level, "不该进诊断"))
        self.assertFalse(bad, f"等级与可用性不一致：{bad[:5]}")

    def test_broken_questions_are_quarantined_with_reason(self):
        """broken 级题必须退出诊断，并且写明残缺原因（不能只留个标记）。"""
        broken = [q for q in self.ext if q.get("admission_level") == "broken"]
        self.assertTrue(broken, "一道 broken 题都没有，封存逻辑可能没跑到")
        self.assertLessEqual(len(broken), 50,
                             f"broken 题 {len(broken)} 道，封存规则是不是太宽了？")
        for q in broken:
            self.assertFalse(q.get("usable_for_diagnosis"),
                             f"{q['id']} 已判残却仍可诊断")
            self.assertTrue(q.get("broken_reason", "").strip(),
                            f"{q['id']} 封存了但没写原因")

    def test_accept_lists_are_clean(self):
        """accept 列表（清洗器补的）不得含空串或非字符串——否则判分会误判"""
        bad = []
        for q in self.ext:
            acc = q.get("accept")
            if acc is None:
                continue
            self.assertIsInstance(acc, list, f"{q['id']} 的 accept 不是列表")
            for item in acc:
                if not isinstance(item, str) or not item.strip():
                    bad.append((q["id"], item))
        self.assertFalse(bad, f"accept 里有脏项：{bad[:5]}")

    def test_cleaned_answers_still_have_original(self):
        """改过解析的题必须留着原文（explanation_original），便于回查。"""
        rewritten = [q for q in self.ext if "explanation_original" in q]
        self.assertTrue(rewritten, "没有任何改写记录，改写工具可能没跑到")
        for q in rewritten[:100]:
            self.assertTrue(q.get("explanation"),
                            f"{q['id']} 改写后解析为空")

    def test_levels_are_known(self):
        seen = collections.Counter(q.get("admission_level") for q in self.ext)
        known = {"verified", "cross-checked", "tag-mapped", "ai-mapped",
                 "compound-reference", "broken"}
        unknown = {k: v for k, v in seen.items() if k not in known}
        self.assertFalse(unknown, f"出现未知准入等级：{unknown}")


if __name__ == "__main__":
    unittest.main()
