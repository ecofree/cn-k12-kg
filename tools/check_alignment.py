"""Require real support and classified tasks for every goal; retain deeper bank checks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.alignment import build_alignment_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "build/alignment-report.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    report = build_alignment_report(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "task_inventory": report["task_inventory"],
                      "all_goal_errors": report["all_goal_errors"], "focus_errors": report["focus_errors"]}, ensure_ascii=False, indent=2))
    return int(args.check and bool(report["focus_errors"] or report["all_goal_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
