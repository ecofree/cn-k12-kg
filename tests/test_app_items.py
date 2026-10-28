"""Declarative application-source guards through the historical read-only view.

The canonical JSON owns every answer and question ID. These compatibility
views retain existing field-level regression checks without a second author.
"""
from __future__ import annotations

import collections
from fractions import Fraction
import hashlib
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from cnkg import evidence  # noqa: E402
from cnkg.verify import compare, evaluate  # noqa: E402
from cnkg.task_families import task_contract  # noqa: E402
import app_items  # noqa: E402
import write_application_items as writer  # noqa: E402

ITEMS = app_items.all_items()
TIERS = json.loads((ROOT / "data" / "topic_tiers.json").read_text(encoding="utf-8"))["topics"]
AUTHORED = {row['id']: row for row in json.loads(
    (ROOT / 'data/question_sources/application.json').read_text())['questions']}
V7_REVIEW = {row['id']: row for row in json.loads(
    (ROOT / 'docs/reviews/v7-bank-metadata-review.json').read_text())['tasks']}
BINDINGS = json.loads((ROOT / 'data/skills/catalog-bindings.json').read_text())['tasks']
# These two preserved IDs were reviewed as a given difference / direct function
# substitution. They do not supply an advanced application merely by having
# carried that historical label. The exact authored tasks are checked below.
REVIEWED_BASIC_APPLICATIONS = {
    "m8b.22.2-q102": {"topic": "m8b.22.2", "verify": "22-19",
                      "family_id": "arithmetic.fixed-discount-difference"},
    "mh4.5.3.1-q102": {"topic": "mh4.5.3.1", "verify": "-1+3",
                       "family_id": "function.value-at-given-point"},
}


V6_REVIEWED_BASIC = {
    "m8b.24.4-q102": {"topic": "m8b.24.4", "verify": "50*0.24"},
    "mh5.7.1.1-q102": {"topic": "mh5.7.1.1", "verify": "2/5"},
    "m1a.5.5-q101": {"topic": "m1a.5.5", "verify": "16-7"},
    "m1a.5.5-q102": {"topic": "m1a.5.5", "verify": "14-6"},
}

# Explicitly inspected design changes. These are finite computations with
# supplied quantities/rules, not evidence of empirically calibrated difficulty.
# The source and answer contract must also match the individual V7 review.
V7_REVIEWED_DESIGN = {
    'm7b.8.3-q102': ('basic', '能先求完全平方数的算术平方根，再计算两个结果的乘积', 3*2),
    'm7b.9.2.1-q102': ('standard', '能按给定水平和竖直路线计算长度并作单位比例换算', (4+3)*50),
    'm9a.26.3-q102': ('basic', '能由二次函数解析式求与y轴交点的纵坐标', 0**2-4*0+3),
    'mh1.5.4.3-q102': ('standard', '能用正切的180度周期求相差一个周期的函数值', Fraction(-2, -1)),
    'mh3.1.4.1-q102': ('basic', '能计算已给平面法向量与直线方向向量的数量积', 1*1+1*(-1)+0*3),
}
V7_REVIEWED_TOPIC_LEVELS = {
    'm7b.8.3': {'standard', 'basic'},
    'm7b.9.2.1': {'standard'},
    'm9a.26.3': {'standard', 'basic'},
    'mh1.5.4.3': {'standard'},
    'mh3.1.4.1': {'standard', 'basic'},
}


