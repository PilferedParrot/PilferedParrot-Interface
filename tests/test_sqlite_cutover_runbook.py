"""Offline cutover rehearsal with synthetic history only."""

from __future__ import annotations

import hashlib
import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pilferedparrot.sqlite_cutover import inspect, validate_paths
from pilferedparrot.sqlite_state import SourceChanged, StateStoreError
from pilferedparrot.web import ChatStore
from pilferedparrot import web_server


@unittest.skipUnless(os.name == "posix", "private rollback export needs POSIX")
class CutoverRunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "chats.json"
        self.raw = json.dumps({
            "version": 8,
            "chats": [{"id": "work-a", "window_id": "main", "messages": [],
                       "provider_messages": [], "draft": "unsent"}],
            "preferences": {}, "unknown_top": {"nested": [1, 2]},
        }).encode() + b"\n"
        self.source.write_bytes(self.raw)
        self.source.chmod(0o600)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.database = self.private / "chats.sqlite3"

    def test_prepare_restart_export_and_json_rollback(self) -> None:
        source_hash = hashlib.sha256(self.raw).hexdigest()
        first = inspect(self.source, self.database, create=True,
                        expected_source_sha256=source_hash)
        self.assertEqual(first["revision"], 0)
        self.assertEqual(first["source_sha256"], source_hash)
        store = ChatStore(self.source, sqlite_state_path=self.database)
        try:
            store.set_draft("work-a", "saved in SQLite")
        finally:
            store.close()
        second = inspect(self.source, self.database)
        self.assertGreater(second["revision"], first["revision"])
        destination = self.private / "rollback.json"
        exported = inspect(self.source, self.database, export=destination)
        self.assertEqual(exported["tree_sha256"], second["tree_sha256"])
        self.assertEqual(self.source.read_bytes(), self.raw)
        rolled_back = ChatStore(destination)
        try:
            self.assertEqual(rolled_back.get("work-a")["draft"], "saved in SQLite")
        finally:
            rolled_back.close()
        self.assertEqual(json.loads(destination.read_bytes())["unknown_top"],
                         {"nested": [1, 2]})
        with self.assertRaises(StateStoreError):
            inspect(self.source, self.database, export=destination)

    def test_refuses_changed_source_and_nonprivate_database(self) -> None:
        inspect(self.source, self.database, create=True)
        self.source.write_bytes(self.raw + b" ")
        with self.assertRaises(SourceChanged):
            inspect(self.source, self.database)
        other = self.root / "shared"
        other.mkdir(mode=0o755)
        with self.assertRaises(StateStoreError):
            validate_paths(self.source, other / "state.sqlite3")

    def test_refuses_database_alias_and_wrong_source_hash(self) -> None:
        with self.assertRaises(StateStoreError):
            validate_paths(self.source, self.source)
        with self.assertRaises(StateStoreError):
            inspect(self.source, self.database, create=True,
                    expected_source_sha256="0" * 64)


class FreshServerTests(unittest.TestCase):
    def test_sqlite_start_never_attaches_or_replaces_existing_server(self) -> None:
        config = {"web": {"host": "127.0.0.1", "port": 8765,
                          "open_browser": False}}
        for state in ("compatible", "stale", "other"):
            with self.subTest(state=state):
                create_app = Mock()
                terminate = Mock()
                with self.assertRaisesRegex(RuntimeError, "previous app"):
                    web_server.serve(
                        config, Path.cwd(), open_browser=False,
                        create_app=create_app, make_handler=Mock(),
                        read_capability=Mock(), browser_url=Mock(),
                        browser_open=Mock(), status=lambda _url: state,
                        terminate=terminate, require_fresh=True,
                    )
                create_app.assert_not_called()
                terminate.assert_not_called()

    def test_bind_race_closes_prepared_sqlite_app(self) -> None:
        config = {"web": {"host": "127.0.0.1", "port": 8765,
                          "open_browser": False}}
        app = Mock()
        statuses = iter(("unavailable", "compatible"))
        with self.assertRaisesRegex(RuntimeError, "unused app port"):
            web_server.serve(
                config, Path.cwd(), open_browser=False,
                create_app=Mock(return_value=app), make_handler=Mock(),
                read_capability=Mock(), browser_url=Mock(), browser_open=Mock(),
                status=lambda _url: next(statuses), terminate=Mock(),
                http_server=Mock(side_effect=OSError(errno.EADDRINUSE, "in use")),
                require_fresh=True,
            )
        app.shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
