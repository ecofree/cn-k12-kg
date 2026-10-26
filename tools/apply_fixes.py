"""改题工具：一次修改，同时写回 data/questions.json 和所有 data/content/*.json。

为什么不用 apply_content.py：
    apply_content 是"按册提交"，同 id 默认跳过、--update 才覆盖。
    但它只写 questions.json，不改原内容源文件。
    于是改完题再重放旧册文件，改动就被**静默还原**了——这正是 D19 那类事故的温床。
    这个工具把两处一起改，任何顺序重放都不会回退。

用法：
    python tools/apply_fixes.py --fixes data/fixes/round3.json --dry-run
    python tools/apply_fixes.py --fixes data/fixes/round3.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def dump(path: pathlib.Path, rows) -> None:
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def dump_content(path: pathlib.Path, payload: dict) -> None:
    body = ",\n".join(json.dumps(q, ensure_ascii=False, separators=(",", ":"))
                      for q in payload.get("questions", []))
    path.write_text(
        '{\n"volume":' + json.dumps(payload["volume"], ensure_ascii=False)
        + ',\n"topics":' + json.dumps(payload.get("topics", {}), ensure_ascii=False, separators=(",", ":"))
        + ',\n"dependencies":' + json.dumps(payload.get("dependencies", []), ensure_ascii=False, separators=(",", ":"))
        + ',\n"questions":[\n' + body + '\n]\n}\n', encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixes", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    fixes = json.loads(pathlib.Path(args.fixes).read_text(encoding="utf-8"))
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in questions}

    missing = [qid for qid in fixes if qid not in by_id]
    if missing:
        raise SystemExit(f"题目不存在：{missing}")

    # 1) 改 questions.json
    changed_fields = collections.Counter()
    for qid, patch in fixes.items():
        for key, value in patch.items():
            if by_id[qid].get(key) != value:
                changed_fields[key] += 1
            by_id[qid][key] = value

    # 2) 同步改所有内容源文件里同一道题
    touched_files = collections.Counter()
    for path in sorted((DATA / "content").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dirty = False
        for q in payload.get("questions", []):
            qid = q.get("id")
            if qid in fixes:
                for key, value in fixes[qid].items():
                    if q.get(key) != value:
                        dirty = True
                        q[key] = value
        if dirty:
            touched_files[path.name] += 1

    print(json.dumps({
        "questions_fixed": len(fixes),
        "changed_fields": dict(changed_fields),
        "content_files_touched": dict(touched_files),
        "dry_run": args.dry_run,
    }, ensure_ascii=False, indent=2))
    if args.dry_run:
        return

    dump(DATA / "questions.json", questions)
    for path in sorted((DATA / "content").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dirty = False
        for q in payload.get("questions", []):
            if q.get("id") in fixes:
                for key, value in fixes[q["id"]].items():
                    if q.get(key) != value:
                        dirty = True
                        q[key] = value
        if dirty:
            dump_content(path, payload)
    print("已写入 questions.json 与相关内容源文件")


if __name__ == "__main__":
    main()
