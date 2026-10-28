"""Release failures that matter: byte corruption, missing files, stale PASS and privacy."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cnkg.change_review import review_changes
from tools.release_manifest import (ManifestError, check_manifest, compute_manifest,
                                    manifest_digest, write_manifest)
from tools.package_release import (PackageError, collect_evidence, history_risks,
                                   identity, package_release, verify_bundle_restore)


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

    def verified_fixture(self, *, journeys=JOURNEYS, observations=None):
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
        for journey in journeys:
            result = {"schema": 1, "journey": journey, "ok": True,
                      "evidence_type": "synthetic_browser_test"}
            if observations and journey in observations:
                result["observations"] = observations[journey]
            files = {"result.json": json.dumps(result).encode(), "browser.log": b"PASS\n",
                     "http.log": b"GET / 200\n", "final.png": b"\x89PNG\r\n\x1a\nfixture"}
            for name, raw in files.items():
                self.write(f"build/browser-check/{journey}/{name}", raw)
            browser_checks.append({**result, "returncode": 0, "artifacts": journey,
                                   "artifact_sha256": {name: sha(raw) for name, raw in files.items()}})
        self.write_json("build/browser-check/report.json", {**common, "checks": browser_checks,
                        "evidence_type": "synthetic_browser_test", "changed_inputs": []})

    def reviewed_preview_fixture(self, *, review_evidence_sha256=None):
        journeys = JOURNEYS + ("changed_content_preview",)
        self.write("tools/browser_check.py", f"JOURNEYS = {journeys!r}\n")
        cards = {topic: {"topicId": topic, "definition": "Count whole objects.",
                        "worked_example": {"prompt": "1 + 1", "answer": "2"}}
                 for topic in ("first", "second")}
        for topic, card in cards.items():
            self.write_json(f"data/teach/{topic}.json", card)
        self.commit("content review baseline")
        base = self.git("rev-parse", "HEAD")
        for topic, card in cards.items():
            card["conditions"] = ["The counted objects use the same unit."]
            self.write_json(f"data/teach/{topic}.json", card)
        report = review_changes(self.root, base)
        evidence_name = "docs/reviews/fixture-evidence.json"
        self.write_json(evidence_name, {"scope": "Synthetic packaging regression fixture only",
                                        "checks": ["Condition additions retain the stated addition answer."]})
        reference = {"path": evidence_name,
                     "sha256": review_evidence_sha256 or sha((self.root / evidence_name).read_bytes())}
        review = copy.deepcopy(report["review_template"])
        review["status"] = "reviewed"
        for item in review["items"]:
            item.update(review_status="reviewed", reviewer_kind="ai", reviewer="Synthetic fixture reviewer",
                        findings=["Checked the added whole-object condition against the example."],
                        decision="accept_with_limits", evidence_refs=[reference])
        self.write_json("docs/reviews/fixture-review.json", review)
        self.write_json("docs/reviews/current.json", {
            "base_commit": base, "review_record": "docs/reviews/fixture-review.json"})
        self.verified_fixture(journeys=journeys, observations={"changed_content_preview": {
            "content_fingerprint": report["content_fingerprint"],
            "previews": [{"path": row["path"]} for row in report["preview_tasks"]],
        }})
        return report

    def rewrite_preview_result(self, result):
        """Keep nominal PASS and raw-byte binding valid while changing its claims."""
        name = "build/browser-check/changed_content_preview/result.json"
        self.write_json(name, result)
        report_name = "build/browser-check/report.json"
        browser = json.loads((self.root / report_name).read_bytes())
        row = next(row for row in browser["checks"] if row["journey"] == "changed_content_preview")
        row["artifact_sha256"]["result.json"] = sha((self.root / name).read_bytes())
        row["observations"] = result["observations"]
        self.write_json(report_name, browser)

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
        self.assertTrue(result["bundle"]["restore_verified"])
        self.assertTrue(result["bundle"]["fsck_verified"])
        self.assertEqual(result["bundle"]["restored_head"], result["head"])
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

    def test_changed_preview_uses_actual_scope_once_per_evidence_collection(self):
        report = self.reviewed_preview_fixture()
        self.assertEqual({row["path"] for row in report["preview_tasks"]},
                         {"/?teach=first", "/?teach=second"})
        # This generated file is not an authority for release coverage.
        self.write_json("build/content-review.json", {
            "base_commit": report["base_commit"], "content_fingerprint": "0" * 64,
            "preview_tasks": [{"path": "/?teach=invented"}],
        })
        with patch("tools.package_release.review_changes", wraps=review_changes) as recompute:
            selected, validation = collect_evidence(self.root, identity(self.root),
                "build/regression_report.json", "build/browser-check/report.json")
        recompute.assert_called_once_with(self.root, report["base_commit"])
        self.assertIn("browser/changed_content_preview/result.json", selected)
        self.assertEqual(validation["browser"]["changed_content_preview"], {
            "base_commit": report["base_commit"], "content_fingerprint": report["content_fingerprint"],
            "preview_count": 2,
        })

    def test_rehashed_changed_preview_cannot_claim_a_stale_content_fingerprint(self):
        self.reviewed_preview_fixture()
        name = "build/browser-check/changed_content_preview/result.json"
        result = json.loads((self.root / name).read_bytes())
        result["observations"]["content_fingerprint"] = "0" * 64
        self.rewrite_preview_result(result)
        with self.assertRaisesRegex(PackageError, "stale content fingerprint"):
            package_release(self.root)

    def test_rehashed_changed_preview_cannot_omit_a_page_by_shrinking_build_inventory(self):
        report = self.reviewed_preview_fixture()
        name = "build/browser-check/changed_content_preview/result.json"
        result = json.loads((self.root / name).read_bytes())
        result["observations"]["previews"].pop()
        self.rewrite_preview_result(result)
        report["preview_tasks"].pop()
        self.write_json("build/content-review.json", report)
        with self.assertRaisesRegex(PackageError, "preview paths are missing"):
            package_release(self.root)

    def test_changed_preview_requires_exact_unique_page_paths(self):
        self.reviewed_preview_fixture()
        name = "build/browser-check/changed_content_preview/result.json"
        result = json.loads((self.root / name).read_bytes())
        valid = copy.deepcopy(result["observations"]["previews"])
        for extra in (valid[0], {"path": "/?teach=outside-current-impact"}, {"path": None}):
            with self.subTest(extra=extra):
                result["observations"]["previews"] = valid + [extra]
                self.rewrite_preview_result(result)
                with self.assertRaisesRegex(PackageError, "preview paths"):
                    package_release(self.root)

    def test_changed_preview_requires_real_semantic_review_evidence_bytes(self):
        self.reviewed_preview_fixture(review_evidence_sha256="0" * 64)
        with self.assertRaisesRegex(PackageError, "Current semantic review is invalid:.*SHA256"):
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

    def test_shallow_source_is_rejected_before_a_complete_history_claim(self):
        self.commit("parent fixture")
        self.verified_fixture()
        shallow = Path(self.temporary.name) / "shallow"
        self.git("clone", "--quiet", "--depth", "1", self.root.as_uri(), str(shallow))
        with self.assertRaisesRegex(PackageError, "shallow repository"):
            package_release(shallow)

    def test_complete_bundle_header_with_missing_parent_fails_actual_restore(self):
        self.commit("parent fixture")
        parent = self.git("rev-parse", "HEAD")
        self.verified_fixture()
        head = self.git("rev-parse", "HEAD")
        packed = subprocess.run(["git", "-C", str(self.root), "pack-objects", "--stdout", "--revs"],
                                input=f"{head}\n^{parent}\n".encode(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=True).stdout
        broken = Path(self.temporary.name) / "missing-parent.bundle"
        broken.write_bytes(f"# v2 git bundle\n{head} refs/heads/main\n\n".encode() + packed)
        # Reproduce the actual bug: the pack is well-formed and its bundle
        # header claims complete history, but an ancestor commit was omitted.
        self.assertEqual(self.git("bundle", "list-heads", str(broken)), f"{head} refs/heads/main")
        with self.assertRaisesRegex(PackageError, "Bundle recovery verification failed"):
            verify_bundle_restore(self.root, broken, {"head": head, "branch": "refs/heads/main"})

    def test_known_git_lock_is_removed_only_after_restore_and_other_files_are_rejected(self):
        self.verified_fixture()
        from tools.release_manifest import git as real_git

        for leftover in ("current-branch.bundle.lock", "unregistered.tmp"):
            destination = self.root / "build" / ("delivery-" + leftover)

            def create_with_leftover(root, *args):
                result = real_git(root, *args)
                if args[:2] == ("bundle", "create"):
                    (Path(args[2]).parent / leftover).write_bytes(b"unfinished temporary artifact")
                return result

            with self.subTest(leftover=leftover), patch("tools.package_release.git", side_effect=create_with_leftover):
                if leftover.endswith(".lock"):
                    result = package_release(self.root, output=destination)
                    self.assertTrue(result["bundle"]["temporary_lock_removed"])
                    self.assertTrue(result["bundle"]["restore_verified"])
                    self.assertEqual({path.name for path in destination.iterdir()},
                                     {"source.zip", "current-branch.bundle", "evidence.zip", "delivery.json"})
                else:
                    with self.assertRaisesRegex(PackageError, "Unexpected delivery staging files"):
                        package_release(self.root, output=destination)
                    self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
