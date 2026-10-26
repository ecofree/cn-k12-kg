"""改题工具：同时更新 questions.json 与来源清单登记的内容源。

历史补丁不再参与回写；app_items 生成题应修改生成器后重建，不能只改产物。
兼容无来源清单的旧临时数据目录，供迁移及隔离测试使用。

用法：
    python tools/apply_fixes.py --fixes data/fixes/round3.json --dry-run
    python tools/apply_fixes.py --fixes data/fixes/round3.json
"""
from __future__ import annotations

# Historical CLI is fenced before imports or authoring side effects.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))


import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))


def _fixture_target(path: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(path)
    if path.resolve().is_relative_to((ROOT / "data").resolve()):
        raise ValueError("历史兼容工具不能写入正式data目录；仅允许隔离的测试fixture")
    return path


def dump(path: pathlib.Path, rows) -> None:
    path = _fixture_target(path)
    lines = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
    path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def dump_content(path: pathlib.Path, payload: dict) -> None:
    path = _fixture_target(path)
    body = ",\n".join(json.dumps(q, ensure_ascii=False, separators=(",", ":"))
                      for q in payload.get("questions", []))
    path.write_text(
        '{\n"volume":' + json.dumps(payload["volume"], ensure_ascii=False)
        + ',\n"topics":' + json.dumps(payload.get("topics", {}), ensure_ascii=False, separators=(",", ":"))
        + ',\n"dependencies":' + json.dumps(payload.get("dependencies", []), ensure_ascii=False, separators=(",", ":"))
        + ',\n"questions":[\n' + body + '\n]\n}\n', encoding="utf-8")


def active_content_paths() -> list[pathlib.Path]:
    if (DATA / "content_sources.json").is_file():
        from tools.rebuild_data import load_registry
        registry = load_registry(DATA)
        return [DATA / source["path"] for source in registry["content_sources"]]
    return sorted((DATA / "content").glob("*.json"))


def main(argv=None) -> None:
    if DATA.resolve() == (ROOT / "data").resolve():
        from tools.retired_writer import reject
        return reject(__file__)
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixes", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    fixes = json.loads(pathlib.Path(args.fixes).read_text(encoding="utf-8"))
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    by_id = {q["id"]: q for q in questions}

    missing = [qid for qid in fixes if qid not in by_id]
    if missing:
        raise SystemExit(f"题目不存在：{missing}")
    generated = [qid for qid in fixes
                 if by_id[qid].get("source") == "self-generated-application"]
    if generated:
        raise SystemExit(f"先修改 tools/app_items 并重建生成题：{generated}")
    if any("id" in patch and patch["id"] != qid for qid, patch in fixes.items()):
        raise SystemExit("修订不能更改题目 id；新增/迁移题目请编辑登记的内容源")
    paths = active_content_paths()

    # 1) 改 questions.json
    changed_fields = collections.Counter()
    for qid, patch in fixes.items():
        for key, value in patch.items():
            if by_id[qid].get(key) != value:
                changed_fields[key] += 1
            by_id[qid][key] = value

    # 2) 只更新登记的生效内容源，不回放/修改历史补丁
    touched_files = collections.Counter()
    for path in paths:
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
    for path in paths:
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
