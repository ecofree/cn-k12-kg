"""A source edit cannot give an already loaded grader a new identity."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cnkg import instruction, session_content
from cnkg.runtime_version import FrozenRuntimeVersion, freeze_grader
from tests import test_instruction_v4


class FrozenRuntimeVersionTest(unittest.TestCase):
    def test_capture_is_eager_even_when_first_version_read_follows_an_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "grade.py"
            source.write_text("def grade(value): return value == 4\n")
            namespace = {}
            exec(compile(source.read_text(), str(source), "exec"), namespace)
            running_grade = namespace["grade"]
            frozen = FrozenRuntimeVersion.capture(root, ["grade.py"])
            original = frozen.version
            source.write_text("def grade(value): return value == 5\n")
            self.assertTrue(running_grade(4))
            self.assertFalse(running_grade(5))
            self.assertEqual(original, frozen.version)
            self.assertEqual(["grade.py"], frozen.changed_sources())
            # A fresh process/release capture must distinguish the new source.
            next_release = FrozenRuntimeVersion.capture(root, ["grade.py"])
            self.assertNotEqual(original, next_release.version)
            self.assertEqual([], next_release.changed_sources())

    def test_hash_format_preserves_unchanged_existing_grader_records(self):
        frozen = freeze_grader("support")
        prior_format = hashlib.sha256(json.dumps(dict(frozen.source_hashes),
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(prior_format, frozen.version)


class RunningEntryPointVersionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load an isolated copy of the HTTP module. These tests exercise the
        # real version/continuation functions, without touching shared catalog
        # fixtures that another content-authoring process may be rebuilding.
        path = Path(__file__).resolve().parents[1] / "web/server.py"
        spec = importlib.util.spec_from_file_location("runtime_version_http_fixture", path)
        cls.server = importlib.util.module_from_spec(spec)
        with patch("cnkg.content_repository.ContentRepository"):
            spec.loader.exec_module(cls.server)

    def test_disk_edit_does_not_stamp_new_sessions_with_unloaded_code(self):
        fixture = test_instruction_v4.InstructionTest()
        fixture.setUp()
        expected = instruction.grader_version()
        expected_unit = self.server.unit_grader_version()
        expected_topic = self.server.topic_grader_version()
        loaded_function = instruction.grade_answer
        with patch.object(Path, "read_bytes", return_value=b"changed while server is running"):
            record = fixture.create()
            self.assertIs(loaded_function, instruction.grade_answer)
            self.assertEqual(expected, record["grader_sha256"])
            self.assertEqual(expected_unit, self.server.unit_grader_version())
            self.assertEqual(expected_topic, self.server.topic_grader_version())
            self.assertIsNone(instruction.version_problem(record))
            newly_loaded_release = freeze_grader("support")
            self.assertNotEqual(expected, newly_loaded_release.version)
            with patch.object(instruction, "_GRADER_RUNTIME", newly_loaded_release):
                self.assertIn("答案规则已经更新", instruction.version_problem(record))
            # The historical snapshot and original answers are not rewritten.
            self.assertEqual(expected, record["grader_sha256"])
            self.assertEqual([], record["answers"])

    def test_public_version_function_remains_patchable_for_continuation_tests(self):
        snapshot = {"schema": 1, "test": "frozen topic payload"}
        record = {"kind": "topic", "graph_snapshot": snapshot,
                  "content_sha256": session_content.digest(snapshot),
                  "grader_sha256": self.server.topic_grader_version()}
        self.assertIsNone(self.server._version_problem(record))
        with patch.object(self.server, "topic_grader_version", return_value="new-release"):
            self.assertIn("判分规则已经更新", self.server._version_problem(record))
        self.assertIsNone(self.server._version_problem(record))

    def test_legacy_graph_first_lookup_also_uses_startup_runtime(self):
        graph = SimpleNamespace(topics=[], dependencies=[], questions=[], tiers={})
        expected = self.server._hash({"topics": [], "dependencies": [], "questions": [], "tiers": {},
            "runtime": dict(self.server._TOPIC_GRADER_RUNTIME.source_hashes)})
        with patch.object(self.server, "GRAPH", graph), patch.object(Path, "read_bytes", return_value=b"edited later"):
            self.assertEqual(expected, self.server.graph_version())


if __name__ == "__main__":
    unittest.main()
