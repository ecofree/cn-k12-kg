"""Generate a fingerprint-bound change review and real-page preview checklist."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.change_review import review_changes, validate_review_binding


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="真实Git基线提交")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("build/content-review.json"))
    parser.add_argument("--template", type=Path, help="另存待填写的逐变更审查模板")
    parser.add_argument("--preview-base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--check-review", type=Path, help="核对已填写记录的内容绑定与逐项证据是否齐备，不自动判定语义为真")
    args = parser.parse_args()
    try:
        report = review_changes(args.root, args.base, preview_base_url=args.preview_base_url)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    output = args.output if args.output.is_absolute() else args.root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.template:
        template = args.template if args.template.is_absolute() else args.root / args.template
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(json.dumps(report["review_template"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    errors = []
    if args.check_review:
        path = args.check_review if args.check_review.is_absolute() else args.root / args.check_review
        errors = validate_review_binding(report, json.loads(path.read_text(encoding="utf-8")), root=args.root)
    print(json.dumps({"base_commit": report["base_commit"], "content_fingerprint": report["content_fingerprint"],
                      "summary": report["summary"], "review_status": report["review_status"], "errors": errors},
                     ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
