"""应用题批次登记表：一册一个模块，模块里只有数据。

加一册：新建 `j8a.py`（照 `primary.py` 的字段写），在本文件 import 并加进
`BATCHES`。顺序决定 id 分配顺序，但每点内部独立编号，所以插批次不会
改动已有 id。
"""
from __future__ import annotations

from . import h1a, h1b, h1c, h1d, h2a, h2b, h2c, h2d, h3a, h3b, h3c, h4, h5a, h5b, j7a, j7b, j8a, j8b, j9a, j9b, primary, primary2, primary_completion

BATCHES = [primary, primary2, j7a, j7b, j8a, j8b, j9a, j9b, h1a, h1b, h1c, h1d, h2a, h2b, h2c, h2d, h3a, h3b, h3c, h4, h5a, h5b, primary_completion]


def all_items() -> list[dict]:
    """所有批次按登记顺序摊平。"""
    return [it for b in BATCHES for it in b.ITEMS]
