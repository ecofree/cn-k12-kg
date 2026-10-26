"""检查原创综合实践资源；结构通过不替代人工评价。"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.projects import load_projects, ProjectError, project_content_sha256


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    try:
        projects = load_projects(args.data_dir)
        errors = [] if projects else ['没有综合实践资源，空目录不能视作验收通过']
    except ProjectError as exc:
        projects, errors = {}, exc.errors
    report = {'ok': not errors, 'errors': errors, 'projects': len(projects),
              'objectives': sum(len(p['objectives']) for p in projects.values()),
              'teacher_review_pending': sum(p['teacher_review']['status'] == 'pending' for p in projects.values()),
              'versions': {pid: project_content_sha256(p) for pid, p in projects.items()},
              'scope': '目录引用、目标与量规结构；教师审核和真实作品评价仍待完成'}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif errors:
        print('实践项目检查失败：\n' + '\n'.join(errors))
    else:
        print(f"PASS：{report['projects']} 个实践项目，{report['objectives']} 个目标；教师审核待完成 {report['teacher_review_pending']}。")
        print(report['scope'])
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
