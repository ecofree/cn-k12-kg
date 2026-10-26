"""一次运行当前项目内容与工程验收，失败时保留完整日志并返回非零。

不需要外部题库、教师审核或真实学生数据。后两项按当前交付范围暂缓，
状态仍真实保留；程序通过不能被解读为教育效果已经验证。
"""
from __future__ import annotations

import json
import importlib.util
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verification_context import capture, changes, raw_digest

CHECKS = [
    ('manifest', '全部发布文件原始字节与清单一致', ['tools/release_manifest.py', '--check']),
    ('sources', '来源与全部构建字段一致', ['tools/rebuild_data.py', '--check', '--report', 'build/source-differences.json']),
    ('registry', '稳定技能与任务身份登记完整', ['tools/register_catalog.py', '--check']),
    ('catalog', '统一资源目录与作者来源一致', ['tools/compile_catalog.py', '--check']),
    ('structure', '图、目录、题库与答案结构', ['tools/validate.py']),
    ('teaching', '完整讲解与逐目标材料', ['tools/teach_cards.py', '--check']),
    ('units', '分阶段学习单元与任务', ['tools/check_learning_units.py']),
    ('projects', '综合实践与目录关联', ['tools/check_projects.py']),
    ('curriculum', '课标对照记录与来源绑定', ['tools/curriculum_compare.py']),
    ('coverage', '既定内容范围的结构覆盖', ['tools/coverage_report.py', '--check', '--require-complete', '--output', 'build/coverage-report.json']),
    ('arithmetic', '全部数值题答案复算', ['tools/verify_questions.py']),
    ('tests', '核心回归测试', ['-m', 'unittest', 'discover', '-s', 'tests']),
    ('status', '可重复计算的项目状态', ['tools/status_report.py', '--json']),
    ('build', '自有内容库与发布指纹', ['tools/build_db.py', '--own-only', '--quiet']),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-browser', action='store_true', help='同时执行八条浏览器学习流程；需要先安装Playwright与Chromium')
    args = parser.parse_args(argv)
    for name in ('regression_report.json', 'regression_report.md'):
        (ROOT / 'build' / name).unlink(missing_ok=True)
    if importlib.util.find_spec('jsonschema') is None:
        print('缺少数据契约验收依赖。请先运行：python -m pip install -r requirements-dev.txt')
        return 2
    output = ROOT / 'build' / 'release-check'
    output.mkdir(parents=True, exist_ok=True)
    context = capture(ROOT)
    generated_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    results = []
    checks = CHECKS + ([('browser', '八条真实界面的合成功能流程', ['tools/browser_check.py'])] if args.with_browser else [])
    for key, title, check_args in checks:
        command = [sys.executable, *check_args]
        started = time.monotonic()
        log = output / f'{key}.log'
        print(f'[{key}] {title}', flush=True)
        with log.open('w', encoding='utf-8') as stream:
            try:
                result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                        text=True, timeout=1200 if key == 'browser' else 300, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                stream.write('\n验收命令超过规定时限，未当作通过。\n')
                code = 124
        results.append({'id': key, 'title': title, 'command': ['python', *check_args], 'returncode': code,
                        'ok': code == 0, 'elapsed_seconds': round(time.monotonic() - started, 2),
                        'log': log.relative_to(ROOT).as_posix(), 'log_sha256': raw_digest(log)})
        print(f'  {"PASS" if code == 0 else "FAIL"} ({results[-1]["elapsed_seconds"]} 秒)', flush=True)
        if code:
            print(log.read_text(encoding='utf-8')[-2400:], flush=True)
    final_context = capture(ROOT)
    context_errors = changes(context, final_context)
    ok = all(row['ok'] for row in results) and not context_errors
    report = {'schema': 2, 'ok': ok, 'checks': results, **context,
              'generated_at': generated_at, 'context_errors': context_errors,
              'manifest_valid': context['manifest_valid'] and final_context['manifest_valid'],
              'source_clean': context['source_clean'] and final_context['source_clean'],
              'scope': '当前登记的内容与工程验收；外部可选题库可能未安装。',
              'deferred_by_user': ['independent_teacher_review', 'real_student_effect_validation']}
    (ROOT / 'build/regression_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 项目验收报告', '', f'结果：{"通过" if ok else "未通过"}。', '',
             f'提交：`{context["head"] or "无Git提交"}`；发布清单SHA-256：`{context["release_manifest_sha256"]}`。', '',
             '| 检查 | 结果 | 日志 |', '| --- | --- | --- |']
    lines.extend(f'| {row["title"]} | {"PASS" if row["ok"] else "FAIL"} | `{row["log"]}` |' for row in results)
    if context_errors:
        lines += ['', *[f'- {error}' for error in context_errors]]
    lines += ['', '教师独立审核、真实学生教学效果验证按用户要求暂缓。通过上述检查不等于这两项已完成。', '']
    (ROOT / 'build/regression_report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'报告：build/regression_report.md；结果：{"PASS" if ok else "FAIL"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
