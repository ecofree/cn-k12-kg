"""Precise goal navigation must keep identity, uncertainty and selected tasks."""
from __future__ import annotations

import json
from http.cookiejar import CookieJar
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

from cnkg import engine
from cnkg.learning_needs import goal_pathway, support_routes, task_start_action

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    def skill(statement, prerequisites=()):
        return {"statement": statement, "granularity": "observable", "stage": "primary",
                "prerequisite_skill_ids": list(prerequisites), "refines_skill_ids": []}
    return {"skills": {"whole": skill("先确定同一个整体"), "divide": skill("同除以非零数", ["whole"]),
                        "missing": skill("尚无材料的明确目标")},
        "units": {"fraction": {"id": "fraction", "title": "分数", "resource_version": "v1",
            "skill_ids": ["whole", "divide"], "objectives": [
                {"id": "fraction:whole", "skill_id": "whole", "support_ids": ["whole-help"]},
                {"id": "fraction:divide", "skill_id": "divide", "support_ids": ["divide-help"]}],
            "task_ids": ["whole-p", "divide-p"]}}, "placements": {}, "projects": {},
        "tasks": {key: {"id": key, "type": "numeric", "prompt": "private prompt", "answer": "2",
             "explanation": "private answer explanation", "phase": "practice", "source_kind": "unit",
             "material_id": "unit:fraction", "availability": "active", "skill_ids": [sid],
             "family_id": "family-" + sid, "family_status": "declared", "representation": "diagram"}
             for key, sid in (("whole-p", "whole"), ("divide-p", "divide"))}}


class GoalPathwayTests(unittest.TestCase):
    def test_same_unit_prerequisite_is_a_real_goal_and_support_route(self):
        result = goal_pathway(fixture(), "divide", preferred_unit="fraction")
        self.assertEqual(result["prerequisite_skill_ids"], ["whole"])
        route = result["routes"][0]
        self.assertTrue(route["within_current_unit"])
        self.assertEqual(route["role"], "prerequisite")
        self.assertEqual(route["support_ids"], ["whole-help"])
        self.assertEqual(route["task_ids_by_phase"]["practice"], ["whole-p"])
        self.assertEqual(route["actions"][-1]["task_ids"], ["whole-p"])

    def test_goal_options_never_leak_unpresented_answers_or_prompts(self):
        result = goal_pathway(fixture(), "divide")
        encoded = json.dumps(result)
        self.assertNotIn("private", encoded)
        self.assertFalse(result["independent_mastery_evidence"])
        option = result["task_options"][0]
        self.assertEqual(option["start_action"]["task_ids"], ["divide-p"])

    def test_missing_route_remains_visible_without_guessed_replacement(self):
        result = goal_pathway(fixture(), "missing")
        self.assertFalse(result["routes"][0]["available"])
        self.assertEqual(result["routes"][0]["actions"], [])
        self.assertEqual(result["task_options"], [])
        with self.assertRaises(ValueError):
            goal_pathway(fixture(), "missing", preferred_unit="fraction")
        with self.assertRaises(KeyError):
            goal_pathway(fixture(), "unknown")

    def test_topic_self_check_path_retains_criterion_index(self):
        catalog = fixture()
        catalog["placements"]["topic"] = {"criterion_skill_ids": ["whole", "missing"]}
        task = {"id": "check", "source_kind": "self_check", "material_id": "topic:topic",
                "criterion_index": 1, "skill_ids": ["missing"]}
        catalog["tasks"]["check"] = task
        result = goal_pathway(catalog, "missing")
        self.assertEqual(result["routes"][0]["actions"][0],
                         {"action": "open_topic_goal", "topic_id": "topic", "criterion_index": 1, "skill_id": "missing"})
        self.assertEqual(task_start_action(catalog, task)["criterion_index"], 1)

    def test_registry_does_not_broadcast_gcd_to_identifying_the_whole(self):
        registry = json.loads((ROOT / "data/skills/registry.json").read_text())
        unit = json.loads((ROOT / "data/learning_units/fraction_equivalence.json").read_text())
        whole = next(obj for obj in unit["objectives"] if obj["id"].endswith(":whole"))
        self.assertEqual(whole.get("prerequisite_objective_ids", []), [])
        sid = registry["objective_skill_ids"][whole["id"]]
        self.assertEqual(registry["skills"][sid]["prerequisite_skill_ids"], [])
        self.assertTrue(unit["prerequisite_objective_ids"], "单元整体导航仍保留")

    def test_all_new_triangle_goals_keep_original_goal_ids(self):
        registry = json.loads((ROOT / "data/skills/registry.json").read_text())
        ids = registry["placements"]["m8a.13.2"]["criterion_skill_ids"]
        self.assertEqual(len(ids), 6)
        self.assertEqual(ids[0], "sk_6c96612201c440b8ae2f")
        for sid in ids[2:5]:
            self.assertEqual(registry["skills"][sid]["granularity"], "observable")
            self.assertEqual(registry["skills"][sid]["refines_skill_ids"], ids[:1])
        # Doubling a given half-area does not observe any construction.
        self.assertEqual(registry["skills"][ids[5]]["statement"], '能由中线分成的一半面积求原三角形面积')
        self.assertEqual(registry["skills"][ids[5]]["granularity"], "observable")
        self.assertEqual(registry["skills"][ids[5]]["refines_skill_ids"], [])


