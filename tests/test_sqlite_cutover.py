"""Opt-in ChatStore cutover tests using only synthetic temporary files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.sqlite_state import (
    RevisionConflict, SourceChanged, SQLiteStateStore, StateStoreError,
)
from pilferedparrot.web import ChatStore


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
            self.assertEqual(inspected.source_backup()[0], RAW)
        self.assertEqual(self.source.read_bytes(), RAW)

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


if __name__ == "__main__":
    unittest.main()
