"""Privacy and bounds checks for the value-free SQLite load report."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pilferedparrot.sqlite_transform_report as transform_report
from pilferedparrot.sqlite_transform_report import build_transform_report
from pilferedparrot.web import ChatStore


class SQLiteTransformReportTests(unittest.TestCase):
    def test_report_counts_transforms_without_exposing_values_or_unknown_keys(self):
        raw = {
            "version": 7,
            "chats": [{
                "id": "SECRET-SESSION-ID-8327",
                "draft": "private prompt text",
                "qwen_messages": [],
                "future_secret_key_901": "PRIVATE UNKNOWN VALUE",
                "cwd": "/private/home/alice/worktree",
            }],
            "preferences": {
                "future_preference": {"account_email": "alice@example.invalid"},
            },
            "account_email": "alice@example.invalid",
        }
        normalized = {
            "version": 8,
            "chats": [{
                "id": "GENERATED-SESSION-ID-9911",
                "draft": "private prompt text",
                "provider_messages": [],
                "window_id": "main",
                "cwd": "/private/home/alice/worktree",
            }],
            "preferences": {},
        }
        report = build_transform_report(
            raw, normalized, source_sha256="a" * 64, revision=0,
        )
        encoded = json.dumps(report, sort_keys=True)

        for secret in (
            "SECRET-SESSION-ID-8327", "GENERATED-SESSION-ID-9911",
            "private prompt text", "PRIVATE UNKNOWN VALUE",
            "future_secret_key_901", "alice@example.invalid",
            "/private/home/alice/worktree", "account_email",
        ):
            self.assertNotIn(secret, encoded)
        counts = {(row["path"], row["operation"]): row["count"]
                  for row in report["counts"]}
        self.assertEqual(counts[("$.version", "changed")], 1)
        self.assertEqual(counts[("$.chats[*].qwen_messages", "retired")], 1)
        self.assertEqual(counts[("$.chats[*].provider_messages", "added")], 1)
        self.assertEqual(counts[("$.chats[*].window_id", "added")], 1)
        self.assertGreaterEqual(
            counts[("$.chats[*].<opaque>", "opaque-retained")], 1,
        )
        self.assertEqual(report["metadata"], {"source_sha256": "a" * 64, "revision": 0})

    def test_sample_field_names_are_capped(self):
        fields = [
            "window_id", "title", "cwd", "created_at", "updated_at",
            "requested_provider", "requested_model", "provider", "model",
            "provider_session_id", "acp_mode", "messages", "draft",
            "archived", "archived_at", "last_used_order", "provider_messages",
            "context_used_tokens", "reasoning_effort", "context_chars",
        ]
        raw = {"chats": [{field: "old" for field in fields}]}
        normalized = {"chats": [{field: "new" for field in fields}]}
        report = build_transform_report(raw, normalized)
        self.assertEqual(len(report["sample_fields"]), 16)
        self.assertLessEqual(len(report["counts"]), 256)

    def test_node_budget_marks_partial_report(self):
        raw = {"chats": [{"id": str(index)} for index in range(20)]}
        normalized = {"chats": [{"id": f"changed-{index}"} for index in range(20)]}
        with patch.object(transform_report, "_MAX_NODES", 12):
            report = build_transform_report(raw, normalized)
        self.assertTrue(report["truncated"])
        self.assertIn("$.<bounded>", {row["path"] for row in report["counts"]})

    def test_chat_store_caches_first_load_report_before_save(self):
        raw = (
            '{"version":8,"chats":[{"id":"SECRET-ID-77",'
            '"qwen_messages":[],"unknown_private_key":"SECRET VALUE"}],'
            '"preferences":{"unknown_preference":"PRIVATE"}}'
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "chats.json"
            source.write_text(raw, encoding="utf-8")
            store = ChatStore(source, sqlite_state_path=root / "chats.sqlite3")
            self.addCleanup(store.close)
            report = store.sqlite_transform_report()
            self.assertIsNotNone(report)
            encoded = json.dumps(report)
            for secret in ("SECRET-ID-77", "SECRET VALUE", "unknown_private_key",
                           "unknown_preference", "PRIVATE"):
                self.assertNotIn(secret, encoded)
            self.assertEqual(source.read_text(encoding="utf-8"), raw)
            self.assertEqual(report["metadata"]["revision"], 0)

    def test_report_matches_retired_and_preserved_fields_after_save(self):
        raw = {
            "version": 4,
            "chats": [],
            "coordinator": {"id": "private-legacy-id", "messages": []},
            "coordinator_history": [],
            "preferences": {
                "title": "PRIVATE-UNKNOWN-PREFERENCE",
                "work_models": {"codex": "  private-model  "},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "chats.json"
            source.write_text(json.dumps(raw), encoding="utf-8")
            store = ChatStore(source, sqlite_state_path=root / "chats.sqlite3")
            self.addCleanup(store.close)
            report = store.sqlite_transform_report()
            rows = {(row["path"], row["category"], row["operation"]): row["count"]
                    for row in report["counts"]}
            self.assertEqual(rows[("$.coordinator", "known", "retired")], 1)
            self.assertEqual(rows[("$.coordinator_history", "known", "retired")], 1)
            self.assertEqual(rows[("$.preferences.<opaque>", "opaque", "opaque-retained")], 1)
            self.assertEqual(rows[("$.preferences.work_models.<opaque>", "opaque", "changed")], 1)
            encoded = json.dumps(report)
            for private in ("PRIVATE-UNKNOWN-PREFERENCE", "private-legacy-id",
                            "private-model", "codex", "$.preferences.title"):
                self.assertNotIn(private, encoded)
            store.save()
            saved = store._sqlite_state.load().document
            self.assertNotIn("coordinator", saved)
            self.assertNotIn("coordinator_history", saved)
            self.assertEqual(saved["preferences"]["title"], "PRIVATE-UNKNOWN-PREFERENCE")
            self.assertEqual(saved["preferences"]["work_models"]["codex"], "private-model")


if __name__ == "__main__":
    unittest.main()
