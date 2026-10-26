"""Package one clean, verified commit into source, branch history and evidence.

Run the raw manifest writer, commit, and run both verification suites first.
The package command is read-only with respect to Git and application data. It
does not repair history or convert a failing/old report into a release PASS.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

if __package__:
    from .release_manifest import (ROOT, MANIFEST, HEX, ManifestError, check_manifest,
        exclusion, git, is_repository_root, manifest_digest, publication_rules, relative_name, safe_file)
else:
    from release_manifest import (ROOT, MANIFEST, HEX, ManifestError, check_manifest,
        exclusion, git, is_repository_root, manifest_digest, publication_rules, relative_name, safe_file)

BROWSER_REQUIRED = frozenset({"result.json", "browser.log", "http.log", "final.png"})
BROWSER_ALLOWED = BROWSER_REQUIRED | frozenset({"desktop-unit.png", "desktop-home.png",
    "desktop-project.png", "primary-entry.png"})


class PackageError(ValueError):
    """The proposed delivery cannot be tied to complete passing evidence."""


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def identity(root: Path) -> dict:
    if not is_repository_root(root):
        raise PackageError("Packaging requires the actual Git repository root; portable source can only be checked or built")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    branch = git(root, "symbolic-ref", "HEAD").decode().strip()
    if not branch.startswith("refs/heads/"):
        raise PackageError("A source delivery requires an attached current branch")
    if git(root, "status", "--porcelain=v1", "--untracked-files=all").strip():
        raise PackageError("Source tree is not clean; commit source changes before verification and packaging")
    errors = check_manifest(root)
    if errors:
        raise PackageError("Release manifest is invalid: " + "; ".join(errors))
    tracked = {name.decode("utf-8") for name in git(root, "ls-files", "-z").split(b"\0") if name}
    if MANIFEST not in tracked:
        raise PackageError("The release manifest must be tracked in the clean commit")
    return {"head": head, "branch": branch, "release_manifest_sha256": manifest_digest(root)}


def _constant(root: Path, filename: str, variable: str):
    # Read literal suite declarations, without importing a runner or starting an app.
    tree = ast.parse(safe_file(root, filename).read_bytes(), filename=filename)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except (ValueError, TypeError) as exc:
                raise PackageError(f"{filename}:{variable} must be a literal release requirement list") from exc
    raise PackageError(f"Missing suite declaration {filename}:{variable}")


def _required_checks(root: Path) -> tuple[set[str], set[str]]:
    try:
        core = [row[0] for row in _constant(root, "tools/release_check.py", "CHECKS")]
        browser = list(_constant(root, "tools/browser_check.py", "JOURNEYS"))
    except (IndexError, TypeError) as exc:
        raise PackageError("Malformed verification suite declarations") from exc
    for label, items in (("core", core), ("browser", browser)):
        if not items or any(not isinstance(item, str) or not item for item in items) or len(set(items)) != len(items):
            raise PackageError(f"Invalid or duplicated {label} verification requirements")
    return set(core), set(browser)


def _report_path(root: Path, path: str | Path) -> str:
    path = Path(path)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise PackageError("Evidence must be inside this repository's build directory") from exc
    name = relative_name(path.as_posix())
    if not name.startswith("build/") or not name.endswith(".json"):
        raise PackageError("Verification report must be a JSON file under build/")
    return name


def _read_report(root: Path, name: str, source: dict) -> tuple[dict, bytes]:
    raw = safe_file(root, name).read_bytes()
    try:
        report = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise PackageError(f"Invalid report JSON: {name}") from exc
    if not isinstance(report, dict) or report.get("schema") != 2 or report.get("ok") is not True:
        raise PackageError(f"Report is not a passing version 2 report: {name}")
    for key in ("head", "release_manifest_sha256"):
        if report.get(key) != source[key]:
            raise PackageError(f"Report {name} is bound to a different or missing {key}")
    if report.get("source_clean") is not True or report.get("manifest_valid") is not True:
        raise PackageError(f"Report was not obtained from a clean manifest-verified commit: {name}")
    if report.get("context_errors") or report.get("changed_inputs") or report.get("error"):
        raise PackageError(f"Report contains a source-change or execution error: {name}")
    try:
        when = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("timezone missing")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise PackageError(f"Report has no valid timezone-aware generated_at: {name}") from exc
    return report, raw


def _passed_rows(report: dict, key: str, required: set[str], label: str) -> list[dict]:
    rows = report.get("checks")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise PackageError(f"{label} report has no valid check list")
    names = [row.get(key) for row in rows]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise PackageError(f"{label} report contains missing or duplicated check IDs")
    if not required.issubset(names):
        raise PackageError(f"Incomplete {label} report; missing: {', '.join(sorted(required - set(names)))}")
    allowed = required | ({"browser"} if label == "core" else set())
    if set(names) - allowed:
        raise PackageError(f"Unexpected checks in {label} report: {sorted(set(names) - allowed)}")
    if any(row.get("ok") is not True or type(row.get("returncode")) is not int or row["returncode"] != 0 for row in rows):
        raise PackageError(f"{label} report contains a failed check")
    return rows


def _verified_evidence(root: Path, name: str, expected: str) -> bytes:
    if not isinstance(expected, str) or not HEX.fullmatch(expected):
        raise PackageError(f"Missing/invalid evidence hash: {name}")
    raw = safe_file(root, name).read_bytes()
    if digest(raw) != expected:
        raise PackageError(f"Evidence bytes changed since verification: {name}")
    return raw


def collect_evidence(root: Path, source: dict, core_path: str | Path,
                     browser_path: str | Path) -> tuple[dict[str, bytes], dict]:
    core_name, browser_name = (_report_path(root, path) for path in (core_path, browser_path))
    core, core_raw = _read_report(root, core_name, source)
    browser, browser_raw = _read_report(root, browser_name, source)
    if browser.get("evidence_type") != "synthetic_browser_test":
        raise PackageError("Browser evidence must explicitly declare synthetic_browser_test")
    required_core, required_browser = _required_checks(root)
    core_rows = _passed_rows(core, "id", required_core, "core")
    browser_rows = _passed_rows(browser, "journey", required_browser, "browser")
    selected = {"core/report.json": core_raw, "browser/report.json": browser_raw}
    for row in core_rows:
        name = f"build/release-check/{row['id']}.log"
        if row.get("log") != name:
            raise PackageError(f"Unexpected core log path for {row['id']}")
        selected[f"core/logs/{row['id']}.log"] = _verified_evidence(root, name, row.get("log_sha256"))
    base = Path(browser_name).parent.as_posix()
    for row in browser_rows:
        journey = row["journey"]
        if row.get("artifacts") != journey:
            raise PackageError(f"Unexpected browser artifact directory for {journey}")
        hashes = row.get("artifact_sha256")
        if not isinstance(hashes, dict) or not BROWSER_REQUIRED.issubset(hashes):
            raise PackageError(f"Missing bound browser artifacts for {journey}")
        if set(hashes) - BROWSER_ALLOWED:
            raise PackageError(f"Non-release browser evidence listed for {journey}: {sorted(set(hashes) - BROWSER_ALLOWED)}")
        for filename, expected in sorted(hashes.items()):
            name = f"{base}/{journey}/{filename}"
            raw = _verified_evidence(root, name, expected)
            if filename == "result.json":
                try:
                    result = json.loads(raw)
                except ValueError as exc:
                    raise PackageError(f"Invalid journey result: {journey}") from exc
                if not isinstance(result, dict) or result.get("journey") != journey or result.get("ok") is not True \
                        or result.get("evidence_type") != "synthetic_browser_test":
                    raise PackageError(f"Journey result is not the matching synthetic PASS: {journey}")
            if filename.endswith(".png") and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise PackageError(f"Screenshot does not contain PNG bytes: {name}")
            selected[f"browser/{journey}/{filename}"] = raw
    validation = {
        "core": {"ok": True, "check_count": len(core_rows), "report_sha256": digest(core_raw),
                 "generated_at": core["generated_at"]},
        "browser": {"ok": True, "check_count": len(browser_rows), "report_sha256": digest(browser_raw),
                    "generated_at": browser["generated_at"], "evidence_type": "synthetic_browser_test"},
        "deferred_by_user": core.get("deferred_by_user", []),
    }
    summary = ["# Verified delivery evidence", "", f"Commit: `{source['head']}`", "",
               f"Raw release manifest SHA256: `{source['release_manifest_sha256']}`", "",
               f"Core checks passed: {len(core_rows)}. Browser journeys passed: {len(browser_rows)}.", "",
               "Browser evidence is synthetic_browser_test and uses author-provided answers.",
               "This delivery does not establish independent teacher approval or real student learning effects.", ""]
    selected["verification-summary.md"] = "\n".join(summary).encode("utf-8")
    return selected, validation


def history_risks(root: Path, branch: str) -> list[str]:
    """Inspect names only. Never read an excluded historical database blob."""
    allowed, excluded = publication_rules(root)
    # -m also inspects merge-only additions; --root includes the initial tree.
    raw = git(root, "log", "--root", "--full-history", "-m", "--format=", "--name-only", "-z", branch)
    paths = {part.decode("utf-8").lstrip("\n") for part in raw.split(b"\0") if part.strip(b"\n")}
    return [f"{name}: {exclusion(name, allowed, excluded)}" for name in sorted(paths)
            if exclusion(name, allowed, excluded)]


def verify_source_archive(path: Path, manifest: dict, manifest_raw: bytes) -> None:
    expected = {row["path"]: row for row in manifest["files"]}
    expected[MANIFEST] = {"sha256": digest(manifest_raw), "size": len(manifest_raw)}
    with zipfile.ZipFile(path) as archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in files]
        if len(set(names)) != len(names) or set(names) != set(expected):
            raise PackageError("git archive did not contain exactly the manifest's published files")
        for item in files:
            raw = archive.read(item)
            row = expected[item.filename]
            if len(raw) != row["size"] or digest(raw) != row["sha256"]:
                raise PackageError(f"Git archive bytes differ from the verified worktree: {item.filename}")


def package_release(root: Path = ROOT, output: Path | None = None,
                    core_report: str | Path = "build/regression_report.json",
                    browser_report: str | Path = "build/browser-check/report.json") -> dict:
    root = Path(root).resolve()
    source = identity(root)
    manifest_raw = safe_file(root, MANIFEST).read_bytes()
    manifest = json.loads(manifest_raw)
    evidence, validation = collect_evidence(root, source, core_report, browser_report)
    risks = history_risks(root, source["branch"])
    if risks:
        raise PackageError("Current-branch history contains excluded paths; no history was changed:\n" + "\n".join(risks))
    output = Path(output) if output is not None else root / "build/release" / source["head"][:12]
    if not output.is_absolute():
        output = root / output
    # Do not overwrite an existing delivery or follow a directory link.
    if output.exists() or output.is_symlink():
        raise PackageError(f"Output already exists: {output}")
    if any(parent.is_symlink() for parent in output.parents):
        raise PackageError("Delivery output may not traverse a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".cnkg-delivery-", dir=output.parent))
    try:
        source_zip = stage / "source.zip"
        names = [row["path"] for row in manifest["files"]] + [MANIFEST]
        git(root, "--literal-pathspecs", "archive", "--format=zip", f"--output={source_zip}", "HEAD", "--", *names)
        verify_source_archive(source_zip, manifest, manifest_raw)
        bundle = stage / "current-branch.bundle"
        git(root, "bundle", "create", str(bundle), source["branch"])
        git(root, "bundle", "verify", str(bundle))
        heads = git(root, "bundle", "list-heads", str(bundle)).decode("utf-8").splitlines()
        if heads != [f"{source['head']} {source['branch']}"]:
            raise PackageError("Bundle contains references other than the verified current branch")
        evidence_index = {name: {"sha256": digest(raw), "size": len(raw)} for name, raw in sorted(evidence.items())}
        index = {"schema_version": 1, **source, "validation": validation, "files": evidence_index,
                 "exclusions": ["learning databases", "trace.zip", "failure.png", "unbound stale artifacts"]}
        evidence["evidence-index.json"] = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with zipfile.ZipFile(stage / "evidence.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(evidence.items()):
                archive.writestr(name, raw)
        # Source/report/log changes during packaging cannot inherit the old PASS.
        if identity(root) != source:
            raise PackageError("The source identity changed during packaging")
        final_evidence, final_validation = collect_evidence(root, source, core_report, browser_report)
        if validation != final_validation or any(final_evidence.get(name) != raw for name, raw in evidence.items()
                                                if name != "evidence-index.json"):
            raise PackageError("Verification evidence changed during packaging")
        artifacts = {}
        for name in ("source.zip", "current-branch.bundle", "evidence.zip"):
            raw = (stage / name).read_bytes()
            artifacts[name] = {"sha256": digest(raw), "size": len(raw)}
        delivery = {"schema_version": 1, **source,
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "hash_policy": "sha256-raw-bytes-v1", "validation": validation,
                    "source_file_count": len(names), "evidence_file_count": len(evidence),
                    "bundle": {"verified": True, "refs": [source["branch"]], "history_path_scan_passed": True},
                    "artifacts": artifacts}
        (stage / "delivery.json").write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # The destination did not exist when checked; rename keeps partial output private.
        os.rename(stage, output)
        return delivery
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--core-report", default="build/regression_report.json")
    parser.add_argument("--browser-report", default="build/browser-check/report.json")
    args = parser.parse_args(argv)
    try:
        result = package_release(args.root, args.output, args.core_report, args.browser_report)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ManifestError, PackageError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Release packaging refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
