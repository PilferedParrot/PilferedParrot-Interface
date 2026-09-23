import hashlib
from contextlib import closing
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import feedback


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "feedback.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        return feedback.FeedbackStore(self.path)

    def enable(self, store, category="usage"):
        return store.set_consent({"policy_version": 1, "category": category, "enabled": True})

    def test_configured_profiles_keep_independent_consent(self):
        first = feedback.FeedbackStore.from_config({"web": {"chat_store": str(self.path.parent / "one.json")}})
        second = feedback.FeedbackStore.from_config({"web": {"chat_store": str(self.path.parent / "two.json")}})
        self.enable(first)
        self.assertFalse(second.status()["consent"]["usage"])
        self.assertNotEqual(first.path, second.path)

    def test_status_and_report_do_not_create_store_before_opt_in(self):
        store = self.store()
        self.assertFalse(self.path.exists())
        self.assertFalse(store.status()["consent"]["usage"])
        self.assertFalse(self.path.exists())
        store.report()
        self.assertFalse(self.path.exists())

    def test_consent_payload_is_exact_and_categories_are_independent(self):
        store = self.store()
        for payload in (
            {}, {"policy_version": 1, "category": "usage", "enabled": True, "extra": 1},
            {"policy_version": True, "category": "usage", "enabled": True},
            {"policy_version": 1, "category": "unknown", "enabled": True},
            {"policy_version": 2, "category": "usage", "enabled": True},
            {"policy_version": 1, "category": "usage", "enabled": 1},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    store.set_consent(payload)
        self.enable(store, "usage")
        self.enable(store, "problems")
        status = store.status()
        self.assertTrue(status["consent"]["usage"])
        self.assertTrue(status["consent"]["problems"])
        self.assertFalse(status["consent"]["preferences"])

    def test_revoke_across_instances_deletes_and_fences_queued_events(self):
        first, second = self.store(), self.store()
        self.enable(first)
        first.record("usage", "message_sent", "cli")
        first._queue.join()
        second.set_consent({"policy_version": 1, "category": "usage", "enabled": False})
        self.assertEqual(second.report()["counts"], [])
        # An observation made while revoked must not survive re-enabling.
        first.record("usage", "message_sent", "cli")
        second.set_consent({"policy_version": 1, "category": "usage", "enabled": True})
        first._queue.join()
        self.assertEqual(second.report()["counts"], [])
        first.close()
        second.close()

    def test_clear_removes_counts_but_retains_consent(self):
        store = self.store()
        self.enable(store)
        store.record("usage", "message_sent", "cli")
        store._queue.join()
        self.assertTrue(store.report()["counts"])
        store.clear()
        self.assertTrue(store.status()["consent"]["usage"])
        self.assertEqual(store.report()["counts"], [])
        store.close()

    def test_old_policy_fails_closed_and_retention_expires_old_days(self):
        store = self.store()
        self.enable(store)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE consent SET policy = 99 WHERE category = 'usage'")
            db.execute("INSERT INTO counts VALUES (?, ?, ?, ?, ?)", (feedback._day() - 31, "usage", "message_sent", "cli", 4))
        self.assertFalse(store.status()["consent"]["usage"])
        self.assertEqual(store.report()["counts"], [])

    def test_count_is_bounded_and_invalid_vocabulary_never_enters_report(self):
        store = self.store()
        self.enable(store)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO counts VALUES (?, ?, ?, ?, ?)", (feedback._day(), "usage", "message_sent", "cli", 50000))
            db.execute("INSERT INTO counts VALUES (?, ?, ?, ?, ?)", (feedback._day(), "usage", "prompt text", "cli", 3))
        rows = store.report()["counts"]
        self.assertEqual(rows, [{"category": "usage", "event": "message_sent", "surface": "cli", "count": 10000}])
        self.assertNotIn("prompt", json.dumps(rows))

    def test_corrupt_store_fails_closed_and_record_is_best_effort(self):
        self.path.write_bytes(b"not sqlite")
        store = self.store()
        status = store.status()
        self.assertFalse(status["available"])
        self.assertTrue(status["disabled"] is False)
        store.record("usage", "message_sent", "cli")
        if store._thread is not None:
            store._queue.join()
        store.close()

    def test_disabled_environment_blocks_opt_in_and_record(self):
        with patch.dict(os.environ, {"PILFEREDPARROT_TELEMETRY_DISABLED": "1"}):
            store = self.store()
            self.assertTrue(store.status()["disabled"])
            with self.assertRaises(ValueError):
                self.enable(store)
            store.record("usage", "message_sent", "cli")
            self.assertFalse(self.path.exists())

    def test_snapshot_is_required_and_stale_before_optin_token_is_rejected(self):
        store = self.store()
        old = store.snapshot()
        self.assertEqual(old, {})
        self.enable(store)
        store.record("usage", "message_sent", "cli", consent=old)
        self.assertIsNone(store._thread)
        self.assertEqual(store.report()["counts"], [])
        fresh = store.snapshot()
        store.record("usage", "message_sent", "cli", consent=fresh)
        store._queue.join()
        self.assertEqual(store.report()["counts"][0]["count"], 1)
        store.close()

    def test_clear_and_reenable_fence_old_snapshot_but_fresh_snapshot_counts(self):
        store = self.store()
        self.enable(store)
        old = store.snapshot()
        store.clear()
        store.record("usage", "message_sent", "cli", consent=old)
        if store._thread is not None:
            store._queue.join()
        self.enable(store)
        store.record("usage", "message_sent", "cli", consent=old)
        if store._thread is not None:
            store._queue.join()
        fresh = store.snapshot()
        store.record("usage", "message_sent", "cli", consent=fresh)
        store._queue.join()
        self.assertEqual(store.report()["counts"][0]["count"], 1)
        store.close()

    def test_snapshot_revision_fencing_survives_wall_clock_change(self):
        store = self.store()
        self.enable(store)
        old = store.snapshot()
        with patch.object(feedback.time, "time", return_value=0):
            store.clear()
            store.record("usage", "message_sent", "cli", consent=old)
            if store._thread is not None:
                store._queue.join()
            self.assertEqual(store.report()["counts"], [])
        store.close()

    def test_no_thread_or_local_scan_before_consent_and_invalid_category(self):
        store = self.store()
        with patch.object(feedback, "local_change_summary") as scan:
            store.report()
            store.snapshot()
            store.record("bad", "message_sent", "cli")
            store.record("usage", "bad event", "cli")
            self.assertIsNone(store._thread)
            scan.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_do_not_track_prevents_local_change_scan_even_when_consented(self):
        store = self.store()
        self.enable(store, "local_changes")
        with patch.dict(os.environ, {"DO_NOT_TRACK": "true"}), patch.object(
            feedback, "local_change_summary"
        ) as scan:
            store.report()
            scan.assert_not_called()

    def test_local_changes_are_inspected_only_with_explicit_consent(self):
        store = self.store()
        self.enable(store, "usage")
        self.assertNotIn("local_changes", store.report())
        self.enable(store, "local_changes")
        self.assertIn("local_changes", store.report())

    @unittest.skipUnless(os.name == "posix", "POSIX file mode and FIFO checks")
    def test_existing_store_is_hardened_to_mode_0600(self):
        store = self.store()
        self.enable(store)
        store.close()
        os.chmod(self.path, 0o644)
        store.status()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_sidecar_or_wal_storage_is_unavailable(self):
        store = self.store()
        self.enable(store)
        store.close()
        Path(str(self.path) + "-wal").write_bytes(b"x" * 4_000_001)
        self.assertFalse(self.store().status()["available"])
        Path(str(self.path) + "-wal").unlink()
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.commit()
        finally:
            db.close()
        self.assertFalse(self.store().status()["available"])


class LocalChangeSummaryTests(unittest.TestCase):
    def manifest_root(self, content=b"print('ok')\n", *, expected=None, version=None):
        root = Path(tempfile.mkdtemp())
        (root / "sample.py").write_bytes(content)
        digest = expected or hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
        (root / "feedback-baseline.json").write_text(json.dumps({
            "version": version or feedback.__version__, "files": {"sample.py": digest},
        }), encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        return root

    def test_manifest_matching_changed_missing_symlink_and_crlf(self):
        root = self.manifest_root(content=b"line\r\n")
        self.assertEqual(feedback.local_change_summary(root)["components"]["core"]["matching"], 1)
        (root / "sample.py").write_text("changed\n", encoding="utf-8")
        self.assertEqual(feedback.local_change_summary(root)["components"]["core"]["changed"], 1)
        (root / "sample.py").unlink()
        self.assertEqual(feedback.local_change_summary(root)["components"]["core"]["unavailable"], 1)
        try:
            (root / "sample.py").symlink_to("elsewhere.py")
        except OSError:
            return  # Windows may require Developer Mode for symlink creation.
        self.assertEqual(feedback.local_change_summary(root)["components"]["core"]["unavailable"], 1)

    def test_manifest_malformed_out_of_root_and_bad_version_fail_closed(self):
        root = self.manifest_root()
        manifest = root / "feedback-baseline.json"
        manifest.write_text(json.dumps({"version": feedback.__version__, "files": {"../secret": "0" * 64}}))
        self.assertEqual(feedback.local_change_summary(root)["status"], "unavailable")
        manifest.write_text("[]")
        self.assertEqual(feedback.local_change_summary(root)["status"], "unavailable")
        root = self.manifest_root(version="old")
        self.assertEqual(feedback.local_change_summary(root)["status"], "unavailable")

    @unittest.skipUnless(os.name == "posix", "POSIX FIFO check")
    def test_nonregular_manifest_returns_unavailable_promptly(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        os.mkfifo(root / "feedback-baseline.json")
        self.assertEqual(feedback.local_change_summary(root)["status"], "unavailable")

    def test_oversized_manifest_and_source_are_unavailable(self):
        root = self.manifest_root()
        (root / "feedback-baseline.json").write_bytes(b"{" + b" " * 100_000 + b"}")
        self.assertEqual(feedback.local_change_summary(root)["status"], "unavailable")
        root = self.manifest_root(content=b"x" * 1_000_001)
        self.assertEqual(feedback.local_change_summary(root)["components"]["core"]["unavailable"], 1)


class ManualFeedbackTests(unittest.TestCase):
    def test_manual_feedback_schema(self):
        valid = {"kind": "local_fix", "area": "accessibility", "scope": "general",
                 "description": "A clear issue", "change": "Increased contrast."}
        self.assertEqual(feedback.validate_feedback(valid), valid)
        for key in valid:
            bad = dict(valid)
            bad.pop(key)
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    feedback.validate_feedback(bad)
        bad = dict(valid, description="x" * 2001)
        with self.assertRaises(ValueError):
            feedback.validate_feedback(bad)


if __name__ == "__main__":
    unittest.main()
