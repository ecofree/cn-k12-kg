"""Build/check the complete curriculum goal inventory and evidence matrix."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.quality_scope import build_scope_report, scope_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--write-manifest", action="store_true", help="审查目标与角色变更后，显式写入范围分母")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-complete", action="store_true", help="发现实际目标材料缺口时失败")
    parser.add_argument("--json", type=Path, help="输出每目标材料、任务、边界、修订路径及缺口")
    args = parser.parse_args()
    if args.write_manifest:
        (args.data / "curriculum_scope.json").write_text(
            json.dumps(scope_manifest(args.data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = build_scope_report(args.data)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "groups": report["groups"], "errors": report["errors"]},
                     ensure_ascii=False, indent=2))
    return int(bool(report["errors"]) or (args.require_complete and report["summary"]["incomplete"] > 0))


if __name__ == "__main__":
    raise SystemExit(main())
