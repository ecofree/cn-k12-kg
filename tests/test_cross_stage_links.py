"""跨学段承接链的守门测试。

**为什么要有这个测试**：2026-10-25 发现 check_stage_policy.py 的承接链
匹配逻辑有个静默 bug——它拿策略里的**知识点 id** 去和知识点**名字**做
前缀匹配（`name.startswith("m3b.2.1")`），永远匹配不上，导致 7 条
承接链被全量误报为「缺失」，而事实上这些边早就落在 dependencies.json 里。

这个测试固化三件事：
1. 策略要求的每条承接链，图上必须有对应的边（漏建会被抓住）
2. link_ids 只认真实知识点 id，名字里的数字（「100以内」）不会误判
3. link_satisfied 对没有边的链必须返回 False（防止匹配器退化成恒真）

另外附带依赖图的完整性检查：边必须引用真实知识点、不能重复、
不能自环、不能有环、跨学段边必须带理由。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.check_stage_policy import link_ids, link_satisfied  # noqa: E402


def load(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


class TestCrossStageLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = load("topics.json")
        cls.tmap = {t["id"]: t for t in cls.topics}
        cls.deps = load("dependencies.json")
        cls.policy = load("stage_policy.json")
        cls.required = cls.policy["cross_stage"]["required_links"]

    def test_required_links_all_satisfied(self):
        """策略要求的承接链必须全部落成图上的边。"""
        missing = [l["from"] for l in self.required
                   if not link_satisfied(l, self.deps, self.tmap)]
        self.assertFalse(missing, f"承接链缺失：{missing}")

    def test_required_links_have_valid_ids(self):
        """每条链的 from/to 都必须能解析出真实知识点 id。

        历史教训：策略里曾写过「m3b.2.1 分数的意义」，而 m3b.2.1 实际是
        「口算除法」；「m1b.6.3 100以内数的认识」实际是「连续两问」。
        id 与名字对不上时，解析结果必须是「有 id 可查」而不是静默按名字猜。
        """
        for l in self.required:
            self.assertTrue(link_ids(l["from"], self.tmap),
                            f"from 解析不出 id：{l['from']}")
            self.assertTrue(link_ids(l["to"], self.tmap),
                            f"to 解析不出 id：{l['to']}")

    def test_link_ids_ignores_numbers_in_names(self):
        """名字里的数字不是 id：「100以内数的认识」不得产生伪 id。"""
        ids = link_ids("m4b.4.1 小数的认识 / 100以内数的认识", self.tmap)
        self.assertEqual(ids, ["m4b.4.1"])

    def test_link_ids_multi_target(self):
        ids = link_ids("m7a.2.1.1 有理数的加法 / m7a.2.2.1 有理数的乘法", self.tmap)
        self.assertEqual(ids, ["m7a.2.1.1", "m7a.2.2.1"])

    def test_link_ids_rejects_unknown_id(self):
        """不存在的 id 不应被当作候选（防拼错后静默匹配）。"""
        self.assertEqual(link_ids("m9z.9.9 不存在的点", self.tmap), [])

    def test_stale_id_is_reported_missing_not_matched(self):
        """旧策略里的失效 id 必须报缺失，而不是被名字模糊匹配混过去。

        「m3b.2.1 分数的意义」：m3b.2.1 存在但它是「口算除法」，
        与 m7a.1.2.1 之间没有边——必须判为未落地。
        """
        stale = {"from": "m3b.2.1 分数的意义",
                 "to": "m7a.1.2.1 有理数的概念与分类",
                 "reason": "数系第一次扩充"}
        self.assertFalse(link_satisfied(stale, self.deps, self.tmap))

    def test_link_satisfied_positive(self):
        """真实存在的边必须判为已落地（含多目标任一命中）。"""
        link = {"from": "m5b.1.1 因数和倍数",
                "to": "m7a.4.1 整式 / m8a.16.2 整式的乘法"}
        self.assertTrue(link_satisfied(link, self.deps, self.tmap))

    def test_link_satisfied_negative(self):
        """两个真实存在但不相连的点必须判为未落地。"""
        link = {"from": "m5b.1.1 因数和倍数", "to": "m7a.2.3.2 科学记数法"}
        self.assertFalse(link_satisfied(link, self.deps, self.tmap))

    def test_graph_integrity(self):
        """依赖图完整性：端点存在、无重复、无自环、无环、跨学段边带理由。"""
        # 端点必须存在
        dangling = [e for e in self.deps
                    if e["prerequisiteId"] not in self.tmap
                    or e.get("topicId") not in self.tmap]
        self.assertFalse(dangling, f"边引用了不存在的知识点：{dangling[:3]}")

        # 不能重复
        pairs = [(e["prerequisiteId"], e["topicId"]) for e in self.deps]
        dupes = {p for p in pairs if pairs.count(p) > 1}
        self.assertFalse(dupes, f"重复边：{dupes}")

        # 不能自环
        self_loops = [e for e in self.deps if e["prerequisiteId"] == e["topicId"]]
        self.assertFalse(self_loops, f"自环：{self_loops[:3]}")

        # 不能有环（先修图必须是有向无环图）
        adj: dict[str, list[str]] = {}
        for e in self.deps:
            adj.setdefault(e["prerequisiteId"], []).append(e["topicId"])
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(self.tmap, WHITE)

        def dfs(u: str) -> bool:
            color[u] = GRAY
            for v in adj.get(u, []):
                if color[v] == GRAY:
                    return False
                if color[v] == WHITE and not dfs(v):
                    return False
            color[u] = BLACK
            return True

        cyclic = [t for t in self.tmap if color[t] == WHITE and not dfs(t)]
        self.assertFalse(cyclic, f"依赖图存在环，涉及：{cyclic[:5]}")

        # 跨学段边必须带理由（承接链是教学事实，不是猜的）
        def stage(tid: str) -> str:
            g = self.tmap[tid].get("grade")
            return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")

        cross = [e for e in self.deps
                 if stage(e["prerequisiteId"]) != stage(e["topicId"])]
        no_reason = [e for e in cross if not e.get("reason", "").strip()]
        self.assertFalse(no_reason, f"跨学段边缺理由：{no_reason[:3]}")
        self.assertGreaterEqual(len(cross), len(self.required),
                                "跨学段边数不应少于策略承接链数")


if __name__ == "__main__":
    unittest.main()
