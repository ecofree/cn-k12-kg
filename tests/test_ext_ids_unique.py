"""外部题库 id 唯一性的守门测试。

**为什么要有这个测试**：2026-10-25 发现 `questions_ext.json` 里 **4429 个 id
被 10628 道题共用**（例如 ext-cmmmath-00001 同时是六年级「圆柱侧面展开」、
七年级「2022 的绝对值」和高二「sin α > sin β」）。根因在 importer：
`seq = len(imported) + 1` 按「本轮导入」计数，CMM 分小学/初中/高中三批
导入时每批都从 1 重数，于是撞车。

后果是**静默的**：图内存题库按知识点把题 append 进列表所以不受影响，
但任何按 id 取题的写法都会拿错对象——实测 id-keyed 视图里 6199 道题不可见，
其中 **546 道是可诊断题**（store.py 建 SQLite 时靠 `ext-{source}-{id}` 前缀
改名才躲开主键冲突，代价是库里的 id 与 JSON 里的对不上）。

更根本的原因：`validate.py` 的题库校验从来只加载 `questions.json`，
压根没看过 `questions_ext.json`。现在它两个都查了，这个测试再把「唯一性」
这条不变式钉死在测试里——工具改了也不会退化。
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



def load(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


class TestExternalIds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.own = load("questions.json")
        cls.ext = load("questions_ext.json")

    def test_own_ids_unique(self):
        """自有题 id 必须唯一（基础不变式，防止回归）。"""
        dupes = [k for k, v in collections.Counter(q["id"] for q in self.own).items()
                 if v > 1]
        self.assertFalse(dupes, f"自有题 id 重复：{dupes[:5]}")

    def test_external_ids_unique(self):
        """外部题 id 必须唯一——历史上有 4429 个重复。"""
        counter = collections.Counter(q["id"] for q in self.ext)
        dupes = {k: v for k, v in counter.items() if v > 1}
        self.assertFalse(dupes, f"外部题 id 重复 {len(dupes)} 个：{list(dupes)[:5]}")

    def test_no_cross_file_collision(self):
        """外部题 id 不得与自有题 id 撞车。"""
        own_ids = {q["id"] for q in self.own}
        clash = {q["id"] for q in self.ext if q["id"] in own_ids}
        self.assertFalse(clash, f"与自有题撞车 {len(clash)} 个：{list(clash)[:5]}")

    def test_total_question_count_unchanged(self):
        """重命名后总数必须一题不少——历史总量 23313 是审计基线。"""
        self.assertEqual(len(self.ext), 23313,
                         f"外部题总数变了（{len(self.ext)}），是不是丢了题？")

    def test_ext_prompt_uniqueness_per_topic(self):
        """同一知识点下题干不应完全重复（重复题会稀释诊断抽题的多样性）。"""
        per_topic = collections.defaultdict(set)
        dup = []
        for q in self.ext:
            tid = q.get("primaryTopicId") or q.get("topicId")
            if not tid:
                continue
            p = q.get("prompt", "")
            if p in per_topic[tid]:
                dup.append((q["id"], tid))
            per_topic[tid].add(p)
        # 外部题池允许少量重复（同源多次出现），但不能大面积坍塌
        self.assertLess(len(dup), len(self.ext) * 0.05,
                        f"同点重复题干过多：{len(dup)} 条")


if __name__ == "__main__":
    unittest.main()
