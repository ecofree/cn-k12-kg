"""Observation diversity, frozen process work and real artifact regressions.

These are synthetic contract checks. They never certify teacher review or
students' mastery, and process tasks cannot be counted as objective families.
"""
from __future__ import annotations

import base64
import copy
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from cnkg import assessments, portfolio
from cnkg.catalog import Catalog, content_hash
from cnkg.learning_store import LearningStore
from cnkg.work_attachments import validate_attachments


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jP1sAAAAASUVORK5CYII=")


def attachment(field="response"):
    return {"field_id": field, "filename": "构图.png", "media_type": "image/png",
            "byte_size": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest(),
            "data_base64": base64.b64encode(PNG).decode()}


def fixture_catalog(base):
    """Small explicit test source; no content is added to the release catalog."""
    payload = copy.deepcopy(base.data)
    payload["tasks"] = {identity: task for identity, task in payload["tasks"].items()
                        if task.get("source_kind") != "assessment"}
    sid = payload["placements"]["m1a.1.1"]["criterion_skill_ids"][0]
    component_ids = ["fixture-count-result", "fixture-count-process", "fixture-unobserved"]
    tasks = []
    for identity, kind, family, component in (("fixture-observe-result", "numeric", "fixture.count", component_ids[0]),
                                             ("fixture-observe-process", "rubric", "fixture.arrangement", component_ids[1])):
        task = {"id": identity, "objective_ids": [sid], "skill_ids": [sid], "component_ids": [component],
                "topic_ids": ["m1a.1.1"], "phase": "practice", "type": kind, "family_id": family,
                "family_status": "declared", "variant_id": identity, "representation": "verbal",
                "cognitive_demand": "execute" if kind == "numeric" else "explain",
                "source_kind": "assessment", "material_id": "assessment:" + sid,
                "availability": "active", "alignment": "explicit_component", "purpose": ["practice"],
                "prompt": "把2个和3个合起来，共有几个？" if kind == "numeric" else "摆出五个物体，重排后保存图稿并解释是否添减。",
                "explanation": "REFERENCE_ONLY_AFTER_SUBMISSION",
                "feedback": {"correct": "有限结果已记录。", "incorrect": "请保留原作答并继续检查。"},
                "answer_spec": {"version": 1, "kind": "number" if kind == "numeric" else "rubric"},
                "observation_boundary": "只观察这次具体提交，不自动完成宽目标。"}
        if kind == "numeric":
            task.update(answer="5", verify="2+3")
        else:
            task["rubric"] = [{"criterion": "提交重排前后实际物体图与对应说明", "points": 1, "example": "PRIVATE_PROCESS_EXAMPLE"}]
        task["resource_version"] = content_hash(task)
        payload["tasks"][identity] = task
        tasks.append(task)
    old_check = next(t for t in payload["tasks"].values() if t["source_kind"] == "self_check" and sid in t["skill_ids"])
    components = [{"id": cid, "statement": title, "submission_requirements": ["保存实际作答及所用帮助。"],
                   "criteria": ["PRIVATE_COMPONENT_CRITERION"], "task_ids": [tid]}
                  for cid, title, tid in zip(component_ids, ("记录结果", "保存过程", "保留未观察项目"),
                                            (tasks[0]["id"], tasks[1]["id"], old_check["id"]))]
    plan = {"skill_id": sid, "source_kind": "topic", "source_id": "m1a.1.1", "assessment_mode": "mixed",
            "scope_note": "分别观察有限结果和实际过程。", "components": components,
            "task_ids": [tasks[0]["id"], tasks[1]["id"], old_check["id"]],
            "family_distinctions": [{"left_family_id": "fixture.count", "right_family_id": "fixture.arrangement",
                                     "reason": "PRIVATE_FAMILY_WORKED_REASON"}],
            "unobserved_aspects": ["一次提交不能证明所有条件下的表现。"],
            "resource_version": "1" * 64, "source_path": "fixture.json"}
    payload["assessment_plans"] = {sid: plan}
    return Catalog(payload), sid, tasks, plan


