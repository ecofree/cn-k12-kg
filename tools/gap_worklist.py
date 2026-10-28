"""Build actionable goal-level work from the admitted catalog, without optional external banks."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.alignment import build_alignment_report

REQUIRED = frozenset({'goal_specific_help', 'authored_observation_boundary', 'known_task_structure_and_representation'})
ACTIONS = {
    'goal_specific_help': '实写能辨别本目标典型误区的问题、渐进提示和修订任务；核对提示中的数学条件与参考解。',
    'authored_observation_boundary': '说明当前任务观察的具体行为，以及仍需另一情境、作品或稍后表现确认的部分。',
    'known_task_structure_and_representation': '实读任务并登记数学结构与实际呈现的表征；需要补题或补图时修改作者源，不从题号或关键词推断。',
    'multiple_objective_task_families': '审查是否需要另一种可自动观察的数学结构；证明、作图与建模保留过程任务，不能为凑客观题数量替换量规。',
}


def build_worklist(report, stage='all'):
    rows = []
    for goal in report['goals']:
        if (stage != 'all' and goal['stage'] != stage) or not goal['gaps']:
            continue
        rows.append({key: goal.get(key) for key in ('goal_id', 'statement', 'title', 'stage', 'role',
            'resource_kind', 'resource_id', 'path', 'explicit_task_ids', 'family_ids', 'representations')} | {
                'required_gaps': [gap for gap in goal['gaps'] if gap in REQUIRED],
                'additional_observation_opportunities': [gap for gap in goal['gaps'] if gap not in REQUIRED],
                'actions': [{'gap': gap, 'instruction': ACTIONS[gap]} for gap in goal['gaps']]})
    rows.sort(key=lambda row: (not bool(row['required_gaps']), row['stage'], row['resource_id'], row['goal_id']))
    return {'schema': 2, 'stage': stage, 'catalog_version': report.get('catalog_version'),
        'summary': {'goals_in_scope': sum(stage == 'all' or goal['stage'] == stage for goal in report['goals']),
                    'goals_with_required_gaps': sum(bool(row['required_gaps']) for row in rows),
                    'goals_with_observation_opportunities': sum(bool(row['additional_observation_opportunities']) for row in rows),
                    'gap_counts': dict(Counter(action['gap'] for row in rows for action in row['actions']))},
        'goals': rows, 'notice': '工单依据实际目标与当前准入任务，不按每学段题量配额宣布达标；补齐字段仍须数学语义审查和实际页面验收。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', default='all', choices=('all', 'primary', 'junior', 'senior'))
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    report = build_worklist(build_alignment_report(), args.stage)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    for row in report['goals']:
        print(f"{row['goal_id']} {row['resource_id']}：{row['statement']}")
        for action in row['actions']:
            print(f"  {action['gap']}：{action['instruction']}")
    if args.json:
        target = args.json if args.json.is_absolute() else ROOT / args.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
