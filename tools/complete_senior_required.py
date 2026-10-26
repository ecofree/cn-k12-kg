"""Retired writer. Historical source: git show 9f0471009f13:tools/complete_senior_required.py.

Use registered author JSON and tools/build_project.py for current releases.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.retired_writer import reject as _reject_retired_writer


def main(*args, **kwargs):
    return _reject_retired_writer(__file__)


if __name__ == "__main__":
    raise SystemExit(main())
