"""Live event replay and reset behavior across disconnected browser readers."""

import os
import threading
import time
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pilferedparrot.config import DEFAULTS
from pilferedparrot.dispatch import RunResult
from pilferedparrot.web import PilferedParrotApp
from pilferedparrot.web_events import EventHub
from pilferedparrot.web_server import BrowserHTTPServer, make_handler


class EventHubTests(unittest.TestCase):
    def test_replay_is_ordered_and_isolated_by_chat(self):
        hub = EventHub(per_chat_limit=3)
        self.assertEqual(hub.publish("first", "progress", {"text": "one"}), 1)
        self.assertEqual(hub.publish("second", "progress", {"text": "other"}), 2)
        self.assertEqual(hub.publish("first", "completed", {}), 3)
        self.assertEqual([event["seq"] for event in hub.read_after("first", 0)], [1, 3])
        self.assertEqual([event["seq"] for event in hub.read_after("second", 0)], [2])
        self.assertEqual(hub.read_after("first", 3), [])

    def test_gap_and_eviction_require_snapshot_reset(self):
        hub = EventHub(per_chat_limit=2, chat_limit=1)
        for index in range(3):
            hub.publish("first", "progress", {"index": index})
        self.assertEqual(hub.read_after("first", 0)[0]["kind"], "reset")
        self.assertEqual([event["seq"] for event in hub.read_after("first", 1)], [2, 3])
        hub.publish("second", "progress", {})
        self.assertEqual(hub.read_after("first", 3)[0]["kind"], "reset")
        self.assertEqual(hub.publish("first", "progress", {"index": 4}), 5)
        self.assertEqual(hub.read_after("first", 3)[0]["kind"], "reset")
        with self.assertRaises(ValueError):
            hub.publish("first", "_auth/status_update", {"email": "private"})
        with self.assertRaises(ValueError):
            hub.publish("first", "acp_update", {"update": {
                "sessionUpdate": "_auth/status_update", "account": "private",
            }})

    def test_wait_wakes_on_new_event_and_close(self):
        hub = EventHub()
        observed = []
        waiting = threading.Thread(target=lambda: observed.extend(
            hub.wait_after("first", 0, timeout=1),
        ))
        waiting.start()
        time.sleep(0.02)
        hub.publish("first", "progress", {"text": "ready"})
        waiting.join(timeout=1)
        self.assertFalse(waiting.is_alive())
        self.assertEqual(observed[0]["payload"]["text"], "ready")
        waiting = threading.Thread(target=lambda: observed.extend(
            hub.wait_after("first", 1, timeout=1),
        ))
        waiting.start()
        time.sleep(0.02)
        hub.close()
        waiting.join(timeout=1)
        self.assertFalse(waiting.is_alive())
        self.assertEqual(len(observed), 1)


