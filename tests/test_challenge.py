"""挑战层（TAL 竞赛题）的守门测试。

三条底线：
1. 挑战题**永不进诊断池**——超纲题答错不代表课内点没掌握。
2. 题目自身完整（选项 ≥2、答案在选项里、有解析）——竞赛题的价值全在解析。
3. 挂靠只到板块，年级差受控——二年级发六年级压轴题是劝退，不是挑战。
"""
from __future__ import annotations

import json
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import challenge, tiers  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402

DATA = ROOT / "data"


class TalImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = challenge.load()
        cls.by_id = {x["id"]: x for x in cls.items}

    def test_imported(self):
        self.assertGreater(len(self.items), 4000, "挑战题没导入或大量丢失")

    def test_never_for_diagnosis(self):
        bad = [x["id"] for x in self.items if x.get("for_diagnosis") is not False]
        self.assertFalse(bad, f"挑战题不得进诊断：{bad[:3]}")

    def test_all_challenge_flagged(self):
        bad = [x["id"] for x in self.items if not x.get("challenge")]
        self.assertFalse(bad)

    def test_item_is_complete(self):
        for x in self.items:
            with self.subTest(item=x["id"]):
                self.assertGreaterEqual(len(x["choices"]), 2)
                self.assertIn(x["answer"], {c["id"] for c in x["choices"]})
                self.assertGreaterEqual(len(x["solution"]), 10)
                self.assertTrue(x["prompt"].strip())

    def test_stage_and_strand_valid(self):
        for x in self.items:
            with self.subTest(item=x["id"]):
                self.assertIn(x["stage"], ("primary", "junior", "senior"))
                self.assertIn(x["strand"], set(challenge.CLUSTER_STRAND.values()) | {"formal"})

    def test_graph_never_loads_challenge(self):
        g = load_graph()
        ids = {q["id"] for bank in g.bank.values() for q in bank}
        overlap = ids & set(self.by_id)
        self.assertFalse(overlap, f"诊断池混入挑战题：{list(overlap)[:3]}")


class ChallengeLinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = {t["id"]: t for t in json.loads(
            (DATA / "topics.json").read_text(encoding="utf-8"))}
        cls.links = json.loads((DATA / "challenge" / "links.json")
                               .read_text(encoding="utf-8"))["samples"]
        cls.items = {x["id"]: x for x in challenge.load()}
        cls.tier = tiers.load()["topics"]

    def test_links_point_to_real_items(self):
        for tid, ids in self.links.items():
            for i in ids:
                with self.subTest(topic=tid, item=i):
                    self.assertIn(i, self.items)

    def test_only_core_topics_get_challenge(self):
        for tid in self.links:
            with self.subTest(topic=tid):
                self.assertEqual(self.tier[tid]["tier"], "core")

    def test_grade_gap_bounded(self):
        for tid, ids in self.links.items():
            grade = self.topics[tid].get("grade")
            for i in ids:
                gh = self.items[i].get("grade_hint")
                if gh is None or grade is None:
                    continue
                with self.subTest(topic=tid, item=i):
                    self.assertLessEqual(abs(gh - grade), 2)

    def test_sample_is_deterministic(self):
        index = challenge.index_by_strand(challenge.load())
        for tid in [t for t, v in self.tier.items() if v["tier"] == "core"][:20]:
            t = self.topics[tid]
            a = [x["id"] for x in challenge.candidates_for(
                t, self.tier[tid]["cluster"], index)]
            b = [x["id"] for x in challenge.candidates_for(
                t, self.tier[tid]["cluster"], index)]
            with self.subTest(topic=tid):
                self.assertEqual(a, b, "同一知识点两次抽样应一致")

    def test_candidate_and_old_hash_link_do_not_authorize_recommendation(self):
        topic = {"id": "T", "grade": 8, "name": "三角形", "mastery_criteria": ["解释角度关系"]}
        item = {"id": "Q", "stage": "junior", "strand": "geo", "level": 1, "prompt": "题面"}
        index = {("junior", "geo"): [item]}
        row = {"topic_id": "T", "item_id": "Q", "target_objectives": ["解释角度关系"],
               "rationale": "要求由角的关系列等式", "prerequisites": []}
        data = {"alignments": [row], "quarantine": {}}
        self.assertEqual(challenge.sample_for(topic, "j-triangle", index, alignment_data=data), [])
        row["review"] = {"status": "approved", "reviewer_type": "teacher",
                         "reviewer_id": "fixture-teacher", "record_id": "fixture-only",
                         "content_sha256": challenge.alignment_content_sha256(topic, item, row)}
        self.assertEqual(challenge.sample_for(topic, "j-triangle", index, alignment_data=data), [item])
        row["review"]["reviewer_type"] = "ai"
        self.assertEqual(challenge.sample_for(topic, "j-triangle", index, alignment_data=data), [])
        row["review"]["reviewer_type"] = "teacher"
        changed = copy.deepcopy(topic); changed["mastery_criteria"] = ["证明全等"]
        self.assertEqual(challenge.sample_for(changed, "j-triangle", index, alignment_data=data), [])
        data["quarantine"] = {"Q": {"reason": "缺图"}}
        self.assertEqual(challenge.sample_for(topic, "j-triangle", index, alignment_data=data), [])

    def test_known_missing_diagram_is_never_a_candidate(self):
        topic = {"id": "T", "grade": 8}
        item = {"id": "tal-j-256", "stage": "junior", "strand": "geo", "level": 1}
        self.assertEqual(challenge.candidates_for(topic, "j-triangle", {("junior", "geo"): [item]}), [])


if __name__ == "__main__":
    unittest.main()
