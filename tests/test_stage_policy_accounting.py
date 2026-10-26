"""分学段对账的口径守门测试。

**为什么要有这个测试**：2026-10-26 做体检时，`check_stage_policy.py` 里
「诊断池」被写成 `[q for q in allq if q.get("usable_for_diagnosis")]`。
自有题压根没有这个字段（它们本来就是诊断主库），于是 2235 道自有题
整段被过滤掉，小学「诊断池题量不足」被虚报成 169 个点（真值 24）。

这类错很危险，因为它**不报错、不缺字段、测试全绿**，只是数字悄悄变坏。
所以固化四条方向性不变式：

1. 诊断池 ⊆ 全库  →  诊断池题点对 ≤ 全库题点对
2. 诊断池 ⊆ 全库  →  诊断池不足点数 ≥ 全库不足点数
3. 诊断池包含通过用途门禁的自有题，隔离题与仅练习题不计入
4. 两套账的键必须同时存在（防止有人嫌麻烦把诊断池那套删了）

另外加一条：stage_policy.json 里的具体数字不能和实测漂移——
过期文档比没有文档更误导。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 缺文件时整组跳过，而不是报失败——否则 clone 下来第一件事就是红一片。
sys.path.insert(0, str(ROOT))

from cnkg.evidence import question_policy  # noqa: E402
from tools.check_stage_policy import build_report, stage_of  # noqa: E402


def load(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


@unittest.skipUnless((ROOT / "data" / "questions_ext.json").exists(),
                     "外部题库未下载，见 data/README.md")
class StagePolicyAccountingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = load("topics.json")
        cls.own = load("questions.json")
        cls.ext = load("questions_ext.json")
        cls.policy = load("stage_policy.json")
        cls.deps = load("dependencies.json")
        cls.report = build_report(
            cls.topics, cls.own, cls.ext, cls.policy, cls.deps)

    def test_usable_pool_never_exceeds_full_pool(self):
        """诊断池是全库的子集，题点对数不可能更多。"""
        for stage in ("primary", "junior", "senior"):
            r = self.report[stage]
            self.assertLessEqual(
                r["questions_pairs_usable"], r["questions_pairs"],
                f"{stage}: 诊断池题点对 {r['questions_pairs_usable']} "
                f"超过全库 {r['questions_pairs']}，说明过滤逻辑反了")

    def test_deficit_grows_when_pool_shrinks(self):
        """池子变小，不足的点只可能变多。"""
        for stage in ("primary", "junior", "senior"):
            r = self.report[stage]
            self.assertGreaterEqual(
                r["below_target_usable"], r["below_target"],
                f"{stage}: 诊断池不足 {r['below_target_usable']} "
                f"少于全库 {r['below_target']}，方向不可能对")

    def test_usable_pool_contains_eligible_own_questions(self):
        """通过用途门禁的自有题不能因没有导入准入字段而被漏计。

        这就是 2026-10-26 那个 bug 的靶子：自有题没有 usable_for_diagnosis
        字段，从合并列表过滤时会被整段丢掉。
        """
        by_stage = {s: 0 for s in ("primary", "junior", "senior")}
        stage_by_id = {t["id"]: stage_of(t) for t in self.topics}
        for q in self.own:
            if not question_policy(q)["diagnosis_eligible"]:
                continue
            tids = q.get("topicIds") or (
                [q.get("topicId")] if q.get("topicId") else [])
            for tid in tids:
                s = stage_by_id.get(tid)
                if s:
                    by_stage[s] += 1

        for stage in ("primary", "junior", "senior"):
            self.assertGreaterEqual(
                self.report[stage]["questions_pairs_usable"], by_stage[stage],
                f"{stage}: 诊断池 {self.report[stage]['questions_pairs_usable']} "
                f"少于可诊断自有题 {by_stage[stage]}，说明可用题被过滤掉了")

    def test_both_accountings_are_reported(self):
        """两套账必须同时存在，缺一套就是回到「只看全库」的老路。"""
        for stage in ("primary", "junior", "senior"):
            r = self.report[stage]
            for key in ("below_target", "below_target_usable",
                        "single_difficulty_topics",
                        "single_difficulty_topics_usable",
                        "type_mix_actual", "type_mix_actual_usable",
                        "difficulty_actual", "difficulty_actual_usable"):
                self.assertIn(key, r, f"{stage} 缺 {key}")

    def test_policy_numbers_do_not_drift(self):
        """stage_policy 里写死的数字必须和实测一致。

        这条会逼着人改文档，而不是让过期数字躺在策略文件里冒充现状。
        """
        from tools.audit_project import (
            audit_ext, audit_graph, audit_policy_drift)
        g = audit_graph(self.topics, self.deps)
        e = audit_ext(self.ext)
        usable_ext = [q for q in self.ext if q.get("usable_for_diagnosis")]
        cov = {}
        for stage in ("primary", "junior", "senior"):
            tids = [t["id"] for t in self.topics if stage_of(t) == stage]
            have = set()
            for q in usable_ext:
                have.update(q.get("topicIds") or (
                    [q.get("topicId")] if q.get("topicId") else []))
            cov[stage] = {
                "topics": len(tids),
                "pairs_without": sum(1 for t in tids if t not in have),
            }
        drift = audit_policy_drift(
            self.policy, e, {"external_coverage": cov}, g)
        self.assertEqual(
            drift, [],
            "stage_policy 有口径漂移，请同步文档或数据：\n" +
            "\n".join(f"  {d['where']}: 文档 {d['said']} / 实测 {d['actual']}"
                      for d in drift))


if __name__ == "__main__":
    unittest.main()
