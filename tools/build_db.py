"""检查来源一致性，把 data/*.json 装进 SQLite 并生成 LF 规范化 hash 清单。

用法：
    python tools/build_db.py
    python tools/build_db.py --db build/kg.sqlite
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import store  # noqa: E402
from tools.rebuild_data import (REGISTRY, OUTPUTS, SourceError, build_data, differences,
                                load_registry, read_json, sha256_lf, write_json)  # noqa: E402

DATA_FILES = ["topics.json", "dependencies.json", "questions.json",
              "standards.json", "outline.json"]


def sha256(path: pathlib.Path) -> str:
    """Compatibility name; manifest declares that this is a LF-normalized hash."""
    return sha256_lf(path)


def write_manifest(counts: dict, *, include_external=True) -> pathlib.Path:
    files = []
    names = list(DATA_FILES)
    if include_external and (store.DATA / "questions_ext.json").is_file():
        names.append("questions_ext.json")
    for name in names:
        path = store.DATA / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, dict) and "volumes" in payload:
            count = sum(len(c["leaves"]) for v in payload["volumes"] for c in v["chapters"])
        else:
            count = 1
        files.append({"name": name, "count": count, "sha256": sha256(path)})

    registry = load_registry(store.DATA)
    sources = [{"name": f"data/{REGISTRY}", "sha256": sha256(store.DATA / REGISTRY)}]
    for source in registry["content_sources"] + registry.get("question_sources", []):
        sources.append({"name": "data/" + source["path"],
                        "sha256": sha256(store.DATA / source["path"])})
    code_inputs = {"tools/rebuild_data.py", "cnkg/engine.py", "cnkg/graph.py",
                   "cnkg/evidence.py", "cnkg/verify.py", "cnkg/learning.py",
                   "cnkg/answers.py", "cnkg/contracts.py", "cnkg/learning_store.py", "cnkg/store.py",
                   "cnkg/catalog.py", "cnkg/planner.py", "cnkg/portfolio.py", "cnkg/session_content.py",
                   "cnkg/admissions.py", "cnkg/change_review.py", "cnkg/content_repository.py",
                   "cnkg/instruction.py", "cnkg/learning_needs.py", "cnkg/manipulatives.py",
                   "cnkg/profile_recovery.py", "cnkg/quality_scope.py", "cnkg/runtime_version.py",
                   "tools/compile_catalog.py", "tools/register_catalog.py",
                   "tools/build_project.py", "tools/retired_writer.py",
                   "tools/task_admissions.py", "tools/quality_scope.py",
                   "tools/review_changes.py", "tools/check_change_review.py",
                   "cnkg/projects.py", "cnkg/challenge.py", "web/server.py",
                   "tools/curriculum_compare.py", "tools/teach_cards.py", "tools/coverage_report.py",
                   "tools/release_check.py", "web/static/index.html", "web/static/app.js",
                   "web/static/style.css", "web/static/math.js", "web/static/work.js",
                   "web/static/profile.js", "web/static/support.js", "web/static/manipulatives.js",
                   "requirements.txt", "requirements-dev.txt"}
    code_inputs.update(str(path.relative_to(ROOT)) for path in (ROOT / "schema").glob("*.schema.json"))
    for build_step in registry.get("question_generators", []) + registry.get("question_transforms", []):
        code_inputs.add(build_step["entrypoint"])
        code_inputs.update(build_step["inputs"])
    sources.extend({"name": name, "sha256": sha256(ROOT / name)}
                   for name in sorted(code_inputs))
    versions = sorted({t.get("curriculum_version", "unknown")
                       for t in read_json(store.DATA / "topics.json")})
    # 教学材料、图形与质量登记也参与发布指纹；匿名学习库不属于发布产物。
    asset_paths = set()
    for directory in ("learning_units", "projects", "teach", "curriculum_comparisons", "skills"):
        asset_paths.update((store.DATA / directory).rglob("*.json"))
    for name in ("catalog.json", "topic_tiers.json", "curriculum_versions.json", "coverage_backlog.json",
                 "challenge/alignments.json", "challenge/links.json", "authoring_migration.json",
                 "question_retirements.json", "curriculum_scope.json", "task_admissions.json"):
        path = store.DATA / name
        if path.is_file():
            asset_paths.add(path)
    asset_paths.update((ROOT / "web/static/diagrams").glob("*.svg"))
    learning_assets = [{"name": str(path.relative_to(ROOT)), "sha256": sha256(path)}
                       for path in sorted(asset_paths)]
    manifest = {
        "schema_version": store.SCHEMA_VERSION,
        "curriculum_version": versions[0] if len(versions) == 1 else "mixed",
        "curriculum_versions": versions,
        "edition": "人教版",
        "hash_policy": "sha256-lf-v1: UTF-8 file bytes with CRLF/CR normalized to LF",
        "include_external": include_external,
        "counts": counts,
        "files": files,
        "sources": sources,
        "learning_assets": learning_assets,
        "quality_scope": "来源与结构一致性；算式核验和独立学科审核是不同门禁",
    }
    # An optional fixed timestamp supports reproducible release archives.
    # Local wall-clock time is deliberately not part of artifact identity.
    if "SOURCE_DATE_EPOCH" in os.environ:
        manifest["generated_at"] = datetime.fromtimestamp(
            int(os.environ["SOURCE_DATE_EPOCH"]), timezone.utc).isoformat(timespec="seconds")
    out = store.DATA / "manifest.json"
    write_json(out, manifest)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(store.DEFAULT_DB))
    parser.add_argument("--own-only", action="store_true", help="只装入自有题，忽略可选外部题")
    parser.add_argument("--quiet", action="store_true", help="省略按章明细")
    args = parser.parse_args(argv)

    try:
        expected, _ = build_data(store.DATA)
        delta = differences(expected, {name: read_json(store.DATA / name) for name in OUTPUTS})
        if delta:
            print("来源不一致：先运行 python tools/rebuild_data.py --check 查看差异，再修正来源/产物。")
            return 1
        if (store.DATA / "catalog.json").is_file():
            from cnkg.catalog import load_catalog
            load_catalog(store.DATA)
    except (SourceError, OSError, ValueError, KeyError) as exc:
        print(f"来源门禁失败：{exc}")
        return 1
    counts = store.build(args.db, include_external=not args.own_only)
    print(f"数据库已重建：{pathlib.Path(args.db).resolve()}")
    print(f"  表行数：{counts}")

    manifest = write_manifest(counts, include_external=not args.own_only)
    print(f"  manifest：{manifest}")
    if args.quiet:
        return 0

    conn = store.connect(args.db)
    try:
        print("\n== 按章统计 ==")
        print(f"{'册':<4}{'章':<24}{'知识点':>6}{'题':>5}{'可验算':>7}{'边':>5}")
        for row in store.chapter_summary(conn):
            print(f"{row['volume']:<4}{row['chapter']:<24}{row['topics']:>6}"
                  f"{row['questions']:>5}{row['verifiable']:>7}{row['edges']:>5}")

        gaps = store.coverage_gaps(conn, min_questions=3)
        if gaps:
            print(f"\n题量不足 3 的知识点：{len(gaps)} 个")
            for row in gaps[:10]:
                print(f"  {row['id']} {row['name']}（{row['questions']} 题）")
    finally:
        conn.close()

    print("\n下一步：python tools/query.py ancestors m7a.5.2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
