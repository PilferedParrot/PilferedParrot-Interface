"""Opt-in ACP Work integration with fake turns; no provider process or paid calls."""
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

from pilferedparrot.acp_engine import ACPCancelled, ACPWorkResult
from pilferedparrot.config import DEFAULTS
from pilferedparrot.web import PilferedParrotApp
from pilferedparrot.web_server import BrowserHTTPServer, make_handler


def _result(text, session_id, stop_reason, **kwargs):
    return ACPWorkResult(text=text, text_truncated=False, session_id=session_id,
                         stop_reason=stop_reason, **kwargs)


class ACPWorkBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = deepcopy(DEFAULTS)
        self.config["web"]["chat_store"] = str(self.root / "chats.json")
        self.config["web"]["port"] = 0
        self.config["web"]["model_catalog_store"] = str(self.root / "models.json")
        self.config["ledger"] = str(self.root / "runs.jsonl")
        self.config["codex"]["engine"] = "acp"
        self.config["codex"]["model"] = "exact-model"
        self.config["codex"]["api_key_env"] = "PPI_ACP_TEST_KEY"
        self.secret = "ACP-SECRET-12345"
        self.env_patch = patch.dict(os.environ, {"PPI_ACP_TEST_KEY": self.secret})
        self.env_patch.start()
        self.app = PilferedParrotApp(self.config, self.root)
        self.app.acp_adapters.locate = lambda provider: ["fake", provider]
        self.chat = self.app.create_chat({"cwd": str(self.root)}, window_id="main",
                                         window_provider="codex")

    def tearDown(self):
        self.app.shutdown()
        self.env_patch.stop()
        self.temp.cleanup()

    def wait_done(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.app.runs_lock:
                if self.chat["id"] not in self.app.runs:
                    return
            time.sleep(.01)
        self.fail("ACP run did not finish")

    def messages(self):
        with self.app.store.lock:
            return deepcopy(self.app.store.get(self.chat["id"])["messages"])

    def test_exact_model_resume_and_recursive_secret_redaction(self):
        calls = []
        def fake_turn(argv, **kwargs):
            calls.append(kwargs)
            kwargs["on_update"]("session", kwargs["sanitize_update"]({
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": f"safe {self.secret}"},
                "account": {"email": "private@example.test"},
            }))
            kwargs["on_update"]("session", kwargs["sanitize_update"]({
                "sessionUpdate": "available_commands_update", "commands": [self.secret],
            }))
            return _result(f"safe {self.secret}", "session", "end_turn",
                                 model=kwargs["model"])
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "first"}, window_id="main")
            self.wait_done()
            self.app.send_message(self.chat["id"], {"content": "second"}, window_id="main")
            self.wait_done()
        self.assertEqual([call["session_id"] for call in calls], [None, "session"])
        self.assertEqual([call["model"] for call in calls], ["exact-model"] * 2)
        self.assertEqual(calls[0]["cwd"], self.root)
        self.assertNotIn("CODEX_THREAD_ID", calls[0]["env"])
        self.assertEqual(self.messages()[-1]["content"], "safe [redacted]")
        snapshot = (self.root / "chats.json").read_text()
        events = json.dumps(self.app.events.read_after(self.chat["id"], 0))
        self.assertNotIn(self.secret, snapshot + events)
        self.assertNotIn("private@example.test", snapshot + events)
        self.assertNotIn("available_commands_update", snapshot + events)
        entries = self.messages()[-1]["acp_updates"]
        streamed = [e["payload"]["entry"] for e in self.app.events.read_after(self.chat["id"], 0)
                    if e["kind"] == "acp_update"]
        self.assertEqual(entries, streamed[-1:])

    def test_engine_switch_never_loads_legacy_session(self):
        with self.app.store.lock:
            chat = self.app.store.get(self.chat["id"])
            chat["provider"] = "codex"
            chat["model"] = "exact-model"
            chat["provider_session_id"] = "legacy-id"
            self.app.store.save()
        captured = []
        def fake_turn(_argv, **kwargs):
            captured.append(kwargs["session_id"])
            return _result("done", "acp-id", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        self.assertEqual(captured, [None])
        with self.app.store.lock:
            chat = self.app.store.get(self.chat["id"])
            self.assertEqual(chat["session_engine"], "acp")
            self.assertEqual(chat["provider_session_id"], "acp-id")

    def test_permission_choice_and_wrong_window(self):
        started = threading.Event()
        choice = []
        def fake_turn(_argv, **kwargs):
            def seek_permission():
                choice.append(kwargs["on_permission"]({
                    "sessionId": "session", "toolCall": {
                        "toolCallId": "tool", "name": "shell", "kind": "execute",
                        "rawInput": {"command": "pwd"}},
                    "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once",
                                 "_meta": {"account": "private@example.test"}},
                                {"optionId": "no", "name": "Reject", "kind": "reject_once"}],
                }))
            worker = threading.Thread(target=seek_permission)
            worker.start()
            started.set()
            worker.join(timeout=1)
            return _result("done", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.assertTrue(started.wait(1))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                permissions = self.app.work_permissions(self.chat["id"], window_id="main")["permissions"]
                if permissions:
                    break
                time.sleep(.01)
            self.assertTrue(permissions)
            request = permissions[0]["request"]
            with self.assertRaises(KeyError):
                self.app.work_permissions(self.chat["id"], window_id="wrong")
            with self.assertRaises(KeyError):
                self.app.decide_work_permission(self.chat["id"], {
                    "request_id": request["requestId"], "option_id": "yes"}, window_id="wrong")
            self.assertFalse(self.app.decide_work_permission(self.chat["id"], {
                "request_id": request["requestId"], "option_id": "forged"}, window_id="main")["accepted"])
            self.assertTrue(self.app.decide_work_permission(self.chat["id"], {
                "request_id": request["requestId"], "option_id": "yes"}, window_id="main")["accepted"])
            self.wait_done()
        self.assertEqual(choice, ["yes"])
        self.assertEqual(self.app.work_permissions(self.chat["id"], window_id="main"),
                         {"permissions": []})
        self.assertIn("permission_closed", [e["kind"] for e in
                                         self.app.events.read_after(self.chat["id"], 0)])

    def test_cancel_during_acp_turn(self):
        started = threading.Event()
        observed_cancel = threading.Event()
        def fake_turn(_argv, **kwargs):
            started.set()
            self.assertTrue(kwargs["cancel_event"].wait(1))
            observed_cancel.set()
            raise ACPCancelled("cancelled")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.assertTrue(started.wait(1))
            self.app.cancel_message(self.chat["id"], window_id="main")
            self.wait_done()
        self.assertTrue(observed_cancel.is_set())
        self.assertTrue(self.messages()[-1]["cancelled"])

    def test_redacted_permission_action_is_default_denied(self):
        decisions = []
        base = {"sessionId": "session", "toolCall": {
            "toolCallId": "tool", "name": "shell", "kind": "execute",
            "rawInput": {"command": "pwd"}},
            "options": [{"optionId": "yes", "name": "Allow", "kind": "allow_once"}],
        }
        def fake_turn(_argv, **kwargs):
            for field, value in (
                ("title", f"review {self.secret}"),
                ("rawInput", {"command": f"echo {self.secret}"}),
                ("content", [{"type": "diff", "path": "file.txt",
                              "newText": f"{self.secret}"}]),
                ("name", "private@example.test"),
            ):
                request = deepcopy(base)
                request["toolCall"][field] = value
                decisions.append(kwargs["on_permission"](request))
            return _result("done", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        self.assertEqual(decisions, [None] * 4)
        self.assertFalse(any(e["kind"] == "permission" for e in
                             self.app.events.read_after(self.chat["id"], 0)))

    def test_oversized_assistant_chunk_fails_visibly(self):
        def fake_turn(_argv, **kwargs):
            try:
                kwargs["on_update"]("session", kwargs["sanitize_update"]({
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "x" * (129 * 1024)},
                }))
            except ValueError:
                raise RuntimeError("wrapped callback failure")
            return _result("unreachable", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        message = self.messages()[-1]
        self.assertTrue(message["error"])
        self.assertIn("ACP assistant update exceeds the display limit", message["content"])
        self.assertNotIn("x" * 1024, (self.root / "chats.json").read_text())

    def test_usage_update_maps_to_context_and_empty_success_completes(self):
        def fake_turn(_argv, **kwargs):
            kwargs["on_update"]("session", kwargs["sanitize_update"]({
                "sessionUpdate": "usage_update", "used": 42, "size": 100,
            }))
            kwargs["on_update"]("session", kwargs["sanitize_update"]({
                "sessionUpdate": "usage_update", "used": -1, "size": 100,
            }))
            return _result("", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        self.assertEqual(self.messages()[-1]["content"], "Completed.")
        with self.app.store.lock:
            chat = self.app.store.get(self.chat["id"])
            self.assertEqual(chat["live_context_usage"]["input_tokens"], 42)
            self.assertEqual(chat["context_limit_tokens"], 100)
        usage = [e for e in self.app.events.read_after(self.chat["id"], 0)
                 if e["kind"] == "usage"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["payload"]["context_usage"]["used_tokens"], 42)

    def test_nested_auth_status_is_never_stored_or_streamed(self):
        def fake_turn(_argv, **kwargs):
            kwargs["on_update"]("session", {
                "sessionUpdate": "tool_call_update",
                "nested": {"method": "_auth/status_update", "email": "private@example.test"},
            })
            return _result("done", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        self.assertNotIn("acp_updates", self.messages()[-1])
        self.assertNotIn("_auth/status_update", (self.root / "chats.json").read_text())
        self.assertFalse(any(e["kind"] == "acp_update" for e in
                             self.app.events.read_after(self.chat["id"], 0)))

    def test_split_secret_and_email_use_redacted_replacement_stream(self):
        email = "private@example.test"
        chunks = [
            "Start " + self.secret[:5],
            self.secret[5:] + " and " + email[:8],
            email[8:] + " finished.",
        ]
        def fake_turn(_argv, **kwargs):
            for chunk in chunks:
                kwargs["on_update"]("session", kwargs["sanitize_update"]({
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": chunk},
                }))
            return _result("", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        message = self.messages()[-1]
        self.assertEqual(message["content"],
                         "Start [redacted] and [redacted-email] finished.")
        updates = message["acp_updates"]
        self.assertTrue(all(entry["update"]["content"]["text"] == "" for entry in updates))
        self.assertEqual(updates[-1]["update"]["streamed_text"], message["streamed_text"])
        events = [item for item in self.app.events.read_after(self.chat["id"], 0)
                  if item["kind"] == "acp_update"]
        replacements = "".join(item["payload"]["entry"]["update"]["streamed_text"]
                               for item in events)
        serialized = (self.root / "chats.json").read_text() + json.dumps(events) + replacements
        self.assertNotIn(self.secret, serialized)
        self.assertNotIn(email, serialized)

    def test_tool_diff_preserved_without_extension_or_terminal_deltas(self):
        def fake_turn(_argv, **kwargs):
            kwargs["on_update"]("session", kwargs["sanitize_update"]({
                "sessionUpdate": "tool_call_update", "toolCallId": "tool",
                "content": [
                    {"type": "diff", "path": "file.txt", "newText": "updated"},
                    {"type": "terminal", "text": self.secret[:6]},
                ],
                "rawOutput": self.secret[6:],
                "_meta": {"terminal_delta": self.secret},
            }))
            return _result("done", "session", "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        update = self.messages()[-1]["acp_updates"][0]["update"]
        self.assertEqual(update["content"], [
            {"type": "diff", "path": "file.txt", "newText": "updated"},
        ])
        self.assertNotIn("rawOutput", update)
        self.assertNotIn("_meta", update)
        serialized = (self.root / "chats.json").read_text() + json.dumps(
            self.app.events.read_after(self.chat["id"], 0))
        self.assertNotIn(self.secret, serialized)

    def test_invalid_stop_reason_is_rejected_without_persistence(self):
        def fake_turn(_argv, **_kwargs):
            return _result("done", "session", self.secret)
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "hello"}, window_id="main")
            self.wait_done()
        message = self.messages()[-1]
        self.assertTrue(message["error"])
        self.assertIn("unsupported stop reason", message["content"])
        self.assertNotIn("acp_stop_reason", message)
        snapshot = (self.root / "chats.json").read_text()
        self.assertNotIn(self.secret, snapshot)
        with self.app.store.lock:
            self.assertIsNone(self.app.store.get(self.chat["id"]).get("provider_session_id"))

    def test_max_tokens_keeps_session_for_next_turn(self):
        seen = []
        def fake_turn(_argv, **kwargs):
            seen.append(kwargs["session_id"])
            return _result("partial" if len(seen) == 1 else "done", "session",
                                 "max_tokens" if len(seen) == 1 else "end_turn")
        with patch("pilferedparrot.web.run_acp_turn", side_effect=fake_turn):
            self.app.send_message(self.chat["id"], {"content": "first"}, window_id="main")
            self.wait_done()
            self.assertTrue(self.messages()[-1]["error"])
            with self.app.store.lock:
                chat = self.app.store.get(self.chat["id"])
                self.assertEqual(chat["provider_session_id"], "session")
                self.assertEqual(chat["session_engine"], "acp")
            self.app.send_message(self.chat["id"], {"content": "second"}, window_id="main")
            self.wait_done()
        self.assertEqual(seen, [None, "session"])
        self.assertFalse(self.messages()[-1]["error"])

    def test_permission_http_requires_owning_capability_and_origin(self):
        server = BrowserHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/chats/{self.chat['id']}/permissions"
            wrong = self.app.issue_capability("dashboard", window_id="other", provider="codex")
            for token, status in ((wrong, 404), ("invalid", 403)):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(Request(url, headers={
                        "X-PilferedParrot-Capability": token,
                    }), timeout=2)
                self.assertEqual(failure.exception.code, status)
                failure.exception.close()
            with urlopen(Request(url, headers={
                "X-PilferedParrot-Capability": self.app.dashboard_capability,
            }), timeout=2) as response:
                self.assertEqual(json.load(response), {"permissions": []})
            body = json.dumps({"request_id": "forged", "option_id": "yes"}).encode()
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(url, data=body, method="POST", headers={
                    "X-PilferedParrot-Capability": self.app.dashboard_capability,
                    "Content-Type": "application/json",
                }), timeout=2)
            self.assertEqual(failure.exception.code, 403)
            failure.exception.close()
            with urlopen(Request(url, data=body, method="POST", headers={
                "X-PilferedParrot-Capability": self.app.dashboard_capability,
                "Origin": f"http://127.0.0.1:{server.server_address[1]}",
                "Content-Type": "application/json",
            }), timeout=2) as response:
                self.assertEqual(json.load(response), {"accepted": False})
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
