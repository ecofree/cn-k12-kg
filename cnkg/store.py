"""SQLite 存储层：把三个 JSON 装载成可查询的表，并提供递归 CTE 查询。

设计原则：
  1. JSON 是唯一真源，SQLite 是可重建的派生产物（build/ 可随时删除重跑）。
  2. 先修关系用递归 CTE 查祖先/后代，避免在应用层反复遍历。
  3. 所有表都带 schema 版本与构建时间，便于后续增量迁移。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
from contextlib import closing

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DEFAULT_DB = ROOT / "build" / "kg.sqlite"
SCHEMA_VERSION = 2

DDL = """
PRAGMA journal_mode = DELETE;

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE topics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    volume TEXT NOT NULL,
    chapter TEXT NOT NULL,
    section TEXT NOT NULL,
    grade INTEGER NOT NULL,
    semester TEXT NOT NULL,
    domain TEXT NOT NULL,
    theme TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    mastery_json TEXT,
    standards_json TEXT,
    curriculum_version TEXT NOT NULL,
    edition TEXT NOT NULL,
    review_status TEXT NOT NULL,
    root_reason TEXT
);

CREATE TABLE dependencies (
    topic_id TEXT NOT NULL REFERENCES topics(id),
    prereq_id TEXT NOT NULL REFERENCES topics(id),
    strength TEXT NOT NULL CHECK (strength IN ('hard', 'soft')),
    reason TEXT,
    PRIMARY KEY (topic_id, prereq_id)
);

CREATE TABLE questions (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL REFERENCES topics(id),
    difficulty TEXT NOT NULL,
    type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    choices_json TEXT,
    answer TEXT NOT NULL,
    verify TEXT,
    explanation TEXT,
    answer_spec_json TEXT
);

CREATE TABLE retired_questions (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    content_json TEXT NOT NULL,
    answer_spec_json TEXT,
    resource_version TEXT
);

-- 复合题的「题目-知识点」多对多关系。
-- topic_id 仍是主知识点（兼容旧查询），本表记录全部涉及的知识点。
CREATE TABLE question_topics (
    question_id TEXT NOT NULL REFERENCES questions(id),
    topic_id TEXT NOT NULL REFERENCES topics(id),
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (question_id, topic_id)
);

CREATE TABLE standards (
    id TEXT PRIMARY KEY,
    band INTEGER,
    domain TEXT,
    theme TEXT,
    title TEXT,
    summary TEXT,
    verbatim INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    page_or_ref TEXT
);

CREATE INDEX idx_topics_chapter ON topics(volume, chapter);
CREATE INDEX idx_questions_topic ON questions(topic_id);
CREATE INDEX idx_deps_prereq ON dependencies(prereq_id);
CREATE INDEX idx_qtopics_topic ON question_topics(topic_id);
CREATE INDEX idx_qtopics_q ON question_topics(question_id);
"""

METADATA_VIEW = """
DROP VIEW IF EXISTS v_topics;
CREATE VIEW v_topics AS
SELECT t.*,
       (SELECT COUNT(*) FROM dependencies d WHERE d.topic_id = t.id)   AS prereq_count,
       (SELECT COUNT(*) FROM dependencies d WHERE d.prereq_id = t.id) AS dependent_count,
       -- 含复合题：按「知识点-题目对」计数，而不是按去重题目数
       (SELECT COUNT(*) FROM question_topics qt WHERE qt.topic_id = t.id) AS question_count
