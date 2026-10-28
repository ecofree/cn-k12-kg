"""Reviewed family equivalence for comparisons, never a rewrite of learner data.

Historical names remain in saved tasks, answers, events and exposure indexes.
An explicitly reviewed alias must not make an old structure look unseen after
a content release. No identity is inferred from names, grades or question IDs.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_aliases(path):
    value = json.loads(Path(path).read_text())
    if value.get("schema") != 1 or not isinstance(value.get("aliases"), list):
        raise ValueError("题族别名登记格式不正确")
    direct = {}
    for row in value["aliases"]:
        alias, canonical = row.get("alias"), row.get("canonical")
        if (not all(isinstance(item, str) and item.strip() and not item.startswith("unclassified:")
                    and item not in {"unknown", "unclassified"} for item in (alias, canonical))
                or alias == canonical or alias in direct or not row.get("reason", "").strip()
                or row.get("reviewer_kind") != "ai" or row.get("independent_review") is not False):
            raise ValueError("题族别名必须有唯一明确关系和实际语义审查理由")
        direct[alias] = canonical
    result = {}
    for alias in direct:
        current, seen = alias, set()
        while current in direct:
            if current in seen:
                raise ValueError("题族别名不能循环")
            seen.add(current)
            current = direct[current]
        result[alias] = current
    return result


# Freeze the reviewed mapping at module import, like the running grader. A new
# release requires a restart; changing a file cannot relabel in-memory rules.
ALIASES = load_aliases(ROOT / "data/task_family_aliases.json")


def canonical_family(identity):
    return ALIASES.get(identity, identity)


def equivalent_names(identities):
    """Expand a selected query to all reviewed historical names, not all data."""
    canonical = {canonical_family(identity) for identity in identities}
    return sorted(canonical | {alias for alias, target in ALIASES.items() if target in canonical})


def matching_exposures(index, identity):
    target = canonical_family(identity)
    return [row for name, row in index.items() if canonical_family(name) == target] if target else []


def family_exposure(index, identity):
    """Presence/counter bounds for selection; never claim a unique event count.

    One event may have indexed both historical names, so summing aggregate
    counters could double-count it. The maximum preserves every positive help
    or repetition flag; original indexed rows remain available unchanged.
    """
    rows = matching_exposures(index, identity)
    if not rows:
        return {}
    return {"family_id": canonical_family(identity), "count_basis": "maximum_per_indexed_name",
            **{field: max(row.get(field, 0) for row in rows) for field in
               ("presentation_count", "answer_count", "feedback_count", "material_count", "reference_count")}}
