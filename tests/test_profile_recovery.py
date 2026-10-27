"""Synthetic recovery drills: loss of cookies, reinstall, corruption and isolation."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from cnkg.learning_store import LearningStore, SCHEMA_VERSION, _json
from cnkg import profile_recovery as recovery
from cnkg.profile_recovery import ProfileRecovery, ProfileRecoveryError, ProfileConflictError


NOW = "2026-10-01T08:00:00+00:00"
OWNER = "a" * 64
OTHER = "b" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>frozen image</title><path d="M0 0 L10 10"/></svg>'
ASSET = "/static/diagrams/frozen.svg"


def session(identity="original-session", owner=OWNER):
    return {"id": identity, "owner_id": owner, "kind": "unit", "unit_id": "old-unit",
            "mode": "practice", "created_at": NOW, "updated_at": NOW,
            "content_sha256": "c" * 64, "resource_version": "legacy-frozen-v1",
            "answers": [{"question_id": "q1", "status": "incorrect", "raw_answer": "13",
                         "answered_at": NOW, "objective_ids": ["o1"], "skill_ids": ["s1"]}],
            "presentations": {"q1": {"presented_at": NOW}}, "feedback_exposed": [],
            "task_metadata": {"q1": {"family_id": "old-family", "family_status": "declared",
                                      "representation": "number_line", "objective_ids": ["o1"], "skill_ids": ["s1"]}},
            "unit_snapshot": {"stage": "primary", "objectives": [{"id": "o1"}], "tasks": [
                {"id": "q1", "family_id": "old-family", "prompt": "一个旧版本任务", "answer": "SECRET_FROZEN_REFERENCE",
                 "diagram": {"src": ASSET, "alt": "旧图"}},
                {"id": "q2", "prompt": "尚未呈现的任务", "answer": "UNSEEN_FROZEN_ANSWER"}]},
            "asset_snapshots": {ASSET: {"sha256": hashlib.sha256(SVG).hexdigest(),
                                        "body_base64": base64.b64encode(SVG).decode()}}}


def work_payload():
    return {"kind": "session_task", "source_ref": {"session_id": "original-session", "question_id": "q1"},
            "resource_version": "legacy-frozen-v1", "resource_snapshot": {
                "id": "q1", "prompt": "说明旧版本方法", "criteria": ["说明理由", "检查单位"], "stage": "primary"},
            "draft": "原来的第一稿", "reflection": "暂时不能说明理由",
            "self_assessment": [{"criterion_index": 0, "status": "revisit"}]}


class ProfileRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LearningStore(Path(self.tmp.name) / "source.sqlite")
        self.profiles = ProfileRecovery(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def new_install(self, name="target"):
        store = LearningStore(Path(self.tmp.name) / (name + ".sqlite"))
        return store, ProfileRecovery(store)

    def seed(self):
        record = session()
        self.store.save(record, [{"event_type": "question_presented", "occurred_at": NOW, "question_id": "q1"}])
        # Noncanonical whitespace is intentional: restore must preserve original
        # JSON strings, not reconstruct historical evidence from summaries.
        record.pop("_revision")
        with self.store._connect() as db:
            db.execute("UPDATE sessions SET record_json=? WHERE id=?", (json.dumps(record, ensure_ascii=False, indent=2), record["id"]))
        first = work_payload()
        self.store.save_work(OWNER, "work-at-old-version", first, 0, "first-request")
        second = copy.deepcopy(first)
        second.update(draft="保留单位，继续修订理由", reflection="先列出实际条件")
        self.store.save_work(OWNER, "work-at-old-version", second, 1, "second-request")
        for kind in ("learning_need_deferred", "learning_need_reopened"):
            self.store.add_event(OWNER, {"event_type": kind, "occurred_at": NOW,
                "resource_id": "need-stable-id", "payload": {"need_id": "need-stable-id", "reason": "稍后继续",
                "source_ref": {"session_id": "original-session", "question_id": "q1"},
                "resource_version": "legacy-frozen-v1"}}, "original-session")
        with self.store._connect() as db:
            db.execute("INSERT INTO exposures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (OWNER, "question", "legacy-without-event", "unknown-legacy-time", NOW,
                        2, 1, 0, 3, 0, NOW, None, "old-content", 0))
        self.store.save(session("other-session", OTHER))
        self.store.save_work(OTHER, "other-work", {**first, "source_ref": "other-resource", "draft": "OTHER_OWNER_PRIVATE"}, 0, "first-request")
        credentials = self.profiles.enable(OWNER, "演示档案")
        return credentials, first

    def dump(self, store):
        with store._connect() as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in (*recovery._TABLE_COLUMNS, "learning_profiles", "profile_cookies")}

    def original_rows(self, store, owner=OWNER):
        with store._connect() as db:
            return {table: [dict(row) for row in db.execute(
                f"SELECT {','.join(columns)} FROM {table} WHERE owner_id=? ORDER BY {recovery._TABLE_ORDER[table]}", (owner,))]
                for table, columns in recovery._TABLE_COLUMNS.items()}

    def test_cookie_loss_recovery_and_explicit_rotation_keep_records_private(self):
        self.store.save(session())
        original = self.original_rows(self.store)
        self.assertEqual(self.profiles.resolve_owner(OWNER), OWNER)
        status = self.profiles.status(OWNER)
        self.assertFalse(status["enabled"])
        self.assertEqual(status["switch_protection"], {"has_records": True, "requirement": "enable_recovery"})
        self.assertIsNone(status["backup_health"]["verified_file"])
        enabled = self.profiles.enable(OWNER)
        self.assertIsNone(self.profiles.resolve_owner(OWNER))
        self.assertEqual(self.profiles.resolve_owner(enabled["cookie_token"]), OWNER)
        public = json.dumps(enabled["profile"])
        for private in (OWNER, enabled["cookie_token"], enabled["recovery_code"], "recovery_hash"):
            self.assertNotIn(private, public)
        with self.store._connect() as db:
            hashes = " ".join(str(tuple(row)) for row in db.execute("SELECT * FROM learning_profiles"))
            hashes += " ".join(str(tuple(row)) for row in db.execute("SELECT * FROM profile_cookies"))
            self.assertNotIn(enabled["recovery_code"], hashes)
            self.assertNotIn(enabled["cookie_token"], hashes)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        # A new service instance represents another browser after site data loss.
        recovered = ProfileRecovery(self.store).recover(enabled["recovery_code"].lower().replace("-", " "))
        self.assertEqual(self.profiles.resolve_owner(recovered["cookie_token"]), OWNER)
        self.assertIsNone(self.profiles.resolve_owner(enabled["cookie_token"]))
        with self.assertRaises(ProfileConflictError):
            self.profiles.enable(OWNER)
        rotated = self.profiles.rotate(OWNER)
        self.assertIsNone(self.profiles.resolve_owner(recovered["cookie_token"]))
        with self.assertRaises(ProfileRecoveryError):
            self.profiles.recover(enabled["recovery_code"])
        self.assertEqual(self.profiles.resolve_owner(rotated["cookie_token"]), OWNER)
        self.assertEqual(original, self.original_rows(self.store))
        self.assertEqual(self.profiles.resolve_owner(OTHER), OTHER)
        for token in (None, "", "not-a-cookie", "../" + OWNER):
            self.assertIsNone(self.profiles.resolve_owner(token))

    def test_complete_new_install_roundtrip_preserves_json_images_versions_and_retries(self):
        credentials, first = self.seed()
        original = self.original_rows(self.store)
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        encoded = json.dumps(envelope)
        for secret in (OWNER, "SECRET_FROZEN_REFERENCE", "UNSEEN_FROZEN_ANSWER", "OTHER_OWNER_PRIVATE",
                       credentials["recovery_code"], "原来的第一稿"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(set(envelope), {"format", "version", "cipher", "salt", "nonce", "ciphertext"})
        target, profiles = self.new_install()
        receipt = profiles.restore(encoded.encode(), credentials["recovery_code"])
        self.assertEqual(self.original_rows(target), original)
        self.assertEqual(target.records(OTHER), [])
        self.assertEqual(target.count_works(OTHER), 0)
        self.assertEqual(profiles.resolve_owner(receipt["cookie_token"]), OWNER)
        self.assertIsNone(profiles.resolve_owner(OWNER))
        self.assertNotIn(OWNER, json.dumps(receipt))
        self.assertEqual(receipt["counts"]["work_revisions"], 2)
        self.assertEqual(receipt["counts"]["events_reindexed"], 0)
        restored = target.load("original-session", OWNER)
        self.assertEqual(restored["answers"][0]["status"], "incorrect")
        self.assertEqual(restored["resource_version"], "legacy-frozen-v1")
        self.assertEqual(base64.b64decode(restored["asset_snapshots"][ASSET]["body_base64"]), SVG)
        self.assertEqual(restored["unit_snapshot"]["tasks"][1]["answer"], "UNSEEN_FROZEN_ANSWER")
        self.assertEqual(target.need_states(OWNER)[0]["status"], "open")
        self.assertEqual(target.works(OWNER)[0]["unresolved_criterion_indices"], [0, 1])
        replay = target.save_work(OWNER, "work-at-old-version", first, 0, "first-request")
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(self.original_rows(target), original)
        # A second installation can restore a backup made from the first import.
        second, second_profiles = self.new_install("second")
        second_profiles.restore(profiles.backup(OWNER, credentials["recovery_code"]), credentials["recovery_code"])
        self.assertEqual(self.original_rows(second), original)

    def test_wrong_owner_code_cannot_export_and_wrong_code_or_tampering_cannot_import(self):
        credentials, _ = self.seed()
        other = self.profiles.enable(OTHER)
        with self.assertRaises(ProfileRecoveryError):
            self.profiles.backup(OWNER, other["recovery_code"])
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.new_install()
        before = self.dump(target)
        with self.assertRaisesRegex(ProfileRecoveryError, "恢复码不匹配"):
            profiles.restore(envelope, other["recovery_code"])
        for field in ("ciphertext", "nonce", "salt"):
            bad = copy.deepcopy(envelope)
            decoded = bytearray(base64.b64decode(bad[field]))
            decoded[0] ^= 1
            bad[field] = base64.b64encode(decoded).decode()
            with self.subTest(field=field), self.assertRaises(ProfileRecoveryError):
                profiles.restore(bad, credentials["recovery_code"])
            self.assertEqual(self.dump(target), before)
        for field, value in (("version", 2), ("version", True), ("cipher", "plaintext"), ("format", "readable_learning_record_not_database_backup")):
            bad = {**envelope, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ProfileRecoveryError):
                profiles.restore(bad, credentials["recovery_code"])
        self.assertEqual(self.dump(target), before)

    def test_occupied_install_offsets_only_event_indexes_and_never_merges_profiles(self):
        credentials, _ = self.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.new_install()
        target.save(session("already-present", OTHER), [{"event_type": "question_presented", "occurred_at": NOW, "question_id": "q1"}])
        other_rows = self.original_rows(target, OTHER)
        original = self.original_rows(self.store)
        receipt = profiles.restore(envelope, credentials["recovery_code"])
        actual = self.original_rows(target)
        self.assertEqual(self.original_rows(target, OTHER), other_rows)
        self.assertEqual(receipt["counts"]["events_reindexed"], len(original["events"]))
        for row in actual["events"]:
            row["seq"] -= 1
        for row in actual["exposures"]:
            if row["last_event_seq"]:
                row["last_event_seq"] -= 1
        self.assertEqual(actual, original)
        before = self.dump(target)
        with self.assertRaises(ProfileConflictError):
            profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(self.dump(target), before)

    def test_cross_owner_session_collision_or_existing_anonymous_owner_never_overwrites(self):
        credentials, _ = self.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        for name, record in (("collision", session(owner=OTHER)), ("anonymous", session("other-id", OWNER))):
            target, profiles = self.new_install(name)
            target.save(record)
            before = self.dump(target)
            with self.subTest(name=name), self.assertRaises(ProfileConflictError):
                profiles.restore(envelope, credentials["recovery_code"])
            self.assertEqual(self.dump(target), before)

    def test_late_failure_rolls_back_records_profiles_and_cookies_together(self):
        credentials, _ = self.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.new_install()
        before = self.dump(target)
        with mock.patch.object(ProfileRecovery, "_issue_cookie", side_effect=RuntimeError("simulated write failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated write failure"):
                profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(self.dump(target), before)
        profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(self.original_rows(target), self.original_rows(self.store))

    def test_authenticated_but_malformed_records_are_rejected_before_any_write(self):
        credentials, _ = self.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        secret = recovery._recovery_secret(credentials["recovery_code"])
        plaintext = recovery._decrypt(envelope, secret)
        target, profiles = self.new_install()
        before = self.dump(target)

        def duplicate_session(data):
            data["tables"]["sessions"].append(copy.deepcopy(data["tables"]["sessions"][0]))

        def bad_raw_record(data):
            row = data["tables"]["sessions"][0]
            record = json.loads(row["record_json"])
            record["owner_id"] = OTHER
            row["record_json"] = json.dumps(record)

        cases = [lambda d: d.update(learning_schema=SCHEMA_VERSION + 1),
                 lambda d: d["tables"]["events"][0].update(owner_id=OTHER),
                 lambda d: d["tables"]["events"][0].update(session_id="absent"),
                 lambda d: d["tables"]["work_requests"][0].update(request_sha256="0" * 64),
                 lambda d: d["tables"]["work_requests"][0].update(resource_id=[]),
                 lambda d: d["tables"]["work_revisions"].pop(),
                 lambda d: d["tables"]["work_requests"].clear(),
                 lambda d: d["tables"]["exposures"][0].update(last_event_seq=9_999_999),
                 lambda d: d["tables"]["exposures"][0].update(presentation_count=-1),
                 lambda d: d["tables"]["exposures"][0].update(entity_kind=[]),
                 duplicate_session, bad_raw_record]
        for index, mutate in enumerate(cases):
            data = copy.deepcopy(plaintext)
            mutate(data)
            with self.subTest(case=index), self.assertRaises(ProfileRecoveryError):
                profiles.restore(recovery._encrypt(data, secret), credentials["recovery_code"])
            self.assertEqual(self.dump(target), before)

    def test_corrupt_or_active_frozen_svg_is_rejected_without_rewriting_old_assets(self):
        credentials, _ = self.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        secret = recovery._recovery_secret(credentials["recovery_code"])
        plaintext = recovery._decrypt(envelope, secret)
        target, profiles = self.new_install()
        for raw, valid_hash in ((SVG + b" ", False), (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', True),
                                (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', True)):
            data = copy.deepcopy(plaintext)
            row = data["tables"]["sessions"][0]
            record = json.loads(row["record_json"])
            asset = record["asset_snapshots"][ASSET]
            asset["body_base64"] = base64.b64encode(raw).decode()
            if valid_hash:
                asset["sha256"] = hashlib.sha256(raw).hexdigest()
            row["record_json"] = _json(record)
            with self.assertRaises(ProfileRecoveryError):
                profiles.restore(recovery._encrypt(data, secret), credentials["recovery_code"])
            self.assertEqual(target.count(OWNER), 0)

    def test_self_check_nested_svg_roundtrip_and_active_import_rejection_are_atomic(self):
        credentials, _ = self.seed()
        resource_id = "self-check-with-frozen-figure"
        # A current lesson asset may later change; the saved work must retain
        # the exact figure that accompanied the student's earlier reasoning.
        current_asset = Path(self.tmp.name) / "current-self-check.svg"
        current_asset.write_bytes(SVG)
        frozen = current_asset.read_bytes()
        payload = {
            "kind": "self_check", "source_ref": "topic:old-statistics:criterion:0",
            "resource_version": "old-self-check-version",
            "resource_snapshot": {
                "id": "old-statistics:0", "prompt": "说明当时图中的刻度",
                "criteria": ["逐格说明", "保留单位"], "stage": "primary",
                "diagram": {"src": ASSET, "alt": "保存作品时使用的图"},
                "asset_snapshots": {ASSET: {"sha256": hashlib.sha256(frozen).hexdigest(),
                    "body_base64": base64.b64encode(frozen).decode()}},
            },
            "draft": "每格两人", "reflection": "还需要核对单位",
            "self_assessment": [{"criterion_index": 1, "status": "revisit"}],
        }
        self.store.save_work(OWNER, resource_id, payload, 0, "nested-first")
        revised = copy.deepcopy(payload)
        revised["draft"] = "每格两人，从0刻度开始读"
        self.store.save_work(OWNER, resource_id, revised, 1, "nested-second")
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        original_rows = self.original_rows(self.store)
        current_asset.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><text>NEW FIGURE</text></svg>')
        target, profiles = self.new_install()
        profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(self.original_rows(target), original_rows)
        history = target.work_history(OWNER, resource_id)
        self.assertEqual(len(history), 2)
        for record in history:
            snapshot = record["payload"]["resource_snapshot"]
            self.assertEqual(snapshot["diagram"]["src"], ASSET)
            self.assertEqual(base64.b64decode(snapshot["asset_snapshots"][ASSET]["body_base64"]), frozen)
            self.assertNotEqual(frozen, current_asset.read_bytes())

        secret = recovery._recovery_secret(credentials["recovery_code"])
        plaintext = recovery._decrypt(envelope, secret)
        occupied, occupied_profiles = self.new_install("occupied-nested-target")
        occupied.save(session("other-preserved", OTHER))
        current = occupied_profiles.enable(OTHER)
        before = self.dump(occupied)
        cases = [(SVG + b" ", False, "图示内容校验失败"),
                 (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', True, "活动内容")]
        for raw, matching_hash, message in cases:
            data = copy.deepcopy(plaintext)
            revisions = {}
            for table in ("works", "work_revisions"):
                for row in data["tables"][table]:
                    if row["resource_id"] != resource_id:
                        continue
                    record = json.loads(row["record_json"])
                    asset = record["payload"]["resource_snapshot"]["asset_snapshots"][ASSET]
                    asset["body_base64"] = base64.b64encode(raw).decode()
                    if matching_hash:
                        asset["sha256"] = hashlib.sha256(raw).hexdigest()
                    row["record_json"] = _json(record)
                    revisions[row["revision"]] = record
            # Keep surrounding history/request contracts internally consistent,
            # so the refusal specifically tests the nested SVG validator.
            for row in data["tables"]["work_requests"]:
                if row["resource_id"] == resource_id:
                    row["request_sha256"] = hashlib.sha256(_json({
                        "payload": revisions[row["revision"]]["payload"],
                        "expected_revision": row["revision"] - 1}).encode()).hexdigest()
            with self.subTest(message=message), self.assertRaisesRegex(ProfileRecoveryError, message):
                occupied_profiles.restore(recovery._encrypt(data, secret), credentials["recovery_code"],
                                          current_owner=OTHER, current_recovery_code=current["recovery_code"])
            self.assertEqual(self.dump(occupied), before)
            self.assertEqual(occupied_profiles.resolve_owner(current["cookie_token"]), OTHER)

    def test_support_session_snapshot_is_preserved_and_mismatch_is_rejected(self):
        record = session()
        record.update(kind="support", mode="support", task_ids=["q1"], index=0,
                      question_snapshots={"q1": {"id": "q1", "answer": "frozen-answer"}})
        snapshot = {"support": {"id": "support-one"}, "questions": record["question_snapshots"],
                    "metadata": record["task_metadata"], "unit_version": record["resource_version"]}
        record.update(support_snapshot=snapshot, content_sha256=hashlib.sha256(_json(snapshot).encode()).hexdigest())
        self.store.save(record)
        credentials = self.profiles.enable(OWNER)
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.new_install()
        profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(self.original_rows(target), self.original_rows(self.store))
        secret = recovery._recovery_secret(credentials["recovery_code"])
        data = recovery._decrypt(envelope, secret)
        stored = json.loads(data["tables"]["sessions"][0]["record_json"])
        stored["question_snapshots"]["q1"]["answer"] = "changed-answer"
        data["tables"]["sessions"][0]["record_json"] = _json(stored)
        another, another_profiles = self.new_install("another")
        with self.assertRaisesRegex(ProfileRecoveryError, "快照与内容版本不一致"):
            another_profiles.restore(recovery._encrypt(data, secret), credentials["recovery_code"])
        self.assertEqual(another.count(OWNER), 0)

    def test_all_rows_are_backed_up_beyond_ui_pagination_and_limits_fail_explicitly(self):
        self.store.save(session())
        with self.store._connect() as db:
            db.executemany("""INSERT INTO events (owner_id, session_id, event_type, occurred_at, payload_json)
                VALUES (?, ?, ?, ?, ?)""", [(OWNER, "original-session", "legacy_note", NOW, _json({"index": index})) for index in range(1005)])
        credentials = self.profiles.enable(OWNER)
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.new_install()
        receipt = profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(receipt["counts"]["events"], 1005)
        self.assertEqual(target.events(OWNER)[-1]["payload"], {"index": 1004})
        last_backup = self.profiles.status(OWNER)["last_backup_at"]
        with mock.patch.object(recovery, "MAX_BACKUP_BYTES", 1024):
            with self.assertRaises(ProfileRecoveryError):
                self.profiles.backup(OWNER, credentials["recovery_code"])
        self.assertEqual(self.profiles.status(OWNER)["last_backup_at"], last_backup)
        with mock.patch.object(recovery, "MAX_TABLE_ROWS", 1000):
            with self.assertRaises(ProfileRecoveryError):
                self.profiles.backup(OWNER, credentials["recovery_code"])

    def test_parsing_size_depth_duplicate_keys_and_nonfinite_values_are_bounded(self):
        credentials = self.profiles.enable(OWNER)
        target, profiles = self.new_install()
        for raw in (b'{"version":1,"version":2}', b'{"x":NaN}', b'{"x":1e999}',
                    b'\xff', b'[' * 102 + b'0' + b']' * 102):
            with self.subTest(raw=raw[:50]), self.assertRaises(ProfileRecoveryError):
                profiles.restore(raw, credentials["recovery_code"])
        with mock.patch.object(recovery, "MAX_ENVELOPE_BYTES", 100):
            with self.assertRaises(ProfileRecoveryError):
                profiles.restore(" " * 101, credentials["recovery_code"])
        self.assertEqual(target.count(OWNER), 0)

    def test_code_rotation_does_not_make_old_backups_depend_on_a_server_secret(self):
        self.store.save(session())
        credentials = self.profiles.enable(OWNER)
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        newer = self.profiles.rotate(OWNER)
        target, profiles = self.new_install()
        with self.assertRaises(ProfileRecoveryError):
            profiles.restore(envelope, newer["recovery_code"])
        profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(target.load("original-session", OWNER)["resource_version"], "legacy-frozen-v1")

    def test_missing_crypto_fails_explicitly_and_empty_profiles_still_roundtrip(self):
        credentials = self.profiles.enable(OWNER)
        with mock.patch.object(recovery, "_crypto", side_effect=recovery.ProfileDependencyError("安装固定版本依赖")):
            with self.assertRaises(recovery.ProfileDependencyError):
                self.profiles.backup(OWNER, credentials["recovery_code"])
        self.assertIsNone(self.profiles.status(OWNER)["last_backup_at"])
        target, profiles = self.new_install()
        receipt = profiles.restore(self.profiles.backup(OWNER, credentials["recovery_code"]), credentials["recovery_code"])
        self.assertTrue(receipt["profile"]["enabled"])
        self.assertEqual(sum(receipt["counts"].values()), 0)

    def test_concurrent_restore_creates_exactly_one_profile_without_partial_copy(self):
        credentials, _ = self.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.new_install()

        def attempt(_):
            try:
                profiles.restore(envelope, credentials["recovery_code"])
                return "restored"
            except ProfileConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, range(2))), ["restored", "conflict"])
        self.assertEqual(self.original_rows(target), self.original_rows(self.store))


if __name__ == "__main__":
    unittest.main()