class WorkEventHTTPTests(unittest.TestCase):
    def test_slow_run_cleans_up_after_stream_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = deepcopy(DEFAULTS)
            config["web"]["chat_store"] = str(root / "chats.json")
            config["web"]["model_catalog_store"] = str(root / "models.json")
            config["ledger"] = str(root / "runs.jsonl")
            started, release = threading.Event(), threading.Event()

            def fake_dispatch(_provider, _prompt, _cwd, _conversation, _config, _cancel_event):
                started.set()
                release.wait(1)
                return RunResult("done", 0, session_id="late-session")

            with patch("pilferedparrot.web.capture_dispatch", side_effect=fake_dispatch):
                app = PilferedParrotApp(config, root)
                chat = app.create_chat({"cwd": str(root)}, window_id="main",
                                       window_provider="codex")
                app.send_message(chat["id"], {"content": "test", "provider": "codex"},
                                 window_id="main", window_provider="codex")
                self.assertTrue(started.wait(1))
                app.shutdown(timeout=0.01)
                release.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with app.runs_lock:
                        if chat["id"] not in app.runs:
                            break
                    time.sleep(0.01)
                with app.runs_lock:
                    self.assertNotIn(chat["id"], app.runs)
                with app.store.lock:
                    self.assertFalse(any(message.get("pending") for message in
                                         app.store.get(chat["id"])["messages"]))

    def test_progress_redacts_secret_before_bounding_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = deepcopy(DEFAULTS)
            config["web"]["chat_store"] = str(root / "chats.json")
            config["web"]["model_catalog_store"] = str(root / "models.json")
            config["ledger"] = str(root / "runs.jsonl")
            config["codex"]["api_key_env"] = "PPI_STREAM_TEST_KEY"
            secret = "ABCDEFGH"

            def fake_dispatch(_provider, _prompt, _cwd, _conversation, _config, cancel_event):
                cancel_event._pilferedparrot_progress(
                    "status", "x" * 3996 + secret + " after-boundary",
                )
                return RunResult("done", 0, session_id="fake-stream-session")

            with patch.dict(os.environ, {"PPI_STREAM_TEST_KEY": secret}), \
                    patch("pilferedparrot.web.capture_dispatch", side_effect=fake_dispatch):
                app = PilferedParrotApp(config, root)
                try:
                    chat = app.create_chat({"cwd": str(root)}, window_id="main",
                                           window_provider="codex")
                    app.send_message(chat["id"], {"content": "test", "provider": "codex"},
                                     window_id="main", window_provider="codex")
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        with app.store.lock:
                            messages = app.store.get(chat["id"])["messages"]
                            if messages and not any(item.get("pending") for item in messages):
                                break
                        time.sleep(0.01)
                    events = app.events.read_after(chat["id"], 0)
                    progress = next(item for item in events if item["kind"] == "progress")
                    with app.store.lock:
                        activity = app.store.get(chat["id"])["messages"][-1]["activity"][0]
                    for text in (activity["content"], progress["payload"]["activity"]["content"]):
                        self.assertNotIn(secret, text)
                        self.assertNotIn("ABCD", text)
                        self.assertLessEqual(len(text), 4000)
                finally:
                    app.shutdown()

    def test_stream_replays_in_order_and_rejects_other_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = deepcopy(DEFAULTS)
            config["web"]["port"] = 0
            config["web"]["chat_store"] = str(root / "chats.json")
            config["web"]["model_catalog_store"] = str(root / "models.json")
            config["ledger"] = str(root / "runs.jsonl")
            app = PilferedParrotApp(config, root)
            server = BrowserHTTPServer(("127.0.0.1", 0), make_handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                chat = app.create_chat({"cwd": str(root)}, window_id="main",
                                       window_provider="codex")
                app.events.publish(chat["id"], "progress", {"activity": {"content": "ready"}})
                app.events.publish(chat["id"], "completed", {"message_id": "done"})
                url = f"http://127.0.0.1:{server.server_address[1]}/api/chats/{chat['id']}/events?after=0"
                with urlopen(Request(url, headers={
                    "X-PilferedParrot-Capability": app.dashboard_capability,
                }), timeout=2) as response:
                    body = response.read().decode("utf-8")
                    self.assertEqual(response.headers.get_content_type(), "text/event-stream")
                self.assertLess(body.index("event: progress"), body.index("event: completed"))
                self.assertIn('"content":"ready"', body)
                other = app.issue_capability("dashboard", window_id="provider-codex",
                                             provider="codex")
                with self.assertRaises(HTTPError) as blocked:
                    urlopen(Request(url, headers={
                        "X-PilferedParrot-Capability": other,
                    }), timeout=2)
                self.assertEqual(blocked.exception.code, 404)
                blocked.exception.close()
                with self.assertRaises(HTTPError) as missing_epoch:
                    urlopen(Request(url.replace("after=0", "after=1"), headers={
                        "X-PilferedParrot-Capability": app.dashboard_capability,
                    }), timeout=2)
                self.assertEqual(missing_epoch.exception.code, 400)
                missing_epoch.exception.close()
                resume = url.replace("after=0", f"after=2&epoch={app.events.epoch}")
                with urlopen(Request(resume, headers={
                    "X-PilferedParrot-Capability": app.dashboard_capability,
                }), timeout=2) as response:
                    for _ in range(4):
                        if response.readline() == b"\n":
                            break  # Consume only the hello frame before revocation.
                    else:
                        self.fail("stream did not send a hello frame")
                    app.revoke_capability(app.dashboard_capability)
                    app.events.publish(chat["id"], "progress", {"activity": {"content": "private"}})
                    app.events.publish(chat["id"], "completed", {"message_id": "late"})
                    self.assertNotIn("event: progress", response.read().decode("utf-8"))
                fresh = app.issue_capability("dashboard", window_id="main", provider="codex")
                app.events.close()
                started = time.monotonic()
                with self.assertRaises(HTTPError) as closed:
                    urlopen(Request(url, headers={
                        "X-PilferedParrot-Capability": fresh,
                    }), timeout=2)
                self.assertEqual(closed.exception.code, 503)
                closed.exception.close()
                self.assertLess(time.monotonic() - started, 1)
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
