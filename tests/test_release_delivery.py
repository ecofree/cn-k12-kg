"""Release failures that matter: byte corruption, missing files, stale PASS and privacy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.release_manifest import (ManifestError, check_manifest, compute_manifest,
                                    manifest_digest, write_manifest)
from tools.package_release import PackageError, history_risks, package_release


JOURNEYS = ("self_check_autosave", "rubric_revision", "unit_phases", "project_history",
            "mobile_stage", "pool_exhaustion")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ReleaseDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cnkg-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "user.name", "Synthetic release test")
        self.write(".gitignore", "build/\nvar/\nnode_modules/\ndata/external/\n")
        self.write(".gitattributes", "* -text\n")
        self.write("README.md", "Synthetic test source only.\n")
        self.write("cnkg/example.py", "VALUE = 1\n")
        self.write("schema/example.json", '{"type":"string"}\n')
        self.write("package-lock.json", '{"lockfileVersion":3}\n')
        self.write("web/static/app.js", "const ready = true;\n")
        self.font = b"\x00WOFF\r\n\x00CR\rLF\n\xff"
        self.write("web/static/vendor/fonts/test.woff2", self.font)
        self.write("web/static/vendor/fonts/LICENSE", "MIT license fixture\n")
        self.write("data/catalog.json", '{"schema":1}\n')
        self.write_json("data/publication_assets.json", {
            "schema_version": 1, "assets": [
                {"id": "fixture-font", "distributed": True, "license": "MIT",
                 "paths": ["web/static/vendor/fonts/"]},
                {"id": "optional-bank", "distributed": False,
                 "paths": ["data/external/", "data/questions_ext.json"]},
            ],
        })
        self.write("tools/release_check.py", "CHECKS = [('manifest', 'Manifest', []), ('tests', 'Tests', [])]\n")
        self.write("tools/browser_check.py", f"JOURNEYS = {JOURNEYS!r}\n")

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode().strip()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))

    def write_json(self, name, value):
        self.write(name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def commit(self, message="fixture"):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", message)

    def verified_fixture(self):
        write_manifest(self.root)
        self.commit()
        common = {"schema": 2, "ok": True, "head": self.git("rev-parse", "HEAD"),
                  "release_manifest_sha256": manifest_digest(self.root), "source_clean": True,
                  "manifest_valid": True, "generated_at": "2026-10-26T12:00:00+00:00",
                  "context_errors": []}
        checks = []
        for key in ("manifest", "tests"):
            name = f"build/release-check/{key}.log"
            raw = f"{key}: synthetic fixture PASS\n".encode()
            self.write(name, raw)
            checks.append({"id": key, "ok": True, "returncode": 0, "log": name, "log_sha256": sha(raw)})
        self.write_json("build/regression_report.json", {**common, "checks": checks,
                        "deferred_by_user": ["independent_teacher_review", "real_student_effect_validation"]})
        browser_checks = []
        for journey in JOURNEYS:
            result = {"schema": 1, "journey": journey, "ok": True,
                      "evidence_type": "synthetic_browser_test"}
            files = {"result.json": json.dumps(result).encode(), "browser.log": b"PASS\n",
                     "http.log": b"GET / 200\n", "final.png": b"\x89PNG\r\n\x1a\nfixture"}
            for name, raw in files.items():
                self.write(f"build/browser-check/{journey}/{name}", raw)
            browser_checks.append({**result, "returncode": 0, "artifacts": journey,
                                   "artifact_sha256": {name: sha(raw) for name, raw in files.items()}})
        self.write_json("build/browser-check/report.json", {**common, "checks": browser_checks,
                        "evidence_type": "synthetic_browser_test", "changed_inputs": []})

    def test_raw_bytes_cover_font_license_schema_lock_and_new_source(self):
        manifest = write_manifest(self.root)
        files = {row["path"]: row for row in manifest["files"]}
        self.assertEqual(files["web/static/vendor/fonts/test.woff2"]["sha256"], sha(self.font))
        self.assertNotEqual(sha(self.font), sha(self.font.replace(b"\r\n", b"\n")))
        for name in ("web/static/vendor/fonts/LICENSE", "schema/example.json", "package-lock.json", "data/catalog.json"):
            self.assertIn(name, files)
        self.assertEqual([], check_manifest(self.root))
        self.write("web/static/new-feature.js", "const newFeature = 1;\n")
        self.assertIn("Missing from release manifest: web/static/new-feature.js", check_manifest(self.root))
        write_manifest(self.root)
        self.write("web/static/vendor/fonts/test.woff2", self.font.replace(b"\r\n", b"\n"))
        self.assertIn("Raw bytes changed: web/static/vendor/fonts/test.woff2", check_manifest(self.root))

    def test_excluded_private_and_unregistered_files_are_not_opened(self):
        private = ("var/learning.sqlite", "build/old/learning.db", "node_modules/x/private.sqlite3",
                   "data/external/unknown.json", "data/questions_ext.json", "local.sqlite.bak",
                   "web/static/vendor/unregistered/library.js")
        for name in private:
            self.write(name, b"private fixture bytes\r\n")
        original_read = Path.read_bytes

        def guarded(path):
            if path.relative_to(self.root).as_posix() in private:
                raise AssertionError(f"Excluded file was opened: {path}")
            return original_read(path)

        with patch.object(Path, "read_bytes", guarded):
            manifest = compute_manifest(self.root)
        self.assertTrue(set(private).isdisjoint(row["path"] for row in manifest["files"]))

    def test_missing_tracked_source_and_symlinks_fail_closed(self):
        write_manifest(self.root)
        self.commit()
        (self.root / "cnkg/example.py").unlink()
        self.assertTrue(any("missing" in error for error in check_manifest(self.root)))
        self.write("cnkg/example.py", "VALUE = 1\n")
        outside = Path(self.temporary.name) / "private.txt"
        outside.write_bytes(b"private")
        (self.root / "outside-link.txt").symlink_to(outside)
        with self.assertRaisesRegex(ManifestError, "symlink"):
            compute_manifest(self.root)
        (self.root / "outside-link.txt").unlink()
        (self.root / "inside-link.txt").symlink_to("README.md")
        with self.assertRaisesRegex(ManifestError, "symlink"):
            compute_manifest(self.root)

    def test_delivery_has_complete_raw_source_current_branch_and_bound_evidence(self):
        self.verified_fixture()
        self.git("branch", "unrelated-branch")
        self.write("var/learning.sqlite", b"real-looking private fixture")
        self.write("build/browser-check/self_check_autosave/trace.zip", b"stale failed trace")
        self.write("build/browser-check/self_check_autosave/failure.png", b"stale failure")
        self.write("build/browser-check/learning.sqlite", b"private fixture")
        self.write("build/regression_report.md", "Old unbound report, do not copy.\n")
        result = package_release(self.root)
        output = self.root / "build/release" / result["head"][:12]
        with zipfile.ZipFile(output / "source.zip") as archive:
            self.assertEqual(archive.read("web/static/vendor/fonts/test.woff2"), self.font)
            self.assertIn("web/static/vendor/fonts/LICENSE", archive.namelist())
            self.assertIn("package-lock.json", archive.namelist())
            self.assertIn("release-manifest.json", archive.namelist())
            self.assertFalse(any("sqlite" in name or name.startswith("build/") for name in archive.namelist()))
        heads = self.git("bundle", "list-heads", str(output / "current-branch.bundle"))
        self.assertEqual(heads, f"{result['head']} refs/heads/main")
        with zipfile.ZipFile(output / "evidence.zip") as archive:
            names = archive.namelist()
            self.assertIn("browser/self_check_autosave/final.png", names)
            self.assertIn("evidence-index.json", names)
            self.assertFalse(any("trace" in name or "failure" in name or "sqlite" in name for name in names))
            self.assertNotIn("regression_report.md", names)
        for name, details in result["artifacts"].items():
            self.assertEqual(sha((output / name).read_bytes()), details["sha256"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_failed_missing_binding_and_incomplete_reports_are_not_publishable(self):
        self.verified_fixture()
        name = "build/regression_report.json"
        original = json.loads((self.root / name).read_bytes())
        for changed in ({**original, "ok": False}, {key: value for key, value in original.items() if key != "head"}):
            self.write_json(name, changed)
            with self.assertRaises(PackageError):
                package_release(self.root)
        self.write_json(name, original)
        browser_name = "build/browser-check/report.json"
        browser = json.loads((self.root / browser_name).read_bytes())
        browser["checks"].pop()
        self.write_json(browser_name, browser)
        with self.assertRaisesRegex(PackageError, "Incomplete browser"):
            package_release(self.root)

    def test_log_or_screenshot_changed_after_pass_is_rejected(self):
        self.verified_fixture()
        log = "build/release-check/tests.log"
        original = (self.root / log).read_bytes()
        self.write(log, b"substituted successful log")
        with self.assertRaisesRegex(PackageError, "Evidence bytes changed"):
            package_release(self.root)
        self.write(log, original)
        self.write("build/browser-check/unit_phases/final.png", b"\x89PNG\r\n\x1a\nsubstituted")
        with self.assertRaisesRegex(PackageError, "Evidence bytes changed"):
            package_release(self.root)

    def test_git_archive_export_ignore_cannot_silently_omit_published_source(self):
        self.write(".gitattributes", "* -text\nschema/example.json export-ignore\n")
        self.verified_fixture()
        with self.assertRaisesRegex(PackageError, "exactly the manifest"):
            package_release(self.root)
        self.assertFalse(any((self.root / "build/release").glob("*/delivery.json")))

    def test_archived_source_is_portable_even_inside_a_different_repository(self):
        self.verified_fixture()
        result = package_release(self.root)
        source_zip = self.root / "build/release" / result["head"][:12] / "source.zip"
        # This extraction is deliberately nested in the parent Git repository.
        extracted = self.root / "build/extracted-source"
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(extracted)
        self.assertFalse((extracted / ".git").exists())
        self.assertEqual(compute_manifest(self.root), compute_manifest(extracted))
        self.assertEqual([], check_manifest(extracted))
        (extracted / "var").mkdir()
        (extracted / "var/learning.sqlite").write_bytes(b"private fixture")
        (extracted / "web/static/new-portable.js").write_text("const added = true;\n")
        self.assertIn("Missing from release manifest: web/static/new-portable.js", check_manifest(extracted))
        (extracted / "web/static/new-portable.js").unlink()
        (extracted / "schema/example.json").unlink()
        self.assertIn("No longer distributable or present: schema/example.json", check_manifest(extracted))
        with self.assertRaisesRegex(PackageError, "actual Git repository root"):
            package_release(extracted)

    def test_changed_or_newly_committed_code_cannot_reuse_old_pass(self):
        self.verified_fixture()
        self.write("cnkg/example.py", "VALUE = 2\n")
        with self.assertRaisesRegex(PackageError, "not clean"):
            package_release(self.root)
        write_manifest(self.root)
        self.commit("new source")
        with self.assertRaisesRegex(PackageError, "different or missing head"):
            package_release(self.root)

    def test_removed_private_database_in_history_blocks_bundle_without_opening_blob(self):
        self.write("var/old-learning.sqlite", b"private fixture")
        self.git("add", "-f", "var/old-learning.sqlite")
        self.commit("bad historical fixture")
        self.git("rm", "var/old-learning.sqlite")
        self.commit("remove historical fixture")
        self.verified_fixture()
        risks = history_risks(self.root, "refs/heads/main")
        self.assertEqual(len(risks), 1)
        self.assertTrue(risks[0].startswith("var/old-learning.sqlite:"))
        with self.assertRaisesRegex(PackageError, "var/old-learning.sqlite"):
            package_release(self.root)
        self.assertFalse((self.root / "build/release").exists())


if __name__ == "__main__":
    unittest.main()
