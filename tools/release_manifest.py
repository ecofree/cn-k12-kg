"""Inventory distributable repository files using SHA256 of their original bytes.

This is the delivery manifest, separate from data/manifest.json's content build
fingerprints. It includes tracked and non-ignored new source files, does not walk
runtime directories, and never opens a learning database. A release is portable:
version 1 rejects symlinks rather than changing their meaning during archiving.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release-manifest.json"
HASH_POLICY = "sha256-raw-bytes-v1"
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", ".idea",
})
EXCLUDED_ROOTS = ("var/", "build/", "dist/", "release/", "releases/",
                  "inbox/candidates/", "inbox/dl/")
CONTROLLED_PREFIXES = ("web/static/vendor/", "data/external/", "data/challenge/")
CONTROLLED_FILES = ("data/questions_ext.json", "web/static/ext-data.json")
OWN_CHALLENGE_METADATA = frozenset({"data/challenge/alignments.json", "data/challenge/links.json"})
DATABASE_NAME = re.compile(r"\.(?:db|sqlite3?)(?:[.-].*)?$", re.IGNORECASE)
HEX = re.compile(r"[0-9a-f]{64}\Z")


class ManifestError(ValueError):
    """A source path or publication declaration is not safe to distribute."""


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise ManifestError(result.stderr.decode("utf-8", "replace").strip()
                            or f"git {' '.join(args)} failed")
    return result.stdout


def relative_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 for c in name):
        raise ManifestError(f"Invalid publication path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ManifestError(f"Invalid publication path: {name!r}")
    return name


def static_exclusion(name: str) -> str | None:
    """Classify paths before reading any bytes, including historical Git paths."""
    relative_name(name)
    parts = PurePosixPath(name).parts
    if any(part in EXCLUDED_DIRECTORIES for part in parts):
        return "dependency/cache/runtime directory"
    if any(name == prefix[:-1] or name.startswith(prefix) for prefix in EXCLUDED_ROOTS):
        return "private or generated output directory"
    if DATABASE_NAME.search(parts[-1]):
        return "learning/database file"
    if parts[-1] == ".DS_Store" or parts[-1].lower().endswith((".pyc", ".pyo", ".zip", ".bundle")):
        return "cache or nested delivery archive"
    if name.startswith("data/questions_ext.json.bak-"):
        return "external bank backup"
    return None


def safe_file(root: Path, name: str) -> Path:
    """Reject symlinks, including ancestor links, before resolving/opening a file."""
    relative_name(name)
    current = root.resolve()
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError(f"Publication symlinks are not supported: {name}")
    if not current.is_file():
        raise ManifestError(f"Publication file is missing or not a regular file: {name}")
    return current


def _matches(name: str, declared: str) -> bool:
    return (name == declared[:-1] or name.startswith(declared)) if declared.endswith("/") else name == declared


def publication_rules(root: Path) -> tuple[list[str], list[str]]:
    registry = safe_file(root, "data/publication_assets.json")
    try:
        value = json.loads(registry.read_bytes())
    except (ValueError, OSError) as exc:
        raise ManifestError(f"Invalid publication asset registry: {exc}") from exc
    if value.get("schema_version") != 1 or not isinstance(value.get("assets"), list):
        raise ManifestError("Publication registry requires schema_version=1 and assets[]")
    allowed, excluded = [], []
    for asset in value["assets"]:
        if not isinstance(asset, dict) or type(asset.get("distributed")) is not bool:
            raise ManifestError("Each publication asset must declare distributed as a boolean")
        paths = asset.get("paths", [])
        if not isinstance(paths, list):
            raise ManifestError("Publication asset paths must be a list")
        if asset["distributed"] and paths:
            license_name = str(asset.get("license", "")).lower()
            if not license_name or any(word in license_name for word in ("unknown", "unresolved", "unlicensed")):
                raise ManifestError(f"Distributed asset has no established license: {asset.get('id')}")
        for path in paths:
            if not isinstance(path, str):
                raise ManifestError("Publication asset paths must be strings")
            relative_name(path[:-1] if path.endswith("/") else path)
            (allowed if asset["distributed"] else excluded).append(path)
    return sorted(set(allowed)), sorted(set(excluded))


def exclusion(name: str, allowed: list[str], excluded: list[str]) -> str | None:
    reason = static_exclusion(name)
    if reason:
        return reason
    if any(_matches(name, path) for path in excluded):
        return "asset explicitly excluded from distribution"
    controlled = any(name.startswith(prefix) for prefix in CONTROLLED_PREFIXES) or name in CONTROLLED_FILES
    if controlled and not any(_matches(name, path) for path in allowed):
        # Local challenge alignment/link metadata are our own catalog data.
        if name in OWN_CHALLENGE_METADATA:
            return None
        return "unregistered external or vendor asset"
    return None


def is_repository_root(root: Path = ROOT) -> bool:
    """An extracted source directory must not inherit its parent repository."""
    root = Path(root).resolve()
    try:
        top = git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    except (ManifestError, OSError, UnicodeDecodeError):
        return False
    return bool(top) and Path(top).resolve() == root


def _portable_paths(root: Path, allowed: list[str], excluded: list[str]) -> set[str]:
    """Enumerate portable source independently of old manifest entries."""
    found = set()

    def visit(directory: Path):
        with os.scandir(directory) as entries:
            for entry in entries:
                name = Path(entry.path).relative_to(root).as_posix()
                relative_name(name)
                if static_exclusion(name) or any(_matches(name, declared) for declared in excluded):
                    continue  # Do not walk runtime/private directories or open their contents.
                controlled = any(_matches(name, prefix) for prefix in CONTROLLED_PREFIXES)
                if controlled and not any(_matches(name, declared) or declared.startswith(name + "/")
                                          for declared in [*allowed, *OWN_CHALLENGE_METADATA]):
                    continue
                if entry.is_symlink():
                    raise ManifestError(f"Publication symlinks are not supported: {name}")
                if entry.is_dir(follow_symlinks=False):
                    visit(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    found.add(name)
                else:
                    raise ManifestError(f"Publication file is not a regular file: {name}")

    visit(root)
    return found


def source_paths(root: Path = ROOT, manifest_path: str = MANIFEST) -> list[str]:
    root = Path(root).resolve()
    relative_name(manifest_path)
    allowed, excluded = publication_rules(root)
    if is_repository_root(root):
        raw = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        try:
            names = {name.decode("utf-8") for name in raw.split(b"\0") if name}
        except UnicodeDecodeError as exc:
            raise ManifestError("Publication filenames must be valid UTF-8") from exc
    else:
        names = _portable_paths(root, allowed, excluded)
    selected = []
    for name in sorted(names):
        if name == manifest_path or exclusion(name, allowed, excluded):
            continue
        safe_file(root, name)
        selected.append(name)
    return selected


def compute_manifest(root: Path = ROOT, manifest_path: str = MANIFEST) -> dict:
    root = Path(root).resolve()
    files = []
    for name in source_paths(root, manifest_path):
        raw = safe_file(root, name).read_bytes()
        files.append({"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return {
        "schema_version": 1, "hash_policy": HASH_POLICY,
        "selection": "repository: tracked and non-ignored new files; portable source: recursive published files; sorted by path",
        "symlink_policy": "reject",
        "excluded": {
            "directories": sorted(EXCLUDED_DIRECTORIES), "root_prefixes": list(EXCLUDED_ROOTS),
            "file_patterns": ["*.db*", "*.sqlite*", "*.pyc", "*.pyo", "*.zip", "*.bundle", ".DS_Store"],
            "publication_rules": "data/publication_assets.json; only registered distributed vendor/external assets",
            "self": manifest_path,
        },
        "file_count": len(files), "total_bytes": sum(row["size"] for row in files), "files": files,
    }


def write_manifest(root: Path = ROOT, manifest_path: str = MANIFEST) -> dict:
    root = Path(root).resolve()
    relative_name(manifest_path)
    target = root / manifest_path
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != root):
        raise ManifestError("Manifest output may not be a symlink")
    result = compute_manifest(root, manifest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return result


def check_manifest(root: Path = ROOT, manifest_path: str = MANIFEST) -> list[str]:
    root = Path(root).resolve()
    try:
        saved = json.loads(safe_file(root, manifest_path).read_bytes())
        current = compute_manifest(root, manifest_path)
        if not isinstance(saved, dict) or not isinstance(saved.get("files"), list):
            return ["Release manifest must contain a files array"]
        problems = []
        names = [row.get("path") for row in saved["files"] if isinstance(row, dict)]
        if len(names) != len(saved["files"]) or any(not isinstance(name, str) for name in names):
            return ["Malformed file entry in release manifest"]
        if len(set(names)) != len(names):
            problems.append("Duplicate path in release manifest")
        old = {row["path"]: row for row in saved["files"]}
        new = {row["path"]: row for row in current["files"]}
        problems += [f"Missing from release manifest: {name}" for name in sorted(new.keys() - old.keys())]
        problems += [f"No longer distributable or present: {name}" for name in sorted(old.keys() - new.keys())]
        problems += [f"Raw bytes changed: {name}" for name in sorted(old.keys() & new.keys()) if old[name] != new[name]]
        if saved != current and not problems:
            problems.append("Release manifest policy, totals, or ordering differs from the current tool")
        return problems
    except (ManifestError, OSError, ValueError, TypeError) as exc:
        return [str(exc)]


def manifest_digest(root: Path = ROOT, manifest_path: str = MANIFEST) -> str:
    return hashlib.sha256(safe_file(Path(root).resolve(), manifest_path).read_bytes()).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", default=MANIFEST)
    args = parser.parse_args(argv)
    try:
        if args.write:
            result = write_manifest(args.root, args.manifest)
            print(f"Wrote {args.manifest}: {result['file_count']} files, {result['total_bytes']} raw bytes")
            return 0
        problems = check_manifest(args.root, args.manifest)
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print(f"PASS: {args.manifest} covers all distributable source bytes")
        return 1 if problems else 0
    except (ManifestError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
