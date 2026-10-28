"""Legacy field aliases, without another authored answer or ID generator."""
from __future__ import annotations
import copy
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "data/question_sources/application.json"

def source_payload():
    return json.loads(SOURCE.read_text(encoding="utf-8"))

def batch_names():
    return [batch["id"] for batch in source_payload()["legacy_batches"]]

def items_for(name):
    payload = source_payload()
    batch = next((batch for batch in payload["legacy_batches"] if batch["id"] == name), None)
    if batch is None:
        raise ValueError("未登记的历史应用题批次：" + name)
    questions = {q["id"]: q for q in payload["questions"]}
    result = []
    for qid in batch["question_ids"]:
        item = copy.deepcopy(questions[qid])
        item["topic"] = item["topicId"]
        basis = item.get("difficulty_basis", "")
        prefix, separator, explanation = basis.partition("：")
        # Older records prefixed the reason with a design-label caption. New
        # authored reasons are already complete text; a missing caption must
        # not turn that real explanation into an empty compatibility field.
        legacy_caption = prefix.startswith(("自产应用题（", "设计难度复核（", "设计难点（"))
        item["why"] = explanation if separator and legacy_caption else basis
        result.append(item)
    return result
