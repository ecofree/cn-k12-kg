"""Run isolated synthetic browser journeys, never a student-effect evaluation.

Install with npm ci && npx playwright install chromium. Use the Python virtual
environment that runs the application. Optional runtime overrides:
CNKG_NODE, CNKG_PLAYWRIGHT_MODULE, CNKG_CHROMIUM_EXECUTABLE, FONTCONFIG_FILE.
Every journey receives a new temporary SQLite file and browser context; the
application's real content and compiled catalog remain unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verification_context import capture, changes, raw_digest
SCRIPT = ROOT / "tests/browser/learning-flow.cjs"
JOURNEYS = ("self_check_autosave", "rubric_revision", "unit_phases",
            "project_history", "mobile_stage", "pool_exhaustion",
            "navigation_recovery", "keyboard_accessibility", "learning_needs_support",
            "profile_backup_recovery", "scope_and_manipulatives", "goal_alignment", "equation_steps", "support_branches", "changed_content_preview")
ARTIFACT_NAMES = ("result.json", "browser.log", "http.log", "final.png",
                  "desktop-unit.png", "desktop-home.png", "desktop-project.png", "primary-entry.png", "profile-health.png", "counting-prediction.png",
                  "steps-desktop.png", "steps-mobile.png")
INPUTS = ("web/server.py", "web/static/index.html", "web/static/app.js", "web/static/work.js",
          "web/static/profile.js", "web/static/support.js", "web/static/manipulatives.js",
          "web/static/style.css", "cnkg/portfolio.py", "cnkg/planner.py", "cnkg/learning_store.py",
          "cnkg/session_content.py", "cnkg/instruction.py", "cnkg/profile_recovery.py", "cnkg/learning_needs.py",
          "cnkg/content_repository.py", "cnkg/quality_scope.py", "cnkg/manipulatives.py", "cnkg/runtime_version.py",
          "cnkg/alignment.py", "data/alignment_targets.json", "data/curriculum_scope.json", "data/catalog.json", "package-lock.json",
          "cnkg/step_practice.py", "web/static/steps.js", "tests/browser/step-practice.cjs", "tests/browser/support-branches.cjs",
          "tools/browser_check.py", "tests/browser/learning-flow.cjs")


def fingerprints():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in INPUTS}


def require_current_preview(root=ROOT):
    """A hand-edited or stale preview checklist cannot shrink the current impact."""
    from cnkg.change_review import review_changes
    saved = json.loads((root / "build/content-review.json").read_text(encoding="utf-8"))
    current = review_changes(root, saved["base_commit"])
    expected = [row["path"] for row in current["preview_tasks"]]
    actual = [row["path"] for row in saved.get("preview_tasks", [])]
    if current.get("source_changed_during_review") or saved.get("content_fingerprint") != current["content_fingerprint"]:
        raise ValueError("内容预览清单已过期：请在当前固定版本重新生成并审查变更清单")
    if not expected or len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("内容预览清单缺页、重复或包含当前影响范围以外的页面")
    return current["content_fingerprint"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journey", choices=JOURNEYS, action="append", help="Run only these journeys; repeat as needed")
    parser.add_argument("--output", type=Path, default=ROOT / "build/browser-check")
    parser.add_argument("--timeout", type=int, default=180, help="Per-journey timeout in seconds")
    args = parser.parse_args(argv)
    if not 10 <= args.timeout <= 600:
        parser.error("--timeout must be between 10 and 600 seconds")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "report.md"):
        (output / name).unlink(missing_ok=True)
    node = os.environ.get("CNKG_NODE") or shutil.which("node")
    if not node:
        print("Node.js 20+ is required. Install it, then run npm ci and npx playwright install chromium.", file=sys.stderr)
        return 2
    source_context = capture(ROOT)
    report = {"schema": 2, "evidence_type": "synthetic_browser_test", "ok": False, **source_context,
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "scope": "Local synthetic browser journeys using author-provided answers; not real student outcomes.",
              "isolated_database_per_journey": True, "isolated_browser_context_per_journey": True,
              "checks": [], "input_sha256": fingerprints()}
    try:
        if "changed_content_preview" in (args.journey or JOURNEYS):
            report["preview_content_fingerprint"] = require_current_preview()
        # Import only after CLI parsing, so help never loads the full content graph.
        sys.path.insert(0, str(ROOT))
        from web import server
        from cnkg.learning_store import LearningStore
        report["catalog_version"] = server.get_catalog().version  # Reject stale compiled content.
        for journey in args.journey or JOURNEYS:
            destination = output / journey
            destination.mkdir(parents=True, exist_ok=True)
            result_path = destination / "result.json"
            for name in ARTIFACT_NAMES:
                # A fresh report must never rebind an old screenshot or result.
                (destination / name).unlink(missing_ok=True)
            started = time.monotonic()
            print(f"[browser] {journey}", flush=True)
            with tempfile.TemporaryDirectory(prefix="cnkg-browser-") as temporary, \
                    (destination / "http.log").open("w", encoding="utf-8") as http_log, \
                    (destination / "browser.log").open("w", encoding="utf-8") as browser_log:
                server._STORE = LearningStore(Path(temporary) / "learning.sqlite")
                server._SESSION_COUNTER = 0
                log_lock = threading.Lock()

                class Handler(server.Handler):
                    def log_message(self, fmt, *values):
                        with log_lock:
                            http_log.write((fmt % values) + "\n")
                            http_log.flush()

                class IsolatedHTTPServer(server.ThreadingHTTPServer):
                    # Complete pending handlers before clearing their database.
                    daemon_threads = False

                httpd = IsolatedHTTPServer(("127.0.0.1", 0), Handler)
                httpd.learning_store = server._STORE
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                command = [str(node), str(SCRIPT), "--journey", journey,
                           "--base-url", f"http://127.0.0.1:{httpd.server_port}",
                           "--output", str(destination)]
                recovery_server = recovery_thread = None
                if journey == "profile_backup_recovery":
                    recovery_server = IsolatedHTTPServer(("127.0.0.1", 0), Handler)
                    recovery_server.learning_store = LearningStore(Path(temporary) / "fresh-installation.sqlite")
                    recovery_thread = threading.Thread(target=recovery_server.serve_forever, daemon=True)
                    recovery_thread.start()
                    command += ["--restore-base-url", f"http://127.0.0.1:{recovery_server.server_port}"]
                code = 1
                try:
                    process = subprocess.Popen(command, cwd=ROOT, env=os.environ.copy(),
                        stdout=browser_log, stderr=subprocess.STDOUT, start_new_session=os.name == "posix")
                    code = process.wait(timeout=max(args.timeout, 900) if journey == "changed_content_preview" else args.timeout)
                except subprocess.TimeoutExpired:
                    browser_log.write(f"\nJourney exceeded {args.timeout} seconds; not accepted as PASS.\n")
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait()
                    code = 124
                finally:
                    if recovery_server:
                        recovery_server.shutdown()
                        recovery_server.server_close()
                        recovery_thread.join(timeout=5)
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=5)
                    server._STORE = None
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {"journey": journey, "ok": False, "error": "Browser process did not write a valid result; inspect browser.log."}
            result.update(returncode=code, elapsed_seconds=round(time.monotonic() - started, 2),
                          ok=bool(result.get("ok")) and code == 0,
                          artifacts=str(destination.relative_to(output)))
            result["artifact_sha256"] = {name: raw_digest(destination / name) for name in ARTIFACT_NAMES
                                         if (destination / name).is_file()}
            report["checks"].append(result)
            print(f"  {'PASS' if result['ok'] else 'FAIL'} ({result['elapsed_seconds']}s)", flush=True)
            if not result["ok"]:
                print((destination / "browser.log").read_text(encoding="utf-8")[-3500:], flush=True)
        after = fingerprints()
        report["changed_inputs"] = [name for name, digest in report["input_sha256"].items() if after[name] != digest]
        final_context = capture(ROOT)
        report["context_errors"] = changes(source_context, final_context)
        report["manifest_valid"] = source_context["manifest_valid"] and final_context["manifest_valid"]
        report["source_clean"] = source_context["source_clean"] and final_context["source_clean"]
        report["ok"] = bool(report["checks"]) and not report["changed_inputs"] and not report["context_errors"] and all(row["ok"] for row in report["checks"])
        if report["changed_inputs"]:
            report["error"] = "Application inputs changed during the run; rerun against one fixed content/code version."
        elif report["context_errors"]:
            report["error"] = "; ".join(report["context_errors"])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr)
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# Synthetic browser journey report", "", "Evidence type: `synthetic_browser_test`.", "",
                 f"Commit: `{report['head']}`; release manifest SHA-256: `{report['release_manifest_sha256']}`.", "",
                 "These checks exercise the application with author-provided answers. They do not measure real student learning.", "",
                 "| Journey | Result | Time | Artifacts |", "| --- | --- | --- | --- |"]
        lines += [f"| {row['journey']} | {'PASS' if row['ok'] else 'FAIL'} | {row['elapsed_seconds']}s | {row['artifacts']}/ |"
                  for row in report["checks"]]
        if report.get("error"):
            lines += ["", report["error"]]
        (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Report: {output / 'report.json'}", flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
