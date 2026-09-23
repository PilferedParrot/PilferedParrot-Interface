"""Synthetic JSON migration and transactional SQLite journal coverage."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import sqlite_state
from pilferedparrot.sqlite_state import (
    APPLICATION_ID, BUSY_TIMEOUT_MS, SCHEMA_VERSION,
    RevisionConflict, SourceChanged, SQLiteStateStore, StateStoreError,
)


RAW = (
    '{\n  "version": 8, "chats": [{"id":"work-a","window_id":"provider-codex",'
    '"messages":[],"draft":"unsent","provider_messages":[{"custom":"héllo"}],'
    '"future_field":{"nested":[1,{"x":true}]} }],\n'
    '  "chat": {"id":"chat-a","messages":[]}, "chat_history": [{"id":"old-chat"}],\n'
    '  "preferences": {"project_workrooms":{"provider-codex":{"selected":"/project"}},'
    '"future_preference":42}, "unknown_top":{"keep":"🦜"}\n}\n'
).encode("utf-8")


def paths(root: Path) -> tuple[Path, Path]:
    source = root / "chats.json"
    source.write_bytes(RAW)
    return source, root / "chats.sqlite3"


class SQLiteStateTests(unittest.TestCase):
    def test_schema_initialization_fault_never_publishes_partial_final_db(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            original = SQLiteStateStore._create_schema
            def crash_after_schema(self):
                original(self)
                raise RuntimeError("injected crash before publication")
            with patch.object(SQLiteStateStore, "_create_schema", crash_after_schema):
                with self.assertRaises(RuntimeError):
                    SQLiteStateStore(database)
            self.assertFalse(database.exists())
            # A process killed during staging can leave an orphan. It cannot
            # block creation or be mistaken for the published database.
            (root / f".{database.name}.init-orphan.sqlite3").write_bytes(b"partial")
            with SQLiteStateStore(database) as recovered:
                self.assertEqual(recovered.import_json(source).revision, 0)
            self.assertEqual(source.read_bytes(), RAW)

    def test_source_replacement_after_read_cannot_pair_old_bytes_with_new_stat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            original = sqlite_state._read_stable_source
            replacement = RAW.replace(b"unsent", b"edited")
            self.assertEqual(len(replacement), len(RAW))
            def swap_after_read(path):
                result = original(path)
                temporary = root / "replacement.json"
                temporary.write_bytes(replacement)
                os.replace(temporary, source)
                return result
            with SQLiteStateStore(database) as store:
                with patch.object(sqlite_state, "_read_stable_source", side_effect=swap_after_read):
                    with self.assertRaises(SourceChanged):
                        store.import_json(source)
                with self.assertRaises(StateStoreError):
                    store.load()
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(connection.execute(
                        "SELECT raw_json FROM source_backup WHERE id=1"
                    ).fetchone()[0], RAW)
            with SQLiteStateStore(database) as reopened:
                with self.assertRaises(SourceChanged):
                    reopened.import_json(source)

    def test_replay_revision_and_rows_share_one_snapshot_during_concurrent_save(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as reader:
                before = reader.import_json(source)
                with SQLiteStateStore(database) as writer:
                    writer.import_json(source)
                    changed = deepcopy(before.document)
                    changed["chats"][0]["draft"] = "writer committed"
                    original_revision = reader._current_revision
                    fired = False
                    def interleave():
                        nonlocal fired
                        revision = original_revision()
                        if not fired:
                            fired = True
                            writer.save_document(
                                changed, expected_revision=0,
                                event_kind="completed", event_payload={"ok": True},
                                session_key="work-a", run_id="run-a",
                            )
                        return revision
                    with patch.object(reader, "_current_revision", side_effect=interleave):
                        self.assertEqual(reader.replay(), [])
                    self.assertEqual([event.seq for event in reader.replay()], [1])
                    self.assertEqual(reader.load().revision, 1)

    def test_exact_source_bytes_unknown_fields_and_owner_only_wal(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                imported = store.import_json(source)
                self.assertEqual(imported.revision, 0)
                canonical = json.dumps(imported.document, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode("utf-8")
                self.assertEqual(imported.tree_hash, hashlib.sha256(canonical).hexdigest())
                self.assertEqual(imported.document["unknown_top"], {"keep": "🦜"})
                self.assertEqual(imported.document["chats"][0]["future_field"]["nested"][1],
                                 {"x": True})
                raw, digest, path = store.source_backup()
                self.assertEqual(raw, RAW)
                self.assertEqual(digest, hashlib.sha256(RAW).hexdigest())
                self.assertEqual(path, str(source.resolve()))
                with closing(sqlite3.connect(database)) as connection, connection:
                    self.assertEqual(connection.execute(
                        "SELECT full_json FROM state_document WHERE id=1").fetchone()[0], RAW)
                    self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                    self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
                    self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0],
                                     APPLICATION_ID)
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0],
                                     SCHEMA_VERSION)
                self.assertEqual(store._db.execute("PRAGMA busy_timeout").fetchone()[0],
                                 BUSY_TIMEOUT_MS)
                if os.name == "posix":
                    for candidate in (database, Path(str(database) + "-wal"),
                                      Path(str(database) + "-shm")):
                        if candidate.exists():
                            self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
            self.assertEqual(source.read_bytes(), RAW)

    def test_repeated_import_is_idempotent_even_after_document_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                first = store.import_json(source)
                self.assertEqual(store.import_json(source), first)
                document = deepcopy(first.document)
                document["chats"][0]["draft"] = "changed in SQLite"
                saved = store.save_document(document, expected_revision=0)
                self.assertEqual(saved.revision, 1)
                self.assertEqual(store.import_json(source).revision, 1)
            with SQLiteStateStore(database) as reopened:
                snapshot = reopened.import_json(source)
                self.assertEqual(snapshot.revision, 1)
                self.assertEqual(snapshot.document["chats"][0]["draft"], "changed in SQLite")
                self.assertEqual(snapshot.document["unknown_top"], {"keep": "🦜"})
                with closing(sqlite3.connect(database)) as connection, connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM source_backup").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT count(*) FROM state_document").fetchone()[0], 1)
            self.assertEqual(source.read_bytes(), RAW)

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_export_current_complete_document_after_multiple_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            destination = root / "restored.json"
            with SQLiteStateStore(database) as store:
                snapshot = store.import_json(source)
                for revision in range(2):
                    document = deepcopy(snapshot.document)
                    document["chats"][0]["draft"] = f"revision {revision + 1}"
                    document["preferences"]["future_preference"] += 1
                    saved = store.save_document(document, expected_revision=revision)
                    snapshot = store.load()
                    self.assertEqual(saved.revision, snapshot.revision)
                exported = store.export_json(destination)
                self.assertEqual(exported.revision, 2)
                self.assertEqual(exported.tree_hash, snapshot.tree_hash)
                self.assertEqual(exported.sha256,
                                 hashlib.sha256(destination.read_bytes()).hexdigest())
                self.assertEqual(json.loads(destination.read_bytes()), snapshot.document)
                self.assertEqual(json.loads(destination.read_bytes())["unknown_top"],
                                 {"keep": "🦜"})
                self.assertEqual(json.loads(destination.read_bytes())["chat"]["id"], "chat-a")
                self.assertEqual(json.loads(destination.read_bytes())["preferences"]
                                 ["future_preference"], 44)
                self.assertEqual(store.source_backup()[0], RAW)
            self.assertEqual(source.read_bytes(), RAW)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_export_refuses_collisions_and_redirected_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            existing = root / "existing.json"
            existing.write_text("keep", encoding="utf-8")
            with SQLiteStateStore(database) as store:
                store.import_json(source)
                for destination in (source, database, Path(str(database) + "-wal"),
                                    existing):
                    with self.assertRaises(StateStoreError):
                        store.export_json(destination)
                self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
                public = root / "public"
                public.mkdir(mode=0o755)
                public.chmod(0o755)
                with self.assertRaises(StateStoreError):
                    store.export_json(public / "new.json")
                alias = root / "alias.json"
                alias.symlink_to(source)
                redirected = root / "redirected"
                redirected.symlink_to(root, target_is_directory=True)
                for destination in (alias, redirected / "new.json"):
                    with self.assertRaises(StateStoreError):
                        store.export_json(destination)
                self.assertFalse((root / "new.json").exists())
                self.assertEqual(store.source_backup()[0], RAW)

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_export_write_failure_leaves_no_destination_or_private_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            destination = root / "never-published.json"
            with SQLiteStateStore(database) as store:
                snapshot = store.import_json(source)
                private = "unsent"
                self.assertIn(private, json.dumps(snapshot.document))
                with patch.object(sqlite_state.os, "fsync", side_effect=OSError(
                    f"write failed while handling {private}"
                )):
                    with self.assertRaises(StateStoreError) as caught:
                        store.export_json(destination)
                self.assertNotIn(private, str(caught.exception))
                self.assertFalse(destination.exists())
                self.assertEqual(list(root.glob(f".{destination.name}.export-*.tmp")), [])
                self.assertEqual(store.load(), snapshot)
            self.assertEqual(source.read_bytes(), RAW)

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_export_ancestor_swap_does_not_redirect_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            parent = root / "private"
            parent.mkdir(mode=0o700)
            redirected = root / "redirected"
            redirected.mkdir(mode=0o700)
            destination = parent / "export.json"
            original = sqlite_state._same_export_parent
            with SQLiteStateStore(database) as store:
                store.import_json(source)
                def swap_ancestor(path, descriptor):
                    parent.rename(root / "moved-private")
                    parent.symlink_to(redirected, target_is_directory=True)
                    return original(path, descriptor)
                with patch.object(sqlite_state, "_same_export_parent",
                                  side_effect=swap_ancestor):
                    with self.assertRaises(StateStoreError):
                        store.export_json(destination)
            self.assertFalse((redirected / "export.json").exists())
            self.assertFalse((root / "moved-private" / "export.json").exists())

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_export_rejects_stage_replacement_at_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            destination = root / "export.json"
            original_link = sqlite_state.os.link
            with SQLiteStateStore(database) as store:
                store.import_json(source)
                def swap_stage(src, dst, **kwargs):
                    stage = next(root.glob(".export.json.export-*.tmp"))
                    stage.rename(root / "saved-stage")
                    stage.write_bytes(b"different private content")
                    return original_link(src, dst, **kwargs)
                with patch.object(sqlite_state.os, "link", side_effect=swap_stage):
                    with self.assertRaises(StateStoreError):
                        store.export_json(destination)
            self.assertEqual(destination.read_bytes(), b"different private content")
            self.assertEqual(source.read_bytes(), RAW)

    @unittest.skipUnless(os.name == "posix", "private export requires POSIX file permissions")
    def test_export_fsync_failure_preserves_other_writers_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            destination = root / "export.json"
            original_fsync = sqlite_state.os.fsync
            with SQLiteStateStore(database) as store:
                store.import_json(source)
                def replace_then_fail(descriptor):
                    if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                        replacement = root / "other.json"
                        replacement.write_bytes(b"another writer")
                        os.replace(replacement, destination)
                        raise OSError("directory fsync failed")
                    return original_fsync(descriptor)
                with patch.object(sqlite_state.os, "fsync", side_effect=replace_then_fail):
                    with self.assertRaises(StateStoreError) as caught:
                        store.export_json(destination)
                self.assertIn("may exist", str(caught.exception))
            self.assertEqual(destination.read_bytes(), b"another writer")

    def test_event_and_state_fault_roll_back_together_across_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                before = store.import_json(source)
                changed = deepcopy(before.document)
                changed["chats"][0]["messages"].append({"id": "new", "content": "not committed"})
                with patch.object(store, "_insert_event", side_effect=RuntimeError("fault after state update")):
                    with self.assertRaises(RuntimeError):
                        store.save_document(changed, expected_revision=0, event_kind="progress",
                                            event_payload={"text": "not committed"},
                                            session_key="work-a", run_id="run-a")
                self.assertEqual(store.load(), before)
                self.assertEqual(store.replay(), [])
            with SQLiteStateStore(database) as reopened:
                self.assertEqual(reopened.import_json(source), before)
                self.assertEqual(reopened.replay(), [])

    def test_interrupted_import_rolls_back_source_and_document(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                store._db.execute(
                    "CREATE TRIGGER fail_initial_document BEFORE INSERT ON state_document "
                    "BEGIN SELECT RAISE(FAIL, 'injected fault'); END"
                )
                with self.assertRaises(StateStoreError):
                    store.import_json(source)
                self.assertEqual(store._db.execute("SELECT count(*) FROM source_backup").fetchone()[0], 0)
                self.assertEqual(store._db.execute("SELECT count(*) FROM state_document").fetchone()[0], 0)
                store._db.execute("DROP TRIGGER fail_initial_document")
            with SQLiteStateStore(database) as reopened:
                self.assertEqual(reopened.import_json(source).revision, 0)
                self.assertEqual(reopened.source_backup()[0], RAW)

    def test_monotonic_replay_stable_ids_sanitization_and_cas(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                first = store.import_json(source)
                doc = deepcopy(first.document)
                doc["chats"][0]["draft"] = "later"
                saved = store.save_document(
                    doc, expected_revision=0, event_kind="progress",
                    session_key="work-a", run_id="run-a",
                    event_id="event_first_123", event_payload={
                        "text": "alice@example.test worked", "account": {"email": "hidden"},
                        "nested": {"accountEmail": "hidden", "ok": 1},
                    },
                )
                self.assertEqual((saved.revision, saved.event_seq, saved.event_id),
                                 (1, 1, "event_first_123"))
                with self.assertRaises(RevisionConflict):
                    store.save_document(doc, expected_revision=0, event_kind="progress",
                                        event_payload={"text": "stale"},
                                        session_key="work-a", run_id="run-a")
                with self.assertRaises(StateStoreError):
                    store.save_document(doc, expected_revision=1, event_kind="progress",
                                        event_id="event_first_123",
                                        event_payload={"text": "duplicate ID"},
                                        session_key="work-a", run_id="run-a")
                self.assertEqual(store.load().revision, 1)
                saved2 = store.save_document(doc, expected_revision=1,
                                             event_kind="completed", event_payload={"ok": True},
                                             session_key="work-a", run_id="run-a")
                self.assertEqual((saved2.revision, saved2.event_seq), (2, 2))
                events = store.replay()
                self.assertEqual([event.seq for event in events], [1, 2])
                self.assertEqual([event.state_revision for event in events], [1, 2])
                self.assertEqual(events[0].event_id, "event_first_123")
                self.assertEqual((events[0].session_key, events[0].run_id),
                                 ("work-a", "run-a"))
                self.assertEqual(events[0].payload, {
                    "text": "[redacted-email] worked", "nested": {"ok": 1},
                })
                self.assertEqual([event.seq for event in store.replay(after_seq=1)], [2])
                self.assertEqual(store.load().revision, 2)
            with SQLiteStateStore(database) as reopened:
                reopened.import_json(source)
                self.assertEqual(reopened.replay()[0].event_id, "event_first_123")
                self.assertEqual(reopened.load().revision, 2)

    def test_progress_append_is_durable_scoped_and_does_not_rewrite_document(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                original = store.import_json(source)
                with closing(sqlite3.connect(database)) as connection, connection:
                    full_json = connection.execute(
                        "SELECT full_json FROM state_document WHERE id=1"
                    ).fetchone()[0]
                    indexes = [row[1] for row in connection.execute("PRAGMA index_list(events)")]
                self.assertIn("events_session_seq", indexes)
                for number in range(25):
                    event = store.append_event(
                        "work-a", "run-a", "progress", {"index": number},
                        event_id=f"progress_{number:04d}",
                    )
                    self.assertEqual((event.seq, event.state_revision), (number + 1, 0))
                retry = store.append_event(
                    "work-a", "run-a", "progress", {"index": 0},
                    event_id="progress_0000",
                )
                self.assertEqual(retry.seq, 1)
                with self.assertRaises(StateStoreError):
                    store.append_event("work-a", "run-a", "progress", {"index": 99},
                                       event_id="progress_0000")
                other = store.append_event("work-b", "run-b", "progress", {"index": 1})
                self.assertEqual(other.seq, 26)
                self.assertEqual(store.load(), original)
                with closing(sqlite3.connect(database)) as connection, connection:
                    self.assertEqual(connection.execute(
                        "SELECT full_json FROM state_document WHERE id=1"
                    ).fetchone()[0], full_json)
                    self.assertEqual(connection.execute("SELECT count(*) FROM events").fetchone()[0], 26)
                self.assertEqual([event.seq for event in store.replay(session_key="work-a",
                                                                      after_seq=23)], [24, 25])
                self.assertEqual([event.seq for event in store.replay(session_key="work-b")], [26])
                changed = deepcopy(original.document)
                changed["chats"][0]["draft"] = "terminal result"
                terminal = store.save_document(
                    changed, expected_revision=0, event_kind="completed",
                    event_payload={"message_id": "answer"},
                    session_key="work-a", run_id="run-a", event_id="terminal_0001",
                )
                self.assertEqual((terminal.revision, terminal.event_seq), (1, 27))
                self.assertEqual([event.seq for event in store.replay(session_key="work-a",
                                                                      after_seq=25)], [27])
            with SQLiteStateStore(database) as reopened:
                self.assertEqual(reopened.import_json(source).revision, 1)
                self.assertEqual(len(reopened.replay(session_key="work-a")), 26)

    def test_stale_writer_cannot_append_new_progress_for_changed_document(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as writer, SQLiteStateStore(database) as stale:
                initial = writer.import_json(source)
                stale.import_json(source)
                first = stale.append_event(
                    "work-a", "run-a", "progress", {"index": 1},
                    event_id="progress_0001", expected_revision=0,
                )
                changed = deepcopy(initial.document)
                changed["chats"][0]["draft"] = "writer committed"
                writer.save_document(changed, expected_revision=0)
                with self.assertRaises(RevisionConflict):
                    stale.append_event(
                        "work-a", "run-a", "progress", {"index": 2},
                        event_id="progress_0002", expected_revision=0,
                    )
                # A retry of an already committed event stays idempotent.
                retry = stale.append_event(
                    "work-a", "run-a", "progress", {"index": 1},
                    event_id="progress_0001", expected_revision=0,
                )
                self.assertEqual(retry.seq, first.seq)
                self.assertEqual([event.seq for event in writer.replay()], [1])

    def test_changed_source_and_unsupported_or_corrupt_database_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                snapshot = store.import_json(source)
                source.write_bytes(RAW + b" ")
                with self.assertRaises(SourceChanged):
                    store.save_document(snapshot.document, expected_revision=0)
                with self.assertRaises(SourceChanged):
                    store.import_json(source)
                with self.assertRaises(StateStoreError):
                    store.load()
            with SQLiteStateStore(database) as reopened:
                with self.assertRaises(SourceChanged):
                    reopened.import_json(source)
            source.write_bytes(RAW)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE meta SET value='wrong' WHERE key='source_sha256'")
            with self.assertRaises(StateStoreError):
                SQLiteStateStore(database)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("UPDATE meta SET value=? WHERE key='source_sha256'",
                                   (hashlib.sha256(RAW).hexdigest(),))
                connection.execute("PRAGMA user_version=99")
            with self.assertRaises(StateStoreError):
                SQLiteStateStore(database)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "corrupt.sqlite3"
            database.write_bytes(b"not a SQLite database")
            database.chmod(0o600)
            with self.assertRaises(StateStoreError):
                SQLiteStateStore(database)

    def test_retargeted_source_symlink_fails_closed_with_identical_bytes(self):
        if os.name == "nt":
            self.skipTest("directory symlink privileges vary on Windows")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = paths(root)
            alias = root / "active-chats.json"
            alias.symlink_to(source)
            with SQLiteStateStore(database) as store:
                store.import_json(alias)
                replacement = root / "replacement.json"
                replacement.write_bytes(RAW)
                alias.unlink()
                alias.symlink_to(replacement)
                with self.assertRaises(SourceChanged):
                    store.load()
                with self.assertRaises(SourceChanged):
                    store.import_json(alias)

    def test_invalid_json_and_auth_status_do_not_enter_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                snapshot = store.import_json(source)
                with self.assertRaises(StateStoreError):
                    store.save_document(snapshot.document, expected_revision=0,
                                        event_kind="progress", event_payload={
                                            "update": {"sessionUpdate": "_auth/status_update",
                                                       "accountEmail": "private"},
                                        }, session_key="work-a", run_id="run-a")
                self.assertEqual(store.load().revision, 0)
                self.assertEqual(store.replay(), [])
            source.write_bytes(b'{"version":8,"chats":[],"chats":[]}')
            with self.assertRaises(StateStoreError):
                with SQLiteStateStore(Path(directory) / "other.sqlite3") as another:
                    another.import_json(source)

    def test_replay_rejects_semantically_corrupt_or_unsanitized_event_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source, database = paths(Path(directory))
            with SQLiteStateStore(database) as store:
                store.import_json(source)
                store.append_event("work-a", "run-a", "progress", {"text": "safe"})
            for payload in (b"[]", b'{"accountEmail":"private@example.test"}'):
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.execute("UPDATE events SET payload_json=? WHERE seq=1", (payload,))
                with SQLiteStateStore(database) as reopened:
                    reopened.import_json(source)
                    with self.assertRaises(StateStoreError):
                        reopened.replay()


if __name__ == "__main__":
    unittest.main()