class ExactTaskSelectionTests(unittest.TestCase):
    def test_practice_selection_never_substitutes_same_topic_question(self):
        from cnkg.graph import Graph
        topic = "m7a.5.1.2"
        questions = [{"id": topic + suffix, "topicId": topic, "type": "numeric", "answer": "3",
                      "prompt": "解x+2=5", "difficulty": "basic", "verify": "5-2", "explanation": "两边同减2得3。"}
                     for suffix in ("-q1", "-q2")]
        graph = Graph([{"id": topic, "name": "等式", "grade": 7}], [], questions)
        chosen = graph.practice_bank[topic][-1]["id"]
        session = engine.create_session(graph, [topic], mode="practice", limit=1)
        session["allowed_question_ids"] = [chosen]
        self.assertEqual(engine.next_item(graph, session)["question"]["id"], chosen)
        engine.apply_answer(graph, session, "wrong")
        self.assertIsNone(engine.next_item(graph, session))


class GoalPathwayHTTPTests(unittest.TestCase):
    def setUp(self):
        from web import server
        from cnkg.learning_store import LearningStore
        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.learning_store = LearningStore(Path(self.temp.name) / "learning.sqlite")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.cookies = CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}",
            data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with self.client.open(request, timeout=15) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_goal_metadata_has_exact_routes_but_does_not_count_as_material_exposure(self):
        catalog = self.server.get_catalog()
        goal = next(row for row in catalog.units["fraction_equivalence"]["objectives"] if row["id"].endswith(":whole"))
        code, payload = self.request("/api/goal?skill_id=" + goal["skill_id"] + "&unit_id=fraction_equivalence")
        self.assertEqual(code, 200)
        self.assertEqual(payload["prerequisite_skill_ids"], [])
        self.assertTrue(payload["routes"])
        self.assertFalse(payload["independent_mastery_evidence"])
        for option in payload["task_options"]:
            self.assertNotIn("prompt", option)
            self.assertNotIn("answer", option)
            self.assertNotIn("explanation", option)
        owner = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
        self.assertEqual(self.httpd.learning_store.exposures(owner)["families"], {})
        self.assertEqual(self.request("/api/goal?skill_id=unknown")[0], 404)
        self.assertEqual(self.request("/api/goal?skill_id=" + goal["skill_id"] + "&unit_id=independent_events")[0], 400)

    def test_exact_topic_action_is_checked_and_returns_the_requested_question(self):
        request = {"targets": ["m7a.5.1.2"], "mode": "practice", "task_ids": ["m7a.5.1.2-q3"], "limit": 1}
        code, payload = self.request("/api/session", request)
        self.assertEqual(code, 200)
        self.assertEqual(payload["item"]["question"]["id"], "m7a.5.1.2-q3")
        self.assertEqual(self.request("/api/session", {**request, "mode": "diagnose"})[0], 400)
        self.assertEqual(self.request("/api/session", {**request, "task_ids": ["mh2.10.2-q1"]})[0], 400)


if __name__ == "__main__":
    unittest.main()
