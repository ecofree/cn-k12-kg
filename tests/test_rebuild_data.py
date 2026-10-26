"""Source authority, reproducibility and drift detection regressions."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
from io import StringIO
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from tools import apply_content, rebuild_data as RD


class SourceBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = pathlib.Path(self.temp.name) / "data"
        self.source = self.data / "content" / "vol.json"
        self.registry = {"schema_version": 1,
                         "content_sources": [{"path": "content/vol.json", "volume": "一上"}]}
        self.payload = {
            "volume": "一上",
            "topics": {tid: {"id": tid, "volume": "一上"}
                       for tid in ("m1a.10.1", "m1a.2.1")},
            "dependencies": [{"topicId": "m1a.10.1", "prerequisiteId": "m1a.2.1", "strength": "hard"}],
            "questions": [{"id": tid + "-q1", "topicId": tid, "type": "numeric",
                           "answer": "2", "verify": "1+1", "choices": [], "accept": []}
                          for tid in ("m1a.10.1", "m1a.2.1")],
        }
        RD.write_json(self.data / RD.REGISTRY, self.registry)
        RD.write_json(self.source, self.payload)

    def test_clean_build_needs_no_existing_outputs_and_is_byte_reproducible(self):
        first = self.data.parent / "first"
        second = self.data.parent / "second"
        for destination in (first, second):
            with redirect_stdout(StringIO()):
                self.assertEqual(RD.main(["--data-dir", str(self.data),
                                          "--output-dir", str(destination)]), 0)
        for name in RD.OUTPUTS:
            self.assertFalse((self.data / name).exists())
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
            self.assertNotIn(b"\r", (first / name).read_bytes())
        self.assertEqual([t["id"] for t in RD.read_json(first / "topics.json")],
                         ["m1a.2.1", "m1a.10.1"])

    def test_duplicate_id_has_no_last_writer_override(self):
        other = {"volume": "二上", "topics": {}, "questions": [self.payload["questions"][0]]}
        self.registry["content_sources"].append({"path": "content/second.json", "volume": "二上"})
        RD.write_json(self.data / RD.REGISTRY, self.registry)
        RD.write_json(self.data / "content/second.json", other)
        with self.assertRaisesRegex(RD.SourceError, "redefined"):
            RD.build_data(self.data)

    def test_unregistered_patch_fails_instead_of_replaying(self):
        RD.write_json(self.data / "content/old.json", {"questions": []})
        with self.assertRaisesRegex(RD.SourceError, "unregistered"):
            RD.build_data(self.data)

    def test_retired_source_is_ignored_and_cannot_be_changed(self):
        retired = self.data / "content/old.json"
        RD.write_json(retired, {"questions": [{"id": "bad-stale-record"}]})
        self.registry["retired_sources"] = [{"path": "content/old.json", "sha256_lf": RD.sha256_lf(retired)}]
        RD.write_json(self.data / RD.REGISTRY, self.registry)
        built, _ = RD.build_data(self.data)
        self.assertEqual(len(built["questions.json"]), 2)
        RD.write_json(retired, {"questions": []})
        with self.assertRaisesRegex(RD.SourceError, "retired source was edited"):
            RD.build_data(self.data)

    def test_legacy_update_flag_cannot_reactivate_a_retired_source(self):
        retired = self.data / "content/old.json"
        RD.write_json(retired, {"questions": []})
        self.registry["retired_sources"] = [{"path": "content/old.json", "sha256_lf": RD.sha256_lf(retired)}]
        RD.write_json(self.data / RD.REGISTRY, self.registry)
        with patch.object(apply_content, "DATA", self.data), redirect_stdout(StringIO()):
            self.assertEqual(apply_content.main(["--file", str(retired), "--update"]), 1)
        self.assertFalse((self.data / "questions.json").exists())

    def test_all_fields_are_compared_including_null_vs_missing(self):
        built, _ = RD.build_data(self.data)
        actual = copy.deepcopy(built)
        actual["questions.json"][0].update({"verify": "3-1", "accept": ["two"],
                                          "choices": [{"key": "A", "text": "2"}], "new_field": None})
        delta = RD.differences(built, actual)
        self.assertEqual(delta["questions.json"]["changed"][0]["fields"],
                         ["accept", "choices", "new_field", "verify"])

    def test_source_sync_is_explicit_and_requires_a_reason(self):
        built, _ = RD.build_data(self.data)
        for name, rows in built.items():
            RD.write_json(self.data / name, rows)
        built["questions.json"][0]["answer"] = "changed intentionally"
        RD.write_json(self.data / "questions.json", built["questions.json"])
        with self.assertRaisesRegex(RD.SourceError, "requires a reason"):
            RD.synchronize_sources(self.data, "")
        self.assertNotEqual(RD.build_data(self.data)[0], built)
        result = RD.synchronize_sources(self.data, "fixture intentional correction")
        self.assertEqual(result["changed_sources"], ["content/vol.json"])
        self.assertEqual(RD.build_data(self.data)[0], built)

    def test_line_ending_hash_does_not_depend_on_checkout(self):
        lf = self.data.parent / "lf.json"
        crlf = self.data.parent / "crlf.json"
        lf.write_bytes(b'{\n "a": 1\n}\n')
        crlf.write_bytes(b'{\r\n "a": 1\r\n}\r\n')
        self.assertNotEqual(lf.read_bytes(), crlf.read_bytes())
        self.assertEqual(RD.sha256_lf(lf), RD.sha256_lf(crlf))


class RepositorySourceTest(unittest.TestCase):
    def test_registered_generator_does_not_read_generated_json_outputs(self):
        original_read = pathlib.Path.read_text
        forbidden = {RD.DATA / name for name in RD.OUTPUTS}

        def guarded(path, *args, **kwargs):
            if path.resolve() in forbidden:
                raise AssertionError(f"build read its output: {path}")
            return original_read(path, *args, **kwargs)

        with patch.object(pathlib.Path, "read_text", guarded):
            first, origins = RD.build_data()
            second, _ = RD.build_data()
        self.assertEqual(first, second)
        self.assertTrue(any(q.get("source") == RD.APPLICATION_SOURCE for q in first["questions.json"]))
        self.assertEqual(len(origins["questions.json"]), len(first["questions.json"]))


if __name__ == "__main__":
    unittest.main()
