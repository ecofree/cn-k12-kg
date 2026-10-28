"""Population-independent display order with a frozen legacy migration.

The snapshot contains only stable option IDs, never answers or review decisions.
New questions sort their option IDs by a per-question hash; adding/removing a
question cannot move another question or silently invalidate its admission.
"""
from __future__ import annotations
import copy
import hashlib

NAME = "stable-choice-order"
VERSION = 1
ENTRYPOINT = "cnkg/choice_order.py"
INPUTS = ["data/choice_orders.json"]


def stable_order(questions, snapshot):
    from tools.balance_options import ChoiceOrderError, correct_position, position_reference_fields
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ChoiceOrderError("choice order snapshot requires schema_version=1")
    frozen = snapshot.get("orders")
    if not isinstance(frozen, dict):
        raise ChoiceOrderError("choice order snapshot requires orders")
    for qid, ids in frozen.items():
        if (not isinstance(qid, str) or not qid or not isinstance(ids, list) or not ids
                or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids)):
            raise ChoiceOrderError(f"invalid frozen option IDs: {qid!r}")
    seen = set()
    result = copy.deepcopy(questions)
    for q in result:
        if q.get("type") != "choice":
            continue
        qid = q.get("id")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ChoiceOrderError(f"missing or duplicate choice question ID: {qid!r}")
        seen.add(qid)
        if correct_position(q) is None:
            raise ChoiceOrderError(f"{qid}: choices require unique IDs and a valid answer ID")
        fields = position_reference_fields(q)
        if fields:
            raise ChoiceOrderError(f"{qid}: display-position reference in {', '.join(fields)}")
        options = {o["id"]: o for o in q["choices"]}
        if qid in frozen:
            ids = frozen[qid]
            if set(ids) != set(options):
                raise ChoiceOrderError(f"{qid}: option identity set changed; explicitly review the frozen order")
        else:
            # Stable if source files, input sequence, answer ID, or other tasks change.
            ids = sorted(options, key=lambda oid: (hashlib.sha256((qid + '\0' + oid).encode()).digest(), oid))
        q["choices"] = [options[oid] for oid in ids]
    return result
