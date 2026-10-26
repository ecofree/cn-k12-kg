"""校验教学单元及目标覆盖；不将结构通过宣称为教师审核或真实教学效果。"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.learning import LearningUnitError, load_units, unit_content_sha256  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--json", action="store_true", help="输出机器可读检查结果")
    args = parser.parse_args(argv)
    try:
        units = load_units(args.data_dir)
        errors = [] if units else ["没有教学单元；空目录不能视为覆盖验收通过"]
    except LearningUnitError as exc:
        units, errors = {}, exc.errors
    tasks = [task for unit in units.values() for task in unit["tasks"]]
    result = {
        "ok": not errors, "errors": errors, "units": len(units),
        "objectives": sum(len(unit["objectives"]) for unit in units.values()),
        "tasks": len(tasks), "phases": dict(Counter(t["phase"] for t in tasks)),
        "types": dict(Counter(t["type"] for t in tasks)),
        "teacher_review_pending": sum(u["teacher_review"]["status"] == "pending" for u in units.values()),
        "versions": {key: unit_content_sha256(unit) for key, unit in units.items()},
        "scope": "结构、引用、目标覆盖与给定算式一致；未证明教学效果或完整课程覆盖",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif errors:
        print("教学单元检查失败：")
        for error in errors:
            print(f"  - {error}")
    else:
        print(f"PASS：{result['units']} 个教学单元，{result['objectives']} 个目标，{result['tasks']} 个任务")
        print(f"阶段任务：{result['phases']}；教师审核待完成：{result['teacher_review_pending']}")
        print(result["scope"])
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
