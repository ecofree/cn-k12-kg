"""应用题批次的护栏：数据层面的约束，不依赖 data/questions.json 是否已写入。

写库工具会拦作弊，但它是「跑一遍才知道」；这里的测试保证任何人改
`tools/app_items/*.py` 时，坏数据在 CI 第一步就红，而不是等到写库。
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from cnkg import evidence  # noqa: E402
from cnkg.verify import compare, evaluate  # noqa: E402
import app_items  # noqa: E402
import write_application_items as writer  # noqa: E402

ITEMS = app_items.all_items()
TIERS = json.loads((ROOT / "data" / "topic_tiers.json").read_text(encoding="utf-8"))["topics"]


class BatchShapeTest(unittest.TestCase):
    def test_batches_are_not_empty(self):
        self.assertTrue(app_items.BATCHES)
        for batch in app_items.BATCHES:
            self.assertGreater(len(batch.ITEMS), 0, batch.__name__)

    def test_required_fields(self):
        for it in ITEMS:
            with self.subTest(topic=it.get("topic"), prompt=(it.get("prompt") or "")[:16]):
                self.assertTrue(it.get("topic"))
                self.assertGreaterEqual(len(it.get("prompt") or ""), 15, "题干太短，多半不是应用题")
                self.assertTrue(str(it.get("answer") or "").strip())
                self.assertGreaterEqual(len(it.get("explanation") or ""), 20)
                self.assertIn(it.get("difficulty", "standard"), ("standard", "advanced"))

    def test_advanced_items_say_why(self):
        """advanced 不是随手标的：必须在 why 里写清难在哪。"""
        for it in ITEMS:
            if it.get("difficulty") == "advanced":
                with self.subTest(topic=it["topic"]):
                    self.assertGreaterEqual(len(it.get("why") or ""), 8)

    def test_topics_are_core(self):
        """现有应用题批次归属独立教学点；不排除工具点也需要理解和应用。"""
        for it in ITEMS:
            with self.subTest(topic=it["topic"]):
                self.assertEqual(TIERS.get(it["topic"], {}).get("tier"), "core")

    def test_no_duplicate_prompts(self):
        seen = collections.Counter(
            "".join((it.get("prompt") or "").split()) for it in ITEMS)
        dup = [p for p, n in seen.items() if n > 1]
        self.assertEqual(dup, [], "同一道题写了两遍")


class BatchMachineCheckTest(unittest.TestCase):
    def test_numeric_verify_matches_answer(self):
        for it in ITEMS:
            if it.get("type", "numeric") != "numeric":
                continue
            with self.subTest(topic=it["topic"], prompt=(it["prompt"] or "")[:16]):
                self.assertTrue(it.get("verify"), "数值题必须有 verify")
                self.assertTrue(compare(it["answer"], evaluate(it["verify"])))

    def test_short_answers_have_accept(self):
        for it in ITEMS:
            if it.get("type") == "short":
                with self.subTest(topic=it["topic"]):
                    self.assertIn(it["answer"], it.get("accept") or [])

    def test_evidence_level(self):
        """数值题要 verified（解析末步可复算），短答至少 explained。"""
        for it in ITEMS:
            qtype = it.get("type", "numeric")
            q = {"type": qtype, "answer": it["answer"], "choices": it.get("choices", []),
                 "prompt": it["prompt"], "explanation": it["explanation"]}
            if qtype == "numeric":
                q["verify"] = it["verify"]
            want = "verified" if qtype == "numeric" else "explained"
            with self.subTest(topic=it["topic"], prompt=(it["prompt"] or "")[:16]):
                got = evidence.grade(q)["level"]
                self.assertGreaterEqual(evidence.rank(got), evidence.rank(want),
                                        f"{it['topic']} 证据等级 {got}")


class CoverageTest(unittest.TestCase):
    """保留已有覆盖，并将资源分层修正后新增的缺口逐项列入工作清单。

    题目存在不证明目标对齐；新增核心点仍有真实缺口。不能通过压低
    核心点分母来继续声称全覆盖，也不能默默丢掉既有应用题。
    """

    def test_every_application_gap_is_explicit_and_no_stale_gap_is_claimed(self):
        covered = {it["topic"] for it in ITEMS}
        want = {tid for tid, info in TIERS.items() if info.get("tier") == "core"}
        backlog = json.loads((ROOT / "data" / "coverage_backlog.json").read_text(encoding="utf-8"))
        entries = backlog["items"]
        declared = {row["topic_id"] for row in entries}
        self.assertEqual(len(entries), len(declared), "缺口不能重复计数")
        self.assertEqual(want - covered, declared, "应用题新增缺口或已关闭缺口必须同步登记")
        for row in entries:
            self.assertEqual(row["status"], "open")
            self.assertTrue(row["target_objectives"])
            self.assertTrue(row["resource_needed"])

    def test_points_above_primary_have_standard_and_advanced(self):
        """初中与高中每点都要两种难度：standard 会用、advanced 会换一步想。"""
        by_topic: dict[str, set[str]] = collections.defaultdict(set)
        for it in ITEMS:
            by_topic[it["topic"]].add(it.get("difficulty", "standard"))
        target = [t for t in by_topic
                  if t[:2] in {"m7", "m8", "m9"} or t.startswith("mh")]
        thin = [t for t in target if by_topic[t] != {"standard", "advanced"}]
        self.assertEqual(sorted(thin), [], "初中与高中每点应各有一道 standard 与 advanced")

    def test_primary_points_are_not_over_drilled(self):
        """小学基础点按用户要求「不堆题」：每点至少 2 道，但**不强制 advanced**。

        基础点的熟练度靠 basic 题练，应用题只用来「搬到情境里」，
        所以这一档只保底够用，不设难度门槛。
        """
        counts = collections.Counter(it["topic"] for it in ITEMS)
        primary = [t for t in counts if t[:2] in
                   {"m1", "m2", "m3", "m4", "m5", "m6"}]
        thin = sorted(t for t in primary if counts[t] < 2)
        self.assertEqual(thin, [], "小学每点至少配 2 道应用题")


class BatchPlacementTest(unittest.TestCase):
    def test_items_live_in_their_own_book_module(self):
        """`j8a.py` 里只放八年级上册的题——放错册会让按册 review 失效。"""
        for batch in app_items.BATCHES:
            name = batch.__name__.rsplit(".", 1)[-1]
            if name.startswith("primary"):
                bad = [it["topic"] for it in batch.ITEMS
                       if not it["topic"].startswith("m")]
            elif name.startswith("h"):
                # 高中模块名形如 h1a / h5b，对应知识点前缀 mh1 / mh5
                book = "m" + name[:2]
                bad = [it["topic"] for it in batch.ITEMS
                       if not it["topic"].startswith(book)]
            else:
                book = "m" + name[1:]
                bad = [it["topic"] for it in batch.ITEMS
                       if not it["topic"].startswith(book)]
            with self.subTest(batch=name):
                self.assertEqual(bad, [], f"{name} 里混进了别册的题")


class IdStabilityTest(unittest.TestCase):
    def test_rerun_keeps_ids(self):
        """同一批题重跑两次，题号和顺序必须完全一致（否则 diff 无法 review）。"""
        first, errors = writer.build(ITEMS, [])
        self.assertEqual(errors, [])
        second, errors2 = writer.build(ITEMS, [])
        self.assertEqual(errors2, [])
        self.assertEqual([q["id"] for q in first], [q["id"] for q in second])

    def test_ids_start_at_101_per_topic(self):
        built, _ = writer.build(ITEMS, [])
        by_topic: dict[str, list[str]] = collections.defaultdict(list)
        for q in built:
            by_topic[q["topicId"]].append(q["id"])
        for tid, ids in by_topic.items():
            with self.subTest(topic=tid):
                self.assertEqual(ids[0], f"{tid}-q101")
                self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
