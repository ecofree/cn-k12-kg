"""Bind functional evidence to one source snapshot without changing that source."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from tools.release_manifest import check_manifest, manifest_digest


def raw_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(root: Path) -> dict:
    def git(*args):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                timeout=30, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    try:
        top = git("rev-parse", "--show-toplevel")
        if top is not None and Path(top).resolve() == root.resolve():
            head = git("rev-parse", "HEAD")
            status = git("status", "--porcelain", "--untracked-files=normal")
        else:
            head, status = None, None
    except (OSError, subprocess.TimeoutExpired):
        head, status = None, None
    try:
        digest = manifest_digest(root=root)
        errors = check_manifest(root=root)
    except (OSError, ValueError, KeyError) as exc:
        digest, errors = None, [f"{type(exc).__name__}: {exc}"]
    return {"head": head, "source_clean": bool(head) and status == "",
            "release_manifest_sha256": digest, "manifest_valid": not errors,
            "manifest_errors": errors}


def changes(before: dict, after: dict) -> list[str]:
    errors = []
    for key in ("head", "release_manifest_sha256"):
        if before[key] != after[key]:
            errors.append(f"{key} changed during verification")
    if not before["manifest_valid"]:
        errors.append("The source manifest was invalid at the start of verification")
    if not after["manifest_valid"]:
        errors.append("The source manifest is no longer valid after verification")
    if before["source_clean"] and not after["source_clean"]:
        errors.append("The clean source tree changed during verification")
    return errors
