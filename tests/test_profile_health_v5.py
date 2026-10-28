"""Backup freshness is exact asset equality, not a recent timestamp or download claim."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

from cnkg import learning_store
from cnkg import profile_recovery as recovery
from tests import test_profile_recovery as recovery_fixture

NOW, OWNER, OTHER, session = recovery_fixture.NOW, recovery_fixture.OWNER, recovery_fixture.OTHER, recovery_fixture.session


class ProfileHealthTests(unittest.TestCase):
    def setUp(self):
        self.fixture = recovery_fixture.ProfileRecoveryTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store, self.profiles = self.fixture.store, self.fixture.profiles

    def dump_all(self, store):
        result = self.fixture.dump(store)
        with store._connect() as db:
            result["profile_backup_receipts"] = [tuple(row) for row in db.execute(
                "SELECT * FROM profile_backup_receipts ORDER BY owner_id, kind")]
        return result

    def branch_fixture(self):
        from tests import test_instruction_v4 as instruction_fixture
        fixture = instruction_fixture.InstructionTest(methodName="runTest")
        fixture.setUp()
        fixture.support["diagnostic"]["choices"].append(
            {"id": "c", "text": "0/4", "feedback": "细分不会消去已经涂色的部分。"})
        fixture.support["guided_branches"] = [
            {"id": "numerator", "choice_ids": ["b"], "title": "把涂色部分一起细分",
             "rationale": "所选答案只把分母乘了2，先检查涂色部分。",
             "observation_note": "这条任务只观察能否把原有涂色份数同倍扩大。",
             "guided_task": {**copy.deepcopy(fixture.support["guided_task"]),
                 "prompt": "原来涂色2份，每份细分成3份，现有多少小份涂色？",
                 "answer": "6", "verify": "2*3", "explanation": "每一份涂色都细分成3小份，共2×3=6小份。"}},
            {"id": "preserve", "choice_ids": ["c"], "title": "保留已经涂色的部分",
             "rationale": "所选答案把涂色变成0，先检查细分前后保留什么。",
             "observation_note": "这里只确认细分仍保留涂色份数。",
             "guided_task": {**copy.deepcopy(fixture.support["guided_task"]),
                 "prompt": "原来涂色4份，每份细分成2份，现有多少小份涂色？",
                 "answer": "8", "verify": "4*2", "explanation": "原涂色还在，共4×2=8小份。"}},
        ]
        for key in ("default_route", "skip_route"):
            fixture.support[key] = {"title": "从同倍细分继续", "rationale": "先观察同一整体的细分。",
                                    "observation_note": "不把一次有帮助的回答当作独立掌握。"}
        fixture.support["skip_route"]["guided_task"] = copy.deepcopy(fixture.support["guided_task"])
        return fixture

    def test_branch_snapshots_restore_before_selection_after_selection_and_after_confirmation(self):
        from cnkg import instruction
        fixture = self.branch_fixture()
        self.assertEqual(instruction.check_supports(fixture.unit), [])
        self.store.save(fixture.origin)
        records = []
        for answered in (0, 1, 3):
            record = fixture.create()
            self.store.save(record)
            for index in range(answered):
                value = "b" if index == 0 else record["question_snapshots"][record["task_ids"][index]]["answer"]
                _, events = fixture.answer(record, value, 5 + index)
                self.store.save(record, events)
            self.assertLess(set(record["task_ids"]), set(record["support_snapshot"]["questions"]))
            self.assertIsNone(instruction.route_problem(record))
            records.append(record)
        credentials = self.profiles.enable(OWNER)
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.fixture.new_install()
        # Content or grading implementation may have changed since the saved
        # activity. Recovery must validate the frozen route, not regrade it.
        with mock.patch.object(instruction, "grader_version", return_value="later-grader"):
            profiles.restore(envelope, credentials["recovery_code"])
            self.assertTrue(profiles.verify_backup(envelope, credentials["recovery_code"], current_owner=OWNER)["matches_current_assets"])
        self.assertEqual(self.fixture.original_rows(target), self.fixture.original_rows(self.store))
        for record in records:
            restored = target.load(record["id"], OWNER)
            self.assertEqual(restored, record)
            self.assertEqual(len(restored["support_snapshot"]["questions"]), 6)
        final = records[-1]["answers"][-1]
        self.assertTrue(final["confirmation_eligible"])
        self.assertEqual(final["resolution_for_need_ids"], [fixture.need["need_id"]])

    def test_branch_tampering_or_legacy_candidate_subset_is_rejected_atomically(self):
        from cnkg import instruction
        fixture = self.branch_fixture()
        record = fixture.create()
        for index in range(3):
            value = "b" if index == 0 else record["question_snapshots"][record["task_ids"][index]]["answer"]
            fixture.answer(record, value, 5 + index)
        self.store.save(record)
        credentials = self.profiles.enable(OWNER)
        secret = recovery._recovery_secret(credentials["recovery_code"])
        payload = recovery._decrypt(self.profiles.backup(OWNER, credentials["recovery_code"]), secret)
        target, profiles = self.fixture.new_install()
        target.save(session("other-profile-session", OTHER))
        other = profiles.enable(OTHER)
        before = self.dump_all(target)
        for fault in ("selection", "sequence", "confirmation", "boolean_schema", "legacy_subset"):
            candidate = copy.deepcopy(payload)
            row = candidate["tables"]["sessions"][0]
            saved = json.loads(row["record_json"])
            if fault == "selection":
                saved["route_selection"]["route_id"] = "default"
            elif fault == "sequence":
                saved["task_ids"][1] = saved["support_snapshot"]["routing"]["routes"]["default"]["guided_task_id"]
            elif fault == "confirmation":
                saved["answers"][-1]["resolution_for_need_ids"] = []
            else:
                if fault == "boolean_schema":
                    saved["support_snapshot"]["routing"]["schema"] = True
                else:
                    saved["support_snapshot"].pop("routing")
                saved["content_sha256"] = instruction.digest(saved["support_snapshot"])
            row["record_json"] = learning_store._json(saved)
            envelope = recovery._encrypt(candidate, secret)
            with self.subTest(fault=fault):
                with self.assertRaises(recovery.ProfileRecoveryError):
                    profiles.restore(envelope, credentials["recovery_code"])
                self.assertEqual(self.dump_all(target), before)
                self.assertEqual(profiles.resolve_owner(other["cookie_token"]), OTHER)

    def test_generated_is_not_file_verified_and_capacity_matches_actual_utf8_payload(self):
        credentials, _ = self.fixture.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        health = self.profiles.status(OWNER)["backup_health"]
        self.assertEqual(health["generated"]["freshness"], "matches_current_assets")
        self.assertIsNone(health["verified_file"])
        plain = recovery._decrypt(envelope, recovery._recovery_secret(credentials["recovery_code"]))
        self.assertEqual(health["capacity"]["payload_bytes"], len(learning_store._json(plain).encode("utf-8")))
        self.assertEqual(health["capacity"]["estimated_envelope_bytes"], len(learning_store._json(envelope).encode("utf-8")))
        self.assertEqual(health["capacity"]["counts"], {table: len(rows) for table,rows in plain["tables"].items()})
        self.assertEqual(set(health["capacity"]["counts"]), set(recovery._TABLE_COLUMNS))
        public = json.dumps(self.profiles.status(OWNER))
        for private in (OWNER, credentials["recovery_code"], credentials["cookie_token"], "recovery_hash", "原来的第一稿"):
            self.assertNotIn(private, public)

    def test_same_timestamp_new_revision_is_stale_then_actual_file_verification_is_explicit(self):
        with mock.patch.object(learning_store, "_now", return_value=NOW), mock.patch.object(recovery, "_now", return_value=NOW):
            credentials, first = self.fixture.seed()
            envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
            saved = self.store.load_work(OWNER, "work-at-old-version")
            new_payload = copy.deepcopy(saved["payload"])
            new_payload["draft"] = "同一毫秒内增加的第三次修订"
            self.store.save_work(OWNER, "work-at-old-version", new_payload, 2, "third-request")
            updated = self.store.load_work(OWNER, "work-at-old-version")
            self.assertEqual(saved["updated_at"], updated["updated_at"])
            health = self.profiles.status(OWNER)["backup_health"]
            self.assertEqual(health["generated"]["freshness"], "changed")
            self.assertEqual(health["generated"]["row_count_changes"]["works"], 0)
            self.assertEqual(health["generated"]["row_count_changes"]["work_revisions"], 1)
            self.assertIn("works", health["generated"]["changed_tables"])
            old_check = self.profiles.verify_backup(envelope, credentials["recovery_code"], current_owner=OWNER)
            self.assertFalse(old_check["matches_current_assets"])
            self.assertFalse(old_check["identity_changed"])
            self.assertFalse(old_check["assets_imported"])
            self.assertEqual(self.profiles.status(OWNER)["backup_health"]["verified_file"]["freshness"], "changed")
            fresh = self.profiles.backup(OWNER, credentials["recovery_code"])
            # Generating another file does not silently replace the stale
            # verified-file receipt with a successful-file assertion.
            self.assertEqual(self.profiles.status(OWNER)["backup_health"]["verified_file"]["freshness"], "changed")
            rows_before = self.fixture.original_rows(self.store)
            checked = self.profiles.verify_backup(json.dumps(fresh, indent=2), credentials["recovery_code"], current_owner=OWNER)
            self.assertTrue(checked["matches_current_assets"])
            self.assertEqual(self.fixture.original_rows(self.store), rows_before)
            self.assertEqual(self.profiles.resolve_owner(credentials["cookie_token"]), OWNER)
            self.assertEqual(self.profiles.status(OWNER)["backup_health"]["generated"]["freshness"], "matches_current_assets")

    def test_exposure_only_change_is_detected_without_new_event_or_timestamp(self):
        credentials, _ = self.fixture.seed()
        self.profiles.backup(OWNER, credentials["recovery_code"])
        with self.store._connect() as db:
            db.execute("UPDATE exposures SET material_count=material_count+1 WHERE owner_id=? AND entity_id=?",
                       (OWNER, "legacy-without-event"))
        generated = self.profiles.status(OWNER)["backup_health"]["generated"]
        self.assertEqual(generated["freshness"], "changed")
        self.assertEqual(generated["changed_tables"], ["exposures"])
        self.assertTrue(all(count == 0 for count in generated["row_count_changes"].values()))

    def test_request_only_change_is_detected_without_work_revision_change(self):
        credentials, _ = self.fixture.seed()
        self.profiles.backup(OWNER, credentials["recovery_code"])
        with self.store._connect() as db:
            db.execute("UPDATE work_requests SET request_id=? WHERE owner_id=? AND request_id=?",
                       ("renamed-original-request", OWNER, "first-request"))
        generated = self.profiles.status(OWNER)["backup_health"]["generated"]
        self.assertEqual(generated["freshness"], "changed")
        self.assertEqual(generated["changed_tables"], ["work_requests"])
        self.assertTrue(all(count == 0 for count in generated["row_count_changes"].values()))

    def test_restore_into_occupied_install_normalizes_only_local_event_indexes(self):
        credentials, _ = self.fixture.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        target, profiles = self.fixture.new_install()
        target.save(session("destination-other", OTHER))
        target.add_event(OTHER, {"event_type": "existing-destination-event", "occurred_at": NOW}, "destination-other")
        receipt = profiles.restore(envelope, credentials["recovery_code"])
        self.assertGreater(receipt["counts"]["events_reindexed"], 0)
        self.assertNotEqual(self.fixture.original_rows(target)["events"], self.fixture.original_rows(self.store)["events"])
        health = profiles.status(OWNER)["backup_health"]
        self.assertEqual(health["restored_snapshot"]["freshness"], "matches_current_assets")
        self.assertIsNone(health["generated"])
        self.assertIsNone(health["verified_file"])
        verified = profiles.verify_backup(envelope, credentials["recovery_code"], current_owner=OWNER)
        self.assertTrue(verified["matches_current_assets"])
        self.assertEqual(profiles.resolve_owner(receipt["cookie_token"]), OWNER)
        self.assertEqual(target.count(OTHER), 1)

    def test_old_recovery_code_can_verify_old_file_without_rotating_current_access(self):
        credentials, _ = self.fixture.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        rotated = self.profiles.rotate(OWNER)
        result = self.profiles.verify_backup(envelope, credentials["recovery_code"], current_owner=OWNER)
        self.assertTrue(result["verified"])
        self.assertTrue(result["matches_current_assets"])
        self.assertFalse(result["matches_current_code"])
        self.assertEqual(self.profiles.resolve_owner(rotated["cookie_token"]), OWNER)
        self.assertIsNone(self.profiles.resolve_owner(credentials["cookie_token"]))
        self.assertFalse(self.profiles.status(OWNER)["backup_health"]["verified_file"]["matches_current_code"])

    def test_wrong_code_corruption_and_future_versions_preserve_every_table_and_receipt(self):
        credentials, _ = self.fixture.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        other = self.profiles.enable(OTHER)
        secret = recovery._recovery_secret(credentials["recovery_code"])
        future_payload = recovery._decrypt(envelope, secret)
        future_payload["learning_schema"] = learning_store.SCHEMA_VERSION + 1
        future = recovery._encrypt(future_payload, secret)
        corrupt = {**envelope, "ciphertext": ("A" if envelope["ciphertext"][0] != "A" else "B") + envelope["ciphertext"][1:]}
        before = self.dump_all(self.store)
        for candidate, code, wording in [(envelope, other["recovery_code"], "恢复码不匹配"),
                                          (corrupt, credentials["recovery_code"], "已损坏"),
                                          ({**envelope,"version":2}, credentials["recovery_code"], "保留原文件"),
                                          (future, credentials["recovery_code"], "不兼容")]:
            with self.subTest(wording=wording), self.assertRaisesRegex(recovery.ProfileRecoveryError, wording):
                self.profiles.verify_backup(candidate, code, current_owner=OWNER)
            self.assertEqual(self.dump_all(self.store), before)
            self.assertEqual(self.profiles.resolve_owner(credentials["cookie_token"]), OWNER)

    def test_other_profile_file_check_never_changes_current_identity_or_records(self):
        credentials, _ = self.fixture.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        other = self.profiles.enable(OTHER)
        before = self.dump_all(self.store)
        result = self.profiles.verify_backup(envelope, credentials["recovery_code"], current_owner=OTHER)
        self.assertFalse(result["matches_current_profile"])
        self.assertIsNone(result["matches_current_assets"])
        self.assertEqual(self.dump_all(self.store), before)
        self.assertEqual(self.profiles.resolve_owner(other["cookie_token"]), OTHER)

    def test_legacy_timestamp_is_not_upgraded_to_verified_or_fresh(self):
        credentials, _ = self.fixture.seed()
        envelope = self.profiles.backup(OWNER, credentials["recovery_code"])
        with self.store._connect() as db:
            db.execute("DELETE FROM profile_backup_receipts WHERE owner_id=?", (OWNER,))
        health = self.profiles.status(OWNER)["backup_health"]
        self.assertEqual(envelope["version"], 1)
        self.assertEqual(health["compatibility"]["learning_schemas"], [4])
        self.assertTrue(health["legacy_generation_at"])
        self.assertIsNone(health["generated"])
        self.assertIsNone(health["verified_file"])
        target, profiles = self.fixture.new_install()
        profiles.restore(envelope, credentials["recovery_code"])
        self.assertEqual(self.fixture.original_rows(target), self.fixture.original_rows(self.store))

    def test_capacity_notice_and_limits_do_not_silently_truncate_or_record_success(self):
        credentials, _ = self.fixture.seed()
        self.profiles.backup(OWNER, credentials["recovery_code"])
        inventory = self.profiles.status(OWNER)["backup_health"]["capacity"]
        before = self.dump_all(self.store)
        with mock.patch.object(recovery,"MAX_BACKUP_BYTES", int(inventory["payload_bytes"]/0.85)):
            self.assertEqual(self.profiles.status(OWNER)["backup_health"]["capacity"]["status"], "near_limit")
        with mock.patch.object(recovery,"MAX_TABLE_ROWS",max(inventory["counts"].values())-1):
            capacity = self.profiles.status(OWNER)["backup_health"]["capacity"]
            self.assertEqual(capacity["status"], "exceeded")
            self.assertEqual(capacity["counts"], inventory["counts"])
            with self.assertRaisesRegex(recovery.ProfileRecoveryError,"数量超过限制"):
                self.profiles.backup(OWNER, credentials["recovery_code"])
        with mock.patch.object(recovery,"MAX_BACKUP_BYTES",inventory["payload_bytes"]-1):
            self.assertEqual(self.profiles.status(OWNER)["backup_health"]["capacity"]["status"], "exceeded")
            with self.assertRaises(recovery.ProfileRecoveryError):
                self.profiles.backup(OWNER, credentials["recovery_code"])
        self.assertEqual(self.dump_all(self.store), before)

    def test_documented_private_sqlite_snapshot_restores_all_profiles_without_size_truncation(self):
        credentials, _ = self.fixture.seed()
        other = self.profiles.enable(OTHER)
        self.profiles.backup(OWNER, credentials["recovery_code"])
        destination = Path(self.fixture.tmp.name) / "private-installation-snapshot.sqlite"
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(self.store.path.resolve().as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as snapshot:
                original.backup(snapshot)
                self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        restored_store = learning_store.LearningStore(destination)
        restored_profiles = recovery.ProfileRecovery(restored_store)
        self.assertEqual(self.fixture.original_rows(restored_store), self.fixture.original_rows(self.store))
        self.assertEqual(self.fixture.original_rows(restored_store, OTHER), self.fixture.original_rows(self.store, OTHER))
        recovered = restored_profiles.recover(credentials["recovery_code"])
        self.assertEqual(restored_profiles.resolve_owner(recovered["cookie_token"]), OWNER)
        self.assertEqual(restored_profiles.resolve_owner(other["cookie_token"]), OTHER)
        self.assertEqual(self.profiles.resolve_owner(credentials["cookie_token"]), OWNER)
        current = restored_store.load_work(OWNER, "work-at-old-version")
        revised = copy.deepcopy(current["payload"])
        revised["draft"] = "完整数据库副本恢复后继续修订"
        restored_store.save_work(OWNER, "work-at-old-version", revised, current["revision"], "snapshot-new-request")
        self.assertEqual(restored_store.load_work(OWNER, "work-at-old-version")["revision"], 3)
        self.assertEqual(self.store.load_work(OWNER, "work-at-old-version")["revision"], 2)


class ProfileHealthHTTPTests(unittest.TestCase):
    def setUp(self):
        from tests import test_profile_v5 as http_fixture
        self.fixture = http_fixture.ProfileSwitchHTTPTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_actual_file_verification_is_owner_scoped_and_read_only_over_http(self):
        f = self.fixture
        client, other = f.client(), f.client()
        resource, work = f.work(client, "文件核验前的完整草稿")
        code = f.enable(client,"核验档案")
        other_code = f.enable(other,"另一个档案")
        envelope = f.backup(client,code)
        cookie = f.cookie(client)
        self.assertIsNone(f.request(client,"/api/profile")[1]["backup_health"]["verified_file"])
        status, error = f.request(client,"/api/profile/verify-backup",{"envelope":envelope,"recovery_code":other_code})
        self.assertEqual(status,400,error)
        self.assertEqual(f.cookie(client),cookie)
        status, result = f.request(client,"/api/profile/verify-backup",{"envelope":json.dumps(envelope,ensure_ascii=False,indent=2),"recovery_code":code})
        self.assertEqual(status,200,result)
        self.assertTrue(result["matches_current_assets"])
        self.assertFalse(result["identity_changed"])
        self.assertFalse(result["assets_imported"])
        self.assertEqual(f.cookie(client),cookie)
        self.assertEqual(f.saved_work(client,resource)[1]["work"],work)
        self.assertEqual(f.request(client,"/api/profile")[1]["backup_health"]["verified_file"]["freshness"],"matches_current_assets")
        other_cookie = f.cookie(other)
        status,result = f.request(other,"/api/profile/verify-backup",{"envelope":envelope,"recovery_code":code,
            "current_owner": "forged-owner", "matches_current_profile":True})
        self.assertEqual(status,200,result)
        self.assertFalse(result["matches_current_profile"])
        self.assertEqual(f.cookie(other),other_cookie)
        self.assertEqual(f.request(other,"/api/profile")[1]["label"],"另一个档案")

    def test_duplicate_json_keys_and_future_versions_have_no_restore_side_effect(self):
        f = self.fixture
        client = f.client()
        code = f.enable(client,"原档案")
        envelope = f.backup(client,code)
        cookie = f.cookie(client)
        duplicate = json.dumps(envelope)[:-1] + ',"version":1}'
        before = f.request(client,"/api/profile")[1]
        for candidate,wording in [(duplicate,"重复字段"),({**envelope,"version":2},"保留原文件")]:
            status,result = f.request(client,"/api/profile/verify-backup",{"envelope":candidate,"recovery_code":code})
            self.assertEqual(status,400,result)
            self.assertIn(wording,result["error"])
            self.assertEqual(f.cookie(client),cookie)
            self.assertEqual(f.request(client,"/api/profile")[1],before)


if __name__ == "__main__":
    unittest.main()
