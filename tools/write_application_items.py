"""Read-only compatibility adapter for historical application-item tests.

The single authoring source is data/question_sources/application.json. Question
IDs are explicit fields; reordering records never allocates new IDs. The old
writer is archived in tools/archive/write_application_items.py.txt. All writes
use the registered source build: python tools/build_project.py --release.
"""
from __future__ import annotations

# Stop before loading content when someone follows an old CLI command.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import app_items

SOURCE = "self-generated-application"


def build(items: list[dict], questions: list[dict]) -> tuple[list[dict], list[str]]:
    """Read/validate explicit historical question records; never allocate IDs.

    Compatibility fields topic/why are views only. Every actual question field,
    including evidence metadata, is taken from the declared JSON unchanged.
    """
    import copy
    from cnkg.verify import check_question
    errors, out = [], []
    used_ids = {q["id"] for q in questions}
    prompts = {re.sub(r"\s+", "", q.get("prompt", "")) for q in questions}
    for item in items:
        q = {k: copy.deepcopy(v) for k, v in item.items() if k not in ("topic", "why")}
        qid = q.get("id")
        if not qid or qid in used_ids:
            errors.append("声明式题目必须有唯一的固定 ID：" + str(qid))
            continue
        if q.get("topicId") != item.get("topic", q.get("topicId")):
            errors.append(str(qid) + " 的历史主题别名不一致")
            continue
        prompt = re.sub(r"\s+", "", q.get("prompt", ""))
        if prompt in prompts:
            errors.append(str(qid) + " 与既有题干重复")
            continue
        if q.get("type") == "numeric" and check_question(q)["status"] != "pass":
            errors.append(str(qid) + " 的数值验算未通过")
            continue
        used_ids.add(qid)
        prompts.add(prompt)
        out.append(q)
    return out, errors



def main() -> int:
    from tools.retired_writer import reject
    return reject(__file__)
