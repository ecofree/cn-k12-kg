#!/usr/bin/env python3
"""Compile or check the stable skill/task/material catalog.

Registration is explicit authoring: this command never allocates new skill or
task IDs, reorders placements, guesses answer contracts or restores quarantined
items. Missing registrations stop compilation with a source-qualified error.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cnkg.catalog import CatalogError, compile_catalog  # noqa: E402
from cnkg.contracts import ContractDependencyError  # noqa: E402


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as out:
        out.write(text)
        temp = Path(out.name)
    temp.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="写入校验通过的 data/catalog.json")
    mode.add_argument("--check", action="store_true", help="检查正式目录是否与当前全部来源一致")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--report", type=Path, default=ROOT / "build/catalog-report.json")
    args = parser.parse_args(argv)
    try:
        catalog = compile_catalog(args.data_dir)
        target = args.data_dir / "catalog.json"
        if args.check:
            if not target.is_file():
                raise CatalogError(["正式 catalog 缺失；请运行 --write"])
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CatalogError([f"正式 catalog 无法读取：{exc}"]) from exc
            if previous != catalog:
                raise CatalogError(["正式 catalog 已过期或被手工修改；请重新运行 --write 后审查差异"])
        else:
            _write(target, catalog)
            report = {"content_sha256": catalog["content_sha256"], **catalog["coverage"],
                      "quarantined_task_ids": sorted(tid for tid, t in catalog["tasks"].items()
                                                     if t["availability"] == "quarantined"),
                      "unmapped_bank_task_ids": sorted(tid for tid, t in catalog["tasks"].items()
                                                       if t["source_kind"] == "bank" and not t["skill_ids"]),
                      "observable_skill_ids": sorted(sid for sid, s in catalog["skills"].items()
                                                     if s["granularity"] == "observable")}
            _write(args.report, report)
        c = catalog["coverage"]
        print(f"目录{'一致' if args.check else '已编译'}：{len(catalog['skills'])} 技能，"
              f"{len(catalog['tasks'])} 任务（{c['quarantined_tasks']} 隔离），"
              f"{len(catalog['materials'])} 材料；细技能与旧复合目标分别统计。")
        print("未分类题族不构成独立证据；没有自动掌握结论。")
        return 0
    except (CatalogError, ContractDependencyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
