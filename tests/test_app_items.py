"""Declarative application-source guards through the historical read-only view.

The canonical JSON owns every answer and question ID. These compatibility
views retain existing field-level regression checks without a second author.
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
# These two preserved IDs were reviewed as a given difference / direct function
# substitution. They do not supply an advanced application merely by having
# carried that historical label. The exact authored tasks are checked below.
REVIEWED_BASIC_APPLICATIONS = {
    "m8b.22.2-q102": {"topic": "m8b.22.2", "verify": "22-19",
                      "family_id": "arithmetic.fixed-discount-difference"},
    "mh4.5.3.1-q102": {"topic": "mh4.5.3.1", "verify": "-1+3",
                       "family_id": "function.value-at-given-point"},
}


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
                allowed = ("basic",) if it["id"] in REVIEWED_BASIC_APPLICATIONS else ("standard", "advanced")
                self.assertIn(it.get("difficulty", "standard"), allowed)

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

    def test_secondary_design_levels_keep_the_reviewed_advanced_gaps_explicit(self):
        """保留既有两档覆盖；两项明确复核的基础任务不能冒充 advanced。"""
        by_topic: dict[str, set[str]] = collections.defaultdict(set)
        for it in ITEMS:
            by_topic[it["topic"]].add(it.get("difficulty", "standard"))
        target = [t for t in by_topic
                  if t[:2] in {"m7", "m8", "m9"} or t.startswith("mh")]
        reviewed_topics = {row["topic"] for row in REVIEWED_BASIC_APPLICATIONS.values()}
        thin = [t for t in target if by_topic[t] != ({"standard", "basic"} if t in reviewed_topics else {"standard", "advanced"})]
        self.assertEqual(sorted(thin), [], "保持既有或具体审查后的设计档位，不能为凑配额伪造advanced")

    def test_reviewed_basic_applications_match_the_actual_observation_boundary(self):
        review = json.loads((ROOT / "docs/reviews/v5-batch2-secondary-ai-review.json").read_text(encoding="utf-8"))
        reviewed = {row["id"]: row for row in review["question_review"]}
        by_id = {item["id"]: item for item in ITEMS}
        for qid, expected in REVIEWED_BASIC_APPLICATIONS.items():
            with self.subTest(question=qid):
                actual, prior_review = by_id[qid], reviewed[qid]["after"]
                for key in ("prompt", "answer", "alignment_note"):
                    self.assertEqual(actual[key], prior_review[key], "范围变化须重新核对，不能复用按ID放行")
                self.assertEqual(expected["topic"], actual["topic"])
                self.assertEqual(expected["family_id"], actual["family_id"])
                self.assertEqual(expected["verify"], actual["verify"])
                self.assertEqual("basic", actual["difficulty"])
                self.assertEqual("topic_only_practice", actual["target_alignment"])
                self.assertFalse(actual["diagnosis_eligible"])
                self.assertFalse(actual["for_diagnosis"])
                self.assertEqual("design_label_pending_student_data", actual["difficulty_calibration"])
                self.assertGreaterEqual(len(actual["why"]), 8)

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
