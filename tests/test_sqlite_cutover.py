"""Opt-in ChatStore cutover tests using only synthetic temporary files."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.sqlite_state import (
    RevisionConflict, SourceChanged, SQLiteStateStore, StateStoreError,
)
from pilferedparrot.config import load_config
from pilferedparrot.web import ChatStore, PilferedParrotApp


RAW = (
    '{"version":8,"chats":[{"id":"work-a","window_id":"provider-codex",'
    '"requested_provider":"codex","provider_messages":[],"messages":[],'
    '"draft":"unsent","future_field":{"nested":[1,2]}}],'
    '"chat":{"id":"chat-a","messages":[]},"chat_history":[],'
    '"preferences":{"future_preference":42},"unknown_top":{"keep":true}}\n'
).encode("utf-8")


class ChatStoreSQLiteCutoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "chats.json"
        self.database = self.root / "chats.sqlite3"
        self.source.write_bytes(RAW)

    def open_store(self, *, source: Path | None = None,
                   legacy_path: Path | None = None) -> ChatStore:
        store = ChatStore(source or self.source, legacy_path=legacy_path,
                          sqlite_state_path=self.database)
        self.addCleanup(store.close)
        return store

    def test_import_precedes_normalization_and_restart_uses_sqlite_authority(self):
        store = self.open_store()
        # Compatibility normalization drops this preference in the runtime tree.
        # The imported revision and raw rollback bytes must still be exact.
        self.assertNotIn("future_preference", store.data["preferences"])
        with SQLiteStateStore(self.database) as inspected:
            snapshot = inspected.import_json(self.source)
            self.assertEqual(snapshot.revision, 0)
            self.assertEqual(snapshot.document, json.loads(RAW))
            self.assertEqual(inspected.source_backup()[0], RAW)
        store.set_draft("work-a", "saved in SQLite")
        self.assertEqual(self.source.read_bytes(), RAW)
        store.close()

        restarted = self.open_store()
        self.assertEqual(restarted.get("work-a")["draft"], "saved in SQLite")
        self.assertEqual(restarted.data["unknown_top"], {"keep": True})
        with SQLiteStateStore(self.database) as inspected:
            snapshot = inspected.import_json(self.source)
            self.assertEqual(snapshot.revision, 1)
            self.assertEqual(snapshot.document["chats"][0]["draft"], "saved in SQLite")
            self.assertEqual(snapshot.document["preferences"]["future_preference"], 42)
            self.assertEqual(snapshot.document["chats"][0]["future_field"], {"nested": [1, 2]})
            self.assertEqual(inspected.source_backup()[0], RAW)
        self.assertEqual(self.source.read_bytes(), RAW)

    def test_app_startup_and_later_edits_preserve_nested_unknown_fields(self):
        source_tree = json.loads(RAW)
        source_tree["preferences"]["appearance"] = {
            "tone": "darker", "future_appearance": {"keep": True},
        }
        source_tree["chat"]["future_chat"] = {"keep": [1, 2]}
        source_tree["chat"]["messages"] = [{
            "id": "message-a", "role": "user", "content": "hello",
            "future_message": {"keep": True},
        }]
        source_tree["chats"][0]["messages"] = [{
            "id": "work-message-a", "role": "assistant", "content": "before",
            "future_message": {"keep": [3]},
        }]
        source_tree["chats"][0]["context_used_tokens"] = 999999
        self.source.write_text(json.dumps(source_tree), encoding="utf-8")
        original_bytes = self.source.read_bytes()
        config = load_config(self.root / "missing.json")
        config["web"]["chat_store"] = str(self.source)
        config["web"]["model_catalog_store"] = str(self.root / "models.json")
        config["ledger"] = str(self.root / "runs.jsonl")
        app = PilferedParrotApp(config, self.root, sqlite_state_path=self.database)
        try:
            work_public = app.store.list_public()[0]
            self.assertNotIn("future_field", work_public)
            self.assertNotIn("future_message", work_public["messages"][0])
            self.assertNotIn("future_chat", app.store.chat_public())
            self.assertNotIn("future_message", app.store.chat_public()["messages"][0])
            with SQLiteStateStore(self.database) as inspected:
                startup = inspected.import_json(self.source).document
            self.assertEqual(startup["preferences"]["future_preference"], 42)
            self.assertEqual(startup["preferences"]["appearance"]["future_appearance"], {"keep": True})
            self.assertEqual(startup["chat"]["future_chat"], {"keep": [1, 2]})
            self.assertEqual(startup["chat"]["messages"][0]["future_message"], {"keep": True})
            self.assertNotIn("context_used_tokens", startup["chats"][0])
            app.store.set_draft("work-a", "later")
            app.store.set_appearance_preferences({"surface": "balanced"})
            with app.store.lock:
                app.store.get("work-a")["messages"][0]["content"] = "after"
                app.store.data["chat"]["messages"].append({
                    "id": "message-b", "role": "user", "content": "later",
                })
                app.store.save()
        finally:
            app.shutdown()
        with SQLiteStateStore(self.database) as inspected:
            saved = inspected.import_json(self.source).document
            self.assertEqual(inspected.source_backup()[0], original_bytes)
        self.assertEqual(saved["preferences"]["future_preference"], 42)
        self.assertEqual(saved["preferences"]["appearance"]["future_appearance"], {"keep": True})
        self.assertEqual(saved["chat"]["future_chat"], {"keep": [1, 2]})
        self.assertEqual(saved["chat"]["messages"][0]["future_message"], {"keep": True})
        self.assertEqual(saved["chat"]["messages"][1]["content"], "later")
        self.assertEqual(saved["chats"][0]["messages"][0]["future_message"], {"keep": [3]})
        self.assertEqual(saved["chats"][0]["messages"][0]["content"], "after")
        self.assertEqual(saved["chats"][0]["draft"], "later")
        self.assertEqual(saved["preferences"]["appearance"]["surface"], "balanced")
        restarted = PilferedParrotApp(config, self.root, sqlite_state_path=self.database)
        try:
            restarted.store.set_draft("work-a", "after restart")
        finally:
            restarted.shutdown()
        with SQLiteStateStore(self.database) as inspected:
            again = inspected.import_json(self.source).document
        self.assertEqual(again["preferences"]["future_preference"], 42)
        self.assertEqual(again["chat"]["messages"][0]["future_message"], {"keep": True})
        self.assertEqual(again["chats"][0]["messages"][0]["future_message"], {"keep": [3]})
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_chat_reset_moves_opaque_fields_into_archive(self):
        source_tree = json.loads(RAW)
        source_tree["chat"]["messages"] = [{
            "id": "message-a", "role": "user", "content": "hello",
            "future_message": "keep",
        }]
        source_tree["chat"]["future_chat"] = {"keep": True}
        self.source.write_text(json.dumps(source_tree), encoding="utf-8")
        store = self.open_store()
        store.reset_chat()
        with SQLiteStateStore(self.database) as inspected:
            saved = inspected.import_json(self.source).document
        self.assertEqual(saved["chat_history"][0]["future_chat"], {"keep": True})
        self.assertEqual(saved["chat_history"][0]["messages"][0]["future_message"], "keep")
        self.assertNotIn("future_chat", saved["chat"])

    def test_deleting_session_does_not_delete_other_opaque_fields(self):
        store = self.open_store()
        store.delete("work-a")
        with SQLiteStateStore(self.database) as inspected:
            saved = inspected.import_json(self.source).document
        self.assertEqual(saved["chats"], [])
        self.assertEqual(saved["preferences"]["future_preference"], 42)
        self.assertEqual(saved["unknown_top"], {"keep": True})

    def test_unkeyed_message_reorder_fails_without_committing(self):
        source_tree = json.loads(RAW)
        source_tree["chat"]["messages"] = [
            {"role": "user", "content": "one", "future": 1},
            {"role": "user", "content": "two", "future": 2},
        ]
        self.source.write_text(json.dumps(source_tree), encoding="utf-8")
        store = self.open_store()
        store.data["chat"]["messages"].reverse()
        with self.assertRaises(StateStoreError):
            store.save()
        with SQLiteStateStore(self.database) as inspected:
            self.assertEqual(inspected.import_json(self.source).document, source_tree)

    def test_historical_unkeyed_prefix_allows_keyed_completion_and_restart(self):
        source_tree = json.loads(RAW)
        source_tree["chats"][0]["messages"] = [
            {"role": "user", "content": "history", "future_message": "keep"},
            {"id": "pending-a", "role": "assistant", "content": "", "pending": True},
        ]
        self.source.write_text(json.dumps(source_tree), encoding="utf-8")
        store = self.open_store()
        store.save()
        with store.lock:
            pending = store.get("work-a")["messages"][1]
            pending["content"] = "complete"
            pending.pop("pending")
            store.save()
        store.close()
        restarted = self.open_store()
        with restarted.lock:
            restarted.get("work-a")["messages"].append({
                "id": "pending-b", "role": "assistant", "content": "", "pending": True,
            })
            restarted.save()
        restarted.close()
        recovered = self.open_store()
        with recovered.lock:
            pending = recovered.get("work-a")["messages"][2]
            pending["content"] = "interrupted"
            pending["interrupted"] = True
            pending.pop("pending")
            recovered.save()
        with SQLiteStateStore(self.database) as inspected:
            saved = inspected.import_json(self.source).document
        self.assertEqual(saved["chats"][0]["messages"][0]["future_message"], "keep")
        self.assertEqual(saved["chats"][0]["messages"][1]["content"], "complete")
        self.assertTrue(saved["chats"][0]["messages"][2]["interrupted"])

    def test_nested_opaque_fields_stay_out_of_browser_public_tree(self):
        source_tree = json.loads(RAW)
        work = source_tree["chats"][0]
        work["attachments"] = [{"name": "legacy", "future_secret": "attachment secret"}]
        work["harness_tasks"] = [{"status": "running", "future_secret": "task secret"}]
        work["messages"] = [{
            "id": "message-a", "role": "assistant", "content": "visible",
            "activity": [{"id": "activity-a", "kind": "status", "content": "working",
                          "future_secret": "activity secret"}],
            "response_identity": {"provider": "codex", "future_secret": "identity secret"},
            "acp_updates": [{"id": "update-a", "future_secret": "entry secret",
                             "update": {"sessionUpdate": "tool_call", "toolCallId": "tool-a",
                                        "title": "Read", "future_secret": "update secret",
                                        "content": [{"type": "diff", "path": "file.txt",
                                                     "newText": "text", "future_secret": "diff secret"}]}}],
        }]
        self.source.write_text(json.dumps(source_tree), encoding="utf-8")
        store = self.open_store()
        store.save()
        public = store.list_public()[0]
        serialized = json.dumps(public)
        self.assertNotIn("future_secret", serialized)
        self.assertNotIn("attachments", public)
        self.assertEqual(public["harness_tasks"], [{"status": "running"}])
        self.assertEqual(public["messages"][0]["activity"][0]["content"], "working")
        self.assertEqual(public["messages"][0]["acp_updates"][0]["update"]["content"][0]["path"],
                         "file.txt")
        with SQLiteStateStore(self.database) as inspected:
            saved = inspected.import_json(self.source).document
        self.assertEqual(saved["chats"][0]["attachments"][0]["future_secret"],
                         "attachment secret")
        self.assertEqual(saved["chats"][0]["messages"][0]["activity"][0]["future_secret"],
                         "activity secret")

    def test_load_repairs_invalid_ids_without_losing_opaque_fields(self):
        source_tree = json.loads(RAW)
        source_tree["chats"][0]["id"] = "invalid id"
        source_tree["chat"]["id"] = "invalid chat id"
        source_tree["chat"]["future_chat"] = "keep"
        self.source.write_text(json.dumps(source_tree), encoding="utf-8")
        store = self.open_store()
        store.save()
        with SQLiteStateStore(self.database) as inspected:
            saved = inspected.import_json(self.source).document
        self.assertNotEqual(saved["chats"][0]["id"], "invalid id")
        self.assertEqual(saved["chats"][0]["future_field"], {"nested": [1, 2]})
        self.assertNotEqual(saved["chat"]["id"], "invalid chat id")
        self.assertEqual(saved["chat"]["future_chat"], "keep")

    def test_stale_writer_fails_closed_without_overwriting_first_writer(self):
        first = self.open_store()
        stale = self.open_store()
        first.set_draft("work-a", "first writer")
        with self.assertRaises(RevisionConflict):
            stale.set_draft("work-a", "stale writer")
        self.assertEqual(stale.get("work-a")["draft"], "unsent")
        with self.assertRaises(StateStoreError):
            stale.delete("work-a")
        self.assertEqual(stale.get("work-a")["draft"], "unsent")
        with SQLiteStateStore(self.database) as inspected:
            self.assertEqual(inspected.import_json(self.source).document["chats"][0]["draft"],
                             "first writer")
        self.assertEqual(self.source.read_bytes(), RAW)

    def test_source_change_rejects_save_and_later_writes(self):
        store = self.open_store()
        self.source.write_bytes(RAW + b" ")
        with self.assertRaises(SourceChanged):
            store.set_draft("work-a", "must not commit")
        self.assertEqual(store.get("work-a")["draft"], "unsent")
        with self.assertRaises(StateStoreError):
            store.delete("work-a")
        self.assertEqual(store.get("work-a")["draft"], "unsent")
        with self.assertRaises(SourceChanged):
            self.open_store()
        self.source.write_bytes(RAW)
        with SQLiteStateStore(self.database) as inspected:
            self.assertEqual(inspected.import_json(self.source).revision, 0)

    def test_failed_transaction_restores_runtime_tree(self):
        store = self.open_store()
        with patch.object(store._sqlite_state, "save_document",
                          side_effect=StateStoreError("injected failure")):
            with self.assertRaises(StateStoreError):
                store.set_draft("work-a", "not committed")
        self.assertEqual(store.get("work-a")["draft"], "unsent")
        with self.assertRaises(StateStoreError):
            store.delete("work-a")
        self.assertEqual(store.get("work-a")["draft"], "unsent")
        with SQLiteStateStore(self.database) as inspected:
            snapshot = inspected.import_json(self.source)
            self.assertEqual(snapshot.revision, 0)
            self.assertEqual(snapshot.document, json.loads(RAW))
        self.assertEqual(self.source.read_bytes(), RAW)

    def test_legacy_source_is_fixed_for_later_restarts(self):
        legacy = self.root / "legacy.json"
        self.source.replace(legacy)
        store = self.open_store(legacy_path=legacy)
        store.set_draft("work-a", "legacy source")
        store.close()
        self.assertFalse(self.source.exists())
        self.assertEqual(legacy.read_bytes(), RAW)
        restarted = self.open_store(legacy_path=legacy)
        self.assertEqual(restarted.get("work-a")["draft"], "legacy source")
        restarted.close()
        self.source.write_bytes(RAW)
        with self.assertRaises(SourceChanged):
            self.open_store(legacy_path=legacy)

    def test_opt_in_requires_existing_json_source(self):
        self.source.unlink()
        with self.assertRaises(StateStoreError):
            self.open_store()
        self.assertFalse(self.database.exists())

    def test_app_opt_in_persists_work_session_without_rewriting_json(self):
        config = load_config(self.root / "missing.json")
        config["web"]["chat_store"] = str(self.source)
        config["web"]["model_catalog_store"] = str(self.root / "models.json")
        config["ledger"] = str(self.root / "runs.jsonl")
        app = PilferedParrotApp(config, self.root, sqlite_state_path=self.database)
        try:
            work = app.create_chat({"provider": "codex", "cwd": str(self.root)})
            app.set_draft(work["id"], {"draft": "draft saved by full app"})
        finally:
            app.shutdown()
        self.assertEqual(self.source.read_bytes(), RAW)

        restarted = PilferedParrotApp(config, self.root, sqlite_state_path=self.database)
        try:
            self.assertEqual(restarted.store.get(work["id"])["draft"],
                             "draft saved by full app")
            with SQLiteStateStore(self.database) as inspected:
                snapshot = inspected.import_json(self.source)
                self.assertTrue(any(chat.get("id") == work["id"]
                                    for chat in snapshot.document["chats"]))
                self.assertEqual(inspected.source_backup()[0], RAW)
        finally:
            restarted.shutdown()
        self.assertEqual(self.source.read_bytes(), RAW)

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_app_opt_in_exports_the_committed_document_to_a_new_file(self):
        config = load_config(self.root / "missing.json")
        config["web"]["chat_store"] = str(self.source)
        config["web"]["model_catalog_store"] = str(self.root / "models.json")
        config["ledger"] = str(self.root / "runs.jsonl")
        app = PilferedParrotApp(config, self.root, sqlite_state_path=self.database)
        try:
            work = app.create_chat({"provider": "codex", "cwd": str(self.root)})
            app.set_draft(work["id"], {"draft": "export this saved work"})
            destination = self.root / "exported-current.json"
            exported = app.store._sqlite_state.export_json(destination)
            snapshot = app.store._sqlite_state.load()
            self.assertEqual(exported.revision, snapshot.revision)
            self.assertEqual(json.loads(destination.read_bytes()), snapshot.document)
            self.assertEqual(snapshot.document["unknown_top"], {"keep": True})
            self.assertEqual(app.store._sqlite_state.source_backup()[0], RAW)
            with self.assertRaises(StateStoreError):
                app.store._sqlite_state.export_json(destination)
        finally:
            app.shutdown()
        self.assertEqual(self.source.read_bytes(), RAW)


if __name__ == "__main__":
    unittest.main()
