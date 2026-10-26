"""Author-source ownership, retired items, runtime contracts and publish gates."""
from __future__ import annotations

import copy
from contextlib import closing, redirect_stdout, redirect_stderr
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cnkg import answers, store
from cnkg.catalog import CatalogError, compile_catalog, load_catalog
from cnkg.contracts import validate_resource
from cnkg.evidence import question_content_sha256, question_policy
from cnkg.graph import Graph, load_graph
from tools import build_project, rebuild_data as RD
from tools import write_application_items as legacy_app

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    RD.write_json(Path(path), data)


class DeclarativeSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "data"
        self.question = copy.deepcopy(next(q for q in read(DATA / "questions.json")
                                          if q["id"] == "m5b.4.1-q1"))
        self.topic = next(t for t in read(DATA / "topics.json") if t["id"] == "m5b.4.1")
        self.manifest = {"schema_version": 2,
                         "content_sources": [{"path": "content/vol.json", "volume": self.topic["volume"]}],
                         "question_sources": [{"name": "fixture", "path": "question_sources/items.json", "status": "active"}]}
        self.source = {"schema_version": 1, "source": "fixture", "questions": [self.question]}
        write(self.data / RD.REGISTRY, self.manifest)
        write(self.data / "content/vol.json", {"volume": self.topic["volume"],
              "topics": {self.topic["id"]: self.topic}, "dependencies": [], "questions": []})
        write(self.data / "question_sources/items.json", self.source)

    def test_json_build_has_no_python_question_generator(self):
        with patch.object(RD, "application_questions", side_effect=AssertionError("Python generator executed")):
            built, origins = RD.build_data(self.data)
            actual, _ = RD.build_data(DATA)
        self.assertEqual(built["questions.json"], [self.question])
        self.assertIn("question_sources/items.json", str(origins))
        self.assertEqual(actual["questions.json"], read(DATA / "questions.json"))

    def test_v2_cannot_reenable_an_old_generator_or_sync_from_outputs(self):
        original = (self.data / "question_sources/items.json").read_bytes()
        self.manifest["question_generators"] = [{"name": "application-items"}]
        write(self.data / RD.REGISTRY, self.manifest)
        with self.assertRaisesRegex(RD.SourceError, "Python question generators are retired"):
            RD.build_data(self.data)
        self.manifest.pop("question_generators")
        write(self.data / RD.REGISTRY, self.manifest)
        with self.assertRaisesRegex(RD.SourceError, "v2|作者|author"):
            RD.synchronize_sources(self.data, "attempt to overwrite authoritative sources")
        self.assertEqual((self.data / "question_sources/items.json").read_bytes(), original)

    def test_duplicate_question_ids_across_sources_are_not_last_writer_wins(self):
        source = read(self.data / "content/vol.json")
        source["questions"] = [{**self.question, "answer": "wrong"}]
        write(self.data / "content/vol.json", source)
        with self.assertRaisesRegex(RD.SourceError, "redefined|duplicate"):
            RD.build_data(self.data)

    def test_frozen_retired_source_and_unregistered_sources_are_rejected(self):
        path = self.data / "question_sources/items.json"
        self.manifest["question_sources"][0].update(status="retired", sha256_lf=RD.sha256_lf(path))
        write(self.data / RD.REGISTRY, self.manifest)
        RD.build_data(self.data)
        self.source["questions"][0]["answer"] = "25"
        write(path, self.source)
        with self.assertRaisesRegex(RD.SourceError, "retired question archive changed"):
            RD.build_data(self.data)
        self.manifest["question_sources"][0]["sha256_lf"] = RD.sha256_lf(path)
        write(self.data / RD.REGISTRY, self.manifest)
        write(self.data / "question_sources/undeclared.json", self.source)
        with self.assertRaisesRegex(RD.SourceError, "unregistered question sources"):
            RD.build_data(self.data)

    def test_application_reordering_preserves_explicit_ids_and_missing_ids_fail(self):
        items = legacy_app.app_items.all_items()
        before, errors = legacy_app.build(items, [])
        self.assertEqual(errors, [])
        after, errors = legacy_app.build(list(reversed(items)), [])
        self.assertEqual(errors, [])
        self.assertEqual({q["id"]: q for q in before}, {q["id"]: q for q in after})
        missing = copy.deepcopy(items[0])
        missing.pop("id")
        _, errors = legacy_app.build([missing], [])
        self.assertTrue(errors)


class ContractAuthorityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "data"
        self.q = copy.deepcopy(next(q for q in read(DATA / "questions.json") if q["id"] == "m5b.4.1-q1"))
        topic = next(t for t in read(DATA / "topics.json") if t["id"] == self.q["topicId"])
        registry = read(DATA / "skills/registry.json")
        registry["placements"] = {topic["id"]: registry["placements"][topic["id"]]}
        used = set(registry["placements"][topic["id"]]["criterion_skill_ids"])
        registry["skills"] = {sid: skill for sid, skill in registry["skills"].items() if sid in used}
        registry["objective_skill_ids"] = {}
        self.bindings = read(DATA / "skills/catalog-bindings.json")
        self.key = "bank:" + self.q["id"]
        self.bindings["tasks"] = {self.key: self.bindings["tasks"][self.key]}
        self.bindings["examples"] = {}
        write(self.data / "skills/registry.json", registry)
        write(self.data / "topics.json", [topic])
        self.save()

    def save(self):
        write(self.data / "questions.json", [self.q])
        write(self.data / "skills/catalog-bindings.json", self.bindings)

    def test_only_one_contract_owner_and_author_edit_reaches_compiled_task(self):
        binding = self.bindings["tasks"][self.key]
        old = compile_catalog(self.data)["tasks"][self.q["id"]]
        self.q["answer_spec"] = binding.pop("answer_spec")
        binding["answer_spec_source"] = "author"
        self.save()
        same = compile_catalog(self.data)["tasks"][self.q["id"]]
        self.assertEqual(old, same, "moving an unchanged contract must not invalidate saved resource versions")
        self.q["answer_spec"]["representation"] = "integer"
        self.save()
        current = compile_catalog(self.data)["tasks"][self.q["id"]]
        self.assertEqual(current["answer_spec"]["representation"], "integer")
        self.assertNotEqual(old["resource_version"], current["resource_version"])
        graded = answers.grade_answer(current, "24.0")
        self.assertEqual(graded["status"], "wrong")
        self.assertEqual(graded["reason"], "representation")

    def test_duplicate_or_missing_contract_owners_block_compilation(self):
        binding = self.bindings["tasks"][self.key]
        self.q["answer_spec"] = copy.deepcopy(binding["answer_spec"])
        self.save()
        with self.assertRaisesRegex(CatalogError, "第二份"):
            compile_catalog(self.data)
        binding["answer_spec_source"] = "author"
        self.save()
        with self.assertRaises(CatalogError):
            compile_catalog(self.data)
        binding.pop("answer_spec")
        self.q.pop("answer_spec")
        self.save()
        with self.assertRaisesRegex(CatalogError, "作者答案契约缺失"):
            compile_catalog(self.data)

    def test_external_id_collision_cannot_replace_own_question_or_contract(self):
        write(self.data / "catalog.json", compile_catalog(self.data))
        write(self.data / "dependencies.json", [])
        external = {**self.q, "source": "fixture-external", "answer": "8", "verify": "4+4",
                    "usable_for_diagnosis": True, "evidence_level": "verified"}
        write(self.data / "questions_ext.json", [external])
        graph = load_graph(data_dir=self.data, include_external=True)
        own = graph.questions_by_id[self.q["id"]]
        imported = graph.questions_by_id["ext-fixture-external-" + self.q["id"]]
        self.assertEqual(own["answer"], "24")
        self.assertIn("answer_spec", own)
        self.assertEqual(imported["answer"], "8")
        self.assertNotIn("answer_spec", imported)


class RuntimeAndArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()
        cls.graph = load_graph()

    def test_active_runtime_uses_explicit_contracts_without_legacy_inference(self):
        self.assertEqual(len(self.graph.questions), 3079)
        self.assertEqual(len(self.graph.retired_questions_by_id), 489)
        self.assertFalse(set(self.graph.questions_by_id) & set(self.graph.retired_questions_by_id))
        self.assertEqual(set(self.graph.retired_questions_by_id), set(self.catalog.retired_tasks))
        with patch.object(answers, "infer_answer_spec", side_effect=AssertionError("legacy inference reached")):
            for question in self.graph.questions:
                compiled = self.catalog.task(question["id"])
                self.assertEqual(question["answer_spec"], compiled["answer_spec"])
                self.assertEqual(answers.grade_answer(question, question["answer"])["status"], "correct", question["id"])
                for field in ("practice_eligible", "diagnosis_eligible"):
                    self.assertEqual(self.graph.question_policies[question["id"]][field], compiled["policy"][field])
        self.assertEqual(sum(p["diagnosis_eligible"] for p in self.graph.question_policies.values()), 1967)

    def test_twelve_retirements_preserve_question_content_and_old_metadata(self):
        audit = read(DATA / "question_retirements.json")
        archived = {q["id"]: q for q in read(DATA / "question_sources/retired-alignment.json")["questions"]}
        self.assertEqual(len(audit["records"]), 12)
        self.assertEqual(audit["reviewer_type"], "ai_author")
        for record in audit["records"]:
            q = archived[record["question_id"]]
            self.assertEqual(question_content_sha256(q), record["original_content_sha256"])
            self.assertEqual(record["content_sha256"], record["original_content_sha256"])
            self.assertEqual(question_policy(q), record["policy_after"])
            self.assertFalse(question_policy(q)["practice_eligible"])
            self.assertFalse(question_policy(q)["diagnosis_eligible"])
            restored = copy.deepcopy(q)
            for field, change in record["changed_fields"].items():
                if change["before_present"]:
                    restored[field] = change["before"]
                else:
                    restored.pop(field, None)
            self.assertEqual(question_policy(restored), record["policy_before"])
            self.assertTrue(record["reason"])
            self.assertEqual(self.catalog.task(q["id"])["purpose"], [])

    def test_legacy_fixtures_and_external_questions_keep_their_own_contract_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            topic = self.graph.topics[0]
            question = {"id": "legacy-1", "topicId": topic["id"], "type": "numeric",
                        "prompt": "计算1+1", "answer": "2", "verify": "1+1"}
            external = {**question, "id": "external-1", "source": "fixture-external",
                        "usable_for_diagnosis": True, "evidence_level": "verified"}
            for name, value in (("topics.json", [topic]), ("dependencies.json", []),
                                ("questions.json", [question]), ("questions_ext.json", [external])):
                write(data / name, value)
            loaded = load_graph(data_dir=data, include_external=True)
            self.assertEqual(loaded.questions_by_id["legacy-1"], question)
            self.assertEqual(loaded.questions_by_id["external-1"], external)
            self.assertIsNone(loaded.catalog)
            self.assertEqual(answers.grade_answer(loaded.questions_by_id["external-1"], "2")["status"], "correct")
            direct = Graph([topic], [], [question], data_dir=data)
            self.assertEqual(direct.questions, [question])

    def test_database_has_active_contracts_and_separate_retired_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "content.sqlite"
            counts = store.build(path, include_external=False)
            self.assertEqual(counts["questions"], 3079)
            self.assertEqual(counts["retired_questions"], 489)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions WHERE answer_spec_json IS NULL").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM questions q JOIN retired_questions r ON q.id=r.id").fetchone()[0], 0)
                self.assertEqual(json.loads(connection.execute("SELECT answer_spec_json FROM questions WHERE id='m5b.4.1-q1'").fetchone()[0]),
                                 self.catalog.task("m5b.4.1-q1")["answer_spec"])
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            before = path.read_bytes()
            with patch.object(store, "DDL", store.DDL + "\nTHIS IS INVALID SQL;"):
                with self.assertRaises(sqlite3.DatabaseError):
                    store.build(path, include_external=False)
            self.assertEqual(path.read_bytes(), before, "failed content rebuild must preserve the previous file")
            self.assertEqual(list(path.parent.glob("*.sqlite")), [path])

    def test_stale_wal_is_recovered_but_a_live_writer_prevents_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            data = directory / "data"
            for name, value in (("topics.json", self.graph.topics[:1]), ("dependencies.json", []),
                                ("questions.json", []), ("standards.json", [])):
                write(data / name, value)
            stale = directory / "stale.sqlite"
            # A committed WAL left by an abrupt process exit is not a live
            # session. Recovery must use SQLite rather than deleting sidecars.
            script = ("import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); "
                      "c.execute('PRAGMA journal_mode=WAL'); "
                      "c.execute('CREATE TABLE previous_content(value)'); "
                      "c.execute('INSERT INTO previous_content VALUES (9)'); c.commit(); os._exit(0)")
            subprocess.run([sys.executable, "-c", script, str(stale)], check=True, timeout=15)
            self.assertTrue(Path(str(stale) + "-wal").exists())
            with patch.object(store, "DATA", data):
                self.assertEqual(store.build(stale, include_external=False)["topics"], 1)
            with closing(sqlite3.connect(stale)) as connection:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            live = directory / "live.sqlite"
            with closing(sqlite3.connect(live)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE previous_content(value)")
                connection.execute("INSERT INTO previous_content VALUES (9)")
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO previous_content VALUES (10)")
                with patch.object(store, "DATA", data), self.assertRaisesRegex(ValueError, "WAL|使用"):
                    store.build(live, include_external=False)
                self.assertEqual(connection.execute("SELECT value FROM previous_content").fetchall(), [(9,), (10,)])
                connection.rollback()


class AuthoringEntrypointTests(unittest.TestCase):
    def test_retired_writers_exit_before_import_or_any_content_write(self):
        files = [path for folder in ("content", "question_sources", "teach", "learning_units", "projects", "skills")
                 for path in (DATA / folder).rglob("*.json")]
        files.extend(DATA / name for name in ("content_sources.json", "questions.json", "topics.json", "catalog.json"))
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        writers = read(ROOT / "tools/legacy_writers.json")["writers"]
        self.assertEqual(len(writers), 30)
        for writer in writers:
            with self.subTest(writer=writer["path"]):
                result = subprocess.run([sys.executable, str(ROOT / writer["path"])],
                                        cwd=ROOT, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("build_project.py --release", result.stderr)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

    def test_publish_pipeline_stops_before_later_writes_after_a_failed_gate(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=5 if "register_catalog.py" in command[1] else 0)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code, results = build_project.run_steps("release", runner=runner)
        self.assertEqual(code, 5)
        self.assertEqual(len(calls), 2)
        self.assertEqual(results[-1]["returncode"], 5)
        self.assertFalse(any("release_check.py" in str(command) for command in calls))

    def test_new_source_contracts_reject_unknown_manifest_fields(self):
        manifest = read(DATA / "content_sources.json")
        self.assertEqual(validate_resource("content-manifest", manifest), [])
        manifest["python_generator_override"] = True
        self.assertTrue(validate_resource("content-manifest", manifest))
        for source in read(DATA / "content_sources.json")["question_sources"]:
            self.assertEqual(validate_resource("question-source", read(DATA / source["path"])), [])


if __name__ == "__main__":
    unittest.main()
