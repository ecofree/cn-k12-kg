"""决策底稿的口径不变量。

这三条不是为了测函数，是为了把 2026-10-26 决策备忘里依赖的三个事实钉住：
  1. 学段判定必须认 m1a / m9b / mh1 三种前缀；
  2. 加外部题只能减少「低于题量下限」的点，不能增加；
  3. 去掉 smse 会让初中缺口变大——这是「隔离容易、替换有成本」的量化依据。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 这个文件测的是「含外部题的大池」行为（题型轮换、池单调性），
# 缺文件时整组跳过。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import decision_analysis as da  # noqa: E402


class TestStageMapping(unittest.TestCase):
    def test_three_prefixes(self):
        self.assertEqual(da.stage_of("m1a.2.6"), "primary")
        self.assertEqual(da.stage_of("m9b.31.2.3"), "junior")
        self.assertEqual(da.stage_of("mh1.1.1"), "senior")
        self.assertIsNone(da.stage_of("not-a-topic"))


class TestPoolMonotonicity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.topics, cls.own, cls.ext,
         _deps, _policy) = da.load()
        cls.usable = [q for q in cls.ext if q.get("usable_for_diagnosis")]

    def test_external_only_reduces_deficit(self):
        own = da.pool_stats(self.topics, self.own)
        opened = da.pool_stats(self.topics, self.own + self.usable)
        for stage in (da.PRIMARY, da.JUNIOR, da.SENIOR):
            self.assertLessEqual(
                opened[stage]["below_min"], own[stage]["below_min"],
                f"{stage}: 加外部题后缺口反而变大，口径或数据有问题")

    def test_smse_removal_costs_junior_coverage(self):
        no_smse = [q for q in self.usable
                   if q.get("source") != "smse-shanghai-zhongkao"]
        with_smse = da.pool_stats(self.topics, self.own + self.usable)["junior"]
        without = da.pool_stats(self.topics, self.own + no_smse)["junior"]
        self.assertGreater(
            without["below_min"], with_smse["below_min"],
            "去掉 smse 后初中缺口没有变大——说明它已经不承担结构作用，"
            "决策备忘第三节的替换成本需要重算")


if __name__ == "__main__":
    unittest.main()