class ObservationContractTests(unittest.TestCase):
    def setUp(self):
        from cnkg.catalog import load_catalog
        self.catalog, self.sid, self.tasks, self.plan = fixture_catalog(load_catalog())

    def test_process_work_does_not_erase_objective_family_gap(self):
        result = assessments.report(self.catalog)
        row = result["goals"][0]
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(row["objective_family_ids"], ["fixture.count"])
        self.assertTrue(row["objective_breadth_gap"])
        self.assertIn("fixture.arrangement", row["process_family_ids"])
        self.assertFalse(row["independent_mastery_evidence"])
        # A renamed numeric task in the same family is not a second structure.
        duplicate = {**self.tasks[0], "id": "fixture-same-family-variant"}
        self.catalog.tasks[duplicate["id"]] = duplicate
        self.plan = self.catalog.assessment_plans[self.sid]
        self.plan["task_ids"].append(duplicate["id"])
        self.plan["components"][0]["task_ids"].append(duplicate["id"])
        self.assertEqual(assessments.report(self.catalog)["goals"][0]["objective_family_ids"], ["fixture.count"])

    def test_dangling_foreign_and_quarantined_tasks_cannot_fill_components(self):
        for corruption in ("missing", "foreign", "quarantined", "reverse_component"):
            with self.subTest(corruption=corruption):
                catalog, sid, tasks, plan = fixture_catalog(self.catalog)
                target = catalog.tasks[tasks[0]["id"]]
                if corruption == "missing":
                    del catalog.tasks[target["id"]]
                elif corruption == "foreign":
                    target["skill_ids"] = ["sk_00000000000000000000"]
                elif corruption == "quarantined":
                    target["availability"] = "quarantined"
                else:
                    target["component_ids"] = ["other-component"]
                self.assertFalse(assessments.report(catalog)["ok"])

    def test_navigation_never_discloses_worked_answers_or_author_criteria(self):
        public = json.dumps(assessments.public_plan(self.catalog, self.sid))
        for secret in ("PRIVATE_COMPONENT_CRITERION", "PRIVATE_FAMILY_WORKED_REASON",
                       "REFERENCE_ONLY_AFTER_SUBMISSION", "PRIVATE_PROCESS_EXAMPLE"):
            self.assertNotIn(secret, public)
        self.assertIn("submission_requirements", public)

    def test_observation_adapter_preserves_identities_and_is_a_snapshot(self):
        unit = assessments.observation_unit(self.catalog, self.sid)
        self.assertEqual(unit["skill_ids"], [self.sid])
        self.assertEqual([task["id"] for task in unit["tasks"]], [task["id"] for task in self.tasks])
        unit["tasks"][0]["answer"] = "999"
        self.assertEqual(self.catalog.tasks[self.tasks[0]["id"]]["answer"], "5")
        self.assertEqual(unit["objectives"][0]["granularity"], self.catalog.skills[self.sid]["granularity"])

    def test_shared_task_keeps_the_current_goals_navigation_and_component_scope(self):
        from cnkg.learning_needs import goal_pathway
        second = next(sid for sid in self.catalog.placements["m1a.1.1"]["criterion_skill_ids"] if sid != self.sid)
        task = self.catalog.tasks[self.tasks[0]["id"]]
        task["skill_ids"].append(second)
        task["objective_ids"].append(second)
        task["component_ids"].append("fixture-second-goal-component")
        other = copy.deepcopy(self.catalog.assessment_plans[self.sid])
        other.update(skill_id=second, assessment_mode="objective", task_ids=[task["id"]], family_distinctions=[],
            components=[{"id": "fixture-second-goal-component", "statement": "另一目标的实际观察项",
                         "submission_requirements": ["保存这个目标的作答过程"], "criteria": ["实际对应这一行为"],
                         "task_ids": [task["id"]]}])
        self.catalog.assessment_plans[second] = other
        self.assertTrue(assessments.report(self.catalog)["ok"])
        for sid in (self.sid, second):
            route = goal_pathway(self.catalog, sid)
            actions = [option["start_action"] for option in route["task_options"] if option["task_id"] == task["id"]]
            actions.extend(option["start_action"] for component in route["observation_plan"]["components"]
                           for option in component["tasks"] if option["task_id"] == task["id"])
            self.assertEqual(len(actions), 2)
            self.assertTrue(all(action["assessment_goal_id"] == sid and action["focus_skill_ids"] == [sid]
                                for action in actions))
            unit = assessments.observation_unit(self.catalog, sid)
            self.assertEqual(unit["observation_plan"]["skill_id"], sid)
            self.assertEqual(unit["observation_plan"]["components"], self.catalog.assessment_plans[sid]["components"])


