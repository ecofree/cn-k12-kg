"""把讲解卡写进 data/teach/ 之前，先把能查的错挡住。

一次写几十张卡，最怕的是"看起来都对、其实指向不存在的题"。
所以落盘前逐张过三关：

1. 知识点存在、名字对得上、且它确实是**核心点**（工具点走速查卡）。
2. 每道练习题真实存在，且挂在同一个知识点上，至少一道非选择。
3. 例题的 `verify` 能求值，并且等于 `answer`。

第 3 关最要紧：讲解里的例题自己算错，学生会照着错的学会。
写完后再由 `tools/teach_cards.py --check` 复查一遍（它还会查文案长度、
易错点、识别信号），这里只挡"会把人带偏"的硬错。
"""
from __future__ import annotations

# Historical CLI is fenced before imports or authoring side effects.
if __name__ == "__main__":
    import pathlib as _legacy_pathlib
    import sys as _legacy_sys
    _legacy_sys.path.insert(0, str(_legacy_pathlib.Path(__file__).resolve().parents[1]))
    from tools.retired_writer import reject as _reject_retired_writer
    raise SystemExit(_reject_retired_writer(__file__))


import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cnkg.evidence import safe_eval  # noqa: E402
from tools.teach_cards import check_card  # noqa: E402

DATA = ROOT / "data"
TEACH = DATA / "teach"


def _load() -> tuple[dict, dict, dict]:
    topics = {t["id"]: t for t in json.loads(
        (DATA / "topics.json").read_text(encoding="utf-8"))}
    bank = {q["id"]: q for q in json.loads(
        (DATA / "questions.json").read_text(encoding="utf-8"))}
    tiers = json.loads((DATA / "topic_tiers.json").read_text(encoding="utf-8"))
    return topics, bank, tiers


def write_cards(cards: list[dict], source: str, *, expect_tier: str = "core") -> int:
    raise RuntimeError('历史讲解写入器已封存；编辑 data/teach JSON 后运行 tools/build_project.py --release')
