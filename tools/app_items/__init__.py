"""Read-only compatibility views over the declared JSON question source.

The release builder does not import this package. Historical batch imports are
kept for tools and mathematical regressions; edit the JSON, never these shims.
"""
from __future__ import annotations
import copy
import importlib
from ._declarative import batch_names

BATCHES = [importlib.import_module("." + name, __name__) for name in batch_names()]

def all_items():
    return copy.deepcopy([item for batch in BATCHES for item in batch.ITEMS])