class ProcessAttachmentTests(unittest.TestCase):
    def test_real_bytes_are_bound_to_exact_process_field(self):
        value = attachment()
        self.assertEqual(validate_attachments([value], {"response"}), [value])
        changes = [{"sha256": "0" * 64}, {"byte_size": len(PNG) + 1}, {"field_id": "foreign"},
                   {"media_type": "image/svg+xml"}, {"filename": "../图.png"},
                   {"data_base64": base64.b64encode(b"<script>bad</script>").decode(), "byte_size": 20}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_attachments([{**value, **change}], {"response"})
        with self.assertRaises(ValueError):
            validate_attachments([value, copy.deepcopy(value)], {"response"})

    def test_attachment_removal_preserves_prior_revision_and_owner_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningStore(Path(directory) / "work.sqlite")
            descriptor = portfolio.descriptor("self_check", "fixture-check", "2" * 64, "实际图稿",
                [{"id": "response", "label": "构图", "prompt": "保存原图及构造步骤。"}], ["检查原图中的对应关系"])
            payload = portfolio.learner_payload(descriptor, {"draft": {"response": "图中逐个对应。"}, "attachments": [attachment()]})
            first = store.save_work("owner-a", descriptor["resource_id"], payload, 0, "first")
            second_payload = {**payload, "attachments": []}
            store.save_work("owner-a", descriptor["resource_id"], second_payload, 1, "second")
            self.assertEqual(store.load_work("owner-a", descriptor["resource_id"], 1)["payload"]["attachments"], [attachment()])
            self.assertEqual(store.load_work("owner-a", descriptor["resource_id"])["payload"]["attachments"], [])
            self.assertIsNone(store.load_work("owner-b", descriptor["resource_id"]))
            self.assertEqual(first["evidence_type"], "learner_reported")
            self.assertEqual(payload["assessment_basis"], "learner_reported_not_automatic_grade")

    def test_encrypted_reinstall_restores_original_attachment_bytes_and_every_revision(self):
        from cnkg.profile_recovery import ProfileRecovery
        owner = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            source = LearningStore(Path(directory) / "source.sqlite")
            descriptor = portfolio.descriptor("self_check", "fixture-check", "3" * 64, "原始作品",
                [{"id": "response", "label": "实际图稿", "prompt": "保存构造与说明。"}], ["核对图稿"])
            first = portfolio.learner_payload(descriptor, {"draft": {"response": "第一版构造"},
                                                            "attachments": [attachment()]})
            source.save_work(owner, descriptor["resource_id"], first, 0, "with-original")
            source.save_work(owner, descriptor["resource_id"], {**first, "draft": {"response": "修订说明"}},
                             1, "explain-original")
            profiles = ProfileRecovery(source)
            credentials = profiles.enable(owner)
            envelope = profiles.backup(owner, credentials["recovery_code"])
            encoded = json.dumps(envelope)
            for secret in ("第一版构造", attachment()["data_base64"], attachment()["sha256"]):
                self.assertNotIn(secret, encoded)
            target = LearningStore(Path(directory) / "fresh-install.sqlite")
            restored = ProfileRecovery(target).restore(envelope, credentials["recovery_code"])
            self.assertEqual(restored["counts"]["work_revisions"], 2)
            for revision in (1, 2):
                old = source.load_work(owner, descriptor["resource_id"], revision)
                new = target.load_work(owner, descriptor["resource_id"], revision)
                self.assertEqual(new, old)
                self.assertEqual(base64.b64decode(new["payload"]["attachments"][0]["data_base64"]), PNG)
            self.assertIsNone(target.load_work("b" * 64, descriptor["resource_id"]))


class ObservationHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server
        cls.server = server
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join(timeout=3)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_store, self.original_catalog = self.server._STORE, self.server._CATALOG_CACHE
        self.catalog, self.sid, self.tasks, self.plan = fixture_catalog(self.server.get_catalog())
        self.server._CATALOG_CACHE = self.catalog
        self.server._STORE = LearningStore(Path(self.tmp.name) / "observations.sqlite")
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def tearDown(self):
        self.server._STORE, self.server._CATALOG_CACHE = self.original_store, self.original_catalog
        self.tmp.cleanup()

    def request(self, path, payload=None, opener=None):
        req = urllib.request.Request(self.base + path, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with (opener or self.opener).open(req, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_real_observation_flow_freezes_process_and_keeps_unobserved_components(self):
        diagram_path = "/static/diagrams/fixture-observation.svg"
        original_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="2"/></svg>'
        assets = {diagram_path: {"sha256": hashlib.sha256(original_svg).hexdigest(),
                  "body_base64": base64.b64encode(original_svg).decode(), "media_type": "image/svg+xml"}}
        self.catalog.tasks[self.tasks[1]["id"]]["diagram"] = {"src": diagram_path, "alt": "原题中的圆形对象"}
        code, goal = self.request("/api/goal?skill_id=" + self.sid)
        self.assertEqual(code, 200)
        self.assertNotIn("PRIVATE_", json.dumps(goal))
        with patch.object(self.server, "unit_assets", return_value=assets):
            code, response = self.request("/api/session", {"assessment_goal_id": self.sid, "mode": "practice",
                                                          "task_ids": [task["id"] for task in self.tasks], "focus_skill_ids": [self.sid]})
        self.assertEqual(code, 200, response)
        session_id = response["session"]["id"]
        self.assertEqual(response["session"]["assessment_goal_id"], self.sid)
        self.assertNotIn("answer", response["item"]["question"])
        # A subsequent content edit cannot rewrite an in-flight task or rubric.
        self.catalog.tasks[self.tasks[0]["id"]]["answer"] = "999"
        self.catalog.tasks[self.tasks[1]["id"]]["diagram"] = {"src": "/static/diagrams/replacement.svg", "alt": "现行不同图示"}
        code, response = self.request("/api/answer", {"session_id": session_id, "question_id": self.tasks[0]["id"], "answer": "5"})
        self.assertEqual(code, 200, response)
        self.assertEqual(response["feedback"]["status"], "correct")
        code, response = self.request("/api/answer", {"session_id": session_id, "question_id": self.tasks[1]["id"], "answer": "我保留了两次摆放的原图，需要核对是否有添减。"})
        self.assertEqual(code, 200, response)
        self.assertTrue(response["done"])
        self.assertEqual(response["feedback"]["status"], "needs_review")
        outcomes = response["summary"]["component_results"]
        self.assertEqual([row["status"] for row in outcomes], ["result_recorded", "needs_review", "not_observed"])
        self.assertEqual(outcomes[2]["remaining_task_ids"], self.plan["components"][2]["task_ids"])
        self.assertTrue(all(not row["independent_mastery_evidence"] for row in outcomes))
        selector = {"kind": "session_task", "session_id": session_id, "question_id": self.tasks[1]["id"]}
        code, work = self.request("/api/work?" + urllib.parse.urlencode(selector))
        self.assertEqual(code, 200, work)
        descriptor = work["descriptor"]
        snapshot = descriptor["resource_snapshot"]
        self.assertEqual(snapshot["diagram"], {"src": diagram_path, "alt": "原题中的圆形对象"})
        self.assertEqual(base64.b64decode(snapshot["asset_snapshots"][diagram_path]["body_base64"]), original_svg)
        self.assertEqual([f["id"] for f in descriptor["resource_snapshot"]["fields"]], ["response", "fixture-count-process"])
        values = {**selector, "resource_id": descriptor["resource_id"], "expected_revision": 0, "request_id": "attach-original",
                  "draft": {"fixture-count-process": "保留图稿与构造顺序。"}, "attachments": [attachment("fixture-count-process")]}
        code, saved = self.request("/api/work", values)
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["work"]["payload"]["attachments"], values["attachments"])
        self.assertNotIn("mastered", json.dumps(saved))
        self.catalog.assessment_plans[self.sid]["components"][1]["statement"] = "CURRENT_REPLACEMENT_NOT_ORIGINAL"
        code, exported = self.request("/api/export")
        self.assertEqual(code, 200)
        old = next(row for row in exported["sessions"] if row["id"] == session_id)
        self.assertEqual(old["assessment_goal_id"], self.sid)
        self.assertEqual([component["id"] for component in old["observation_components"]],
                         ["fixture-count-result", "fixture-count-process"])
        self.assertEqual(old["observation_components"][1]["statement"], "保存过程")
        self.assertTrue(all("criteria" not in component for component in old["observation_components"]))
        self.assertEqual(old["answers"][1]["component_ids"], ["fixture-count-process"])
        presented_process = next(item for item in old["presented_tasks"] if item["question_id"] == self.tasks[1]["id"])
        self.assertEqual(presented_process["task_metadata"]["component_ids"], ["fixture-count-process"])
        other = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        code, _ = self.request("/api/work?resource_id=" + urllib.parse.quote(descriptor["resource_id"]), opener=other)
        self.assertEqual(code, 404)
        code, retry = self.request("/api/answer", {"session_id": session_id, "question_id": self.tasks[0]["id"], "answer": "5"})
        self.assertEqual(code, 200)
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["feedback"]["status"], "correct")

    def test_foreign_tasks_and_mixed_scopes_are_rejected(self):
        for payload in ({"assessment_goal_id": self.sid, "unit_id": "other", "mode": "practice"},
                        {"assessment_goal_id": self.sid, "targets": ["m1a.1.1"], "mode": "practice"},
                        {"assessment_goal_id": self.sid, "task_ids": ["not-owned"], "mode": "practice"},
                        {"assessment_goal_id": self.sid, "focus_skill_ids": ["sk_00000000000000000000"], "mode": "practice"},
                        {"assessment_goal_id": [], "mode": "practice"}):
            with self.subTest(payload=payload):
                code, _ = self.request("/api/session", payload)
                self.assertEqual(code, 400)

    def test_additional_reference_families_use_frozen_content_and_actual_feedback_release(self):
        for mode in ("practice", "transfer"):
            with self.subTest(mode=mode):
                extra = "fixture.reference." + mode
                unrelated = "fixture.changed.after.start." + mode
                for task in self.tasks:
                    self.catalog.tasks[task["id"]]["phase"] = mode
                self.catalog.tasks[self.tasks[0]["id"]]["reference_family_ids"] = [extra]
                code, started = self.request("/api/session", {"assessment_goal_id": self.sid, "mode": mode,
                    "task_ids": [task["id"] for task in self.tasks]})
                self.assertEqual(code, 200, started)
                session_id = started["session"]["id"]
                token = next(cookie.value for cookie in self.cookies if cookie.name == self.server._OWNER_COOKIE)
                owner = self.server.get_profile().resolve_owner(token)
                self.assertNotIn(extra, self.server._STORE.exposures(owner)["families"])
                # Later author edits cannot change which reference this session releases.
                self.catalog.tasks[self.tasks[0]["id"]]["reference_family_ids"] = [unrelated]
                code, answered = self.request("/api/answer", {"session_id": session_id,
                    "question_id": self.tasks[0]["id"], "answer": "5"})
                self.assertEqual(code, 200, answered)
                exposed = self.server._STORE.exposures(owner)["families"]
                if mode == "transfer":
                    self.assertEqual(answered["feedback"], {"deferred": True})
                    self.assertNotIn(extra, exposed)
                else:
                    self.assertEqual(exposed[extra]["feedback_count"], 1)
                    self.assertEqual(exposed[extra]["presentation_count"], 0)
                code, finished = self.request("/api/answer", {"session_id": session_id,
                    "question_id": self.tasks[1]["id"], "answer": "已保留摆放图，过程仍待核对。"})
                self.assertEqual(code, 200, finished)
                self.assertTrue(finished["done"])
                exposed = self.server._STORE.exposures(owner)["families"]
                self.assertEqual(exposed[extra]["feedback_count"], 1)
                self.assertNotIn(unrelated, exposed)


if __name__ == "__main__":
    unittest.main()
