"""Recoverable local profiles and encrypted, complete learning-record backups.

The recovery code is a random 256-bit credential, never a chosen password. Only
its hash is stored. Once enabled, an owner ID is no longer a browser credential:
HTTP handlers must resolve the separate cookie alias and keep ``cookie_token``
out of JSON responses and logs. Recovery rotates aliases, without editing any
learning evidence.

Unlike the readable learning export, this backup contains original frozen
snapshots (including unreleased tasks), images, work revisions and request IDs.
It is always AES-GCM encrypted. Import is one transaction into an absent profile;
it never merges or overwrites records. Historical JSON is copied byte for byte.
Only derived summaries are rebuilt. Event sequence numbers are local database
indexes: an occupied destination offsets them in order and updates the exposure
index references, preserving original timestamps and event payloads.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
from xml.etree import ElementTree

from .learning_store import (
    SCHEMA_VERSION, WORK_KINDS, _IMMUTABLE_WORK_FIELDS,
    _json, _now, _scope, _session_summary, _work_summary,
)
from .review_timing import qualified_review

BACKUP_FORMAT = "cnkg-encrypted-learning-backup"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 50 * 1024 * 1024
MAX_ENVELOPE_BYTES = 70 * 1024 * 1024
MAX_TABLE_ROWS = 100_000
CAPACITY_NOTICE_RATIO = 0.8
ASSET_DIGEST_VERSION = 1
_MAX_INTEGER = 2**63 - 1
_OWNER = re.compile(r"[0-9a-f]{64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ASSET_PATH = re.compile(r"/static/diagrams/[A-Za-z0-9][A-Za-z0-9_.-]*\.svg\Z")
_SVG_TAGS = frozenset({"svg", "g", "path", "line", "rect", "circle", "ellipse", "polyline",
                       "polygon", "text", "tspan", "defs", "marker", "pattern", "clipPath",
                       "mask", "linearGradient", "radialGradient", "stop", "title", "desc",
                       "symbol", "use", "style"})
_TABLE_COLUMNS = {
    "sessions": ("id", "owner_id", "created_at", "updated_at", "revision", "record_json"),
    "events": ("seq", "owner_id", "session_id", "event_type", "occurred_at", "resource_id",
               "question_id", "content_sha256", "payload_json"),
    "works": ("owner_id", "resource_id", "revision", "created_at", "updated_at", "resource_version", "record_json"),
    "work_revisions": ("owner_id", "resource_id", "revision", "saved_at", "record_json"),
    "work_requests": ("owner_id", "resource_id", "request_id", "request_sha256", "revision"),
    "exposures": ("owner_id", "entity_kind", "entity_id", "first_seen_at", "last_seen_at",
                  "presentation_count", "answer_count", "feedback_count", "material_count", "reference_count",
                  "last_material_at", "last_reference_at", "last_content_sha256", "last_event_seq"),
}
_TABLE_ORDER = {"sessions": "created_at, id", "events": "seq", "works": "resource_id",
                "work_revisions": "resource_id, revision", "work_requests": "resource_id, request_id",
                "exposures": "entity_kind, entity_id"}


class ProfileRecoveryError(ValueError):
    """A credential or backup is invalid; messages never include credentials."""


class ProfileConflictError(ProfileRecoveryError):
    """An operation would replace an existing profile or learning record."""


class ProfileDependencyError(RuntimeError):
    """The supported authenticated-encryption implementation is unavailable."""


def _require(condition, message="备份结构或记录关联无效"):
    if not condition:
        raise ProfileRecoveryError(message)


def _text(value, *, optional=False, maximum=512):
    if value is None and optional:
        return
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum)


def _integer(value, minimum=0):
    _require(isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= _MAX_INTEGER)


def _owner(value):
    _require(isinstance(value, str) and _OWNER.fullmatch(value), "学习档案标识无效")
    return value


def _label(value):
    _text(value, maximum=80)
    _require(not any(ord(char) < 32 for char in value), "档案名称不能含控制字符")
    return value.strip()


def _bounded_json(value, limit):
    chunks, size = [], 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            _require(size <= limit, "备份超过大小限制；未生成或导入不完整的备份")
            chunks.append(encoded)
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, ProfileRecoveryError):
            raise
        raise ProfileRecoveryError("备份必须为有效且层级有限的 JSON") from None
    return b"".join(chunks)


def _strict_json(value, limit=MAX_BACKUP_BYTES):
    def pairs(items):
        result = {}
        for key, item in items:
            _require(key not in result, "备份 JSON 含重复字段")
            result[key] = item
        return result

    def constant(_):
        raise ProfileRecoveryError("备份 JSON 含无效数值")

    _require(isinstance(value, (str, bytes)), "备份必须为 JSON 文本")
    _require(len(value) <= limit, "备份超过大小限制")
    try:
        if isinstance(value, str):
            value = value.encode("utf-8")
        _require(len(value) <= limit, "备份超过大小限制")
        result = json.loads(value.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, ProfileRecoveryError):
            raise
        raise ProfileRecoveryError("备份不是有效的 UTF-8 JSON") from None
    stack = [(result, 0)]
    while stack:
        item, depth = stack.pop()
        _require(depth <= 100, "备份 JSON 层级过深")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float):
            _require(math.isfinite(item), "备份 JSON 含无效数值")
    return result


def _recovery_secret(code):
    _require(isinstance(code, str) and len(code) <= 256, "恢复码格式无效")
    compact = re.sub(r"[\s-]", "", code).upper()
    _require(compact.startswith("CNKG") and re.fullmatch(r"[A-Z2-7]{52}", compact[4:]), "恢复码格式无效")
    try:
        raw = base64.b32decode(compact[4:] + "====")
    except (ValueError, binascii.Error):
        raise ProfileRecoveryError("恢复码格式无效") from None
    _require(len(raw) == 32 and base64.b32encode(raw).decode("ascii").rstrip("=") == compact[4:], "恢复码格式无效")
    return raw


def _new_code():
    raw = secrets.token_bytes(32)
    encoded = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "CNKG-" + "-".join(encoded[index:index + 8] for index in range(0, len(encoded), 8)), raw


def _recovery_hash(secret):
    return hashlib.sha256(b"cnkg-profile-recovery-v1\0" + secret).hexdigest()


def _cookie_hash(token):
    return hashlib.sha256(b"cnkg-profile-cookie-v1\0" + token.encode("ascii")).hexdigest()


def _crypto():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError:
        raise ProfileDependencyError("加密备份需要 cryptography；请按项目安装说明安装固定版本依赖") from None
    return AESGCM, HKDF, hashes, InvalidTag


def _key(secret, salt, HKDF, hashes):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b"cnkg-learning-backup-v1").derive(secret)


def _base64(value, maximum, length=None):
    _require(isinstance(value, str) and len(value) <= maximum, "加密备份字段无效")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ProfileRecoveryError("加密备份字段无效") from None
    _require(base64.b64encode(result).decode("ascii") == value, "加密备份字段无效")
    if length is not None:
        _require(len(result) == length, "加密备份字段无效")
    return result


def _encrypt(data, secret):
    AESGCM, HKDF, hashes, _ = _crypto()
    plaintext = _bounded_json(data, MAX_BACKUP_BYTES)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    envelope = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "cipher": "AES-256-GCM",
                "salt": base64.b64encode(salt).decode("ascii"), "nonce": base64.b64encode(nonce).decode("ascii")}
    ciphertext = AESGCM(_key(secret, salt, HKDF, hashes)).encrypt(nonce, plaintext, _json(envelope).encode())
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    _bounded_json(envelope, MAX_ENVELOPE_BYTES)
    return envelope


def _decrypt(envelope, secret):
    if isinstance(envelope, (str, bytes)):
        envelope = _strict_json(envelope, MAX_ENVELOPE_BYTES)
    else:
        _bounded_json(envelope, MAX_ENVELOPE_BYTES)
    _require(isinstance(envelope, dict) and set(envelope) == {
        "format", "version", "cipher", "salt", "nonce", "ciphertext"}, "这不是可恢复的加密备份；可读学习记录不能用于恢复")
    _require(envelope["format"] == BACKUP_FORMAT, "这不是可恢复的加密备份")
    _require(type(envelope["version"]) is int and envelope["version"] == BACKUP_VERSION,
             "不支持此备份版本；请保留原文件和对应恢复码，使用生成备份时或明确支持该版本的程序。不要手改版本号；当前记录未改变")
    _require(envelope["cipher"] == "AES-256-GCM", "不支持此备份的加密格式")
    salt = _base64(envelope["salt"], 24, 16)
    nonce = _base64(envelope["nonce"], 16, 12)
    ciphertext = _base64(envelope["ciphertext"], 4 * ((MAX_BACKUP_BYTES + 18) // 3))
    _require(16 <= len(ciphertext) <= MAX_BACKUP_BYTES + 16, "加密备份大小无效")
    header = {key: value for key, value in envelope.items() if key != "ciphertext"}
    AESGCM, HKDF, hashes, InvalidTag = _crypto()
    try:
        plaintext = AESGCM(_key(secret, salt, HKDF, hashes)).decrypt(nonce, ciphertext, _json(header).encode())
    except InvalidTag:
        raise ProfileRecoveryError("恢复码不匹配，或备份文件已损坏") from None
    return _strict_json(plaintext)


def _validate_assets(record):
    """Frozen SVG bytes remain unchanged, but imported active content is refused."""
    collections = []
    if "asset_snapshots" in record:
        collections.append(record["asset_snapshots"])
    if isinstance(record.get("graph_snapshot"), dict) and "assets" in record["graph_snapshot"]:
        collections.append(record["graph_snapshot"]["assets"])
    for assets in collections:
        _require(isinstance(assets, dict))
        for path, asset in assets.items():
            _require(isinstance(path, str) and _ASSET_PATH.fullmatch(path))
            _require(isinstance(asset, dict) and set(asset) == {"sha256", "body_base64"})
            _require(isinstance(asset["sha256"], str) and _SHA256.fullmatch(asset["sha256"]))
            raw = _base64(asset["body_base64"], 4 * ((512_000 + 2) // 3))
            _require(len(raw) <= 512_000 and hashlib.sha256(raw).hexdigest() == asset["sha256"], "保存的图示内容校验失败")
            try:
                svg_text = raw.decode("utf-8")
                _require("<!DOCTYPE" not in svg_text.upper() and "<!ENTITY" not in svg_text.upper(), "备份图示含不允许的活动内容")
                svg = ElementTree.fromstring(svg_text)
            except (ElementTree.ParseError, UnicodeError, ValueError) as exc:
                if isinstance(exc, ProfileRecoveryError):
                    raise
                raise ProfileRecoveryError("备份图示不是有效 SVG") from None
            _require(svg.tag in {"svg", "{http://www.w3.org/2000/svg}svg"}, "备份图示不是 SVG")
            for element in svg.iter():
                tag = element.tag.removeprefix("{http://www.w3.org/2000/svg}")
                _require(tag in _SVG_TAGS, "备份图示含不允许的活动内容")
                for name, value in element.attrib.items():
                    name = name.rsplit("}", 1)[-1].lower()
                    _require(not name.startswith("on") and (name != "href" or re.fullmatch(r"#[A-Za-z0-9_.:-]+", value)),
                             "备份图示含不允许的活动内容")
                    _validate_svg_style(value)
                if tag == "style":
                    _validate_svg_style(element.text or "")


def _validate_svg_style(value):
    # Static local paint references are allowed; external CSS, escapes and
    # resource loads are not needed by the educational diagram format.
    clean = re.sub(r"url\(\s*#[A-Za-z0-9_.:-]+\s*\)", "", value, flags=re.IGNORECASE)
    _require(not any(marker in clean.lower() for marker in ("url(", "@import", "javascript:", "expression(", "\\")),
             "备份图示含不允许的活动内容")


def _record(row, column="record_json"):
    result = _strict_json(row[column])
    _require(isinstance(result, dict))
    return result


def _validate_payload(data):
    _require(isinstance(data, dict) and set(data) == {"format", "schema", "learning_schema", "created_at", "profile", "tables"})
    _require(data["format"] == "cnkg-owner-records" and type(data["schema"]) is int and data["schema"] == 1,
             "不支持此备份内容版本；请保留原文件和恢复码，使用匹配版本的程序，不要手改版本号")
    _require(type(data["learning_schema"]) is int and data["learning_schema"] == SCHEMA_VERSION,
             "备份的学习记录版本与当前程序不兼容；请保留原文件和对应恢复码，使用生成备份时或明确支持该版本的程序。当前记录未改变")
    _text(data["created_at"], maximum=128)
    profile = data["profile"]
    _require(isinstance(profile, dict) and set(profile) == {"owner_id", "label", "created_at"})
    owner = _owner(profile["owner_id"])
    _require(_label(profile["label"]) == profile["label"])
    _text(profile["created_at"], maximum=128)
    tables = data["tables"]
    _require(isinstance(tables, dict) and set(tables) == set(_TABLE_COLUMNS))
    for table, columns in _TABLE_COLUMNS.items():
        rows = tables[table]
        _require(isinstance(rows, list) and len(rows) <= MAX_TABLE_ROWS, "备份记录数量超过限制")
        for row in rows:
            _require(isinstance(row, dict) and set(row) == set(columns))
            _require(row["owner_id"] == owner, "备份包含不属于同一档案的记录")

    sessions, session_metadata = {}, {}
    for row in tables["sessions"]:
        _text(row["id"])
        _require(row["id"] not in sessions, "备份包含重复会话")
        _integer(row["revision"], 1)
        for key in ("created_at", "updated_at"):
            _text(row[key], maximum=128)
        record = _record(row)
        _require(record.get("id") == row["id"] and record.get("owner_id") == owner
                 and record.get("created_at") == row["created_at"]
                 and record.get("updated_at", record["created_at"]) == row["updated_at"])
        _require(isinstance(record.get("answers", []), list) and all(isinstance(item, dict) for item in record.get("answers", [])))
        for key in ("engine", "presentations", "unit_snapshot", "graph_snapshot", "question_snapshots", "context"):
            _require(key not in record or isinstance(record[key], dict))
        if record.get("kind") == "support":
            snapshot = record.get("support_snapshot")
            has_routing = isinstance(snapshot, dict) and "routing" in snapshot
            keys = {"support", "questions", "metadata", "unit_version"} | ({"routing"} if has_routing else set())
            _require(isinstance(snapshot, dict) and set(snapshot) == keys,
                     "指导会话缺少完整的冻结快照")
            _require(isinstance(snapshot["support"], dict) and isinstance(snapshot["questions"], dict)
                     and isinstance(snapshot["metadata"], dict))
            _require(hashlib.sha256(_json(snapshot).encode()).hexdigest() == record.get("content_sha256")
                     and record.get("question_snapshots") == snapshot["questions"]
                     and record.get("task_metadata") == snapshot["metadata"]
                     and record.get("resource_version") == snapshot["unit_version"], "指导会话快照与内容版本不一致")
            ids = record.get("task_ids")
            _require(isinstance(ids, list) and all(isinstance(item, str) for item in ids))
            _require(len(set(ids)) == len(ids) and set(snapshot["questions"]) == set(snapshot["metadata"]))
            if has_routing:
                from .instruction import route_problem
                routing = snapshot["routing"]
                _require(isinstance(routing, dict) and type(routing.get("schema")) is int and routing["schema"] == 1,
                         "指导分支版本不兼容；请保留原备份并使用匹配版本程序")
                _require(set(ids) <= set(snapshot["questions"]), "指导分支引用了没有冻结的候选任务")
                problem = route_problem(record)
                _require(problem is None, problem or "指导分支记录无效")
            else:
                _require(set(ids) == set(snapshot["questions"]))
            _integer(record.get("index"))
            _require(record["index"] <= len(ids))
        _validate_assets(record)
        try:
            metadata = (_scope(record), record.get("kind"), record.get("mode"), record.get("completed_at"),
                        record.get("content_sha256"), record.get("resource_version"), _json(_session_summary(record)),
                        len(record.get("answers", [])), int(qualified_review(record)))
        except (TypeError, ValueError, KeyError, AttributeError, IndexError):
            raise ProfileRecoveryError("会话快照结构无效；未改变原始记录") from None
        for value in metadata[:6]:
            _text(value, optional=True)
        sessions[row["id"]] = record
        session_metadata[row["id"]] = metadata

    event_sequences, previous = set(), 0
    for row in tables["events"]:
        _integer(row["seq"], 1)
        _require(row["seq"] > previous, "备份事件次序或标识无效")
        previous = row["seq"]
        event_sequences.add(row["seq"])
        _text(row["event_type"])
        _text(row["occurred_at"], maximum=128)
        for key in ("session_id", "resource_id", "question_id", "content_sha256"):
            _text(row[key], optional=True)
        _require(row["session_id"] is None or row["session_id"] in sessions, "备份事件引用缺失或不属于此档案的会话")
        _record(row, "payload_json")

    works, work_records, work_metadata, revisions, revision_numbers = {}, {}, {}, {}, {}
    for row in tables["works"]:
        _text(row["resource_id"])
        _text(row["resource_version"])
        _integer(row["revision"], 1)
        for key in ("created_at", "updated_at"):
            _text(row[key], maximum=128)
        _require(row["resource_id"] not in works, "备份包含重复作品")
        record = _validate_work_record(row, sessions)
        _require(all(record.get(key) == row[key] for key in ("created_at", "updated_at", "resource_version")))
        works[row["resource_id"]] = row
        work_records[row["resource_id"]] = record
        revision_numbers[row["resource_id"]] = []
        try:
            work_metadata[row["resource_id"]] = _json(_work_summary(record))
        except (TypeError, ValueError, KeyError, AttributeError, IndexError):
            raise ProfileRecoveryError("作品快照结构无效；未改变原始记录") from None

    for row in tables["work_revisions"]:
        _text(row["resource_id"])
        _integer(row["revision"], 1)
        _text(row["saved_at"], maximum=128)
        _require(row["resource_id"] in works, "作品历史缺少对应作品")
        record = _validate_work_record(row, sessions)
        current = work_records[row["resource_id"]]
        _require(record["updated_at"] == row["saved_at"] and record["created_at"] == current["created_at"])
        _require(_json({key: record["payload"].get(key) for key in _IMMUTABLE_WORK_FIELDS}) ==
                 _json({key: current["payload"].get(key) for key in _IMMUTABLE_WORK_FIELDS}), "作品历史的冻结来源或内容版本发生改变")
        key = (row["resource_id"], row["revision"])
        _require(key not in revisions, "备份包含重复作品修订")
        revisions[key] = record
        revision_numbers[row["resource_id"]].append(row["revision"])
        _require(row["revision"] <= current["revision"])
        if row["revision"] == current["revision"]:
            _require(row["record_json"] == works[row["resource_id"]]["record_json"], "作品最新修订与作品记录不一致")
    for identity, row in works.items():
        present = sorted(revision_numbers[identity])
        _require(len(present) == row["revision"] and present == list(range(1, row["revision"] + 1)), "作品修订历史不完整")

    requests, requested_revisions = set(), set()
    for row in tables["work_requests"]:
        _text(row["resource_id"])
        _text(row["request_id"])
        _integer(row["revision"], 1)
        key = (row["resource_id"], row["request_id"])
        _require(key not in requests, "备份包含重复保存请求")
        requests.add(key)
        revision_key = (row["resource_id"], row["revision"])
        _require(revision_key in revisions, "保存请求引用了缺失的作品修订")
        requested_revisions.add(revision_key)
        expected = hashlib.sha256(_json({"payload": revisions[revision_key]["payload"],
                                         "expected_revision": row["revision"] - 1}).encode()).hexdigest()
        _require(isinstance(row["request_sha256"], str) and hmac.compare_digest(expected, row["request_sha256"]),
                 "作品保存请求校验失败")
    _require(requested_revisions == set(revisions), "作品修订缺少原始保存请求")

    exposures = set()
    for row in tables["exposures"]:
        _require(isinstance(row["entity_kind"], str) and row["entity_kind"] in {"question", "family"})
        _text(row["entity_id"])
        key = (row["entity_kind"], row["entity_id"])
        _require(key not in exposures, "备份包含重复接触记录")
        exposures.add(key)
        for field in ("presentation_count", "answer_count", "feedback_count", "material_count", "reference_count", "last_event_seq"):
            _integer(row[field])
        _require(row["last_event_seq"] == 0 or row["last_event_seq"] in event_sequences, "接触记录引用了缺失的事件")
        for field in ("first_seen_at", "last_seen_at"):
            _text(row[field], maximum=128)
        for field in ("last_material_at", "last_reference_at", "last_content_sha256"):
            _text(row[field], optional=True)
    return owner, session_metadata, work_metadata


def _validate_work_record(row, sessions):
    record = _record(row)
    _require(record.get("schema") == 2 and record.get("evidence_type") == "learner_reported")
    _require(record.get("resource_id") == row["resource_id"] and record.get("revision") == row["revision"])
    for key in ("created_at", "updated_at", "resource_version"):
        _text(record.get(key))
    payload = record.get("payload")
    _require(isinstance(payload, dict) and isinstance(payload.get("kind"), str) and payload["kind"] in WORK_KINDS)
    _require(payload.get("resource_version") == record["resource_version"])
    _require(isinstance(payload.get("resource_snapshot"), dict) and payload["resource_snapshot"])
    _validate_assets(payload["resource_snapshot"])
    source = payload.get("source_ref")
    if isinstance(source, str):
        _text(source)
    else:
        _require(isinstance(source, dict) and set(source) == {"session_id", "question_id"})
        for value in source.values():
            _text(value)
        _require(source["session_id"] in sessions, "作品来源引用了缺失或不属于此档案的会话")
    _bounded_json(payload, 2_000_000)
    return record


def _compatibility():
    return {"format": BACKUP_FORMAT, "backup_versions": [BACKUP_VERSION],
            "content_schemas": [1], "learning_schemas": [SCHEMA_VERSION],
            "cipher": "AES-256-GCM",
            "unsupported_action": "保留原文件和对应恢复码，使用生成备份时或明确支持该版本的程序；不要手改版本号。"}


def _envelope_size(payload_bytes):
    """Exact size of our compact JSON envelope; pretty-print whitespace is extra."""
    header = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "cipher": "AES-256-GCM",
              "salt": "A" * 24, "nonce": "A" * 16, "ciphertext": ""}
    return len(_json(header).encode("utf-8")) + 4 * ((payload_bytes + 16 + 2) // 3)


def _asset_inventory(rows_for_table, profile, created_at):
    """Hash every asset row, preserving raw historical JSON and semantic order.

    Database-local event sequence numbers change on import into an occupied
    installation. Only those indexes and their exposure references are mapped
    to an owner-local ordinal. No event content, timestamp or historical record
    is reinterpreted. The row stream is not truncated at the backup limits.
    """
    empty = {"format": "cnkg-owner-records", "schema": 1, "learning_schema": SCHEMA_VERSION,
             "created_at": created_at, "profile": profile,
             "tables": {table: [] for table in _TABLE_COLUMNS}}
    payload_bytes = len(_json(empty).encode("utf-8"))
    digest = hashlib.sha256(b"cnkg-owner-assets-v1\n")
    counts, table_digests, sequences = {}, {}, {}
    for table in _TABLE_COLUMNS:
        count, table_digest = 0, hashlib.sha256()
        for original in rows_for_table(table):
            row = dict(original)
            raw = _json(row).encode("utf-8")
            payload_bytes += len(raw) + int(count > 0)
            count += 1
            if table == "events":
                sequences[row["seq"]] = count
                row["seq"] = count
            elif table == "exposures" and row["last_event_seq"]:
                # Invalid existing references stay distinguishable; the actual
                # backup validator will refuse to call such a snapshot valid.
                row["last_event_seq"] = sequences.get(row["last_event_seq"],
                                                      {"missing_event": row["last_event_seq"]})
            canonical = _json(row).encode("utf-8")
            table_digest.update(str(len(canonical)).encode("ascii") + b":" + canonical)
        counts[table], table_digests[table] = count, table_digest.hexdigest()
        digest.update(table.encode("ascii") + b":" + table_digest.digest())
    return {"digest_version": ASSET_DIGEST_VERSION, "asset_digest": digest.hexdigest(),
            "counts": counts, "table_digests": table_digests, "payload_bytes": payload_bytes,
            "estimated_envelope_bytes": _envelope_size(payload_bytes)}


def _payload_inventory(data):
    def rows(table):
        columns = [key.strip() for key in _TABLE_ORDER[table].split(",")]
        return sorted(data["tables"][table], key=lambda row: tuple(row[key] for key in columns))
    return _asset_inventory(rows, data["profile"], data["created_at"])


def _database_inventory(db, owner, profile, created_at):
    identity = ({key: profile[key] for key in ("owner_id", "label", "created_at")} if profile is not None
                else {"owner_id": owner, "label": "我的学习档案", "created_at": created_at})
    def rows(table):
        return db.execute(f"SELECT {','.join(_TABLE_COLUMNS[table])} FROM {table} "
                          f"WHERE owner_id=? ORDER BY {_TABLE_ORDER[table]}", (owner,))
    return _asset_inventory(rows, identity, created_at)


def _capacity(inventory):
    ratios = [inventory["payload_bytes"] / MAX_BACKUP_BYTES,
              inventory["estimated_envelope_bytes"] / MAX_ENVELOPE_BYTES]
    ratios.extend(count / MAX_TABLE_ROWS for count in inventory["counts"].values())
    ratio = max(ratios)
    return {"status": "exceeded" if ratio > 1 else "near_limit" if ratio >= CAPACITY_NOTICE_RATIO else "within_limit",
            "payload_bytes": inventory["payload_bytes"],
            "estimated_envelope_bytes": inventory["estimated_envelope_bytes"],
            "payload_limit_bytes": MAX_BACKUP_BYTES, "envelope_limit_bytes": MAX_ENVELOPE_BYTES,
            "row_limit_per_table": MAX_TABLE_ROWS, "counts": inventory["counts"],
            "notice_ratio": CAPACITY_NOTICE_RATIO,
            "scope": "当前档案六类资产的完整快照；不会截取最近记录来伪装完整备份。"}


def _envelope_details(envelope):
    value = _strict_json(envelope, MAX_ENVELOPE_BYTES) if isinstance(envelope, (str, bytes)) else envelope
    compact = _bounded_json(value, MAX_ENVELOPE_BYTES)
    return len(compact), hashlib.sha256(compact).hexdigest()


class ProfileRecovery:
    """Internal owner-scoped service. The HTTP layer authenticates ``owner``."""

    def __init__(self, store):
        self.store = store
        with store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS learning_profiles (
                owner_id TEXT PRIMARY KEY, label TEXT NOT NULL,
                recovery_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, last_backup_at TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS profile_cookies (
                token_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                created_at TEXT NOT NULL, revoked_at TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS profile_cookies_owner ON profile_cookies(owner_id)")
            # Separate operational receipts: never alter the six learning-asset
            # tables or silently upgrade old generated backups to verified files.
            db.execute("""CREATE TABLE IF NOT EXISTS profile_backup_receipts (
                owner_id TEXT NOT NULL, kind TEXT NOT NULL, recorded_at TEXT NOT NULL,
                backup_created_at TEXT NOT NULL, digest_version INTEGER NOT NULL,
                asset_digest TEXT NOT NULL, counts_json TEXT NOT NULL, table_digests_json TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL, envelope_bytes INTEGER NOT NULL,
                envelope_sha256 TEXT NOT NULL, recovery_hash TEXT NOT NULL,
                PRIMARY KEY (owner_id, kind))""")

    @staticmethod
    def _public(row):
        if row is None:
            return {"enabled": False}
        return {"enabled": True, **{key: row[key] for key in ("label", "created_at", "updated_at", "last_backup_at")}}

    def status(self, owner):
        _owner(owner)
        with self.store._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM learning_profiles WHERE owner_id=?", (owner,)).fetchone()
            inventory = _database_inventory(db, owner, row, _now())
            has_records = any(inventory["counts"].values())
            receipts = {}
            for receipt in db.execute("SELECT * FROM profile_backup_receipts WHERE owner_id=?", (owner,)):
                comparable = receipt["digest_version"] == ASSET_DIGEST_VERSION
                same = comparable and hmac.compare_digest(receipt["asset_digest"], inventory["asset_digest"])
                counts = json.loads(receipt["counts_json"])
                table_digests = json.loads(receipt["table_digests_json"])
                receipts[receipt["kind"]] = {
                    "recorded_at": receipt["recorded_at"], "backup_created_at": receipt["backup_created_at"],
                    "freshness": "matches_current_assets" if same else "changed" if comparable else "unknown",
                    "matches_current_code": bool(row and hmac.compare_digest(row["recovery_hash"], receipt["recovery_hash"])),
                    "counts": counts,
                    "row_count_changes": {table: inventory["counts"][table] - counts.get(table, 0) for table in _TABLE_COLUMNS},
                    "changed_tables": [table for table in _TABLE_COLUMNS
                                       if not comparable or table_digests.get(table) != inventory["table_digests"][table]],
                    "payload_bytes": receipt["payload_bytes"], "envelope_bytes": receipt["envelope_bytes"],
                    "envelope_sha256": receipt["envelope_sha256"],
                }
            return {**self._public(row), "switch_protection": {
                "has_records": has_records,
                "requirement": ("verify_current_code" if row else "enable_recovery") if has_records else "none",
            }, "backup_health": {
                "generated": receipts.get("generated"), "verified_file": receipts.get("verified"),
                "restored_snapshot": receipts.get("restored"),
                "legacy_generation_at": row["last_backup_at"] if row and not receipts else None,
                "capacity": _capacity(inventory), "compatibility": _compatibility(),
                "notice": "生成成功不证明文件已保存。选取实际文件核验只能确认该文件此刻可解密、结构有效及所含记录；不证明将来异机恢复成功或学习效果。",
            }}

    @staticmethod
    def _save_receipt(db, owner, kind, data, envelope, secret, inventory=None):
        inventory = inventory or _payload_inventory(data)
        size, sha256 = _envelope_details(envelope)
        db.execute("""INSERT INTO profile_backup_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, kind) DO UPDATE SET
              recorded_at=excluded.recorded_at, backup_created_at=excluded.backup_created_at,
              digest_version=excluded.digest_version, asset_digest=excluded.asset_digest,
              counts_json=excluded.counts_json, table_digests_json=excluded.table_digests_json,
              payload_bytes=excluded.payload_bytes, envelope_bytes=excluded.envelope_bytes,
              envelope_sha256=excluded.envelope_sha256, recovery_hash=excluded.recovery_hash""",
            (owner, kind, _now(), data["created_at"], inventory["digest_version"], inventory["asset_digest"],
             _json(inventory["counts"]), _json(inventory["table_digests"]), inventory["payload_bytes"],
             size, sha256, _recovery_hash(secret)))

    @staticmethod
    def _has_records(db, owner):
        # A draft, a pending answer or a saved exposure is also a learning asset.
        # Looking only at completed sessions would strand those records.
        return any(db.execute(f"SELECT 1 FROM {table} WHERE owner_id=? LIMIT 1", (owner,)).fetchone()
                   is not None for table in _TABLE_COLUMNS)

    def _protect_switch(self, db, current_owner, target_owner, current_code):
        """Check inside the identity-changing transaction, never from a client flag.

        The HTTP layer always supplies its authenticated current owner. Direct
        service imports without a browser identity may omit it. Possession of a
        matching current code keeps a return path; merely having generated a code
        or checking a client-side box is insufficient.
        """
        if current_owner is None:
            return
        _owner(current_owner)
        if current_owner == target_owner or not self._has_records(db, current_owner):
            return
        row = db.execute("SELECT recovery_hash FROM learning_profiles WHERE owner_id=?", (current_owner,)).fetchone()
        if row is None:
            raise ProfileConflictError("当前档案已有学习记录。请先为当前档案创建并保存恢复码，再找回或导入另一档案。")
        try:
            secret = _recovery_secret(current_code)
        except ProfileRecoveryError:
            raise ProfileConflictError("请先保存当前档案的恢复码，再在切换保护栏重新输入当前恢复码；另一档案的恢复码不能代替它。") from None
        if not hmac.compare_digest(row["recovery_hash"], _recovery_hash(secret)):
            raise ProfileConflictError("切换保护栏的恢复码与当前档案不匹配。请保留当前档案访问方式后重试。")

    def resolve_owner(self, cookie_token):
        """Resolve hashed aliases; preserve pre-profile anonymous cookies."""
        if not isinstance(cookie_token, str) or not _OWNER.fullmatch(cookie_token):
            return None
        with self.store._connect() as db:
            row = db.execute("SELECT owner_id, revoked_at FROM profile_cookies WHERE token_hash=?",
                             (_cookie_hash(cookie_token),)).fetchone()
            if row is not None:
                return row["owner_id"] if row["revoked_at"] is None else None
            if db.execute("SELECT 1 FROM learning_profiles WHERE owner_id=?", (cookie_token,)).fetchone():
                return None
        return cookie_token

    @staticmethod
    def _issue_cookie(db, owner, now):
        db.execute("UPDATE profile_cookies SET revoked_at=? WHERE owner_id=? AND revoked_at IS NULL", (now, owner))
        for _ in range(8):
            token = secrets.token_hex(32)
            if token == owner:
                continue
            try:
                db.execute("INSERT INTO profile_cookies VALUES (?, ?, ?, NULL)", (_cookie_hash(token), owner, now))
                return token
            except sqlite3.IntegrityError:
                continue
        raise ProfileRecoveryError("无法生成新的浏览器凭据，请重试")

    def enable(self, owner, label="我的学习档案"):
        _owner(owner)
        label = _label(label)
        code, secret = _new_code()
        now = _now()
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM learning_profiles WHERE owner_id=?", (owner,)).fetchone():
                raise ProfileConflictError("此档案已启用恢复；如丢失恢复码，请明确选择更换恢复码")
            db.execute("INSERT INTO learning_profiles VALUES (?, ?, ?, ?, ?, NULL)",
                       (owner, label, _recovery_hash(secret), now, now))
            token = self._issue_cookie(db, owner, now)
            profile = self._public(db.execute("SELECT * FROM learning_profiles WHERE owner_id=?", (owner,)).fetchone())
        return {"profile": profile, "recovery_code": code, "cookie_token": token}

    def rotate(self, owner):
        _owner(owner)
        code, secret = _new_code()
        now = _now()
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("UPDATE learning_profiles SET recovery_hash=?, updated_at=? WHERE owner_id=?",
                                (_recovery_hash(secret), now, owner))
            if cursor.rowcount != 1:
                raise ProfileRecoveryError("请先为当前学习记录启用恢复")
            token = self._issue_cookie(db, owner, now)
            profile = self._public(db.execute("SELECT * FROM learning_profiles WHERE owner_id=?", (owner,)).fetchone())
        return {"profile": profile, "recovery_code": code, "cookie_token": token}

    def recover(self, code, *, current_owner=None, current_recovery_code=None):
        secret = _recovery_secret(code)
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM learning_profiles WHERE recovery_hash=?", (_recovery_hash(secret),)).fetchone()
            if row is None:
                raise ProfileRecoveryError("恢复码不匹配此安装中的档案；重装环境请导入加密备份")
            self._protect_switch(db, current_owner, row["owner_id"], current_recovery_code)
            token = self._issue_cookie(db, row["owner_id"], _now())
            profile = self._public(row)
        return {"profile": profile, "cookie_token": token}

    def backup(self, owner, code):
        _owner(owner)
        secret = _recovery_secret(code)
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM learning_profiles WHERE owner_id=?", (owner,)).fetchone()
            if row is None or not hmac.compare_digest(row["recovery_hash"], _recovery_hash(secret)):
                raise ProfileRecoveryError("恢复码与当前学习档案不匹配")
            data = {"format": "cnkg-owner-records", "schema": 1, "learning_schema": SCHEMA_VERSION,
                    "created_at": _now(), "profile": {key: row[key] for key in ("owner_id", "label", "created_at")}, "tables": {}}
            size = 0
            for table, columns in _TABLE_COLUMNS.items():
                rows = []
                for item in db.execute(f"SELECT {','.join(columns)} FROM {table} WHERE owner_id=? ORDER BY {_TABLE_ORDER[table]}", (owner,)):
                    _require(len(rows) < MAX_TABLE_ROWS, "备份记录数量超过限制；未生成不完整的备份")
                    value = dict(item)
                    size += len(_bounded_json(value, MAX_BACKUP_BYTES))
                    _require(size <= MAX_BACKUP_BYTES, "备份超过大小限制；未生成不完整的备份")
                    rows.append(value)
                data["tables"][table] = rows
            _validate_payload(data)
            envelope = _encrypt(data, secret)
            db.execute("UPDATE learning_profiles SET last_backup_at=? WHERE owner_id=?", (data["created_at"], owner))
            self._save_receipt(db, owner, "generated", data, envelope, secret)
        return envelope

    def verify_backup(self, envelope, code, *, current_owner):
        """Inspect an actual file without importing assets or switching identity.

        Only a successful check of the current profile updates its separate
        operational receipt. An older recovery code may decrypt an older file;
        that fact does not grant access to the current profile or rotate cookies.
        """
        _owner(current_owner)
        secret = _recovery_secret(code)
        data = _decrypt(envelope, secret)
        owner, _, _ = _validate_payload(data)
        inventory = _payload_inventory(data)
        size, sha256 = _envelope_details(envelope)
        same_owner, same_assets, same_code = owner == current_owner, None, None
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if same_owner:
                profile = db.execute("SELECT * FROM learning_profiles WHERE owner_id=?", (current_owner,)).fetchone()
                current = _database_inventory(db, current_owner, profile, _now())
                same_assets = hmac.compare_digest(inventory["asset_digest"], current["asset_digest"])
                same_code = bool(profile and hmac.compare_digest(profile["recovery_hash"], _recovery_hash(secret)))
                self._save_receipt(db, current_owner, "verified", data, envelope, secret, inventory)
        return {"verified": True, "backup_created_at": data["created_at"],
                "profile_label": data["profile"]["label"],
                "backup_version": BACKUP_VERSION, "content_schema": data["schema"],
                "learning_schema": data["learning_schema"], "counts": inventory["counts"],
                "payload_bytes": inventory["payload_bytes"], "envelope_bytes": size,
                "envelope_sha256": sha256, "matches_current_profile": same_owner,
                "matches_current_assets": same_assets, "matches_current_code": same_code,
                "assets_imported": False, "identity_changed": False,
                "notice": "文件已通过解密、完整性和记录关联检查；尚未向另一安装实际导入，也不表示学习目标已掌握。请保留文件及制作它时的恢复码。"}

    def restore(self, envelope, code, *, current_owner=None, current_recovery_code=None):
        secret = _recovery_secret(code)
        data = _decrypt(envelope, secret)
        owner, session_metadata, work_metadata = _validate_payload(data)
        tables = data["tables"]
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._protect_switch(db, current_owner, owner, current_recovery_code)
            if db.execute("SELECT 1 FROM learning_profiles WHERE owner_id=? OR recovery_hash=?",
                          (owner, _recovery_hash(secret))).fetchone():
                raise ProfileConflictError("此档案已存在；请使用恢复码打开原档案，导入不会覆盖或合并已有记录")
            for table in _TABLE_COLUMNS:
                if db.execute(f"SELECT 1 FROM {table} WHERE owner_id=? LIMIT 1", (owner,)).fetchone():
                    raise ProfileConflictError("此档案已有学习记录；导入不会覆盖或合并已有记录")
            for row in tables["sessions"]:
                if db.execute("SELECT 1 FROM sessions WHERE id=?", (row["id"],)).fetchone():
                    raise ProfileConflictError("备份会话标识与现有记录冲突；未覆盖任何档案")
            offset = db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
            if tables["events"]:
                _require(offset + tables["events"][-1]["seq"] <= _MAX_INTEGER, "目标数据库的事件序号已超出导入范围")
            self._insert_records(db, data, session_metadata, work_metadata, offset)
            now = _now()
            db.execute("INSERT INTO learning_profiles VALUES (?, ?, ?, ?, ?, ?)",
                       (owner, data["profile"]["label"], _recovery_hash(secret), data["profile"]["created_at"], now, data["created_at"]))
            token = self._issue_cookie(db, owner, now)
            profile = self._public(db.execute("SELECT * FROM learning_profiles WHERE owner_id=?", (owner,)).fetchone())
            self._save_receipt(db, owner, "restored", data, envelope, secret)
        counts = {table: len(rows) for table, rows in tables.items()}
        counts["events_reindexed"] = len(tables["events"]) if offset else 0
        return {"profile": profile, "cookie_token": token, "counts": counts}

    @staticmethod
    def _insert_records(db, data, session_metadata, work_metadata, offset):
        """Called inside the sole import transaction; never regrade or replay events."""
        for table in _TABLE_COLUMNS:
            rows = data["tables"][table]
            columns = _TABLE_COLUMNS[table]
            for original in rows:
                row = dict(original)
                values = tuple(row[column] for column in columns)
                if table == "sessions":
                    derived = ("scope_id", "kind", "mode", "completed_at", "content_sha256", "resource_version",
                               "summary_json", "answered_count", "review_interval_qualified")
                    names = columns + derived
                    values += session_metadata[row["id"]]
                elif table == "works":
                    names = columns + ("summary_json",)
                    values += (work_metadata[row["resource_id"]],)
                else:
                    names = columns
                    if table == "events":
                        row["seq"] += offset
                    elif table == "exposures" and row["last_event_seq"]:
                        row["last_event_seq"] += offset
                    values = tuple(row[column] for column in columns)
                db.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' for _ in names)})", values)
