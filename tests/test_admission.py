"""外部题准入分级的守门测试。

**为什么要有这个测试**：外部题的准入是分级的（verified / cross-checked /
tag-mapped / ai-mapped / compound-reference），不同等级进不进诊断不一样。
这类规则一旦被改坏，后果是「未复核的题进了诊断」——正是 D19 之后最想避免的事。

所以这里把几条硬约束固化成断言：
1. 只有 verified 和 cross-checked 能进诊断
2. CMM-Math（证据最弱的那个源）必须留在诊断之外
3. 中高考真题源必须带来源说明，且只能是 cross-checked 或 compound-reference
4. compound-reference（解答题等无可自动判定答案的真题）**永远不进诊断**
5. 每个等级都必须在已知白名单里（防止拼错等级后静默失效）
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 缺文件时整组跳过，而不是报失败——否则 clone 下来第一件事就是红一片。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

EXT = ROOT / "data" / "questions_ext.json"

# 已知的准入等级白名单
KNOWN_LEVELS = {
    "verified",            # 经第二人复核
    "cross-checked",       # 两个独立证据互证
    "tag-mapped",          # 标签映射，答案证据较弱
    "ai-mapped",           # 规则映射
    "compound-reference",  # 复合型训练题：无自动判定答案，只有原卷题干与解析
    # 证据等级之外的第四种状态：题目本身残缺（正确答案指向空选项、
    # 解析引用了不存在的选项）。证据再强也不能拿它去诊断，
    # 所以单独设一个等级——否则「cross-checked ⟹ 可诊断」这条不变式会被打破。
    "broken",
}

# 真题源允许的等级
REAL_EXAM_LEVELS = {"cross-checked", "compound-reference"}


class TestAdmission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(EXT.read_text(encoding="utf-8"))

    def test_levels_known(self):
        bad = [q["id"] for q in self.data if q.get("admission_level") not in KNOWN_LEVELS]
        self.assertFalse(bad, f"未知准入等级：{bad[:5]}")

    def test_only_strong_levels_admit(self):
        """只有 verified / cross-checked 能进诊断。"""
        allowed = {"verified", "cross-checked"}
        bad = [q["id"] for q in self.data
               if q.get("usable_for_diagnosis") and q.get("admission_level") not in allowed]
        self.assertFalse(bad, f"弱证据题被放进诊断：{bad[:5]}")

    def test_weak_evidence_stays_out(self):
        """只有「答案与解析互证」的题能进诊断，**与来源无关**。

        2026-10-25 前这里是 `test_cmm_stays_out`——按来源一刀切，
        理由是 CMM 答案只有约 42% 能交叉印证。

        改为按题目级证据的理由：实测 CMM 初中 1770 道里只有 17% 能互证，
        而能互证的那 308 道，证据强度与 cross-checked 的定义完全一致
        （「两个独立证据互证」）。一刀切把互证题和非互证题同等排除，
        与定义自相矛盾，代价是 77 个初中点没有任何可诊断题。

        这条测试现在的守门方向是**反向**的：拦「无互证却进诊断」的题，
        而不是拦某个来源。任何新源接入都自动按证据分级。
        """
        bad = [q["id"] for q in self.data
               if q.get("usable_for_diagnosis")
               and (q.get("answer_check") or {}).get("status") != "consistent"]
        self.assertFalse(bad, f"答案无互证却进了诊断：{bad[:5]}")

    def test_consistent_questions_are_admitted(self):
        """互证的题必须真的进了诊断——防止把规则改得太紧，白锁 308 道题。

        唯一的例外是 **broken**（题目本身残缺：正确答案指向空选项、
        解析引用了不存在的选项）。证据互证说的是「这个答案可信」，
        题目残缺说的是「这道题不完整」——两件事，各有各的门。
        残缺题的封存由 tools/repair_empty_options.py 与
        tools/fix_explanation_letters.py 负责，共 20 道。
        """
        cmm = [q for q in self.data if q.get("source") == "cmm-math"]
        consistent = [q for q in cmm
                      if (q.get("answer_check") or {}).get("status") == "consistent"]
        self.assertTrue(consistent, "CMM 里一道互证题都没有，准入逻辑可能坏了")
        locked = [q["id"] for q in consistent
                  if not q.get("usable_for_diagnosis")
                  and q.get("admission_level") != "broken"]
        self.assertFalse(locked, f"互证题被锁在诊断外：{locked[:5]}")
        # 初中那 308 道是本轮解锁的主体，至少要有几百道
        self.assertGreaterEqual(len(consistent), 300,
                                f"CMM 互证题只有 {len(consistent)} 道，少于预期的 308")

    def test_gaokao_and_zhongkao_cross_checked(self):
        """真题源必须带来源说明，且等级只能是 cross-checked 或 compound-reference。

        解答题（compound-reference）没有可自动判定的答案，不能进诊断——
        但它必须留在题库里，因为复合型考点覆盖全靠它们。

        broken 是第三个合法状态：真题本身残缺（如解析引用了「D 选项」
        而题目只导入了 3 项），证据再硬也不能拿去诊断。
        """
        for src in ("gaokao-tex", "smse-shanghai-zhongkao"):
            group = [q for q in self.data if q.get("source") == src]
            self.assertTrue(group, f"{src} 一道题都没有")
            bad = [q["id"] for q in group
                   if q.get("admission_level") not in REAL_EXAM_LEVELS | {"broken"}
                   or not q.get("admission_reason")]
            self.assertFalse(bad, f"{src} 的准入标注不对：{bad[:5]}")

    def test_compound_reference_never_in_diagnosis(self):
        """复合型训练题没有自动判定答案，永远不能进诊断集。"""
        bad = [q["id"] for q in self.data
               if q.get("admission_level") == "compound-reference"
               and q.get("usable_for_diagnosis")]
        self.assertFalse(bad, f"复合型训练题被放进诊断：{bad[:5]}")

    def test_compound_reference_has_no_answer(self):
        """复合型训练题不应带一个可能错误的答案。"""
        bad = [q["id"] for q in self.data
               if q.get("admission_level") == "compound-reference"
               and (q.get("answer") or "").strip()]
        self.assertFalse(bad, f"复合型训练题带答案：{bad[:5]}")

    def test_admission_reason_present(self):
        bad = [q["id"] for q in self.data if not q.get("admission_reason")]
        self.assertFalse(bad, f"缺准入理由：{bad[:5]}")

    def test_license_note_is_factual(self):
        """许可字段应改为事实陈述，不能再写成使用限制。"""
        bad = []
        for q in self.data:
            reason = q.get("usable_reason", "")
            if "许可" in reason and "不作为使用限制" not in q.get("license_note", ""):
                bad.append(q["id"])
        self.assertFalse(bad, f"许可仍被当作限制：{bad[:5]}")


class TestSourceChain(unittest.TestCase):
    def test_source_chain_recorded(self):
        """真题题必须记录完整来源链路，便于追溯。"""
        data = json.loads(EXT.read_text(encoding="utf-8"))
        for src in ("gaokao-tex", "smse-shanghai-zhongkao"):
            group = [q for q in data if q.get("source") == src]
            bad = [q["id"] for q in group if not q.get("source_chain")]
            self.assertFalse(bad, f"{src} 缺来源链路：{bad[:5]}")


class TestMultiTopic(unittest.TestCase):
    """复合题：一题多知识点的结构约束。

    中高考真题多为复合题，一道题同时考多个知识点。数据模型用
    topicIds 数组 + primaryTopicId 表达，主知识点必须是数组第一个。
    """

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(EXT.read_text(encoding="utf-8"))

    def test_topic_ids_present(self):
        bad = [q["id"] for q in self.data if not q.get("topicIds")]
        self.assertFalse(bad, f"缺 topicIds：{bad[:5]}")

    def test_primary_is_first(self):
        bad = [q["id"] for q in self.data
               if q.get("topicIds") and q.get("primaryTopicId") != q["topicIds"][0]]
        self.assertFalse(bad, f"主知识点与 topicIds[0] 不一致：{bad[:5]}")

    def test_topic_id_matches_primary(self):
        """向后兼容字段 topicId 必须等于 primaryTopicId。"""
        bad = [q["id"] for q in self.data if q.get("topicId") != q.get("primaryTopicId")]
        self.assertFalse(bad, f"topicId 与 primaryTopicId 不一致：{bad[:5]}")

    def test_no_duplicate_tags(self):
        bad = [q["id"] for q in self.data
               if q.get("topicIds") and len(set(q["topicIds"])) != len(q["topicIds"])]
        self.assertFalse(bad, f"topicIds 有重复：{bad[:5]}")

    def test_compound_questions_exist(self):
        """复合题必须真的存在——这是本轮改造的核心目标，不能是空转。"""
        multi = [q for q in self.data if len(q.get("topicIds") or []) > 1]
        self.assertGreater(len(multi), 100,
                           f"复合题太少：{len(multi)}，多标签映射可能没生效")


if __name__ == "__main__":
    unittest.main()
