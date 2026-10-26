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
        """应用题只配给核心点：工具点只到「练熟」，不要求迁移。"""
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
            q = {"type": qtype, "answer": it["answer"], "choices": [],
                 "prompt": it["prompt"], "explanation": it["explanation"]}
            if qtype == "numeric":
                q["verify"] = it["verify"]
            want = "verified" if qtype == "numeric" else "explained"
            with self.subTest(topic=it["topic"], prompt=(it["prompt"] or "")[:16]):
                got = evidence.grade(q)["level"]
                self.assertGreaterEqual(evidence.rank(got), evidence.rank(want),
                                        f"{it['topic']} 证据等级 {got}")


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
