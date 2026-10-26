"""Verify the current release's explicit semantic review without self-approval."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.change_review import review_changes, validate_review_binding


def main():
    config_path = ROOT / "docs/reviews/current.json"
    if not config_path.is_file():
        print("缺少 docs/reviews/current.json：先选择审查基线和已填写的记录")
        return 1
    config = json.loads(config_path.read_text(encoding="utf-8"))
    record_path = (ROOT / config["review_record"]).resolve()
    if not record_path.is_relative_to((ROOT / "docs/reviews").resolve()) or not record_path.is_file():
        print("审查记录须在 docs/reviews/ 内且实际存在")
        return 1
    report = review_changes(ROOT, config["base_commit"])
    problems = validate_review_binding(report, json.loads(record_path.read_text(encoding="utf-8")), root=ROOT)
    if report.get("source_changed_during_review"):
        problems.append("生成清单期间源文件改变，请在固定版本重新审查")
    output = ROOT / "build/content-review.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for error in problems:
        print(error)
    print(f"语义审查绑定：{'FAIL' if problems else 'PASS'}；{report['summary']['review_items']}项；自动程序只核对记录与版本，不替代语义判断")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
