"""Check complete goal observation plans without certifying learning outcomes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cnkg.assessments import report
from cnkg.catalog import load_catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "build/assessment-report.json")
    args = parser.parse_args(argv)
    value = report(load_catalog(), require_complete=args.require_complete)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(value["summary"], ensure_ascii=False))
    for error in value["errors"]:
        print(error, file=sys.stderr)
    print("过程任务不会隐藏客观题族不足；未取得学生表现不生成掌握证据。")
    return 0 if value["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
