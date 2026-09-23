"""Fake-provider coverage for opt-in Work file observations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pilferedparrot import observed_turn, web, workspace_checkpoints
from pilferedparrot.config import DEFAULTS
from pilferedparrot.dispatch import RunResult
from pilferedparrot.dispatch import RunCancelled
from pilferedparrot.web_persistence import _sqlite_public_projection
from pilferedparrot.web_server import BrowserHTTPServer, make_handler


@unittest.skipUnless(os.name == "posix", "file observation requires POSIX")
class ObservedTurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        config = deepcopy(DEFAULTS)
        config["web"]["chat_store"] = str(self.root / "state" / "chats.json")
        config["web"]["model_catalog_store"] = str(self.root / "state" / "models.json")
        config["ledger"] = str(self.root / "state" / "runs.jsonl")
        config["codex"]["model"] = "fake-model"
        self.app = web.PilferedParrotApp(config, self.workspace)
        self.addCleanup(self.app.shutdown)
        self.chat = self.app.create_chat({"cwd": str(self.workspace)}, window_id="main",
                                         window_provider="codex")

    def run_turn(self, dispatch, *, observe=False):
        with patch.object(web, "capture_dispatch", side_effect=dispatch):
            self.app.send_message(self.chat["id"], {"content": "fake turn", "observe_files": observe},
                                  window_id="main", window_provider="codex")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with self.app.runs_lock:
                    if self.chat["id"] not in self.app.runs:
                        break
                time.sleep(.01)
            else:
                self.fail("fake turn did not finish")
        return self.app.chat_state(self.chat["id"], window_id="main")["messages"][-1]

    @staticmethod
    def succeeded(*_args):
        return RunResult("done", 0)

    def test_default_off_has_no_copies(self):
        (self.workspace / "secret.txt").write_text("SECRET-BYTES")
        message = self.run_turn(self.succeeded)
        self.assertNotIn("observed_files", message)
        self.assertFalse(self.app.turn_observations.folder.exists())

    def test_create_edit_delete_and_private_bytes(self):
        (self.workspace / "edit.txt").write_text("old")
        (self.workspace / "delete.txt").write_text("remove")

        def dispatch(_provider, _prompt, cwd, *_rest):
            # The provider boundary must see the pre-turn snapshot already sealed.
            self.assertEqual(len(list(self.app.turn_observations.folder.glob("checkpoint-*"))), 1)
            (cwd / "create.txt").write_text("PRIVATE-CREATED-CONTENT")
            (cwd / "edit.txt").write_text("PRIVATE-EDITED-CONTENT")
            (cwd / "delete.txt").unlink()
            return RunResult("done", 0)

        message = self.run_turn(dispatch, observe=True)
        summary = message["observed_files"]
        self.assertEqual(summary["status"], "complete")
        self.assertEqual({item["path"]: item["kind"] for item in summary["changes"]},
                         {"create.txt": "created", "delete.txt": "deleted", "edit.txt": "modified"})
        self.assertEqual(summary["change_count"], 3)
        self.assertNotIn("PRIVATE-", json.dumps(message))
        self.assertNotIn("blob", json.dumps(message))
        live_events = json.dumps(self.app.events.read_after(self.chat["id"], 0))
        self.assertNotIn("PRIVATE-", live_events)
        self.assertNotIn("blobs/", live_events)
        persisted = (self.root / "state" / "chats.json").read_text()
        self.assertNotIn("PRIVATE-", persisted)
        self.assertNotIn("blobs/", persisted)
        self.assertIn(summary["before_checkpoint_id"], persisted)
        self.assertEqual(len(list(self.app.turn_observations.folder.glob("checkpoint-*"))), 2)
        self.assertEqual(self.app.observed_files_summary(
            self.chat["id"], message["id"], window_id="main"), summary)

    def test_human_concurrent_edit_is_observed_without_authorship_claim(self):
        started, release = threading.Event(), threading.Event()
        (self.workspace / "human.txt").write_text("before")

        def dispatch(*_args):
            started.set()
            self.assertTrue(release.wait(2))
            return RunResult("done", 0)

        with patch.object(web, "capture_dispatch", side_effect=dispatch):
            self.app.send_message(self.chat["id"], {"content": "wait", "observe_files": True},
                                  window_id="main", window_provider="codex")
            self.assertTrue(started.wait(2))
            (self.workspace / "human.txt").write_text("human wrote this")
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and self.chat["id"] in self.app.runs:
                time.sleep(.01)
        summary = self.app.chat_state(self.chat["id"], window_id="main")["messages"][-1]["observed_files"]
        self.assertEqual(summary["changes"][0]["path"], "human.txt")
        self.assertIn("authorship unknown", summary["label"])

    def test_incomplete_post_capture_does_not_claim_deletion(self):
        (self.workspace / "a.txt").write_text("a")
        (self.workspace / "b.txt").write_text("b")
        original = workspace_checkpoints.capture
        calls = 0

        def capture(workspace, folder):
            nonlocal calls
            calls += 1
            if calls == 2:
                return original(workspace, folder, workspace_checkpoints.Limits(max_files=1))
            return original(workspace, folder)

        with patch.object(workspace_checkpoints, "capture", side_effect=capture):
            message = self.run_turn(self.succeeded, observe=True)
        summary = message["observed_files"]
        self.assertEqual(summary["status"], "incomplete")
        self.assertFalse(any(item["kind"] == "deleted" for item in summary["changes"]))
        self.assertGreater(summary["coverage"]["after"]["incomplete_count"], 0)

    def test_failed_post_capture_reports_incomplete_without_claiming_changes(self):
        original = workspace_checkpoints.capture
        calls = 0

        def capture(workspace, folder):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("PRIVATE-ERROR-TEXT")
            return original(workspace, folder)

        with patch.object(workspace_checkpoints, "capture", side_effect=capture):
            message = self.run_turn(self.succeeded, observe=True)
        summary = message["observed_files"]
        self.assertEqual(summary["status"], "incomplete")
        self.assertEqual(summary["changes"], [])
        self.assertNotIn("PRIVATE-ERROR-TEXT", json.dumps(message))

    def test_store_limit_refuses_before_provider(self):
        called = threading.Event()

        def dispatch(*_args):
            called.set()
            return RunResult("done", 0)

        with patch.object(observed_turn, "_storage_bytes", return_value=observed_turn.MAX_STORE_BYTES):
            message = self.run_turn(dispatch, observe=True)
        self.assertFalse(called.is_set())
        self.assertTrue(message["error"])
        self.assertNotIn("observed_files", message)

    def test_failure_and_cancel_still_capture_after(self):
        def failed(_provider, _prompt, cwd, *_rest):
            (cwd / "failed.txt").write_text("failed bytes")
            return RunResult("failed", 1)

        failure = self.run_turn(failed, observe=True)
        self.assertTrue(failure["error"])
        self.assertEqual(failure["observed_files"]["changes"][0]["path"], "failed.txt")

        def cancelled(_provider, _prompt, cwd, *_rest):
            (cwd / "cancelled.txt").write_text("cancelled bytes")
            raise RunCancelled()

        cancellation = self.run_turn(cancelled, observe=True)
        self.assertTrue(cancellation["cancelled"])
        self.assertEqual(cancellation["observed_files"]["changes"][0]["path"], "cancelled.txt")

    def test_sqlite_public_projection_keeps_summary_and_drops_blob_field(self):
        message = self.run_turn(self.succeeded, observe=True)
        with self.app.store.lock:
            raw = deepcopy(self.app.store.get(self.chat["id"]))
        raw["messages"][-1]["observed_files"]["private_blob"] = "SECRET-BLOB"
        projected = _sqlite_public_projection(raw)
        self.assertEqual(projected["messages"][-1]["observed_files"]["status"], "complete")
        self.assertNotIn("SECRET-BLOB", json.dumps(projected))

    def test_wrong_window_summary_is_forbidden(self):
        message = self.run_turn(self.succeeded, observe=True)
        other = self.app.issue_capability("dashboard", window_id="other", provider="codex")
        server = BrowserHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/api/chats/{self.chat['id']}/observed-files/{message['id']}"
        request = Request(url, headers={"X-PilferedParrot-Capability": other})
        with self.assertRaises(HTTPError) as failure:
            urlopen(request)
        self.assertEqual(failure.exception.code, 403)
        failure.exception.close()
