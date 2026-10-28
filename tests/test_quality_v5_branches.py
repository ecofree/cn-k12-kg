"""Real help routes, frozen alternatives, and bounded resolution obligations.

These are synthetic checks against authored tasks, not student-outcome evidence.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from cnkg import instruction, session_content
from cnkg.catalog import load_catalog
from cnkg.contracts import validate_resource
from cnkg.learning_needs import derive_needs
from cnkg.learning_store import LearningStore
from cnkg.runtime_version import GRADER_SOURCES
from tests import test_instruction_v4 as fixtures


ROOT = Path(__file__).resolve().parents[1]
OWNER = "a" * 64


def submit(record, value=None, *, skipped=False, minute=1, second=1):
    qid = record["task_ids"][record["index"]]
    task = record["question_snapshots"][qid]
    return instruction.answer(record, qid, str(task["answer"] if value is None else value),
                              f"2026-01-02T00:{minute:02d}:{second:02d}+00:00", skipped=skipped)


class AuthoredBranchesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()
        cls.units = {uid: json.loads((ROOT / "data/learning_units" / (uid + ".json")).read_text())
                     for uid in ("fraction_equivalence", "equation_equivalence", "data_reading_and_mean")}
        original_goals = {"fraction_equivalence:whole", "fraction_equivalence:scale", "fraction_equivalence:common_unit",
                          "equation_equivalence:reversible", "data_reading_and_mean:read_scale", "data_reading_and_mean:mean",
                          "data_reading_and_mean:combine", "data_reading_and_mean:choose_chart", "data_reading_and_mean:limited_claim"}
        cls.supports = [(unit, support) for unit in cls.units.values()
                        for support in unit["instructional_support"]["supports"] if support["objective_id"] in original_goals]

    def create(self, unit, support):
        return instruction.create(unit, support["id"], OWNER, self.catalog, {}, [],
                                  "2026-01-02T00:00:00+00:00")[0]

    def test_authored_routes_have_distinct_tasks_reasons_and_original_goal_followups(self):
        self.assertEqual(9, len(self.supports))
        for unit in self.units.values():
            self.assertEqual([], validate_resource("instructional-support", unit["instructional_support"]))
            self.assertEqual([], instruction.check_supports(unit))
        for unit, support in self.supports:
            with self.subTest(support=support["id"]):
                self.assertEqual(2, len(support["guided_branches"]))
                tasks = [support["guided_task"], support["skip_route"]["guided_task"],
                         *(branch["guided_task"] for branch in support["guided_branches"])]
                self.assertEqual(4, len({task["prompt"] for task in tasks}))
                for task in tasks:
                    self.assertTrue(task["family_id"])
                    self.assertNotEqual("unknown", task["representation"])
                for route in [support["default_route"], support["skip_route"], *support["guided_branches"]]:
                    for field in ("title", "rationale", "observation_note"):
                        self.assertTrue(route[field].strip())
                for tid in support["followup_task_ids"]:
                    authored = next(task for task in unit["tasks"] if task["id"] == tid)
                    self.assertIn(support["objective_id"], authored["objective_ids"])

    def test_each_wrong_choice_selects_its_real_guide_and_never_returns_alternatives(self):
        for unit, support in self.supports:
            for branch in support["guided_branches"]:
                with self.subTest(support=support["id"], route=branch["id"]):
                    record = self.create(unit, support)
                    frozen = copy.deepcopy(record["support_snapshot"])
                    self.assertEqual(6, len(record["question_snapshots"]))
                    self.assertEqual(3, len(record["task_ids"]))
                    initial = instruction.public_session(record)
                    self.assertNotIn("answer", initial["item"])
                    self.assertNotIn("feedback", initial["item"]["choices"][0])
                    for alternative in support["guided_branches"]:
                        self.assertNotIn(alternative["guided_task"]["prompt"], json.dumps(initial, ensure_ascii=False))
                    answer, events = submit(record, branch["choice_ids"][0])
                    self.assertEqual("wrong", answer["status"])
                    shown = instruction.public_session(record)
                    self.assertEqual(branch["id"], shown["support"]["route"]["route_id"])
                    self.assertEqual(branch["guided_task"]["prompt"], shown["item"]["prompt"])
                    self.assertNotIn("answer", shown["item"])
                    self.assertEqual([support["objective_id"]], shown["item"]["objective_ids"])
                    self.assertEqual([shown["item"]["id"]], [event["question_id"] for event in events
                                     if event["event_type"] == "question_presented"])
                    self.assertEqual(frozen, record["support_snapshot"])
                    self.assertIsNone(instruction.route_problem(record))
                    for alternative in support["guided_branches"]:
                        if alternative["id"] != branch["id"]:
                            self.assertNotIn(alternative["guided_task"]["prompt"], json.dumps(shown, ensure_ascii=False))

    def test_correct_and_temporarily_unknown_choices_keep_distinct_routes(self):
        for unit, support in self.supports:
            for skipped, expected, task in [(False, "default", support["guided_task"]),
                                             (True, "skip", support["skip_route"]["guided_task"])]:
                with self.subTest(support=support["id"], route=expected):
                    record = self.create(unit, support)
                    submit(record, "" if skipped else support["diagnostic"]["answer"], skipped=skipped)
                    shown = instruction.public_session(record)
                    self.assertEqual(expected, shown["support"]["route"]["route_id"])
                    self.assertEqual(task["prompt"], shown["item"]["prompt"])
                    self.assertIsNone(instruction.route_problem(record))

    def test_authored_content_changes_cannot_reorder_existing_branch_session(self):
        source, original = self.supports[0]
        unit = copy.deepcopy(source)
        support = next(row for row in unit["instructional_support"]["supports"] if row["id"] == original["id"])
        record = self.create(unit, support)
        branch = support["guided_branches"][0]
        expected = branch["guided_task"]["prompt"]
        choice = branch["choice_ids"][0]
        branch["guided_task"]["prompt"] = "当前作者版本已修改，旧会话不可替换。"
        support["guided_branches"].reverse()
        submit(record, choice)
        self.assertEqual(expected, instruction.public_session(record)["item"]["prompt"])
        self.assertIsNone(instruction.version_problem(record))

    def test_all_alternative_assets_are_frozen_but_only_presented_asset_is_readable(self):
        unit = self.units["data_reading_and_mean"]
        support = next(row for row in unit["instructional_support"]["supports"] if row.get("guided_branches"))
        record = self.create(unit, support)
        probe = "support-scale-probe-v5.svg"
        selected = "support-scale-per-grid-v5.svg"
        alternative = "support-axis-origin-v5.svg"
        self.assertIsNotNone(session_content.asset_bytes(record, probe))
        for name in (selected, alternative):
            self.assertIn("/static/diagrams/" + name, record["asset_snapshots"])
            self.assertIsNone(session_content.asset_bytes(record, name))
        _, events = submit(record, "A")
        self.assertIsNotNone(session_content.asset_bytes(record, selected))
        self.assertIsNone(session_content.asset_bytes(record, alternative))
        self.assertTrue(instruction.public_session(record)["item"]["diagram"]["src"].startswith("/api/session/"))
        _, hint_events = instruction.hint(record, 2, "2026-01-02T00:01:02+00:00")
        families = set(support["hints"][1]["reference_family_ids"])
        self.assertEqual(families, set(hint_events[0]["payload"]["family_ids"]))
        self.assertFalse(hint_events[0]["payload"]["independent_evidence"])
        self.assertFalse(any(event["question_id"].endswith(":restore_axis_origin") for event in events))

    def test_invalid_or_cosmetic_routes_and_unbound_followups_are_rejected(self):
        unit, original = self.supports[0]
        mutations = [lambda support: support["guided_branches"].__setitem__(0, None),
                     lambda support: support["guided_branches"][0].update(choice_ids=[support["diagnostic"]["answer"]]),
                     lambda support: support["guided_branches"][0]["guided_task"].update(prompt=support["guided_task"]["prompt"]),
                     lambda support: support.update(followup_task_ids=["unbound-task"])]
        for mutate in mutations:
            changed = copy.deepcopy(unit)
            support = next(row for row in changed["instructional_support"]["supports"] if row["id"] == original["id"])
            mutate(support)
            self.assertTrue(instruction.check_supports(changed))


class BranchObligationsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InstructionTest(methodName="runTest")
        self.fixture.setUp()

    def needs(self, *records):
        return derive_needs(self.fixture.catalog, records)

    def finish(self, record, *, minute=1, guide="3", skip_at=None):
        submit(record, "b", minute=minute, second=1, skipped=skip_at == 0)
        submit(record, guide, minute=minute, second=2, skipped=skip_at == 1)
        submit(record, minute=minute, second=3, skipped=skip_at == 2)

    def test_wrong_guide_or_any_skipped_step_cannot_close_linked_original(self):
        for guide, skip_at in [("99", None), ("3", 0), ("3", 1), ("3", 2)]:
            with self.subTest(guide=guide, skip_at=skip_at):
                record = self.fixture.create()
                self.finish(record, guide=guide, skip_at=skip_at)
                self.assertFalse(record["answers"][-1]["confirmation_eligible"])
                self.assertEqual([], record["answers"][-1]["resolution_for_need_ids"])
                needs = self.needs(self.fixture.origin, record)
                self.assertEqual("open", next(row for row in needs if row["need_id"] == self.fixture.need["need_id"])["status"])
                self.assertFalse(any(row["kind"] == "independent_followup" for row in needs))
                self.assertIsNone(instruction.route_problem(record))

    def test_successful_guidance_only_closes_bounded_need_and_retains_independent_followup(self):
        record = self.fixture.create()
        self.finish(record)
        needs = self.needs(self.fixture.origin, record)
        original = next(row for row in needs if row["need_id"] == self.fixture.need["need_id"])
        followup = next(row for row in needs if row["kind"] == "independent_followup")
        self.assertEqual("resolved", original["status"])
        self.assertFalse(original["resolution"]["independent_evidence"])
        self.assertEqual("open", followup["status"])
        self.assertEqual(original["need_id"], followup["parent_need_id"])
        self.assertEqual("old-version", followup["resource_version"])
        self.assertEqual("wrong", self.fixture.origin["answers"][0]["status"])

    def test_guided_process_awaiting_review_cannot_be_hidden_by_correct_confirmation(self):
        guide = self.fixture.support["guided_task"]
        guide.update(type="rubric", answer_spec={"kind": "rubric"},
                     rubric=[{"criterion": "说明相同整体的全部份与涂色份同时细分。", "points": 1}])
        guide.pop("verify", None)
        record = self.fixture.create()
        self.finish(record, guide="我画出了相同整体，并把每份细分成3份。")
        self.assertEqual("needs_review", record["answers"][1]["status"])
        self.assertEqual("correct", record["answers"][-1]["status"])
        self.assertFalse(record["answers"][-1]["confirmation_eligible"])
        self.assertEqual("open", self.needs(self.fixture.origin, record)[0]["status"])
        self.assertIsNone(instruction.route_problem(record))

    def test_unlinked_success_cannot_clear_failed_support_but_explicit_retry_can(self):
        failed = self.fixture.create(needs=[])
        self.finish(failed, guide="99")
        original = next(row for row in self.needs(failed) if row["kind"] == "support_revision")
        self.assertEqual([original["need_id"]], instruction.public_session(failed)["session"]["retry_need_ids"])
        unrelated = self.fixture.create(needs=[])
        self.finish(unrelated, minute=2)
        self.assertEqual("open", next(row for row in self.needs(failed, unrelated) if row["need_id"] == original["need_id"])["status"])
        retry = self.fixture.create(needs=[original])
        self.finish(retry, minute=3)
        needs = self.needs(failed, unrelated, retry)
        recovered = next(row for row in needs if row["need_id"] == original["need_id"])
        self.assertEqual("resolved", recovered["status"])
        self.assertEqual(retry["id"], recovered["resolution"]["source_ref"]["session_id"])
        followup = next(row for row in needs if row.get("parent_need_id") == original["need_id"])
        self.assertEqual("open", followup["status"])
        self.assertEqual(1, sum(row["kind"] == "support_revision" and row["source_ref"]["session_id"] == failed["id"] for row in needs))
        self.assertFalse(any(row["kind"] == "support_revision" and row["source_ref"]["session_id"] == retry["id"] for row in needs))
        self.assertEqual("wrong", failed["answers"][1]["status"])

    def test_wrong_version_or_goal_cannot_discharge_saved_support_failure(self):
        failed = self.fixture.create(needs=[])
        self.finish(failed, guide="99")
        original = self.needs(failed)[0]
        for change in ("version", "goal"):
            retry = self.fixture.create(needs=[original])
            self.finish(retry, minute=2)
            if change == "version":
                retry["source_origin"]["resource_version"] = "unrelated-version"
            else:
                retry["answers"][-1]["skill_ids"] = ["unrelated-goal"]
            need = next(row for row in self.needs(failed, retry) if row["need_id"] == original["need_id"])
            self.assertEqual("open", need["status"])

    def test_store_compaction_preserves_failed_guide_eligibility_and_route_identity(self):
        failed = self.fixture.create(needs=[])
        self.finish(failed, guide="99")
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite")
            store.save(failed)
            compact = store.planning_records(OWNER)[0]
            self.assertFalse(compact["answers"][-1]["confirmation_eligible"])
            self.assertEqual("default", compact["answers"][1]["route_id"])
            self.assertNotIn("answer", compact["answers"][-1])
            self.assertEqual(self.needs(failed), self.needs(compact))

    def test_numeric_choice_ids_use_canonical_choice_before_routing(self):
        support = self.fixture.support
        support["diagnostic"]["choices"][0]["id"] = "11"
        support["diagnostic"]["choices"][1]["id"] = "22"
        support["diagnostic"]["choices"].append({"id": "33", "text": "0/4", "feedback": "细分不会消去已经涂色的部分。"})
        support["diagnostic"]["answer"] = "11"
        support["guided_branches"] = [{"id": "numeric_identity", "choice_ids": ["22"], "title": "检查分子",
            "rationale": "本次选择只保留了一份。", "observation_note": "仍需后续确认。",
            "guided_task": {**copy.deepcopy(support["guided_task"]), "prompt": "2份各细分3份后共几份？", "answer": "6", "verify": "2*3"}}]
        support["guided_branches"].append({"id": "preserve_parts", "choice_ids": ["33"], "title": "保留原份数",
            "rationale": "细分以后原来的涂色还在。", "observation_note": "只观察份数保留。",
            "guided_task": {**copy.deepcopy(support["guided_task"]), "prompt": "3份各细分2份后共几份？", "answer": "6", "verify": "3*2"}})
        for key in ("default_route", "skip_route"):
            support[key] = {"title": "从同倍细分继续", "rationale": "先观察整体细分。", "observation_note": "仍需后续确认。"}
        support["skip_route"]["guided_task"] = copy.deepcopy(support["guided_task"])
        self.assertEqual([], instruction.check_supports(self.fixture.unit))
        record = self.fixture.create()
        feedback, _ = submit(record, "B")
        self.assertEqual("22", record["route_selection"]["diagnostic_choice_id"])
        self.assertEqual("numeric_identity", record["route_selection"]["route_id"])
        self.assertEqual(support["diagnostic"]["choices"][1]["feedback"], feedback["feedback"])
        self.assertIsNone(instruction.route_problem(record))

    def test_tampered_route_task_order_and_claimed_confirmation_are_rejected(self):
        original = self.fixture.create()
        submit(original, "b")
        for change in ("selection", "order", "unpresented", "goal", "completed"):
            record = copy.deepcopy(original)
            if change == "selection":
                record["route_selection"]["route_id"] = "skip"
            elif change == "order":
                record["task_ids"][1], record["task_ids"][2] = record["task_ids"][2], record["task_ids"][1]
            elif change == "unpresented":
                record["presentations"][record["task_ids"][-1]] = {"presented_at": record["created_at"]}
            elif change == "goal":
                record["answers"][0]["objective_ids"] = ["unrelated"]
            else:
                record["completed_at"] = record["created_at"]
            self.assertIsNotNone(instruction.route_problem(record), change)
            with self.assertRaises(ValueError):
                submit(record, "3", second=2)
        failed = self.fixture.create()
        self.finish(failed, guide="99")
        failed["answers"][-1]["confirmation_eligible"] = True
        failed["answers"][-1]["resolution_for_need_ids"] = [self.fixture.need["need_id"]]
        self.assertIsNotNone(instruction.route_problem(failed))

    def test_old_snapshot_keeps_original_three_task_path_and_runtime_mismatch_blocks_writes(self):
        record = self.fixture.create()
        original_ids = list(record["task_ids"])
        record["support_snapshot"].pop("routing")
        record.pop("route_selection")
        record["content_sha256"] = instruction.digest(record["support_snapshot"])
        record["grader_sha256"] = "historical-runtime"
        self.assertIsNone(instruction.route_problem(record))
        shown = instruction.public_session(record)
        self.assertTrue(shown["session"]["resume_blocked"])
        self.assertIsNone(shown["support"]["route"])
        self.assertEqual(original_ids[0], shown["item"]["id"])
        self.assertEqual(original_ids, record["task_ids"])
        with self.assertRaisesRegex(ValueError, "答案规则已经更新"):
            submit(record, "b")
        self.assertIn("cnkg/instruction.py", GRADER_SOURCES["support"])

    def test_completed_legacy_snapshot_stays_readable_with_its_saved_answers(self):
        record = self.fixture.create()
        self.finish(record)
        record["support_snapshot"].pop("routing")
        record.pop("route_selection")
        for row in record["answers"]:
            for key in ("route_id", "confirmation_eligible", "confirmation_notice"):
                row.pop(key, None)
        record["content_sha256"] = instruction.digest(record["support_snapshot"])
        record["grader_sha256"] = "historical-runtime"
        shown = instruction.public_session(record)
        self.assertTrue(shown["done"])
        self.assertFalse(shown["session"]["resume_blocked"])
        self.assertEqual(record["answers"], shown["answers"])
        self.assertIsNone(instruction.version_problem(record))


if __name__ == "__main__":
    unittest.main()
