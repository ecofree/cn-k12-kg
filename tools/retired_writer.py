"""Clear CLI boundary for historical writers; no opt-out flag rewrites sources."""
from pathlib import Path
import sys


def reject(script):
    name = Path(script).name
    print(f'{name} 已封存为历史工具，不再覆写当前作者 JSON。', file=sys.stderr)
    print('请编辑 data/content_sources.json 登记的 JSON，或 data/teach、data/learning_units、data/projects。', file=sys.stderr)
    print('然后运行：python tools/build_project.py --release', file=sys.stderr)
    print('说明：docs/authoring-workflow.md；历史纯函数仅为兼容和事故回归保留。', file=sys.stderr)
    return 2
