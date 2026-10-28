"""Publish an edited, registered volume through the complete source build.

Historical patches are archived. Replaying an old file, changing glob order,
or passing --update must never silently restore an old answer. Edit the active
source named in data/content_sources.json, then use this compatibility command
or tools/rebuild_data.py. Application items remain authored in app_items.
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
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))

from tools.rebuild_data import (  # noqa: E402
    OUTPUTS, SourceError, build_data, load_registry, write_json,
)


def dump(path: pathlib.Path, rows: list):
    path = pathlib.Path(path)
    if path.resolve().is_relative_to((ROOT / "data").resolve()):
        raise ValueError("历史兼容工具不能写入正式data目录；仅允许隔离的测试fixture")
    write_json(path, rows)


def main(argv=None):
    if DATA.resolve() == (ROOT / "data").resolve():
        from tools.retired_writer import reject
        return reject(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update", action="store_true",
                        help="兼容旧命令；仍须通过登记来源的完整重建，不允许重放历史补丁")
    args = parser.parse_args(argv)
    try:
        registry = load_registry(DATA)
        path = pathlib.Path(args.file).resolve()
        sources = {(DATA / source["path"]).resolve(): source
                   for source in registry["content_sources"]}
        if path not in sources:
            raise SourceError("该文件不是生效来源；历史补丁不能直接重放。"
                              "请编辑 content_sources.json 登记的册次文件。")
        rebuilt, _ = build_data(DATA)
        print(f"{sources[path]['volume']}: 全量来源构建通过；"
              f"知识点 {len(rebuilt['topics.json'])}，"
              f"先修边 {len(rebuilt['dependencies.json'])}，"
              f"题目 {len(rebuilt['questions.json'])}")
        print("该检查确认来源一致性；不表示题目已通过独立学科审阅。")
        if args.dry_run:
            return 0
        for name in OUTPUTS:
            dump(DATA / name, rebuilt[name])
        print("已从全部登记来源写入 topics.json / dependencies.json / questions.json")
        return 0
    except (SourceError, OSError, ValueError, KeyError) as exc:
        print(f"合并失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
