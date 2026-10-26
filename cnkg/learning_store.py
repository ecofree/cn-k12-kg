"""独立匿名学习记录。内容库重建与本库无关；状态与事件在同一事务提交。"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


class ConflictError(RuntimeError):
    """同一会话已被另一个请求更新。"""


def default_path():
    return Path(os.environ.get("CNKG_LEARNING_DB", ROOT / "var" / "learning.sqlite"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class LearningStore:
    """短事务、按所有者读取；不在浏览器保存题目答案或完整会话状态。"""
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_path()
        if self.path.resolve().is_relative_to((ROOT / 'build').resolve()):
            raise ValueError('学习记录必须保存在 build/ 之外，避免内容重建时丢失')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] > 1:
                raise ValueError('学习数据库版本高于当前程序；请使用匹配版本读取')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL, record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_owner ON sessions(owner_id, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL, session_id TEXT,
                    event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    resource_id TEXT, question_id TEXT, content_sha256 TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_owner ON events(owner_id, seq);
                CREATE INDEX IF NOT EXISTS events_material ON events(owner_id, event_type, resource_id);
                PRAGMA user_version = 1;
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

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

    def records(self, owner_id, limit=None):
        query = "SELECT record_json, revision FROM sessions WHERE owner_id=? ORDER BY created_at DESC, id DESC"
        values = [owner_id]
        if limit is not None:
            query += " LIMIT ?"
            values.append(int(limit))
        with self._connect() as db:
            return [self._record(row) for row in db.execute(query, values)]

    @staticmethod
    def _insert_events(db, owner_id, session_id, events):
        for event in events:
            db.execute("""INSERT INTO events
                (owner_id, session_id, event_type, occurred_at, resource_id, question_id,
                 content_sha256, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (owner_id, session_id, event["event_type"], event["occurred_at"],
                 event.get("resource_id"), event.get("question_id"), event.get("content_sha256"),
                 _json(event.get("payload", {}))))

    def save(self, record, events=()):
        """乐观锁防止旧状态覆盖新状态，事件只有在状态保存成功时才追加。"""
        expected = record.get("_revision", 0)
        stored = {k: v for k, v in record.items() if k != "_revision"}
        with self._connect() as db:
            if expected == 0:
                try:
                    db.execute("""INSERT INTO sessions
                        (id, owner_id, created_at, updated_at, revision, record_json)
                        VALUES (?, ?, ?, ?, 1, ?)""", (record["id"], record["owner_id"],
                        record["created_at"], record.get("updated_at", record["created_at"]), _json(stored)))
                except sqlite3.IntegrityError as exc:
                    raise ConflictError("会话已存在") from exc
            else:
                cursor = db.execute("""UPDATE sessions SET record_json=?, updated_at=?, revision=revision+1
                    WHERE id=? AND owner_id=? AND revision=?""",
                    (_json(stored), record.get("updated_at", record["created_at"]),
                     record["id"], record["owner_id"], expected))
                if cursor.rowcount != 1:
                    raise ConflictError("会话已由另一请求更新，请恢复当前会话")
            self._insert_events(db, record["owner_id"], record["id"], events)
        record["_revision"] = expected + 1

    def add_event(self, owner_id, event, session_id=None):
        with self._connect() as db:
            self._insert_events(db, owner_id, session_id, [event])

    def events(self, owner_id):
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE owner_id=? ORDER BY seq", (owner_id,))
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