def assert_current_v7_review(test, item):
    identity = item['id']
    source, review = AUTHORED[identity], V7_REVIEW[identity]
    actual = {key: value for key, value in item.items() if key not in ('topic', 'why')}
    test.assertEqual(source, actual)
    canonical = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    test.assertEqual(hashlib.sha256(canonical).hexdigest(), review['current_source_task_sha256'])
    test.assertEqual(task_contract(source, BINDINGS['bank:' + identity]), review['current_task_contract'])
    test.assertEqual('ai', review['reviewer_kind'])
    test.assertIs(False, review['independent_teacher_review'])
    test.assertTrue(review['mathematical_reason'])
    test.assertTrue(review['observation_boundary'])
    test.assertEqual(source['tested_criterion_indices'],
                     [goal['criterion_index'] for goal in review['current_goal_mapping']])
    return review


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
                reviewed_basic = (set(REVIEWED_BASIC_APPLICATIONS) | set(V6_REVIEWED_BASIC)
                                  | {qid for qid, row in V7_REVIEWED_DESIGN.items() if row[0] == 'basic'})
                allowed = ("basic",) if it["id"] in reviewed_basic else ("standard", "advanced")
                self.assertIn(it.get("difficulty", "standard"), allowed)

    def test_advanced_items_say_why(self):
        """advanced 不是随手标的：必须在 why 里写清难在哪。"""
        for it in ITEMS:
            if it.get("difficulty") == "advanced":
                with self.subTest(topic=it["topic"]):
                    self.assertGreaterEqual(len(it.get("why") or ""), 8)

    def test_legacy_why_preserves_complete_authored_reasons_without_a_caption(self):
        by_id = {item['id']: item for item in ITEMS}
        # The first task actually has an advanced design label and a complete
        # unprefixed reason; the old partition-only view discarded that text.
        for identity in ('mh4.4.3.1-q102', 'm7b.8.3-q102', 'm8b.22.2-q102'):
            with self.subTest(task=identity):
                item = by_id[identity]
                assert_current_v7_review(self, item)
                self.assertEqual(AUTHORED[identity]['difficulty_basis'], item['why'])
                self.assertGreater(len(item['why']), 8)
        old = by_id['mh4.5.3.1-q102']
        self.assertEqual(AUTHORED[old['id']]['difficulty_basis'].partition('：')[2], old['why'])
        rebuilt, errors = writer.build([by_id['mh4.4.3.1-q102']], [])
        self.assertEqual([], errors)
        self.assertEqual([AUTHORED['mh4.4.3.1-q102']], rebuilt)

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
        """保留实际档位与明确复核的缺口；不把基础换算冒充advanced。"""
        by_topic: dict[str, set[str]] = collections.defaultdict(set)
        for it in ITEMS:
            by_topic[it["topic"]].add(it.get("difficulty", "standard"))
        target = [t for t in by_topic
                  if t[:2] in {"m7", "m8", "m9"} or t.startswith("mh")]
        reviewed_topics = {row["topic"] for row in (REVIEWED_BASIC_APPLICATIONS | V6_REVIEWED_BASIC).values()}
        expected = {t: V7_REVIEWED_TOPIC_LEVELS.get(
            t, {"standard", "basic"} if t in reviewed_topics else {"standard", "advanced"}) for t in target}
        thin = [t for t in target if by_topic[t] != expected[t]]
        self.assertEqual(sorted(thin), [], "保持既有或具体审查后的设计档位，不能为凑配额伪造advanced")

    def test_reviewed_basic_applications_match_the_actual_observation_boundary(self):
        review = json.loads((ROOT / "docs/reviews/v5-batch2-secondary-ai-review.json").read_text(encoding="utf-8"))
        reviewed = {row["id"]: row for row in review["question_review"]}
        by_id = {item["id"]: item for item in ITEMS}
        current_goals = {
            'm8b.22.2-q102': '能按分段收费及固定减免规则计算实际费用',
            'mh4.5.3.1-q102': '能在已给极值点处代入求函数值',
        }
        for qid, expected in REVIEWED_BASIC_APPLICATIONS.items():
            with self.subTest(question=qid):
                actual, prior_review = by_id[qid], reviewed[qid]["after"]
                current_review = assert_current_v7_review(self, actual)
                # The old bounded decision still applies to its exact old
                # task. A V7 source-bound review is required for the changed
                # version; the old ID alone never grants an exception.
                for key in ('prompt', 'answer'):
                    self.assertEqual(current_review['baseline_task_contract'][key], prior_review[key])
                self.assertTrue(prior_review['alignment_note'])
                self.assertEqual(expected['family_id'], current_review['baseline_metadata']['family_id'])
                self.assertEqual(expected['verify'], current_review['baseline_task_contract']['verify'])
                self.assertEqual(expected["topic"], actual["topic"])
                self.assertEqual([current_goals[qid]],
                                 [goal['statement'] for goal in current_review['current_goal_mapping']])
                self.assertEqual("basic", actual["difficulty"])
                self.assertEqual("component_practice_only", actual["target_alignment"])
                self.assertFalse(actual["diagnosis_eligible"])
                self.assertFalse(actual["for_diagnosis"])
                self.assertEqual("design_label_pending_student_data", actual["difficulty_calibration"])
                self.assertGreaterEqual(len(actual["why"]), 8)

    def test_v7_design_changes_only_cover_the_exact_reviewed_computations(self):
        by_id = {item['id']: item for item in ITEMS}
        for identity, (level, goal, expected) in V7_REVIEWED_DESIGN.items():
            with self.subTest(task=identity):
                actual = by_id[identity]
                review = assert_current_v7_review(self, actual)
                self.assertEqual(level, actual['difficulty'])
                self.assertEqual([goal], [entry['statement'] for entry in review['current_goal_mapping']])
                self.assertEqual(Fraction(expected), Fraction(actual['answer']))
                self.assertEqual('design_label_pending_student_data', actual['difficulty_calibration'])
                self.assertIn('未', actual['difficulty_basis'])
        # Keep the concrete old errors observable: neither diagonal-distance
        # answers nor a normal/direction dot product certify the old behavior.
        self.assertNotEqual('250', by_id['m7b.9.2.1-q102']['answer'])
        self.assertIn('包含于平面', by_id['mh3.1.4.1-q102']['alignment_note'])
        self.assertIn('严格平行', by_id['mh3.1.4.1-q102']['alignment_note'])

    def test_revised_discount_task_really_computes_payment_without_claiming_a_model(self):
        item = next(row for row in ITEMS if row['id'] == 'm8b.22.2-q102')
        review = assert_current_v7_review(self, item)
        expected = 10 + Fraction(24, 10) * (8-3) - 3
        self.assertEqual(Fraction(19), expected)
        self.assertEqual(Fraction(item['answer']), expected)
        self.assertIn('不要求独立建立函数模型', item['difficulty_basis'])
        self.assertNotIn('所问差额已由该条件直接给出', item['difficulty_basis'])
        self.assertEqual('money.tiered-charge-then-fixed-discount', item['family_id'])
        self.assertEqual('3', review['baseline_task_contract']['answer'])
        self.assertEqual('19', review['current_task_contract']['answer'])
        self.assertFalse(item['diagnosis_eligible'])

    def test_v6_design_downgrades_are_bound_to_the_actual_reviewed_items(self):
        reviewed = {row["id"]: row for row in json.loads(
            (ROOT / "docs/reviews/v6-corrections-ai-review.json").read_text(encoding="utf-8"))["tasks"]}
        by_id = {item["id"]: item for item in ITEMS}
        for qid, expected in V6_REVIEWED_BASIC.items():
            with self.subTest(question=qid):
                actual, decision = by_id[qid], reviewed[qid]
                self.assertEqual(actual["prompt"], decision["new_prompt"])
                self.assertEqual(actual["answer"], decision["new_answer"])
                self.assertEqual(actual["alignment_note"], decision["observation_boundary"])
                self.assertEqual(actual["verify"], expected["verify"])
                self.assertEqual(actual["topic"], expected["topic"])
                self.assertEqual(actual["difficulty"], "basic")
                self.assertEqual(actual["target_alignment"], "component_practice_only")
                self.assertFalse(actual["diagnosis_eligible"])
                self.assertFalse(actual["for_diagnosis"])
                self.assertEqual(decision["decision"], "practice_only")

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