FROM topics t;
"""


def read_json(name: str):
    with open(DATA / name, encoding="utf-8") as fh:
        return json.load(fh)


def build(db_path: pathlib.Path | str = DEFAULT_DB, include_external: bool = True) -> dict:
    """从 data/*.json 重建 SQLite，返回各表行数。

    include_external：是否同时导入 questions_ext.json（外部真题）。
    正式自有题使用目录中的答案契约；停用题进入 retired_questions，
    不参与活跃题量和知识点题库统计。临时旧数据目录仍可不带 catalog。
    """
    db_path = pathlib.Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        # 即便误把 --db 指向学习库，也不能删除不可重建的学习证据。
        try:
            with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as existing:
                tables = {row[0] for row in existing.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            raise ValueError("目标不是可识别的内容数据库，拒绝覆盖") from exc
        if {"sessions", "events"} & tables:
            raise ValueError("目标包含学习记录，不能作为内容库重建")
    sidecars = [pathlib.Path(str(db_path) + suffix) for suffix in ("-wal", "-shm")]
    if any(path.exists() for path in sidecars):
        # Old builds used WAL. A crashed process can leave valid committed data
        # in those files even when no session is alive. Let SQLite checkpoint
        # them and acquire the required mode-change lock; never unlink a WAL.
        try:
            with closing(sqlite3.connect(db_path, timeout=0.5)) as existing:
                checkpoint = existing.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint and checkpoint[0]:
                    raise ValueError("目标内容库仍有 WAL 会话，请关闭使用它的进程后再重建")
                mode = existing.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if mode != "delete":
                    raise ValueError("目标内容库无法安全切换为单文件，请关闭使用它的进程后再重建")
        except sqlite3.DatabaseError as exc:
            raise ValueError("目标内容库仍被使用，不能安全替换；请关闭使用它的进程后再重建") from exc

    topics = read_json("topics.json")
    deps = read_json("dependencies.json")
    questions = read_json("questions.json")
    retired_questions = []
    reserved_own_ids = {q["id"] for q in questions}
    if (DATA / "catalog.json").is_file():
        from .catalog import CatalogError, load_catalog
        catalog = load_catalog(DATA)
        active = []
        for question in questions:
            task = catalog.tasks.get(question["id"])
            if task is None or task["source_kind"] != "bank":
                raise CatalogError([f"自有题未登记：{question['id']}"])
            if task["availability"] == "active":
                active.append({**question, "answer_spec": task["answer_spec"]})
            else:
                retired_questions.append((question, task))
        questions = active
    if include_external and (DATA / "questions_ext.json").exists():
        ext = read_json("questions_ext.json")
        # 外部题 id 可能与自有题冲突（不同来源的编号体系不同），
        # 冲突时加来源前缀，保证主键唯一。
        own_ids = reserved_own_ids
        seen = set(own_ids)
        for q in ext:
            qid = q["id"]
            if qid in seen:
                qid = f"ext-{q.get('source', 'x')}-{qid}"
                n = 2
                while qid in seen:
                    qid = f"ext-{q.get('source', 'x')}-{q['id']}-{n}"
                    n += 1
            seen.add(qid)
            q = {**q, "id": qid}
            questions.append(q)
    standards = read_json("standards.json")

    # A failed compilation leaves the existing rebuildable content database
    # intact; learning databases were rejected above before creating this file.
    with tempfile.NamedTemporaryFile(dir=db_path.parent, suffix=".sqlite", delete=False) as staged:
        staging_path = pathlib.Path(staged.name)
    conn = sqlite3.connect(staging_path)
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO topics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(t["id"], t["name"], t["volume"], t["chapter"], t["section"], t["grade"],
              t["semester"], t["domain"], t["theme"], t["type"], t.get("description", ""),
              json.dumps(t.get("mastery_criteria", []), ensure_ascii=False),
              json.dumps(t.get("standards", []), ensure_ascii=False),
              t["curriculum_version"], t["edition"], t.get("review_status", "draft"),
              t.get("root_reason")) for t in topics],
        )
        conn.executemany(
            "INSERT INTO dependencies VALUES (?,?,?,?)",
            [(d["topicId"], d["prerequisiteId"], d["strength"], d.get("reason", "")) for d in deps],
        )
        conn.executemany(
            "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(q["id"], q["topicId"], q["difficulty"], q["type"], q["prompt"],
              json.dumps(q.get("choices"), ensure_ascii=False) if q.get("choices") else None,
              q["answer"], q.get("verify"), q.get("explanation", ""),
              json.dumps(q["answer_spec"], ensure_ascii=False) if "answer_spec" in q else None) for q in questions],
        )
        conn.executemany("INSERT INTO retired_questions VALUES (?,?,?,?,?,?)", [
            (q["id"], q["topicId"], task["policy"]["reason"], json.dumps(q, ensure_ascii=False),
             json.dumps(task["answer_spec"], ensure_ascii=False), task["resource_version"])
            for q, task in retired_questions])
        # 复合题展开成多行。没有 topicIds 的旧题退化成单行（主知识点）。
        qtopic_rows = []
        for q in questions:
            ids = q.get("topicIds") or ([q["topicId"]] if q.get("topicId") else [])
            primary = q.get("primaryTopicId") or (ids[0] if ids else None)
            seen = set()
            for tid in ids:
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                qtopic_rows.append((q["id"], tid, 1 if tid == primary else 0))
        conn.executemany(
            "INSERT OR IGNORE INTO question_topics VALUES (?,?,?)", qtopic_rows
        )
        conn.executemany(
            "INSERT INTO standards VALUES (?,?,?,?,?,?,?,?,?)",
            [(s["id"], s.get("band"), s.get("domain"), s.get("theme"), s.get("title"),
              s.get("summary"), 1 if s.get("verbatim") else 0, s.get("source"),
              s.get("page_or_ref")) for s in standards],
        )
        conn.executescript(METADATA_VIEW)
        conn.executemany("INSERT INTO meta VALUES (?,?)", [
            ("schema_version", str(SCHEMA_VERSION)),
            ("curriculum_version", topics[0]["curriculum_version"] if topics else ""),
            ("edition", topics[0]["edition"] if topics else ""),
        ])
        conn.commit()
        counts = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("topics", "dependencies", "questions", "retired_questions", "standards")}
    except Exception:
        conn.close()
        staging_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    try:
        # A single-file content database can be replaced without pairing the
        # new main file with an old process's WAL. Learner stores are separate.
        if any(path.exists() for path in sidecars):
            raise ValueError("目标内容库出现新的 WAL 会话，拒绝替换")
        staging_path.replace(db_path)
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise
    return counts


def connect(db_path: pathlib.Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ancestors(conn, topic_id: str) -> list:
    """全部祖先（含层数），先去重再取最小深度。"""
    sql = """
    WITH RECURSIVE anc(id, depth) AS (
        SELECT prereq_id, 1 FROM dependencies WHERE topic_id = ?
        UNION
        SELECT d.prereq_id, a.depth + 1
        FROM dependencies d JOIN anc a ON d.topic_id = a.id
    )
    SELECT t.id, t.name, t.chapter, MIN(a.depth) AS depth
    FROM anc a JOIN topics t ON t.id = a.id
    GROUP BY t.id ORDER BY depth, t.id
    """
    return [dict(row) for row in conn.execute(sql, (topic_id,))]


def descendants(conn, topic_id: str) -> list:
    sql = """
    WITH RECURSIVE des(id, depth) AS (
        SELECT topic_id, 1 FROM dependencies WHERE prereq_id = ?
        UNION
        SELECT d.topic_id, x.depth + 1
        FROM dependencies d JOIN des x ON d.prereq_id = x.id
    )
    SELECT t.id, t.name, t.chapter, MIN(x.depth) AS depth
    FROM des x JOIN topics t ON t.id = x.id
    GROUP BY t.id ORDER BY depth, t.id
    """
    return [dict(row) for row in conn.execute(sql, (topic_id,))]


def subgraph_edges(conn, topic_id: str) -> list:
    """目标点及其全部祖先内部的边，用于在应用层做拓扑排序。"""
    sql = """
    WITH RECURSIVE anc(id) AS (
        SELECT ?
        UNION
        SELECT d.prereq_id FROM dependencies d JOIN anc a ON d.topic_id = a.id
    )
    SELECT d.topic_id, d.prereq_id, d.strength
    FROM dependencies d
    WHERE d.topic_id IN (SELECT id FROM anc) AND d.prereq_id IN (SELECT id FROM anc)
    """
    return [dict(row) for row in conn.execute(sql, (topic_id,))]


def chapter_summary(conn) -> list:
    sql = """
    SELECT t.volume, t.chapter,
           COUNT(DISTINCT t.id) AS topics,
           COUNT(DISTINCT q.id) AS questions,
           SUM(CASE WHEN q.verify IS NOT NULL AND q.verify <> '' THEN 1 ELSE 0 END) AS verifiable,
           (SELECT COUNT(*) FROM dependencies d
             JOIN topics t2 ON t2.id = d.topic_id
            WHERE t2.chapter = t.chapter AND t2.volume = t.volume) AS edges
    FROM topics t LEFT JOIN questions q ON q.topic_id = t.id
    GROUP BY t.volume, t.chapter
    ORDER BY MIN(t.grade), MIN(t.id)
    """
    return [dict(row) for row in conn.execute(sql)]


def coverage_gaps(conn, min_questions: int = 2) -> list:
    sql = """
    SELECT t.id, t.name, COUNT(q.id) AS questions
    FROM topics t LEFT JOIN questions q ON q.topic_id = t.id
    GROUP BY t.id HAVING questions < ?
    ORDER BY questions, t.id
    """
    return [dict(row) for row in conn.execute(sql, (min_questions,))]
