"""Check exact cumulative bank-review coverage without fabricating review entries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.bank_review import check_reviews


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/bank-review-report.json")
    args = parser.parse_args(argv)
    report = check_reviews(ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    for error in report["errors"][:30]:
        print(error, file=sys.stderr)
    if len(report["errors"]) > 30:
        print(f"共{len(report['errors'])}项错误，完整内容见{args.output}", file=sys.stderr)
    return int(not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
