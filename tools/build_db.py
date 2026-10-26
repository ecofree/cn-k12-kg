"""把 data/*.json 装进 SQLite，并生成带 SHA-256 的 manifest。

用法：
    python tools/build_db.py
    python tools/build_db.py --db build/kg.sqlite
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg import store  # noqa: E402

DATA_FILES = ["topics.json", "dependencies.json", "questions.json",
              "standards.json", "outline.json"]


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(counts: dict) -> pathlib.Path:
    files = []
    for name in DATA_FILES:
        path = store.DATA / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, dict) and "volumes" in payload:
            count = sum(len(c["leaves"]) for v in payload["volumes"] for c in v["chapters"])
        else:
            count = 1
        files.append({"name": name, "count": count, "sha256": sha256(path)})

    manifest = {
        "schema_version": store.SCHEMA_VERSION,
        "curriculum_version": "2024",
        "edition": "人教版",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
        "files": files,
    }
    out = store.DATA / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(store.DEFAULT_DB))
    args = parser.parse_args()

    counts = store.build(args.db)
    print(f"数据库已重建：{pathlib.Path(args.db).resolve()}")
    print(f"  表行数：{counts}")

    manifest = write_manifest(counts)
    print(f"  manifest：{manifest.relative_to(ROOT)}")

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
