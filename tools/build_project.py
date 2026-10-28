#!/usr/bin/env python3
"""One authoring-to-release command; stop at the first failed gate.

    python tools/build_project.py --release

--write rebuilds sources, registers new IDs once, compiles the catalog,
validates resources and builds the content database. --release also runs all
release regressions. --check checks the current sources and outputs without
rebuilding the database. Learning records are never build inputs or outputs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def commands(mode, *, with_browser=False):
    action = "--check" if mode == "check" else "--write"
    steps = [["tools/rebuild_data.py", action], ["tools/register_catalog.py", action],
             ["tools/classify_tiers.py", action],
             ["tools/compile_catalog.py", action]]
    if mode == "release":
        return steps + [["tools/build_db.py", "--own-only", "--quiet"],
                        ["tools/release_manifest.py", "--write"],
                        ["tools/release_check.py", *(["--with-browser"] if with_browser else [])]]
    steps += [["tools/task_admissions.py", "--check"],
              ["tools/quality_scope.py", "--check", "--require-complete", "--json", "build/quality-scope.json"],
              ["tools/check_alignment.py", "--check"],
              ["tools/validate.py"], ["tools/verify_questions.py"],
              ["tools/check_math_statements.py", "--check"],
              ["tools/check_learning_units.py"], ["tools/check_projects.py"],
              ["tools/teach_cards.py", "--check"]]
    if mode == "write":
        steps += [["tools/build_db.py", "--own-only", "--quiet"],
                  ["tools/release_manifest.py", "--write"]]
    else:
        steps += [["tools/release_manifest.py", "--check"]]
    return steps


def run_steps(mode, *, root=ROOT, runner=subprocess.run, with_browser=False):
    results = []
    for arguments in commands(mode, with_browser=with_browser):
        command = [sys.executable, *arguments]
        print("运行：python " + " ".join(arguments), flush=True)
        try:
            code = runner(command, cwd=root, check=False).returncode
        except OSError as exc:
            print(f"无法启动构建步骤：{exc}", file=sys.stderr)
            code = 1
        results.append({"command": ["python", *arguments], "returncode": code})
        if code:
            print("构建已停止，未执行后续发布步骤；修正作者源后可重新运行。", file=sys.stderr)
            return code, results
    return 0, results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--release", action="store_true")
    parser.add_argument("--with-browser", action="store_true", help="与 --release 合用，包含真实浏览器功能验收")
    args = parser.parse_args(argv)
    if args.with_browser and not args.release:
        parser.error("--with-browser 需要与 --release 合用")
    selected = "release" if args.release else "write" if args.write else "check"
    code, steps = run_steps(selected, with_browser=args.with_browser)
    output = ROOT / "build/authoring-build.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema_version": 1, "mode": selected, "ok": code == 0,
                                   "steps": steps}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
