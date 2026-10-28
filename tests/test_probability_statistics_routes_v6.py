"""Authored probability/statistics help, exact observations, and frozen routes.

Synthetic process checks do not measure student learning. Mathematical cases
below use enumeration and independent descriptions, not the author's verify
expressions, so a repeated incorrect answer/verify pair cannot pass them.
"""
import copy
from fractions import Fraction
from itertools import permutations, product
import json
from pathlib import Path
import tempfile
import unittest

from cnkg import instruction
from cnkg.answers import grade_answer
from cnkg.catalog import load_catalog
from cnkg.contracts import validate_resource
from cnkg.learning_store import LearningStore


ROOT = Path(__file__).resolve().parents[1]
OWNER = "e" * 64
UNIT_IDS = ("independent_events", "sampling_and_variation",
            "correlation_and_regression", "data_reading_and_mean")


def submit(record, value=None, *, skipped=False, second=1):
    qid = record["task_ids"][record["index"]]
    task = record["question_snapshots"][qid]
    if value is None:
        value = task.get("answer") or "；".join(row["example"] for row in task.get("rubric", []))
    return instruction.answer(record, qid, str(value), f"2026-02-02T00:00:{second:02d}+00:00", skipped=skipped)


class ProbabilityStatisticsRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = {uid: json.loads((ROOT / "data/learning_units" / (uid + ".json")).read_text())
                     for uid in UNIT_IDS}
        cls.catalog = load_catalog()
        cls.supports = [(unit, support) for unit in cls.units.values()
                        for support in unit["instructional_support"]["supports"]]

    def source(self, uid, objective):
        unit = self.units[uid]
        return unit, next(s for s in unit["instructional_support"]["supports"]
                          if s["objective_id"] == uid + ":" + objective)

    def create(self, unit, support):
        return instruction.create(unit, support["id"], OWNER, self.catalog, {}, [],
                                  "2026-02-02T00:00:00+00:00")[0]

    def test_full_probability_statistics_support_scope_and_hint_contracts(self):
        self.assertEqual(23, len(self.supports))  # One existing scale support plus 22 completed here.
        for unit in self.units.values():
            self.assertEqual([], validate_resource("learning-unit", unit), unit["id"])
            self.assertEqual([], instruction.check_supports(unit), unit["id"])
        for unit, support in self.supports:
            with self.subTest(support=support["id"]):
                wrong = {row["id"] for row in support["diagnostic"]["choices"]} - {support["diagnostic"]["answer"]}
                self.assertEqual(wrong, {cid for branch in support["guided_branches"] for cid in branch["choice_ids"]})
                guides = [support["guided_task"], support["skip_route"]["guided_task"],
                          *(branch["guided_task"] for branch in support["guided_branches"])]
                self.assertEqual(4, len({guide["prompt"] for guide in guides}))
                self.assertTrue(all(guide.get("family_id") and guide.get("representation") != "unknown" for guide in guides))
                for hint in support["hints"]:
                    self.assertIn("reference_family_ids", hint)
                for qid in support["followup_task_ids"]:
                    self.assertIn(support["objective_id"], next(t for t in unit["tasks"] if t["id"] == qid)["objective_ids"])

    def test_every_wrong_choice_uses_its_guide_without_exposing_other_routes(self):
        for unit, support in self.supports:
            for branch in support["guided_branches"]:
                with self.subTest(support=support["id"], route=branch["id"]):
                    record = self.create(unit, support)
                    frozen = copy.deepcopy(record["support_snapshot"])
                    initial = json.dumps(instruction.public_session(record), ensure_ascii=False)
                    for alternate in support["guided_branches"]:
                        self.assertNotIn(alternate["guided_task"]["prompt"], initial)
                    row, events = submit(record, branch["choice_ids"][0])
                    self.assertEqual("wrong", row["status"])
                    public = instruction.public_session(record)
                    self.assertEqual(branch["id"], public["support"]["route"]["route_id"])
                    self.assertEqual(branch["guided_task"]["prompt"], public["item"]["prompt"])
                    self.assertNotIn("answer", public["item"])
                    self.assertEqual([public["item"]["id"]], [e["question_id"] for e in events if e["event_type"] == "question_presented"])
                    self.assertEqual(frozen, record["support_snapshot"])
                    self.assertIsNone(instruction.route_problem(record))
                    for alternate in support["guided_branches"]:
                        if alternate["id"] != branch["id"]:
                            self.assertNotIn(alternate["guided_task"]["prompt"], json.dumps(public, ensure_ascii=False))
                    guide, _ = submit(record, second=2)
                    self.assertEqual("correct", guide["status"])
                    final, _ = submit(record, second=3)
                    self.assertEqual(final["status"] == "correct", final["confirmation_eligible"])
                    self.assertTrue(final["help_used"])
                    self.assertFalse(record["context"]["independent_evidence"])
                    self.assertIsNone(instruction.version_problem(record))

    def test_correct_and_skipped_probe_take_separate_paths_and_skip_is_not_confirmed(self):
        for unit, support in self.supports:
            for skipped in (False, True):
                with self.subTest(support=support["id"], skipped=skipped):
                    record = self.create(unit, support)
                    submit(record, "" if skipped else support["diagnostic"]["answer"], skipped=skipped)
                    route = "skip" if skipped else "default"
                    task = support["skip_route"]["guided_task"] if skipped else support["guided_task"]
                    self.assertEqual(route, record["route_selection"]["route_id"])
                    self.assertEqual(task["prompt"], instruction.public_session(record)["item"]["prompt"])
                    submit(record, second=2)
                    final, _ = submit(record, second=3)
                    if skipped or final["status"] == "needs_review":
                        self.assertFalse(final["confirmation_eligible"])
                    self.assertIsNone(instruction.route_problem(record))

    def test_conditional_confirmation_observes_the_requested_conditional_probability(self):
        unit, support = self.source("independent_events", "conditional")
        record = self.create(unit, support)
        self.assertEqual("independent_events:delayed-3", record["task_ids"][-1])
        self.assertNotIn("independent_events:delayed-2", support["followup_task_ids"])
        self.assertIn("条件概率", record["question_snapshots"][record["task_ids"][-1]]["prompt"])
        self.assertFalse(record["context"]["independent_evidence"])

    def test_hint_levels_expose_only_delivered_families_not_unseen_confirmations(self):
        unit, support = self.source("correlation_and_regression", "r_squared")
        record = self.create(unit, support)
        _, events = submit(record, "A")
        counterexample = "statistics.no-intercept-r-squared-counterexample"
        unselected = "statistics.observational-causality"
        before = {family for event in events for family in event["payload"].get("family_ids", [])}
        self.assertNotIn(counterexample, before)
        self.assertNotIn(unselected, before)
        confirmation = record["task_ids"][-1]
        self.assertNotIn(confirmation, record["presentations"])
        for level in (1, 2, 3):
            _, hint_events = instruction.hint(record, level, f"2026-02-02T00:00:0{level+1}+00:00")
            event = hint_events[0]
            self.assertEqual(set(support["hints"][level-1]["reference_family_ids"]), set(event["payload"]["family_ids"]))
            self.assertIsNone(event["question_id"])
            self.assertEqual([], event["payload"]["task_ids"])
            self.assertEqual("authored_hint", event["payload"]["family_attribution"])
            self.assertFalse(event["payload"]["independent_evidence"])
            self.assertNotIn(unselected, event["payload"]["family_ids"])
            self.assertEqual(level == 3, counterexample in event["payload"]["family_ids"])
            self.assertNotIn(confirmation, record["presentations"])

    def test_explicit_empty_hint_families_do_not_fall_back_to_whole_tasks(self):
        original, source = self.source("sampling_and_variation", "spread")
        unit = copy.deepcopy(original)
        support = next(s for s in unit["instructional_support"]["supports"] if s["id"] == source["id"])
        support["hints"][0]["reference_family_ids"] = []
        record = self.create(unit, support)
        submit(record, "A")
        _, events = instruction.hint(record, 1, "2026-02-02T00:00:02+00:00")
        self.assertEqual([], events[0]["payload"]["family_ids"])
        self.assertIsNone(events[0]["question_id"])

    def test_legacy_hints_keep_frozen_selected_scope_and_never_read_current_source(self):
        original, source = self.source("correlation_and_regression", "r_squared")
        unit = copy.deepcopy(original)
        support = next(s for s in unit["instructional_support"]["supports"] if s["id"] == source["id"])
        for hint in support["hints"]:
            hint.pop("reference_family_ids")
        record = self.create(unit, support)
        support["hints"][0]["reference_family_ids"] = ["not-in-frozen-record"]
        submit(record, "A")
        _, events = instruction.hint(record, 1, "2026-02-02T00:00:02+00:00")
        selected = [record["task_ids"][1], record["task_ids"][2]]
        expected = {record["task_metadata"][qid]["family_id"] for qid in selected}
        expected.update(f for qid in selected for f in record["task_metadata"][qid]["reference_family_ids"])
        self.assertEqual(expected, set(events[0]["payload"]["family_ids"]))
        self.assertEqual("legacy_selected_tasks", events[0]["payload"]["family_attribution"])
        self.assertNotIn("not-in-frozen-record", events[0]["payload"]["family_ids"])
        self.assertNotIn("statistics.observational-causality", events[0]["payload"]["family_ids"])

    def test_branch_specific_hint_families_join_only_the_selected_hint(self):
        original, source = self.source("independent_events", "conditional")
        unit = copy.deepcopy(original)
        support = next(s for s in unit["instructional_support"]["supports"] if s["id"] == source["id"])
        support["branch_hints"] = {
            "B": [{"level": 1, "text": "核对条件范围。", "reference_family_ids": ["selected.method"]}],
            "C": [{"level": 1, "text": "核对除法方向。", "reference_family_ids": ["unselected.method"]}],
        }
        self.assertEqual([], validate_resource("instructional-support", unit["instructional_support"]))
        record = self.create(unit, support)
        submit(record, "B")
        _, events = instruction.hint(record, 1, "2026-02-02T00:00:02+00:00")
        self.assertEqual(set(support["hints"][0]["reference_family_ids"]) | {"selected.method"}, set(events[0]["payload"]["family_ids"]))
        self.assertNotIn("unselected.method", events[0]["payload"]["family_ids"])

    def test_saved_route_and_level_families_survive_current_author_changes(self):
        original, source = self.source("correlation_and_regression", "r_squared")
        unit = copy.deepcopy(original)
        support = next(s for s in unit["instructional_support"]["supports"] if s["id"] == source["id"])
        record = self.create(unit, support)
        submit(record, "A")
        expected = copy.deepcopy(record["support_snapshot"])
        support["guided_branches"].reverse()
        support["hints"][2]["reference_family_ids"] = ["replacement-content"]
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "learning.sqlite")
            store.save(record)
            restored = store.load(record["id"], OWNER)
            self.assertEqual(expected, restored["support_snapshot"])
            self.assertIsNone(instruction.version_problem(restored))
            _, events = instruction.hint(restored, 3, "2026-02-02T00:00:03+00:00")
            self.assertIn("statistics.no-intercept-r-squared-counterexample", events[0]["payload"]["family_ids"])
            self.assertNotIn("replacement-content", events[0]["payload"]["family_ids"])

    def test_hint_family_duplicates_and_non_lists_are_rejected(self):
        for invalid in (["f", "f"], "not-an-array", [None]):
            unit = copy.deepcopy(self.units["independent_events"])
            unit["instructional_support"]["supports"][0]["hints"][0]["reference_family_ids"] = invalid
            self.assertTrue(instruction.check_supports(unit))
            self.assertTrue(validate_resource("instructional-support", unit["instructional_support"]))

    def test_independent_enumeration_checks_route_probabilities_and_rejects_old_misconceptions(self):
        def guide(uid, objective, route):
            _, support = self.source(uid, objective)
            return next(b["guided_task"] for b in support["guided_branches"] if b["id"] == route)

        def correct(task, value, wrong):
            self.assertEqual("correct", grade_answer(task, str(value))["status"], task["prompt"])
            self.assertEqual("wrong", grade_answer(task, str(wrong))["status"], task["prompt"])

        bag = ("R1", "R2", "R3", "W1", "W2")
        given_white = [(first, second) for first, second in permutations(bag, 2) if first.startswith("W")]
        second_red = Fraction(sum(second.startswith("R") for _, second in given_white), len(given_white))
        correct(guide("independent_events", "o2", "update_remaining_space"), second_red, Fraction(3, 5))
        second_white = Fraction(sum(second.startswith("W") for _, second in given_white), len(given_white))
        correct(guide("independent_events", "multiply", "draw_second_step_condition"), second_white, Fraction(2, 5))
        coin_pairs = list(product((False, True), repeat=2))
        at_least = Fraction(sum(any(pair) for pair in coin_pairs), len(coin_pairs))
        correct(guide("independent_events", "o3", "include_exactly_one_and_two"), at_least, Fraction(1, 2))
        # Independent uniform draws with success sets of sizes 1/2 and 1/4.
        outcomes = list(product(range(2), range(4)))
        exactly_one = Fraction(sum((a == 0) != (b == 0) for a, b in outcomes), len(outcomes))
        correct(guide("independent_events", "complement", "count_second_success_position"), exactly_one, Fraction(3, 8))
        cards = [(True, i < 2) for i in range(8)] + [(False, False) for _ in range(12)]
        conditional = Fraction(sum(a and b for a, b in cards), sum(a for a, _ in cards))
        correct(guide("independent_events", "conditional", "condition_on_subset"), conditional, Fraction(2, 20))

    def test_independent_data_models_check_centre_variation_and_regression_examples(self):
        def route(uid, objective, bid):
            _, support = self.source(uid, objective)
            return next(b["guided_task"] for b in support["guided_branches"] if b["id"] == bid)

        data = list(map(Fraction, (1, 5)))
        mean = sum(data) / len(data)
        squared_sum = sum((x-mean)**2 for x in data)
        self.assertEqual("correct", grade_answer(route("sampling_and_variation", "spread", "square_before_averaging"), str(squared_sum))["status"])
        self.assertEqual("wrong", grade_answer(route("sampling_and_variation", "spread", "square_before_averaging"), "0")["status"])
        pages = (0, 3, 6)
        daily = Fraction(sum(pages), len(pages))
        self.assertEqual("correct", grade_answer(route("data_reading_and_mean", "mean", "keep_zero_record_in_count"), str(daily))["status"])
        self.assertEqual("wrong", grade_answer(route("data_reading_and_mean", "mean", "keep_zero_record_in_count"), str(Fraction(sum(pages), 2)))["status"])
        # The carried-over drawing uses a genuine least-squares line through
        # these three points; its slope and centre are derived from the data.
        points = [(Fraction(x), Fraction(y)) for x, y in ((0, 1), (1, 2), (2, 6))]
        xmean, ymean = (sum(p[k] for p in points)/len(points) for k in (0, 1))
        slope = sum((x-xmean)*(y-ymean) for x, y in points) / sum((x-xmean)**2 for x, y in points)
        intercept = ymean-slope*xmean
        self.assertEqual((Fraction(5, 2), Fraction(1, 2)), (slope, intercept))
        assessment = (ROOT / "web/static/diagrams/senior-v6-regression-assessment.svg").read_text()
        self.assertNotIn("r=0", assessment.replace(" ", ""))
        unit = self.units["correlation_and_regression"]
        task = next(t for t in unit["tasks"] if t["id"] == "correlation_and_regression:transfer-1")
        self.assertNotIn("r=0", task["diagram"]["alt"].replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
