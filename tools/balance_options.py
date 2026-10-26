"""Build-time balancing of displayed correct-option positions.

Questions with the same number of choices are sorted by the hash of their
stable question ID, then assigned positions in round-robin order.  The largest
and smallest position counts differ by at most one.  Rotating the option array
preserves option IDs, option content and the answer ID.  Reapplying the same
transform to the same population is a no-op.  Adding or removing questions can
change other target positions in their option-count group.

This is an explicit transform in data/content_sources.json, applied after all
authoring sources and application generators have been merged.  The CLI uses
that same source build; it never patches either generated questions or source
files in isolation.  Quality-review records are preserved, never re-signed:
their content hash includes choice order, so a moved question needs a review
of its resulting version before the old approval can be accepted again.

    python tools/balance_options.py --dry-run
    python tools/balance_options.py
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRANSFORM_NAME = "balanced-choice-positions"
TRANSFORM_VERSION = 1
TRANSFORM_ENTRYPOINT = "tools/balance_options.py"

# These are conservative checks for explicit option references, not a proof
# that arbitrary prose is order-independent.  Geometry labels and mathematical
# terms such as "余弦差角中第二项" are not display positions.  Bare ordinals need
# a content review; do not automatically rewrite mathematical explanations.
OPTION_REF_RE = re.compile(
    r"(?:^|[，。；、\s（(])([ABCD])\s*(?:错|对|正确|错误|项|选项)"
    r"|选项\s*[ABCD]"
    r"|只有\s*[ABCD]\s*(?:符合|是|对)"
    r"|(?:故选|所以选|因此选|应选|应选择)\s*[:：]?\s*[ABCD](?![A-Za-z])"
    r"|(?:正确)?答案(?:应为|为|是)\s*[:：]?\s*[ABCD](?![A-Za-z])")
OPTION_ORDINAL_RE = re.compile(
    r"第\s*[一二三四五六七八九十\d]+\s*(?:个)?选项"
    r"|(?:前|后)\s*[一二两三四五六七八九十\d]+\s*(?:个)?选项")
RELATIVE_CHOICE_RE = re.compile(r"(?:以上|上述|前面|上面|以下|下面)(?:的)?(?:都|均|皆|全|各|选项)")


class ChoiceOrderError(ValueError):
    """A question cannot safely participate in the option-order transform."""


def correct_position(question: dict) -> int | None:
    """Return the actual zero-based display position, never an option-ID label."""
    if question.get("type") != "choice":
        return None
    choices = question.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    if any(not isinstance(choice, dict) for choice in choices):
        return None
    ids = [choice.get("id") for choice in choices]
    if any(not isinstance(oid, str) or not oid for oid in ids) or len(set(ids)) != len(ids):
        return None
    answer = question.get("answer")
    return ids.index(answer) if answer in ids else None


def position_label(position: int) -> str:
    return chr(ord("A") + position) if 0 <= position < 26 else str(position + 1)


def display_position_counts(questions: list) -> dict[int, collections.Counter]:
    """Group actual correct positions by option count, excluding malformed rows."""
    groups = collections.defaultdict(collections.Counter)
    for question in questions:
        position = correct_position(question)
        if position is not None:
            groups[len(question["choices"])][position] += 1
    return dict(groups)


def position_reference_fields(question: dict) -> list[str]:
    """Identify explicit order-dependent text; callers must fix its source."""
    if question.get("type") != "choice":
        return []
    fields = {name: question.get(name) or "" for name in ("prompt", "explanation")}
    for i, choice in enumerate(question.get("choices") or []):
        if isinstance(choice, dict):
            fields[f"choices[{i}].text"] = choice.get("text") or ""
    found = []
    for name, value in fields.items():
        value = str(value)
        if (OPTION_REF_RE.search(value) or OPTION_ORDINAL_RE.search(value)
                or (name.startswith("choices[") and RELATIVE_CHOICE_RE.search(value))):
            found.append(name)
    return found


def hash_rank(qid: str) -> int:
    """A stable rank independent of current option order or input-file order."""
    return int(hashlib.sha256(qid.encode("utf-8")).hexdigest(), 16)


def assign_targets(questions: list) -> dict[str, int]:
    """Assign a balanced position per option-count group in stable ID order."""
    groups = collections.defaultdict(list)
    seen = set()
    for question in questions:
        if question.get("type") != "choice":
            continue
        qid = question.get("id")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ChoiceOrderError(f"missing or duplicate choice question ID: {qid!r}")
        seen.add(qid)
        if correct_position(question) is None:
            raise ChoiceOrderError(f"{qid}: choices need unique stable IDs and a matching answer ID")
        groups[len(question["choices"])].append(question)
    targets = {}
    for size, rows in groups.items():
        # The ID tie-break also makes the result deterministic in a hash collision.
        for i, question in enumerate(sorted(rows, key=lambda q: (hash_rank(q["id"]), q["id"]))):
            targets[question["id"]] = i % size
    return targets


def balance(question: dict, want: int) -> dict:
    """Rotate choices to a zero-based position without modifying any payload."""
    if question.get("type") != "choice":
        return question
    old = correct_position(question)
    if old is None:
        raise ChoiceOrderError(f"{question.get('id')}: invalid choice IDs or answer ID")
    choices = question["choices"]
    if not isinstance(want, int) or not 0 <= want < len(choices):
        raise ChoiceOrderError(f"{question.get('id')}: invalid target position {want!r}")
    if old == want:
        return question
    shift = (old - want) % len(choices)
    return {**question, "choices": choices[shift:] + choices[:shift]}


def balance_questions(questions: list) -> list:
    """Pure, idempotent transform of the complete merged question population."""
    targets = assign_targets(questions)
    for question in questions:
        fields = position_reference_fields(question)
        if fields:
            raise ChoiceOrderError(f"{question['id']}: display-position reference in {', '.join(fields)}; "
                                   "explain the option content in its authoring source")
    # Do not let callers mutate source data, flags or review records via aliases.
    return [balance(q, targets[q["id"]]) if q.get("type") == "choice" else q
            for q in copy.deepcopy(questions)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data-dir", type=pathlib.Path, default=DATA)
    args = ap.parse_args(argv)
    # Import only at the CLI boundary: rebuild_data imports this pure transform.
    sys.path.insert(0, str(ROOT))
    from tools import rebuild_data

    try:
        registry = rebuild_data.load_registry(args.data_dir)
        if not any(t.get("name") == TRANSFORM_NAME for t in registry.get("question_transforms", [])):
            raise rebuild_data.SourceError("register balanced-choice-positions before building")
        rows, _ = rebuild_data.build_data(args.data_dir, apply_transforms=False)
        before = rows["questions.json"]
        after = rebuild_data.apply_question_transforms(before, registry)
        changed = sum(left != right for left, right in zip(before, after))
        print(f"选择题 {sum(q.get('type') == 'choice' for q in before)}，构建变换调整 {changed} 道")
        counts_before, counts_after = display_position_counts(before), display_position_counts(after)
        for size in sorted(counts_after):
            first = {position_label(i): counts_before[size][i] for i in range(size)}
            last = {position_label(i): counts_after[size][i] for i in range(size)}
            print(f"{size} 选项实际显示位置：变换前 {first}；变换后 {last}")
        if args.dry_run:
            return 0
        rows["questions.json"] = after
        for name, output in rows.items():
            rebuild_data.write_json(args.data_dir / name, output)
        print(f"已从登记源重建 {', '.join(rebuild_data.OUTPUTS)}；内容源与审核记录未改写")
        return 0
    except (rebuild_data.SourceError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"OPTION BALANCE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
