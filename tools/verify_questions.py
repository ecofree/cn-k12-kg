"""题库答案核验：跑 verify 表达式，输出报告。

用法：
    python tools/verify_questions.py
    python tools/verify_questions.py --strict-unverifiable   # 无 verify 的题也算失败
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.verify import check_question  # noqa: E402

DATA = ROOT / "data"
OUT = ROOT / "build" / "verification_report.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-unverifiable", action="store_true",
                        help="没有 verify 的题目也计为失败")
    args = parser.parse_args()

    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    topics = {t["id"]: t for t in json.loads((DATA / "topics.json").read_text(encoding="utf-8"))}

    results = [check_question(q) for q in questions]
    status = collections.Counter(r["status"] for r in results)
    by_type = collections.Counter(q["type"] for q in questions)
    by_difficulty = collections.Counter(q["difficulty"] for q in questions)

    bad = [r for r in results if r["status"] in ("mismatch", "error")]
    print("== 题库核验 ==")
    print(f"题目总数 {len(questions)}")
    print(f"  通过（verify 与答案一致） {status['pass']}")
    print(f"  不一致                    {status['mismatch']}")
    print(f"  无法自动核验              {status['unverifiable']}（选择题等，需人工/模型复核）")
    print(f"  表达式错误                {status['error']}")
    print(f"  题型 {dict(by_type)}")
    print(f"  难度 {dict(by_difficulty)}")

    if bad:
        print("\n== 问题清单 ==")
        for r in bad:
            topic = next((q["topicId"] for q in questions if q["id"] == r["id"]), "?")
            name = topics.get(topic, {}).get("name", "")
            print(f"  {r['id']} [{name}] {r['detail']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(questions),
        "summary": dict(status),
        "by_type": dict(by_type),
        "by_difficulty": dict(by_difficulty),
        "items": results,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {OUT.relative_to(ROOT)}")

    failed = bool(status["mismatch"] or status["error"])
    if args.strict_unverifiable and status["unverifiable"]:
        failed = True
    if failed:
        print("\nVERIFICATION FAILED")
        return 1
    print("\nVERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
