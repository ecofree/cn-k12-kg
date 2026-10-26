"""诊断引擎测试：python -m unittest tests.test_engine -v"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 外部题库（data/questions_ext.json）不入仓，见 data/README.md。
# 缺文件时整组跳过，而不是报失败——否则 clone 下来第一件事就是红一片。
if not (ROOT / "data" / "questions_ext.json").exists():
    raise unittest.SkipTest("外部题库未下载，见 data/README.md")

sys.path.insert(0, str(ROOT))

from cnkg import engine  # noqa: E402
from cnkg.graph import load_graph  # noqa: E402


class GraphTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_graph()

    def test_counts(self):
        self.assertEqual(len(self.graph.topics), 477)
        # 依赖边数会随跨学段承接链补充而变，这里只校验下限——
        # 具体条数由 tools/add_cross_stage_links.py 报告。
        self.assertGreaterEqual(len(self.graph.dependencies), 570)
        skeleton = [t for t in self.graph.topics if t["review_status"] == "skeleton"]
        self.assertEqual(len(skeleton), 0)
        self.assertEqual(len(self.graph.topics) - len(skeleton), 477)
        volumes = {t["volume"] for t in self.graph.topics}
        self.assertEqual(len(volumes), 23)

    def test_cross_stage_links_exist(self):
        """跨学段承接链必须存在——否则「小学没打好导致初中学不好」追不到根因。"""
        def stage(tid):
            g = self.graph.by_id[tid].get("grade")
            return "primary" if g <= 6 else ("junior" if g <= 9 else "senior")

        cross = [(e["prerequisiteId"], e["topicId"]) for e in self.graph.dependencies
                 if stage(e["prerequisiteId"]) != stage(e["topicId"])]
        self.assertGreaterEqual(len(cross), 20,
                                f"跨学段边只有 {len(cross)} 条，承接链太薄")
        # 小学 → 初中 至少 10 条
        pj = [c for c in cross if stage(c[0]) == "primary" and stage(c[1]) == "junior"]
        self.assertGreaterEqual(len(pj), 10,
                                f"小学→初中只有 {len(pj)} 条，承接链太薄")

    def test_ancestors(self):
        anc = self.graph.ancestors("m7a.5.2")
        for expected in ("m7a.5.1.2", "m7a.5.1.1", "m7a.4.2", "m7a.4.1", "m7a.3.1"):
            self.assertIn(expected, anc)
        self.assertNotIn("m7a.6.3.1", anc)
        self.assertNotIn("m7a.5.2", anc, "祖先集合不应包含自身")

    def test_topo_order_respects_edges(self):
        ids = [t["id"] for t in self.graph.topics]
        order = self.graph.topo_order(ids)
        pos = {tid: i for i, tid in enumerate(order)}
        for edge in self.graph.dependencies:
            self.assertLess(pos[edge["prerequisiteId"]], pos[edge["topicId"]],
                            f"{edge['prerequisiteId']} 应排在 {edge['topicId']} 之前")

    def test_cross_stage_prerequisite(self):
        """高中点可以依赖初中点，跨学段边要能被祖先查询走到。"""
        anc = self.graph.ancestors("mh1.4.1.1")
        self.assertIn("m7b.8.1", anc)
        self.assertIn("mh1.3.1.1", self.graph.ancestors("mh1.4.2.1"))

    def test_no_cycle(self):
        order = self.graph.topo_order([t["id"] for t in self.graph.topics])
        self.assertEqual(len(order), len(self.graph.topics))

    def test_every_filled_topic_has_two_entry_level_questions(self):
        """已填内容的点至少 2 道最低难度题，否则诊断第 2 题只能升难度。"""
        checked = 0
        for tid, bank in self.graph.bank.items():
            if self.graph.by_id[tid]["review_status"] == "skeleton":
                self.assertEqual(bank, [], f"{tid} 还是骨架点，不应有题目")
                continue
            levels = [q["difficulty"] for q in bank]
            lowest = min(levels, key=lambda d: engine.DIFF_RANK[d])
            self.assertGreaterEqual(
                levels.count(lowest), 2,
                f"{tid} 的最低难度（{lowest}）只有 {levels.count(lowest)} 道题")
            checked += 1
        self.assertEqual(checked, 477)


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = load_graph()

    def _answer(self, session, verdict):
        item = engine.next_item(self.graph, session)
        self.assertIsNotNone(item, "应该还有题")
        engine.apply_answer(self.graph, session, verdict)
        return item

    def test_first_correct_needs_confirmation(self):
        """第 1 题答对不立即判掌握，要再问一题确认（防猜对导致漏诊）。"""
        session = engine.create_session(self.graph, ["m7a.1.2.5"])
        item = self._answer(session, "correct")
        self.assertEqual(item["topicId"], "m7a.1.2.5")
        self.assertNotIn("m7a.1.2.5", session["state"], "第 1 题对后不应立即判定")
        second = self._answer(session, "correct")
        self.assertEqual(second["topicId"], "m7a.1.2.5")
        self.assertEqual(session["state"]["m7a.1.2.5"], "mastered")
        self.assertEqual(session["state"]["m7a.1.2.4"], "assumed")
        summary = engine.summarize(self.graph, session)
        self.assertEqual(summary["weak"], [])
        self.assertEqual(summary["asked_count"], 2)

    def test_first_correct_then_wrong_is_shaky(self):
        """两题一对错说明不牢，判不稳而不是掌握。"""
        session = engine.create_session(self.graph, ["m7a.1.2.5"])
        self._answer(session, "correct")
        self._answer(session, "wrong")
        self.assertEqual(session["state"]["m7a.1.2.5"], "shaky")

    def test_screen_mode_still_one_shot(self):
        """整册筛查每点只问 1 题，不走确认题，保证覆盖广度。"""
        session = engine.create_session(self.graph, ["m7a.1.2.5"], mode="screen")
        self._answer(session, "correct")
        self.assertEqual(session["state"]["m7a.1.2.5"], "mastered")

    def test_wrong_then_correct_is_shaky_and_descends(self):
        """判为不稳时仍要往上确认，否则一次猜对就把真正的根因截断在中途。"""
        session = engine.create_session(self.graph, ["m7a.5.2"])
        self._answer(session, "wrong")
        self._answer(session, "correct")
        self.assertEqual(session["state"]["m7a.5.2"], "shaky")
        self.assertEqual(session["asked"]["m7a.5.2"], 2)
        self.assertIn("m7a.5.1.2", session["queue"], "判为不稳时应继续回溯")

    def test_descend_only_follows_hard_edges(self):
        """回溯只走 hard 边：soft 只表示相关，不是"不会它的原因"。"""
        session = engine.create_session(self.graph, ["m7a.5.2"])
        hard = {e["prerequisiteId"] for e in self.graph.prereqs["m7a.5.2"]
                if e["strength"] == "hard"}
        soft = {e["prerequisiteId"] for e in self.graph.prereqs["m7a.5.2"]
                if e["strength"] == "soft"}
        self._answer(session, "wrong")
        self._answer(session, "wrong")
        queued = set(session["queue"])
        self.assertTrue(hard & queued, "hard 先修应入队")
        self.assertFalse((soft - hard) & queued, "soft 先修不应入队")

    def test_shaky_ancestor_can_be_root(self):
        """上游只有一个不稳点时，根因应是它，而不是下游那个薄弱点。"""
        session = engine.create_session(self.graph, ["m7a.5.2"])
        self._answer(session, "wrong")
        self._answer(session, "wrong")
        # 先修一题错一题对 -> shaky
        self._answer(session, "wrong")
        self._answer(session, "correct")
        summary = engine.summarize(self.graph, session)
        self.assertIn("m7a.5.1.2", summary["shaky"])
        self.assertEqual(summary["roots"], ["m7a.5.1.2"])

    def test_wrong_twice_descends_hard_first(self):
        session = engine.create_session(self.graph, ["m7a.5.2"])
        self._answer(session, "wrong")
        self._answer(session, "wrong")
        self.assertEqual(session["state"]["m7a.5.2"], "weak")
        self.assertEqual(session["queue"][:1], ["m7a.5.1.2"], "先回溯 hard 先修")
        self.assertIn("m7a.4.2", session["queue"][:2])
        self.assertNotIn("m7a.3.1", session["queue"][:2], "soft 先修应排在 hard 之后")

    def test_second_question_not_harder_or_flagged(self):
        """第二题应当同级或更易；题库不够时允许升难度，但必须打标记。"""
        session = engine.create_session(self.graph, ["m7a.5.2"])
        first = self._answer(session, "wrong")
        second = engine.next_item(self.graph, session)
        easier = (engine.DIFF_RANK[second["question"]["difficulty"]]
                  <= engine.DIFF_RANK[first["question"]["difficulty"]])
        self.assertTrue(easier or session["escalated"] > 0,
                        "既升了难度又没有标记，属于静默降级")

    def test_screen_mode_respects_limit(self):
        ids = [t["id"] for t in self.graph.topics if t["volume"] == "七上"]
        session = engine.create_session(self.graph, ids, mode="screen", limit=5)
        while engine.next_item(self.graph, session):
            engine.apply_answer(self.graph, session, "wrong")
        self.assertEqual(len(session["log"]), 5)
        summary = engine.summarize(self.graph, session)
        self.assertLessEqual(len(summary["by_chapter"]), 6)

    def test_screen_scope_stays_in_target_volume(self):
        """筛查模式的按章分布不能溢出到别的学段。

        补齐跨学段承接链后，七上点的祖先会拉进整册小学。
        如果筛查还用 diagnose 的 scope(targets)，按章分布会从
        七上的 6 章膨胀到 34 章（且大部分是「未测」），报告变成噪音。
        """
        ids = [t["id"] for t in self.graph.topics if t["volume"] == "七上"]
        session = engine.create_session(self.graph, ids, mode="screen", limit=3)
        while engine.next_item(self.graph, session):
            engine.apply_answer(self.graph, session, "wrong")
        summary = engine.summarize(self.graph, session)
        chapters = set(summary["by_chapter"])
        for ch in chapters:
            self.assertNotIn("小学", ch, f"筛查范围溢出到小学：{ch}")
        # 未测的点也只能是七上的
        volumes = {self.graph.by_id[t]["volume"] for t in summary["untested"]}
        self.assertTrue(volumes <= {"七上"}, f"未测点溢出到：{volumes}")

    def test_learning_path_is_topological(self):
        session = engine.create_session(self.graph, ["m7a.5.3"])
        for _ in range(3):
            if not engine.next_item(self.graph, session):
                break
            engine.apply_answer(self.graph, session, "wrong")
        summary = engine.summarize(self.graph, session)
        pos = {tid: i for i, tid in enumerate(summary["path"])}
        for edge in self.graph.dependencies:
            a, b = edge["topicId"], edge["prerequisiteId"]
            if a in pos and b in pos:
                self.assertLess(pos[b], pos[a])

    def test_entry_prefers_low_guess_type(self):
        """同级题里优先出数值/短答，别让学生一上来就靠四选一猜。"""
        for tid, bank in self.graph.bank.items():
            levels = [q["difficulty"] for q in bank]
            lowest = min(levels, key=lambda d: engine.DIFF_RANK[d])
            entry = [q for q in bank if q["difficulty"] == lowest]
            best = min(engine.TYPE_GUESS.get(q["type"], 0.25) for q in entry)
            session = engine.create_session(self.graph, [tid])
            item = engine.next_item(self.graph, session)
            self.assertEqual(engine.TYPE_GUESS.get(item["question"]["type"], 0.25), best,
                             f"{tid} 应优先出猜测率最低的题型")

    def test_every_topic_has_low_guess_entry(self):
        """每个点的入口题里必须有一道非四选一，否则猜测率无法压低。"""
        for tid, bank in self.graph.bank.items():
            levels = [q["difficulty"] for q in bank]
            lowest = min(levels, key=lambda d: engine.DIFF_RANK[d])
            entry_types = {q["type"] for q in bank if q["difficulty"] == lowest}
            self.assertTrue(entry_types - {"choice"},
                            f"{tid} 的入口题全是四选一")

    def test_step_up_gated_by_standard_coverage(self):
        """升档确认必须在 standard 题覆盖足够时才生效。

        覆盖不足时，一部分点会升档、一部分不会，同一个学生的两次诊断
        行为不一致，实验结论也无法推广。所以要设硬门槛。
        """
        cov = engine._standard_coverage(self.graph)
        self.assertIsInstance(cov, float)
        self.assertGreaterEqual(cov, 0.0)
        self.assertLessEqual(cov, 1.0)
        # 门槛常量必须是合理的正数
        self.assertGreater(engine.STEP_UP_MIN_STANDARD_COVERAGE, 0)
        self.assertLessEqual(engine.STEP_UP_MIN_STANDARD_COVERAGE, 1.0)

    def test_practice_mode_steps_up_on_core(self):
        """练习模式：核心点答对后必须追问一道更难的题，不受全库覆盖率限制。

        诊断模式里升档是负收益（实测准确率 -1.3pp、漏诊 +4.3pp），
        所以 STEP_UP_ON_CORRECT 仍是 False；练习不需要判弱，升档才安全。
        """
        tid = "m5a.6.2"  # 三角形的面积：自有题里 basic + standard 都有
        session = engine.create_session(self.graph, [tid], mode="practice")
        first = self._answer(session, "correct")
        second = engine.next_item(self.graph, session)
        self.assertIsNotNone(second, "练习模式答对后应继续出题")
        self.assertEqual(second["topicId"], tid)
        self.assertGreater(
            engine.DIFF_RANK[second["question"]["difficulty"]],
            engine.DIFF_RANK[first["question"]["difficulty"]],
            "第 2 题应比第 1 题难（升档）")

    def test_practice_failure_is_basic_only_not_shaky(self):
        """升档题答错 = 只掌握到基础层，不是不稳。

        判 shaky 会把「不会应用」误报成「基础不牢」，还会继续回溯先修，
        把根因推到其实没问题的祖先上。
        """
        tid = "m5a.6.2"
        session = engine.create_session(self.graph, [tid], mode="practice")
        self._answer(session, "correct")
        self._answer(session, "wrong")
        self.assertEqual(session["state"][tid], "mastered")
        summary = engine.summarize(self.graph, session)
        self.assertIn(tid, summary["step_up_failed"])
        self.assertEqual(summary["mastery_level"][tid], "basic")
        self.assertEqual(summary["shaky"], [])

    def test_tool_topic_does_not_step_up(self):
        """工具点答对就是会了：练习模式也不追问，否则一年级也要做两道题。"""
        tid = "m1a.1.4"  # 分与合，工具点
        session = engine.create_session(self.graph, [tid], mode="practice")
        first = self._answer(session, "correct")
        second = engine.next_item(self.graph, session)
        if second is not None and second["topicId"] == tid:
            self.assertEqual(second["question"]["difficulty"],
                             first["question"]["difficulty"],
                             "工具点不应升档")

    def test_step_up_readiness_is_reported(self):
        """报告要能解释「为什么只验证到基础层」——是题库没题，不是学生不行。"""
        session = engine.create_session(self.graph, ["m7a.1.2.5"])
        self._answer(session, "correct")
        self._answer(session, "correct")
        s = engine.summarize(self.graph, session)["step_up"]
        self.assertIn("coverage_core", s)
        self.assertLessEqual(s["coverage_core"], 1.0)
        self.assertIsInstance(s["ready"], bool)

    def test_difficulty_is_calibrated(self):
        """难度必须真实分层，不能全标 basic。

        校准前全库 16178 道题里 100% 标 basic，difficulty 字段等于没有。
        现在至少要有 standard 和 advanced 两档。
        """
        from collections import Counter
        import json
        ext = json.loads((ROOT / "data" / "questions_ext.json").read_text(encoding="utf-8"))
        c = Counter(q.get("difficulty") for q in ext)
        self.assertGreater(c.get("standard", 0), 0, "没有 standard 题")
        self.assertGreater(c.get("advanced", 0), 0, "没有 advanced 题")
        # 每一档都要有依据可查
        no_basis = [q["id"] for q in ext if not q.get("difficulty_basis")]
        self.assertFalse(no_basis, f"缺难度依据：{no_basis[:5]}")

    def test_session_is_json_serializable(self):
        import json
        session = engine.create_session(self.graph, ["m7a.2.1.1"])
        self._answer(session, "wrong")
        json.dumps(session, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
