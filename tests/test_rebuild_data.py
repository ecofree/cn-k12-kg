"""Source authority, reproducibility and drift detection regressions."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from tools import apply_content, balance_options as BO, build_db, rebuild_data as RD


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


class TransformedSourceBuildTest(unittest.TestCase):
    def setUp(self):
        SourceBuildTest.setUp(self)
        # Exercise the historical v1 importer explicitly; production v2 builds
        # use declarative question_sources and never execute this branch.
        registered = RD.load_registry()
        self.registry["question_generators"] = copy.deepcopy(registered.get("question_generators")
                                                               or registered["retired_question_generators"])
        self.registry["question_transforms"] = [{
            "name": BO.TRANSFORM_NAME, "version": BO.TRANSFORM_VERSION,
            "entrypoint": BO.TRANSFORM_ENTRYPOINT, "inputs": [],
        }]

        def choice(number, size=4, application=False):
            return {"id": f"m1a.2.1-q{number}", "topicId": "m1a.2.1", "type": "choice",
                    "prompt": f"题干{number}", "explanation": "正确内容为甲。", "answer": "o1",
                    "choices": [{"id": f"o{i + 1}", "text": f"选项内容{i + 1}",
                                 "metadata": {"value": i}} for i in range(size)],
                    "source": RD.APPLICATION_SOURCE if application else "self-generated-completion"}

        self.payload["questions"] = [choice(n) for n in range(1, 6)] + [choice(6, 3)]
        self.generated = [choice(101, application=True), choice(102, application=True),
                          choice(103, 3, application=True)]
        RD.write_json(self.source, self.payload)
        RD.write_json(self.data / RD.REGISTRY, self.registry)
        self.generator_patch = patch.object(RD, "application_questions",
                                            side_effect=lambda base: copy.deepcopy(self.generated))
        self.generator_patch.start()
        self.addCleanup(self.generator_patch.stop)

    def test_complete_population_transform_and_clean_rebuild(self):
        raw, _ = RD.build_data(self.data, apply_transforms=False)
        built, _ = RD.build_data(self.data)
        self.assertEqual(built["questions.json"], BO.balance_questions(raw["questions.json"]))
        for size, counts in BO.display_position_counts(built["questions.json"]).items():
            self.assertLessEqual(max(counts[i] for i in range(size)) -
                                 min(counts[i] for i in range(size)), 1)
        built_by_id = {q["id"]: q for q in built["questions.json"]}
        subset = BO.balance_questions(self.generated)
        self.assertTrue(any(built_by_id[q["id"]] != q for q in subset),
                        "fixture must expose the incorrect generated-subset balancing strategy")
        SourceBuildTest.test_clean_build_needs_no_existing_outputs_and_is_byte_reproducible(self)

    def test_build_sync_build_is_a_fixed_point_and_does_not_rewrite_sources(self):
        built, _ = RD.build_data(self.data)
        for name, rows in built.items():
            RD.write_json(self.data / name, rows)
        original_bytes = self.source.read_bytes()
        result = RD.synchronize_sources(self.data, "unchanged built display order")
        self.assertEqual(result["changed_sources"], [])
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual(RD.build_data(self.data)[0], built)

    def test_sync_preserves_edited_option_payload_in_canonical_source_order(self):
        built, _ = RD.build_data(self.data)
        target = next(q for q in built["questions.json"] if q["id"] == "m1a.2.1-q1")
        for option in target["choices"]:
            if option["id"] == "o2":
                option.update(text="有意修正的干扰项", extra={"review_note": "fixture"})
        for name, rows in built.items():
            RD.write_json(self.data / name, rows)
        RD.synchronize_sources(self.data, "intentional option content correction")
        stored = RD.read_json(self.source)["questions"][0]
        self.assertEqual([o["id"] for o in stored["choices"]], ["o1", "o2", "o3", "o4"])
        self.assertEqual(stored["choices"][1]["text"], "有意修正的干扰项")
        self.assertEqual(stored["choices"][1]["extra"], {"review_note": "fixture"})
        self.assertEqual(RD.build_data(self.data)[0], built)

    def test_sync_does_not_launder_a_generated_question_edit(self):
        built, _ = RD.build_data(self.data)
        next(q for q in built["questions.json"] if q["source"] == RD.APPLICATION_SOURCE)["answer"] = "o2"
        for name, rows in built.items():
            RD.write_json(self.data / name, rows)
        original_bytes = self.source.read_bytes()
        with self.assertRaisesRegex(RD.SourceError, "generated output differs"):
            RD.synchronize_sources(self.data, "must fix generator instead")
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_pool_edits_in_generated_outputs_are_rejected_with_source_edit_guidance(self):
        built, _ = RD.build_data(self.data)
        actual_apps = {q['id']: q for q in built['questions.json'] if q['source'] == RD.APPLICATION_SOURCE}
        extra = None
        for number in range(7, 40):
            candidate = copy.deepcopy(self.payload['questions'][0])
            candidate.update(id=f'm1a.2.1-q{number}', prompt=f'新题{number}')
            transformed = BO.balance_questions(built['questions.json'] + [candidate])
            if any(q['source'] == RD.APPLICATION_SOURCE and q != actual_apps[q['id']] for q in transformed):
                extra = candidate
                break
        self.assertIsNotNone(extra, 'fixture must change the full-pool application display order')
        built['questions.json'].append(extra)
        for name, rows in built.items():
            RD.write_json(self.data / name, rows)
        original_bytes = self.source.read_bytes()
        with self.assertRaisesRegex(RD.SourceError, 'move those edits to the registered content source'):
            RD.synchronize_sources(self.data, 'unsupported legacy output-pool edit')
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_balance_cli_uses_sources_and_dry_run_writes_nothing(self):
        original_bytes = self.source.read_bytes()
        with redirect_stdout(StringIO()):
            self.assertEqual(BO.main(["--data-dir", str(self.data), "--dry-run"]), 0)
        self.assertTrue(all(not (self.data / name).exists() for name in RD.OUTPUTS))
        with redirect_stdout(StringIO()):
            self.assertEqual(BO.main(["--data-dir", str(self.data)]), 0)
        actual = {name: RD.read_json(self.data / name) for name in RD.OUTPUTS}
        self.assertEqual(actual, RD.build_data(self.data)[0])
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_registry_rejects_an_unrecognized_transform_implementation(self):
        self.registry["question_transforms"][0]["entrypoint"] = "tools/unknown.py"
        RD.write_json(self.data / RD.REGISTRY, self.registry)
        with self.assertRaisesRegex(RD.SourceError, "identity or inputs"):
            RD.build_data(self.data)

    def test_manifest_hashes_transform_and_curriculum_evidence(self):
        built, _ = RD.build_data(self.data)
        for name, rows in built.items():
            RD.write_json(self.data / name, rows)
        RD.write_json(self.data / "standards.json", [])
        RD.write_json(self.data / "outline.json", {"volumes": []})
        capsule = self.data / "curriculum_comparisons" / "fixture.json"
        RD.write_json(capsule, {"comparison_status": "complete"})
        actual_root = build_db.ROOT

        def fixture_hash(path):
            # Runtime code lives in the real checkout; fixture data lives in its
            # temporary root.  Hash both real contents, without copying the repo.
            path = pathlib.Path(path)
            if path.is_file():
                return RD.sha256_lf(path)
            return RD.sha256_lf(actual_root / path.relative_to(self.data.parent))

        with patch.object(build_db.store, "DATA", self.data), \
                patch.object(build_db, "ROOT", self.data.parent), \
                patch.object(build_db, "sha256", fixture_hash):
            manifest_path = build_db.write_manifest({}, include_external=False)
        manifest = RD.read_json(manifest_path)
        sources = {item["name"]: item["sha256"] for item in manifest["sources"]}
        self.assertEqual(sources[BO.TRANSFORM_ENTRYPOINT], RD.sha256_lf(actual_root / BO.TRANSFORM_ENTRYPOINT))
        for name in ("tools/curriculum_compare.py", "tools/teach_cards.py", "tools/coverage_report.py"):
            self.assertIn(name, sources)
        assets = {item["name"]: item["sha256"] for item in manifest["learning_assets"]}
        self.assertEqual(assets["data/curriculum_comparisons/fixture.json"], RD.sha256_lf(capsule))


class ApplicationWriterBuildTest(unittest.TestCase):
    def test_imported_legacy_main_cannot_write_or_trigger_a_build(self):
        from tools import write_application_items as writer
        stream = StringIO()
        with patch.object(RD, "main") as rebuild, redirect_stderr(stream):
            self.assertEqual(writer.main(), 2)
        rebuild.assert_not_called()
        self.assertIn("build_project.py --release", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
