"""Check supported numerical equalities in explanation and reference steps."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.math_statements import scan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "build/math-statements.json")
    args = parser.parse_args(argv)
    report = scan(ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"精确数字等式 {report['checked_numeric_equalities']} 条；数值不一致候选 {len(report['findings'])} 条；未解决 {len(report['errors'])} 条。")
    for error in report["errors"]:
        print(error)
    return int(args.check and not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
