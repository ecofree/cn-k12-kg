"""Owner-scoped learning records, immutable work revisions and exposure indexes.

Schema migration is transactional and never rewrites the original session/event
JSON. Content rebuilds remain independent from this database. Work text and
self-assessment are learner reports, not automatically scored evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .review_timing import timestamp, qualified_review

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 4
WORK_KINDS = frozenset({"self_check", "project", "unit", "session_task"})
_IMMUTABLE_WORK_FIELDS = ("kind", "source_ref", "resource_version", "resource_snapshot")
_EXPOSURE_EVENTS = {"question_presented": "presentation_count",
                    "worked_example_presented": "presentation_count",
                    "answer_submitted": "answer_count", "feedback_released": "feedback_count",
                    "material_provided": "material_count", "reference_opened": "reference_count",
                    "session_content_provided": "presentation_count", "diagram_provided": "presentation_count",
                    "export_content_provided": "material_count"}


class ConflictError(RuntimeError):
    """A stale revision or reused request identifier would overwrite evidence."""


def default_path():
    return Path(os.environ.get("CNKG_LEARNING_DB", ROOT / "var" / "learning.sqlite"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{field} 必须为不超过 512 字符的非空标识")
    return value


def _page(limit, offset):
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000):
        raise ValueError("limit 必须为 1—1000 的整数")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 2**63 - 1:
        raise ValueError("offset 必须为非负整数")
    return (-1 if limit is None else limit), offset


def _scope(record):
    if record.get("unit_id"):
        return record["unit_id"]
    targets = record.get("engine", {}).get("targets", record.get("targets", []))
    if targets:
        return "topics:" + hashlib.sha256(_json(sorted(set(targets))).encode()).hexdigest()
    return record.get("resource_id") or record["id"]


def _session_summary(record):
    """A compact server-generated summary; no correct answers or full snapshots."""
    fields = ("id", "kind", "unit_id", "mode", "title", "stage", "created_at", "updated_at",
              "completed_at", "content_sha256", "resource_version", "grader_sha256")
    result = {key: record[key] for key in fields if key in record}
    result["scope_id"] = _scope(record)
    result["targets"] = record.get("engine", {}).get("targets", record.get("targets", []))
    # Only frozen metadata can describe historical observations. Current catalog
    # edits must never silently relabel an old answer or supply a missing grade.
    from .learning_needs import observation_metadata
    answer_fields = ("question_id", "objective_ids", "skill_ids", "status", "answered_at", "skipped",
                     "family_id", "family_status", "representation", "purpose", "alignment",
                     "content_sha256", "resource_version", "topic_ids", "help_used",
                     "resolution_for_need_ids", "support_kind", "followup_basis", "help_evidence")
    result["answers"] = []
    for entry in record.get("answers", []):
        metadata = observation_metadata(record, entry)
        result["answers"].append({key: metadata[key] for key in answer_fields if key in metadata})
    if "stage" not in result and record.get("unit_snapshot", {}).get("stage"):
        result["stage"] = record["unit_snapshot"]["stage"]
    if isinstance(record.get("source_origin"), dict):
        result["source_origin"] = {key: record["source_origin"][key] for key in
                                   ("need_ids", "resource_version", "source_ref", "unit_id")
                                   if key in record["source_origin"]}
    result["answered_count"] = len(result["answers"])
    result["context"] = {key: record.get("context", {})[key] for key in
                         ("previous_session_id", "same_learning_scope", "actual_delay_seconds",
                          "delay_window_met", "independent_evidence", "timing_policy",
                          "actual_quiet_seconds", "same_content_version", "review_interval_qualified",
                          "latest_scope_activity_at", "latest_scope_activity_type",
                          "interval_reason", "interval_notice") if key in record.get("context", {})}
    return result


def _work_summary(result):
    """Derived, rebuildable state; never change the learner's original payload."""
    payload = result["payload"]
    snapshot = payload["resource_snapshot"]
    criteria = snapshot.get("criteria", [])
    criteria = criteria if isinstance(criteria, list) else []
    assessment = payload.get("self_assessment", [])
    assessment = assessment if isinstance(assessment, list) else []
    draft = payload.get("draft", {})
    draft_values = list(draft.values()) if isinstance(draft, dict) else [draft]
    reflection = payload.get("reflection", "")
    outcomes = {}
    for row in assessment:
        if (isinstance(row, dict) and isinstance(row.get("criterion_index"), int)
                and not isinstance(row["criterion_index"], bool)
                and 0 <= row["criterion_index"] < len(criteria)
                and row.get("status") in {"not_checked", "met", "partial", "revisit"}):
            outcomes[row["criterion_index"]] = row["status"]
    criterion_outcomes = [{"criterion_index": index, "criterion": criterion,
                           "status": outcomes.get(index, "not_checked")}
                          for index, criterion in enumerate(criteria)]
    assessed = {index for index, status in outcomes.items() if status != "not_checked"}
    unresolved = [row["criterion_index"] for row in criterion_outcomes if row["status"] != "met"]
    source = payload["source_ref"]
    summary = {key: result[key] for key in
               ("resource_id", "resource_version", "revision", "created_at", "updated_at", "evidence_type")}
    summary.update(_revision=result["revision"], kind=payload["kind"], source_ref=source,
                   title=(snapshot.get("title") or snapshot.get("prompt")
                          or (source if isinstance(source, str) else source["question_id"])),
                   has_draft=any(isinstance(value, str) and value.strip() for value in draft_values),
                   has_reflection=isinstance(reflection, str) and bool(reflection.strip()),
                   self_assessment_reported=bool(assessed),
                   self_assessment_completed=bool(criteria) and len(assessed) == len(criteria),
                   assessed_criterion_count=len(assessed), criterion_count=len(criteria),
                   criterion_outcomes=criterion_outcomes,
                   unresolved_criterion_indices=unresolved,
                   revision_needed=any(status in {"partial", "revisit"} for status in outcomes.values()),
                   all_criteria_met=bool(criteria) and not unresolved,
                   stage=snapshot.get("stage"), status=payload.get("status", "draft"))
    return summary


class LearningStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()
        if self.path.resolve().is_relative_to((ROOT / "build").resolve()):
            raise ValueError("学习记录必须保存在 build/ 之外，避免内容重建时丢失")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            # A single writer lock covers DDL, backfill and the version marker.
            # executescript is intentionally avoided because it commits early.
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("学习数据库版本高于当前程序；请使用匹配版本读取")
            if version < 2:
                self._migrate_v2(db)
            if version < 3:
                self._migrate_v3(db)
            if version < 4:
                self._migrate_v4(db)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @classmethod
    def _migrate_v2(cls, db):
        statements = [
            """CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL, record_json TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(owner_id, created_at)",
            """CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, session_id TEXT,
                event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, resource_id TEXT,
                question_id TEXT, content_sha256 TEXT, payload_json TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS events_owner ON events(owner_id, seq)",
            "CREATE INDEX IF NOT EXISTS events_material ON events(owner_id, event_type, resource_id)",
            """CREATE TABLE works (
                owner_id TEXT NOT NULL, resource_id TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                resource_version TEXT NOT NULL, record_json TEXT NOT NULL,
                summary_json TEXT NOT NULL, PRIMARY KEY(owner_id, resource_id))""",
            "CREATE INDEX works_owner ON works(owner_id, updated_at DESC, resource_id)",
            """CREATE TABLE work_revisions (
                owner_id TEXT NOT NULL, resource_id TEXT NOT NULL, revision INTEGER NOT NULL,
                saved_at TEXT NOT NULL, record_json TEXT NOT NULL,
                PRIMARY KEY(owner_id, resource_id, revision))""",
            """CREATE TABLE work_requests (
                owner_id TEXT NOT NULL, resource_id TEXT NOT NULL, request_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY(owner_id, resource_id, request_id))""",
            """CREATE TABLE exposures (
                owner_id TEXT NOT NULL, entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                presentation_count INTEGER NOT NULL DEFAULT 0,
                answer_count INTEGER NOT NULL DEFAULT 0, feedback_count INTEGER NOT NULL DEFAULT 0,
                material_count INTEGER NOT NULL DEFAULT 0, reference_count INTEGER NOT NULL DEFAULT 0,
                last_material_at TEXT, last_reference_at TEXT,
                last_content_sha256 TEXT, last_event_seq INTEGER NOT NULL,
                PRIMARY KEY(owner_id, entity_kind, entity_id))""",
        ]
        for statement in statements:
            db.execute(statement)
        existing = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
        for column in ("scope_id", "kind", "mode", "completed_at", "content_sha256", "resource_version", "summary_json"):
            if column not in existing:
                db.execute(f"ALTER TABLE sessions ADD COLUMN {column} TEXT")
        if "answered_count" not in existing:
            db.execute("ALTER TABLE sessions ADD COLUMN answered_count INTEGER NOT NULL DEFAULT 0")
        db.execute("""CREATE INDEX sessions_scope ON sessions
                    (owner_id, scope_id, mode, resource_version, content_sha256, completed_at, updated_at DESC)""")
        snapshots = {}
        for row in db.execute("SELECT id, owner_id, record_json FROM sessions").fetchall():
            record = json.loads(row["record_json"])
            snapshots[row["id"]] = record
            db.execute("""UPDATE sessions SET scope_id=?, kind=?, mode=?, completed_at=?,
                        content_sha256=?, resource_version=?, summary_json=?, answered_count=? WHERE id=?""",
                       (_scope(record), record.get("kind"), record.get("mode"), record.get("completed_at"),
                        record.get("content_sha256"), record.get("resource_version"), _json(_session_summary(record)),
                        len(record.get("answers", [])), row["id"]))
        observed = set()
        for row in db.execute("SELECT * FROM events ORDER BY seq").fetchall():
            event = {**dict(row), "payload": json.loads(row["payload_json"])}
            cls._index_exposure(db, row["owner_id"], event, row["seq"], snapshots.get(row["session_id"]))
            observed.add((row["session_id"], row["question_id"], row["event_type"]))
        # Preserve evidence in older records even when the corresponding v1
        # event is absent; never double-count a recorded event.
        for sid, record in snapshots.items():
            for qid, presentation in record.get("presentations", {}).items():
                if (sid, qid, "question_presented") not in observed:
                    event = {"event_type": "question_presented", "question_id": qid,
                             "occurred_at": presentation.get("presented_at", record["created_at"]),
                             "content_sha256": presentation.get("content_sha256"), "payload": {}}
                    cls._index_exposure(db, record["owner_id"], event, 0, record)
            for answer in record.get("answers", []):
                qid = answer.get("question_id")
                if qid and (sid, qid, "answer_submitted") not in observed:
                    event = {"event_type": "answer_submitted", "question_id": qid,
                             "occurred_at": answer.get("answered_at", record["created_at"]),
                             "content_sha256": answer.get("content_sha256"), "payload": {}}
                    cls._index_exposure(db, record["owner_id"], event, 0, record)
                if qid and qid in record.get("feedback_exposed", []) and (sid, qid, "feedback_released") not in observed:
                    event = {"event_type": "feedback_released", "question_id": qid,
                             "occurred_at": answer.get("feedback_available_at") or answer.get("answered_at") or record["created_at"],
                             "content_sha256": answer.get("content_sha256"), "payload": {}}
                    cls._index_exposure(db, record["owner_id"], event, 0, record)
        db.execute("PRAGMA user_version = 2")

    @classmethod
    def _migrate_v3(cls, db):
        # Only derived indexes change. The session/work JSON, event log, request
        # identities and immutable work revision rows remain byte-for-byte.
        db.execute("ALTER TABLE sessions ADD COLUMN review_interval_qualified INTEGER NOT NULL DEFAULT 0")
        for row in db.execute("SELECT id, record_json FROM sessions").fetchall():
            record = json.loads(row["record_json"])
            db.execute("UPDATE sessions SET summary_json=?, review_interval_qualified=? WHERE id=?",
                       (_json(_session_summary(record)), int(qualified_review(record)), row["id"]))
        for row in db.execute("SELECT owner_id, resource_id, record_json FROM works").fetchall():
            result = json.loads(row["record_json"])
            db.execute("UPDATE works SET summary_json=? WHERE owner_id=? AND resource_id=?",
                       (_json(_work_summary(result)), row["owner_id"], row["resource_id"]))
        db.execute("PRAGMA user_version = 3")

    @classmethod
    def _migrate_v4(cls, db):
        # Rebuildable metadata only: raw historical JSON, timestamps, IDs,
        # revision numbers, event sequence and all work revisions stay intact.
        for row in db.execute("SELECT id, record_json FROM sessions").fetchall():
            db.execute("UPDATE sessions SET summary_json=? WHERE id=?",
                       (_json(_session_summary(json.loads(row["record_json"]))), row["id"]))
        for row in db.execute("SELECT owner_id, resource_id, record_json FROM works").fetchall():
            db.execute("UPDATE works SET summary_json=? WHERE owner_id=? AND resource_id=?",
                       (_json(_work_summary(json.loads(row["record_json"]))), row["owner_id"], row["resource_id"]))
        db.execute("PRAGMA user_version = 4")

    @staticmethod
    def _record(row):
        if row is None:
            return None
        record = json.loads(row["record_json"])
        record["_revision"] = row["revision"]
        return record

    def load(self, session_id, owner_id):
        with self._connect() as db:
            row = db.execute("SELECT record_json, revision FROM sessions WHERE id=? AND owner_id=?",
                             (session_id, owner_id)).fetchone()
        return self._record(row)

    def records(self, owner_id, limit=None, offset=0):
        limit, offset = _page(limit, offset)
        with self._connect() as db:
            return [self._record(row) for row in db.execute("""SELECT record_json, revision FROM sessions
                WHERE owner_id=? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""", (owner_id, limit, offset))]

    def count(self, owner_id):
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM sessions WHERE owner_id=?", (owner_id,)).fetchone()[0]

    def planning_records(self, owner_id):
        """All compact observations for this owner; no latest-phase or page cap.

        A newer phase must not hide an older unanswered need. Answers, prompts,
        diagrams and full frozen snapshots are deliberately absent here.
        """
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT summary_json FROM sessions WHERE owner_id=? ORDER BY created_at, id", (owner_id,))]

    def need_states(self, owner_id):
        """Latest explicit deferral/reopening, retained in the backed-up event log."""
        with self._connect() as db:
            states = {}
            for row in db.execute("""SELECT seq, event_type, occurred_at, payload_json FROM events
                WHERE owner_id=? AND event_type IN ('learning_need_deferred', 'learning_need_reopened')
                ORDER BY seq""", (owner_id,)):
                value = json.loads(row["payload_json"])
                states[value["need_id"]] = {**value, "status": "deferred" if row["event_type"] == "learning_need_deferred" else "open",
                                            "occurred_at": row["occurred_at"], "event_seq": row["seq"]}
            return list(states.values())

    def set_need_state(self, owner_id, need_id, action, reason):
        """Explicitly defer/reopen an owned need; this never resolves or regrades it."""
        from .learning_needs import derive_needs
        _identifier(owner_id, "owner_id")
        _identifier(need_id, "need_id")
        if action not in {"defer", "reopen"}:
            raise ValueError("学习事项只允许暂缓或重新打开，不能手工改成已掌握")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("请填写不超过1000字的暂缓或重新打开理由")
        needs = derive_needs(None, self.planning_records(owner_id), self.works(owner_id, limit=None),
                             self.need_states(owner_id))
        need = next((item for item in needs if item["need_id"] == need_id), None)
        if need is None:
            raise KeyError("找不到这个学习事项")
        if need["status"] == "resolved":
            raise ValueError("该事项已有具体后续证据；如有新困难，请保留新的学习记录")
        payload = {"need_id": need_id, "reason": reason.strip(), "source_ref": need["source_ref"],
                   "resource_version": need.get("resource_version")}
        self.add_event(owner_id, {"event_type": "learning_need_deferred" if action == "defer" else "learning_need_reopened",
                                  "occurred_at": _now(), "resource_id": need_id, "payload": payload},
                       session_id=need["source_ref"].get("session_id"))
        return next(item for item in self.need_states(owner_id) if item["need_id"] == need_id)

    def latest_scope_records(self, owner_id, resource_ids=None):
        """Latest completed/open record for each scope, version and phase.

        Uses compact indexed summaries rather than a recent-history page. Thus
        old completed units remain visible even after hundreds of other runs.
        phase_completion_count counts server-completed sessions, not mastery.
        """
        values = [owner_id]
        where = "owner_id=? AND (completed_at IS NULL OR answered_count>0)"
        if resource_ids is not None:
            ids = list(dict.fromkeys(resource_ids))
            if not ids:
                return []
            if len(ids) > 900:
                raise ValueError("单次学习范围查询最多 900 个 ID")
            where += " AND scope_id IN (" + ",".join("?" for _ in ids) + ")"
            values += ids
        query = f"""WITH ranked AS (
            SELECT summary_json, completed_at,
              ROW_NUMBER() OVER (PARTITION BY scope_id, COALESCE(resource_version, content_sha256), mode, completed_at IS NOT NULL
                ORDER BY julianday(COALESCE(completed_at, updated_at)) DESC, id DESC) AS position,
              COUNT(completed_at) OVER (PARTITION BY scope_id, COALESCE(resource_version, content_sha256), mode) AS completion_count,
              SUM(review_interval_qualified) OVER (PARTITION BY scope_id, COALESCE(resource_version, content_sha256), mode) AS qualified_review_count
            FROM sessions WHERE {where})
            SELECT summary_json, completion_count, qualified_review_count FROM ranked WHERE position=1"""
        with self._connect() as db:
            result = []
            for row in db.execute(query, values):
                item = json.loads(row["summary_json"])
                item["phase_completion_count"] = row["completion_count"]
                item["qualified_review_count"] = row["qualified_review_count"]
                result.append(item)
            return result

    @staticmethod
    def _family(question_id, event, record):
        if event.get("family_status", event.get("payload", {}).get("family_status")) in ("unknown", "unclassified"):
            return None
        family_id = event.get("family_id") or event.get("payload", {}).get("family_id")
        if family_id or not record:
            return family_id
        question = record.get("question_snapshots", {}).get(question_id)
        if not question:
            question = next((task for task in record.get("unit_snapshot", {}).get("tasks", [])
                             if task.get("id") == question_id), {})
        return question.get("family_id") if question.get("family_status") not in ("unknown", "unclassified") else None

    @classmethod
    def _index_exposure(cls, db, owner_id, event, seq, record=None):
        counter = _EXPOSURE_EVENTS.get(event["event_type"])
        if counter is None:
            return
        payload = event.get("payload", {})
        task_ids, family_ids = payload.get("task_ids", []), payload.get("family_ids", [])
        if not isinstance(task_ids, list) or not isinstance(family_ids, list):
            raise ValueError("曝光 task_ids/family_ids 必须为列表")
        question_ids = set(task_ids)
        if event.get("question_id"):
            question_ids.add(event["question_id"])
        families = set(family_ids) if event.get("family_status", payload.get("family_status")) not in ("unknown", "unclassified") else set()
        for question_id in question_ids or [None]:
            family_id = cls._family(question_id, event, record)
            if family_id:
                families.add(family_id)
        identities = [("question", identity) for identity in question_ids]
        identities += [("family", identity) for identity in families]
        for kind, identity in identities:
            if not identity:
                continue
            _identifier(identity, "exposure ID")
            counters = {name: int(counter == name) for name in
                        ("presentation_count", "answer_count", "feedback_count", "material_count", "reference_count")}
            when = event["occurred_at"]
            db.execute("""INSERT INTO exposures
                (owner_id, entity_kind, entity_id, first_seen_at, last_seen_at,
                 presentation_count, answer_count, feedback_count, material_count, reference_count,
                 last_material_at, last_reference_at, last_content_sha256, last_event_seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, entity_kind, entity_id) DO UPDATE SET
                  first_seen_at=MIN(first_seen_at, excluded.first_seen_at),
                  last_seen_at=MAX(last_seen_at, excluded.last_seen_at),
                  presentation_count=presentation_count+excluded.presentation_count,
                  answer_count=answer_count+excluded.answer_count,
                  feedback_count=feedback_count+excluded.feedback_count,
                  material_count=material_count+excluded.material_count,
                  reference_count=reference_count+excluded.reference_count,
                  last_material_at=CASE WHEN excluded.last_material_at IS NULL THEN last_material_at
                    WHEN last_material_at IS NULL THEN excluded.last_material_at
                    ELSE MAX(last_material_at, excluded.last_material_at) END,
                  last_reference_at=CASE WHEN excluded.last_reference_at IS NULL THEN last_reference_at
                    WHEN last_reference_at IS NULL THEN excluded.last_reference_at
                    ELSE MAX(last_reference_at, excluded.last_reference_at) END,
                  last_content_sha256=CASE WHEN excluded.last_seen_at >= last_seen_at
                    THEN COALESCE(excluded.last_content_sha256, last_content_sha256) ELSE last_content_sha256 END,
                  last_event_seq=MAX(last_event_seq, excluded.last_event_seq)""",
                (owner_id, kind, identity, when, when, counters["presentation_count"],
                 counters["answer_count"], counters["feedback_count"], counters["material_count"], counters["reference_count"],
                 when if counter == "material_count" else None, when if counter == "reference_count" else None,
                 event.get("content_sha256"), seq))

    @classmethod
    def _insert_events(cls, db, owner_id, session_id, events, record=None):
        if record is None and session_id:
            row = db.execute("SELECT record_json FROM sessions WHERE id=? AND owner_id=?",
                             (session_id, owner_id)).fetchone()
            record = json.loads(row[0]) if row else None
        for event in events:
            payload = dict(event.get("payload", {}))
            if event.get("family_id"):
                payload["family_id"] = event["family_id"]
            if event.get("family_status"):
                payload["family_status"] = event["family_status"]
            cursor = db.execute("""INSERT INTO events
                (owner_id, session_id, event_type, occurred_at, resource_id, question_id,
                 content_sha256, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (owner_id, session_id, event["event_type"], event["occurred_at"],
                 event.get("resource_id"), event.get("question_id"), event.get("content_sha256"),
                 _json(payload)))
            cls._index_exposure(db, owner_id, event, cursor.lastrowid, record)

    def save(self, record, events=()):
        expected = record.get("_revision", 0)
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("revision 必须为非负整数")
        stored = {k: v for k, v in record.items() if k != "_revision"}
        metadata = (_scope(record), record.get("kind"), record.get("mode"), record.get("completed_at"),
                    record.get("content_sha256"), record.get("resource_version"), _json(_session_summary(record)),
                    len(record.get("answers", [])), int(qualified_review(record)))
        with self._connect() as db:
            if expected == 0:
                try:
                    db.execute("""INSERT INTO sessions
                        (id, owner_id, created_at, updated_at, revision, record_json,
                         scope_id, kind, mode, completed_at, content_sha256, resource_version, summary_json, answered_count,
                         review_interval_qualified)
                        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (record["id"], record["owner_id"], record["created_at"],
                         record.get("updated_at", record["created_at"]), _json(stored), *metadata))
                except sqlite3.IntegrityError as exc:
                    raise ConflictError("会话已存在") from exc
            else:
                cursor = db.execute("""UPDATE sessions SET record_json=?, updated_at=?, revision=revision+1,
                    scope_id=?, kind=?, mode=?, completed_at=?, content_sha256=?, resource_version=?, summary_json=?, answered_count=?,
                    review_interval_qualified=?
                    WHERE id=? AND owner_id=? AND revision=?""",
                    (_json(stored), record.get("updated_at", record["created_at"]), *metadata,
                     record["id"], record["owner_id"], expected))
                if cursor.rowcount != 1:
                    raise ConflictError("会话已由另一请求更新，请恢复当前会话")
            self._insert_events(db, record["owner_id"], record["id"], events, record)
        record["_revision"] = expected + 1

    def add_event(self, owner_id, event, session_id=None):
        with self._connect() as db:
            self._insert_events(db, owner_id, session_id, [event])

    def events(self, owner_id, limit=None, offset=0):
        limit, offset = _page(limit, offset)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE owner_id=? ORDER BY seq LIMIT ? OFFSET ?",
                              (owner_id, limit, offset))
            return [{"seq": row["seq"], "session_id": row["session_id"],
                     "event_type": row["event_type"], "occurred_at": row["occurred_at"],
                     "resource_id": row["resource_id"], "question_id": row["question_id"],
                     "content_sha256": row["content_sha256"], "payload": json.loads(row["payload_json"])}
                    for row in rows]

    def latest_material(self, owner_id, resource_ids):
        if not resource_ids:
            return None
        marks = ",".join("?" for _ in resource_ids)
        with self._connect() as db:
            row = db.execute(f"""SELECT * FROM events
                WHERE owner_id=? AND event_type='material_requested' AND resource_id IN ({marks})
                ORDER BY seq DESC LIMIT 1""", [owner_id, *resource_ids]).fetchone()
        return ({"resource_id": row["resource_id"], "occurred_at": row["occurred_at"],
                 "content_sha256": row["content_sha256"]} if row else None)

    def latest_resource_event(self, owner_id, resource_id, event_type):
        """Recover one saved manipulation without scanning all learner events."""
        with self._connect() as db:
            row = db.execute("""SELECT * FROM events WHERE owner_id=? AND resource_id=? AND event_type=?
                ORDER BY seq DESC LIMIT 1""", (owner_id, resource_id, event_type)).fetchone()
        return ({"occurred_at": row["occurred_at"], "content_sha256": row["content_sha256"],
                 "payload": json.loads(row["payload_json"])} if row else None)

    def latest_scope_activity(self, owner_id, resource_ids, *, question_ids=(), family_ids=()):
        """Latest related material/reference, task presentation, answer or feedback.

        Scope matching uses server-resolved resource IDs and exposure indexes;
        no client timestamp or unrelated owner's activity can establish a gap.
        """
        resources = list(dict.fromkeys(resource_ids))
        candidates = []
        with self._connect() as db:
            for start in range(0, len(resources), 900):
                chunk = resources[start:start + 900]
                marks = ",".join("?" for _ in chunk)
                rows = db.execute(f"""SELECT seq, occurred_at, event_type FROM events
                    WHERE owner_id=? AND resource_id IN ({marks}) AND event_type IN
                      ('material_requested','material_provided','reference_opened','question_presented',
                       'answer_submitted','feedback_released','session_content_provided',
                       'diagram_provided','export_content_provided')""", [owner_id, *chunk])
                candidates.extend({**dict(row), "from_event": True} for row in rows)
        observed = self.exposures(owner_id, question_ids=question_ids, family_ids=family_ids)
        for kind, values in observed.items():
            for item in values.values():
                event_type = ("reference_opened" if item["last_reference_at"] == item["last_seen_at"] else
                              "material_provided" if item["last_material_at"] == item["last_seen_at"] else kind + "_exposure")
                candidates.append({"occurred_at": item["last_seen_at"], "event_type": event_type,
                                   "seq": item["last_event_seq"], "from_event": False})
        if candidates:
            last_written = max(candidates, key=lambda item: (item["seq"], item["from_event"]))
            if timestamp(last_written["occurred_at"]) is None:
                return {key: last_written[key] for key in ("occurred_at", "event_type")}
        candidates = [item for item in candidates if timestamp(item["occurred_at"]) is not None]
        if not candidates:
            return None
        latest = max(candidates, key=lambda item: (timestamp(item["occurred_at"]),
            item["event_type"] in {"material_requested", "material_provided", "reference_opened"}))
        return {key: latest[key] for key in ("occurred_at", "event_type")}

    def exposures(self, owner_id, question_ids=None, family_ids=None):
        """Index-only exposure counts. None means all; [] means none for that kind."""
        result = {"questions": {}, "families": {}}
        with self._connect() as db:
            for kind, selected, key in (("question", question_ids, "questions"), ("family", family_ids, "families")):
                if isinstance(selected, str):
                    raise ValueError("曝光查询需要 ID 列表")
                ids = None if selected is None else list(dict.fromkeys(selected))
                if ids is not None:
                    for identity in ids:
                        _identifier(identity, "exposure ID")
                chunks = [None] if ids is None else [ids[i:i + 900] for i in range(0, len(ids), 900)]
                for chunk in chunks:
                    query = "SELECT * FROM exposures WHERE owner_id=? AND entity_kind=?"
                    values = [owner_id, kind]
                    if chunk is not None:
                        query += " AND entity_id IN (" + ",".join("?" for _ in chunk) + ")"
                        values += chunk
                    for row in db.execute(query, values):
                        item = {field: row[field] for field in
                                ("first_seen_at", "last_seen_at", "presentation_count", "answer_count",
                                 "feedback_count", "material_count", "reference_count", "last_material_at",
                                 "last_reference_at", "last_content_sha256", "last_event_seq")}
                        item[kind + "_id"] = row["entity_id"]
                        result[key][row["entity_id"]] = item
        return result

    @staticmethod
    def _work(row, duplicate=False):
        if row is None:
            return None
        result = json.loads(row["record_json"])
        result["_revision"] = result["revision"]
        result["duplicate"] = duplicate
        return result

    def load_work(self, owner_id, resource_id, revision=None):
        with self._connect() as db:
            if revision is None:
                row = db.execute("SELECT record_json FROM works WHERE owner_id=? AND resource_id=?",
                                 (owner_id, resource_id)).fetchone()
            else:
                row = db.execute("""SELECT record_json FROM work_revisions
                    WHERE owner_id=? AND resource_id=? AND revision=?""", (owner_id, resource_id, revision)).fetchone()
        return self._work(row)

    def save_work(self, owner_id, resource_id, payload, expected_revision, request_id):
        for value, name in ((owner_id, "owner_id"), (resource_id, "resource_id"), (request_id, "request_id")):
            _identifier(value, name)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected_revision 必须为非负整数")
        if not isinstance(payload, dict):
            raise ValueError("作品 payload 必须是对象")
        if not isinstance(payload.get("kind"), str) or payload["kind"] not in WORK_KINDS:
            raise ValueError("不支持的作品 kind")
        _identifier(payload.get("resource_version"), "resource_version")
        source = payload.get("source_ref")
        if isinstance(source, str):
            _identifier(source, "source_ref")
        elif isinstance(source, dict) and set(source) == {"session_id", "question_id"}:
            for key in ("session_id", "question_id"):
                _identifier(source[key], "source_ref." + key)
        else:
            raise ValueError("source_ref 必须为稳定 ID 或 session_id/question_id 对象")
        if not isinstance(payload.get("resource_snapshot"), dict) or not payload["resource_snapshot"]:
            raise ValueError("作品必须保存非空内容快照")
        serialized = _json(payload)
        if len(serialized.encode("utf-8")) > 2_000_000:
            raise ValueError("作品内容超过 2 MB")
        request_hash = hashlib.sha256(_json({"payload": payload, "expected_revision": expected_revision}).encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("""SELECT request_sha256, revision FROM work_requests
                WHERE owner_id=? AND resource_id=? AND request_id=?""", (owner_id, resource_id, request_id)).fetchone()
            if prior:
                if prior["request_sha256"] != request_hash:
                    raise ConflictError("保存请求标识已被不同内容使用，请保留当前草稿并重新保存")
                row = db.execute("""SELECT record_json FROM work_revisions
                    WHERE owner_id=? AND resource_id=? AND revision=?""", (owner_id, resource_id, prior["revision"])).fetchone()
                return self._work(row, duplicate=True)
            current = db.execute("SELECT * FROM works WHERE owner_id=? AND resource_id=?",
                                 (owner_id, resource_id)).fetchone()
            actual_revision = current["revision"] if current else 0
            if actual_revision != expected_revision:
                raise ConflictError("作品已由另一请求更新；请恢复最新修订版后合并草稿")
            now = _now()
            if current:
                old_payload = json.loads(current["record_json"])["payload"]
                if _json({key: payload.get(key) for key in _IMMUTABLE_WORK_FIELDS}) != _json({
                        key: old_payload.get(key) for key in _IMMUTABLE_WORK_FIELDS}):
                    raise ConflictError("已保存作品的内容版本、来源与快照不可改写，请为新内容建立新作品")
            result = {"schema": 2, "resource_id": resource_id, "resource_version": payload["resource_version"],
                      "revision": actual_revision + 1, "created_at": current["created_at"] if current else now,
                      "updated_at": now, "payload": json.loads(serialized), "evidence_type": "learner_reported"}
            record_json = _json(result)
            summary = _work_summary(result)
            db.execute("""INSERT INTO works
                (owner_id, resource_id, revision, created_at, updated_at, resource_version, record_json, summary_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(owner_id, resource_id) DO UPDATE SET
                revision=excluded.revision, updated_at=excluded.updated_at, record_json=excluded.record_json,
                summary_json=excluded.summary_json""",
                (owner_id, resource_id, result["revision"], result["created_at"], now,
                 payload["resource_version"], record_json, _json(summary)))
            db.execute("INSERT INTO work_revisions VALUES (?, ?, ?, ?, ?)",
                       (owner_id, resource_id, result["revision"], now, record_json))
            db.execute("INSERT INTO work_requests VALUES (?, ?, ?, ?, ?)",
                       (owner_id, resource_id, request_id, request_hash, result["revision"]))
            self._insert_events(db, owner_id, None, [{"event_type": "work_saved", "occurred_at": now,
                "resource_id": resource_id, "content_sha256": payload["resource_version"],
                "payload": {"revision": result["revision"], "kind": payload["kind"],
                            "source_ref": payload["source_ref"], "evidence_type": "learner_reported"}}])
            result.update(_revision=result["revision"], duplicate=False)
            return result

    def works(self, owner_id, limit=100, offset=0):
        limit, offset = _page(limit, offset)
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("""SELECT summary_json FROM works
                WHERE owner_id=? ORDER BY updated_at DESC, resource_id LIMIT ? OFFSET ?""", (owner_id, limit, offset))]

    def count_works(self, owner_id):
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM works WHERE owner_id=?", (owner_id,)).fetchone()[0]

    def work_history(self, owner_id, resource_id, limit=100, offset=0):
        limit, offset = _page(limit, offset)
        with self._connect() as db:
            return [self._work(row) for row in db.execute("""SELECT record_json FROM work_revisions
                WHERE owner_id=? AND resource_id=? ORDER BY revision DESC LIMIT ? OFFSET ?""",
                (owner_id, resource_id, limit, offset))]
