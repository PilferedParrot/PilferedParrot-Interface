"""SQLite event ordering through an explicitly opted-in, synthetic app."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.config import DEFAULTS
from pilferedparrot.acp_engine import ACPWorkResult
from pilferedparrot.dispatch import RunCancelled, RunResult
from pilferedparrot.sqlite_state import StateStoreError
from pilferedparrot.web import PilferedParrotApp


SOURCE = b'{"version":8,"chats":[],"preferences":{}}\n'


class DurableEventBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "chats.json"
        self.source.write_bytes(SOURCE)
        self.config = deepcopy(DEFAULTS)
        self.config["web"]["chat_store"] = str(self.source)
        self.config["web"]["model_catalog_store"] = str(self.root / "models.json")
        self.config["ledger"] = str(self.root / "runs.jsonl")
        self.app = PilferedParrotApp(
            self.config, self.root, sqlite_state_path=self.root / "chats.sqlite3",
        )
        self.addCleanup(self.app.shutdown)
        self.chat = self.app.create_chat({"provider": "codex", "cwd": str(self.root)})

    def pending(self):
        with self.app.store.lock:
            chat = self.app.store.get(self.chat["id"])
            pending = {
                "id": "pending_12345678", "role": "assistant", "content": "",
                "pending": True, "run_id": "run_12345678", "activity": [],
            }
            chat["messages"].append(pending)
            self.app.store.save()
            return pending["id"]

    def test_progress_commit_precedes_live_publish_and_payloads_match(self):
        message_id = self.pending()
        entered, release = threading.Event(), threading.Event()
        original = self.app.store._sqlite_state.append_event
        errors = []

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test did not release SQLite append")
            return original(*args, **kwargs)

        payload = {"message_id": message_id, "activity": {
            "id": "activity_12345678", "content": "alice@example.test checked",
            "account": {"email": "alice@example.test"},
        }}
        with patch.object(self.app.store._sqlite_state, "append_event", side_effect=delayed):
            worker = threading.Thread(target=lambda: self._publish_or_record(
                message_id, payload, errors,
            ))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.app.events.read_after(self.chat["id"], 0), [])
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        durable = self.app.store._sqlite_state.replay(session_key=self.chat["id"])
        live = self.app.events.read_after(self.chat["id"], 0)
        self.assertEqual([item.kind for item in durable], ["progress"])
        self.assertEqual([item["kind"] for item in live], ["progress"])
        self.assertEqual(durable[0].payload, live[0]["payload"])
        self.assertNotIn("alice@example.test", json.dumps(live))
        self.assertNotIn("account", durable[0].payload["activity"])
        self.assertEqual(self.source.read_bytes(), SOURCE)

    def _publish_or_record(self, message_id, payload, errors):
        try:
            self.app._publish_work_event(
                self.chat["id"], message_id, "progress", payload,
                event_id="activity_12345678",
            )
        except Exception as error:
            errors.append(error)

    def test_failed_append_restores_tree_and_never_publishes(self):
        message_id = self.pending()
        with self.app.store.lock:
            message = self.app._message(self.app.store.get(self.chat["id"]), message_id)
            message["streamed_text"] = "not committed"
        with patch.object(self.app.store._sqlite_state, "append_event",
                          side_effect=StateStoreError("injected append failure")):
            with self.assertRaises(StateStoreError):
                self.app._publish_work_event(
                    self.chat["id"], message_id, "acp_update",
                    {"message_id": message_id, "entry": {"id": "entry_12345678"}},
                )
        self.assertEqual(self.app.events.read_after(self.chat["id"], 0), [])
        self.assertNotIn("streamed_text", self.app._message(
            self.app.store.get(self.chat["id"]), message_id,
        ))
        with self.assertRaises(StateStoreError):
            self.app.store.set_draft(self.chat["id"], "rejected")
        self.assertEqual(self.source.read_bytes(), SOURCE)

    def test_terminal_document_and_event_survive_closed_browser_stream(self):
        started, release = threading.Event(), threading.Event()

        def fake_dispatch(*_args):
            started.set()
            self.assertTrue(release.wait(2))
            return RunResult("done", 0, session_id="local-provider-session")

        with patch("pilferedparrot.web.capture_dispatch", side_effect=fake_dispatch):
            self.app.send_message(self.chat["id"], {"content": "finish"})
            self.assertTrue(started.wait(1))
            with self.app.runs_lock:
                active = self.app.runs[self.chat["id"]]
            self.app.events.close()
            release.set()
            active.thread.join(2)
        self.assertFalse(active.thread.is_alive())
        durable = self.app.store._sqlite_state.replay(session_key=self.chat["id"])
        self.assertEqual([item.kind for item in durable], ["completed"])
        document = self.app.store._sqlite_state.load().document
        assistant = document["chats"][-1]["messages"][-1]
        self.assertEqual(assistant["content"], "done")
        self.assertFalse(assistant.get("pending"))
        self.assertEqual(durable[0].payload, {"message_id": assistant["id"]})
        self.assertEqual(self.source.read_bytes(), SOURCE)

    def test_terminal_insert_failure_rolls_back_and_cleans_run(self):
        started, release = threading.Event(), threading.Event()
        thread_errors = []

        def fake_dispatch(*_args):
            started.set()
            self.assertTrue(release.wait(2))
            return RunResult("done", 0, session_id="local-provider-session")

        with patch("pilferedparrot.web.capture_dispatch", side_effect=fake_dispatch), \
                patch.object(self.app.store._sqlite_state, "_insert_event",
                             side_effect=RuntimeError("injected terminal event failure")), \
                patch("threading.excepthook", side_effect=lambda args: thread_errors.append(args.exc_value)):
            self.app.send_message(self.chat["id"], {"content": "finish"})
            self.assertTrue(started.wait(1))
            with self.app.runs_lock:
                active = self.app.runs[self.chat["id"]]
            release.set()
            active.thread.join(2)
        self.assertFalse(active.thread.is_alive())
        self.assertEqual(len(thread_errors), 1)
        self.assertIn("injected terminal", str(thread_errors[0]))
        with self.app.runs_lock:
            self.assertNotIn(self.chat["id"], self.app.runs)
        self.assertEqual(self.app.store._sqlite_state.replay(session_key=self.chat["id"]), [])
        self.assertFalse(any(event["kind"] == "completed" for event in
                             self.app.events.read_after(self.chat["id"], 0)))
        document = self.app.store._sqlite_state.load().document
        self.assertTrue(document["chats"][-1]["messages"][-1]["pending"])
        self.assertEqual(self.source.read_bytes(), SOURCE)

    def test_acp_chunks_are_redacted_in_journal_and_live_stream(self):
        secret = "SYNTHETIC-ACP-SECRET"
        self.app.config["codex"]["engine"] = "acp"
        self.app.config["codex"]["api_key_env"] = "PPI_DURABLE_TEST_KEY"
        self.app.acp_adapters.locate = lambda _provider: ["fake", "agent"]

        def fake_turn(_argv, **kwargs):
            for chunk in ("Start " + secret[:9], secret[9:] + " done"):
                kwargs["on_update"]("session", {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": chunk},
                    "account": {"email": "private@example.test"},
                })
            return ACPWorkResult(
                text="Start " + secret + " done", text_truncated=False,
                session_id="local-acp-session", stop_reason="end_turn",
            )

        with patch.dict(os.environ, {"PPI_DURABLE_TEST_KEY": secret}), \
                patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "test"})
            with self.app.runs_lock:
                active = self.app.runs[self.chat["id"]]
            active.thread.join(2)
        self.assertFalse(active.thread.is_alive())
        durable = self.app.store._sqlite_state.replay(session_key=self.chat["id"])
        live = self.app.events.read_after(self.chat["id"], 0)
        self.assertEqual([item.kind for item in durable], ["acp_update", "acp_update", "completed"])
        self.assertEqual([item.kind for item in durable], [item["kind"] for item in live])
        self.assertEqual([item.payload for item in durable], [item["payload"] for item in live])
        document = self.app.store._sqlite_state.load().document
        visible = json.dumps([item.payload for item in durable] + [document])
        self.assertNotIn(secret, visible)
        self.assertNotIn("private@example.test", visible)
        self.assertEqual(self.source.read_bytes(), SOURCE)

    def test_cancel_and_provider_failure_each_commit_one_terminal_event(self):
        for outcome in ("cancel", "failure"):
            with self.subTest(outcome=outcome):
                chat = self.app.create_chat({"provider": "codex", "cwd": str(self.root)})

                def fake_dispatch(*_args):
                    if outcome == "cancel":
                        raise RunCancelled("cancelled")
                    return RunResult("provider failed", 1, error="synthetic failure")

                with patch("pilferedparrot.web.capture_dispatch", side_effect=fake_dispatch):
                    self.app.send_message(chat["id"], {"content": outcome})
                    with self.app.runs_lock:
                        active = self.app.runs[chat["id"]]
                    active.thread.join(2)
                self.assertFalse(active.thread.is_alive())
                durable = self.app.store._sqlite_state.replay(session_key=chat["id"])
                self.assertEqual([item.kind for item in durable], ["completed"])
                assistant = self.app.store._sqlite_state.load().document["chats"][-1]["messages"][-1]
                self.assertFalse(assistant.get("pending"))
                self.assertEqual(durable[0].payload, {"message_id": assistant["id"]})
                self.assertEqual(bool(assistant.get("cancelled")), outcome == "cancel")


if __name__ == "__main__":
    unittest.main()
