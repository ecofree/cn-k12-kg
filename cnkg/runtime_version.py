"""Freeze a grader's source identity when its running module is initialized.

Python keeps imported functions in memory after their files change. Reading
those files every time a session starts would give old running code a new
version label. Grading entry points therefore capture this object eagerly,
after importing their dependencies, and keep it for the lifetime of the
process. A restarted process captures the newly imported release instead.

The hash format deliberately matches the previous grader fingerprints. This
change does not invalidate an unfinished record merely because the version
lookup moved into this module. The diagnostic ``changed_sources`` method
reports edits on disk without relabeling running code or modifying a record.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRADER_SOURCES = {
    "support": ("cnkg/instruction.py", "cnkg/answers.py", "cnkg/verify.py", "schema/answer-spec.schema.json"),
    "unit": ("cnkg/learning.py", "cnkg/verify.py", "cnkg/answers.py",
             "cnkg/contracts.py", "schema/answer-spec.schema.json"),
    "topic": ("cnkg/engine.py", "cnkg/graph.py", "cnkg/evidence.py", "cnkg/admissions.py", "cnkg/verify.py",
              "cnkg/answers.py", "cnkg/contracts.py", "schema/answer-spec.schema.json"),
}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class FrozenRuntimeVersion:
    root: Path
    source_hashes: tuple[tuple[str, str], ...]
    version: str

    @classmethod
    def capture(cls, root, sources):
        """Call during module initialization, never lazily for a first learner."""
        root = Path(root).resolve()
        names = tuple(sorted(set(sources)))
        if not names:
            raise ValueError("判分版本必须包含实际源文件")
        hashes = []
        for name in names:
            path = root / name
            if Path(name).is_absolute() or not path.resolve().is_relative_to(root):
                raise ValueError("判分源路径须位于项目目录内")
            hashes.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
        frozen = tuple(hashes)
        return cls(root, frozen, _digest(dict(frozen)))

    def changed_sources(self):
        """Diagnostic only: a disk edit never changes ``version`` in this process."""
        changed = []
        for name, expected in self.source_hashes:
            try:
                actual = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
            except OSError:
                actual = None
            if actual != expected:
                changed.append(name)
        return changed


def freeze_grader(kind, root=ROOT):
    try:
        sources = GRADER_SOURCES[kind]
    except KeyError:
        raise ValueError("未知判分运行版本") from None
    return FrozenRuntimeVersion.capture(root, sources)
