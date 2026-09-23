"""ACP transport tests against a deterministic stdio agent."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilferedparrot import acp_client
from pilferedparrot.acp_client import ACPClient, ACPClosed, ACPError


FIXTURES = Path(__file__).parent / "fixtures" / "acp"
CONTRACT = json.loads((FIXTURES / "v1_contract.json").read_text(encoding="utf-8"))
SENTINEL = "private.person@example.test"


def client_for(root: Path, *, fake_env: dict[str, str] | None = None, **kwargs) -> ACPClient:
    env = {**os.environ, "FAKE_ACP_SECRET": SENTINEL}
    env.update(fake_env or {})
    return ACPClient([sys.executable, str(FIXTURES / "fake_agent.py")],
                     cwd=root, env=env, **kwargs)


def transcript(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "fake-agent-requests.jsonl").read_text().splitlines()]


class ACPClientTests(unittest.TestCase):
    def assert_agent_stopped(self, client: ACPClient) -> None:
        deadline = time.monotonic() + 2
        while client._proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(client._proc.poll())

    def test_contract_shapes_default_deny_and_identity_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = []
            with client_for(root, on_update=lambda session, update: updates.append((session, update))) as client:
                initialized = client.initialize()
                self.assertEqual(initialized["protocolVersion"], 1)
                session = client.new_session(root)
                self.assertEqual(session["sessionId"], "fake-session")
                self.assertEqual(client.prompt("fake-session", "write a file")["stopReason"], "end_turn")
                client.close_session("fake-session")
            self.assertFalse((root / "allowed.txt").exists())
            self.assertNotIn(SENTINEL, str(updates))
            self.assertNotIn(SENTINEL, client.stderr_tail)
            # The fake sends both a direct _auth/status_update notification
            # and a session/update carrying that status. Neither is delivered.
            self.assertEqual([update.get("sessionUpdate") for _, update in updates],
                             ["agent_message_chunk"])
            sent = [json.loads(line) for line in (root / "fake-agent-sent.jsonl").read_text().splitlines()]
            self.assertTrue(any(message.get("method") == "_auth/status_update"
                                for message in sent))
            self.assertTrue(any(message.get("method") == "session/update"
                                and message.get("params", {}).get("update", {}).get("sessionUpdate")
                                == "_auth/status_update" for message in sent))
            messages = transcript(root)
            definitions = CONTRACT["definitions"]
            by_method = {definition["x-method"]: definition
                         for name, definition in definitions.items()
                         if name.endswith(("Request", "Notification")) and "x-method" in definition}
            for message in messages:
                method = message.get("method")
                if method not in by_method:
                    continue
                for field in by_method[method].get("required", []):
                    self.assertIn(field, message["params"], (method, field))
            init = next(message for message in messages if message.get("method") == "initialize")
            self.assertFalse(init["params"]["clientCapabilities"]["terminal"])
            self.assertEqual(init["params"]["clientCapabilities"]["fs"],
                             {"readTextFile": False, "writeTextFile": False})
            permission = next(message for message in messages if message.get("id") == "permission-1")
            self.assertEqual(permission["result"]["outcome"],
                             {"outcome": "selected", "optionId": "no"})
            self.assertFalse(any(message.get("method", "").startswith(("fs/", "terminal/"))
                                 for message in messages))

    def test_explicit_permission_can_allow_and_dynamic_options_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            updates = []
            permissions = []
            def allow(params):
                permissions.append(params)
                return "yes"
            with client_for(root, on_update=lambda _session, update: updates.append(update),
                            on_permission=allow) as client:
                client.initialize()
                client.new_session(root)
                client.set_config_option("fake-session", "model", "sol")
                client.set_mode("fake-session", "plan")
                self.assertEqual(client.prompt("fake-session", "write a file")["stopReason"], "end_turn")
            self.assertEqual((root / "allowed.txt").read_text(), "changed")
            self.assertEqual(len(permissions), 1)
            seen_permission = json.dumps(permissions[0])
            self.assertNotIn(SENTINEL, seen_permission)
            self.assertNotIn('"account"', seen_permission)
            self.assertNotIn('"accountEmail"', seen_permission)
            self.assertNotIn('"email"', seen_permission)
            self.assertTrue(any(update.get("sessionUpdate") == "config_option_update"
                                and update["configOptions"][0]["currentValue"] == "sol"
                                for update in updates))
            self.assertTrue(any(update.get("sessionUpdate") == "current_mode_update"
                                and update["currentModeId"] == "plan" for update in updates))

    def test_cancel_notification_finishes_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            started = threading.Event()
            def on_update(_session, update):
                if update.get("sessionUpdate") == "agent_message_chunk":
                    started.set()
            with client_for(root, on_update=on_update) as client:
                client.initialize()
                client.new_session(root)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(client.prompt, "fake-session", "wait-cancel", timeout=10)
                    self.assertTrue(started.wait(2))
                    client.cancel("fake-session")
                    self.assertEqual(future.result(timeout=2)["stopReason"], "cancelled")
            self.assertTrue(any(message.get("method") == "session/cancel" for message in transcript(root)))

    def test_agent_eof_fails_pending_prompt_promptly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root) as client:
                client.initialize()
                client.new_session(root)
                start = time.monotonic()
                with self.assertRaises(ACPClosed):
                    client.prompt("fake-session", "exit", timeout=10)
                self.assertLess(time.monotonic() - start, 2)

    def test_update_callback_failure_fails_prompt_and_stops_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def broken_callback(_session, _update):
                # The fake agent can finish the turn before this fails; the
                # completion barrier must still prevent a successful prompt.
                time.sleep(0.05)
                raise RuntimeError(f"callback could not persist event: {SENTINEL}")
            with client_for(root, on_update=broken_callback) as client:
                client.initialize()
                client.new_session(root)
                with self.assertRaisesRegex(ACPClosed, "ACP update callback failed") as captured:
                    client.prompt("fake-session", "write a file", timeout=2)
                self.assertNotIn(SENTINEL, str(captured.exception))
                self.assert_agent_stopped(client)
            self.assertFalse((root / "allowed.txt").exists())

    def test_ingress_exception_fails_request_without_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root) as client:
                client.initialize()
                def broken_ingress(_message):
                    raise ACPClosed("sensitive payload")
                client._ingest = broken_ingress
                with self.assertRaises(ACPClosed) as captured:
                    client.new_session(root, timeout=2)
                self.assertNotIn("sensitive payload", str(captured.exception))
                self.assert_agent_stopped(client)

    def test_invalid_and_oversized_lines_stop_live_agent(self):
        for content in ("invalid-json", "oversized-line"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with client_for(root, max_line_chars=1024) as client:
                    client.initialize()
                    client.new_session(root)
                    with self.assertRaises(ACPClosed):
                        client.prompt("fake-session", content, timeout=2)
                    self.assert_agent_stopped(client)

    def test_prompt_timeout_forces_agent_that_ignores_cancel_to_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root) as client:
                client.initialize()
                client.new_session(root)
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    client.prompt("fake-session", "ignore-cancel", timeout=0.05,
                                  cancel_grace=0.05)
                self.assertLess(time.monotonic() - start, 2)
                self.assert_agent_stopped(client)
            self.assertTrue(any(message.get("method") == "session/cancel"
                                for message in transcript(root)))

    def test_prompt_timeout_includes_blocked_stdin_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root, fake_env={"FAKE_ACP_PAUSE_AFTER_NEW": "1"}) as client:
                client.initialize()
                client.new_session(root)
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    client.prompt("fake-session", "x" * 2_000_000, timeout=0.05,
                                  cancel_grace=0.05)
                self.assertLess(time.monotonic() - start, 1)
                self.assert_agent_stopped(client)

    def test_close_drains_full_update_queue_and_stops_dispatcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = threading.Event(), threading.Event()
            def blocking_callback(_session, _update):
                entered.set()
                release.wait(2)
            client = client_for(root, on_update=blocking_callback)
            try:
                client.initialize()
                client.new_session(root)
                item = ("update", "fake-session", {"sessionUpdate": "agent_message_chunk"})
                client._updates.put_nowait(item)
                self.assertTrue(entered.wait(1))
                for _ in range(client._updates.maxsize):
                    client._updates.put_nowait(item)
                closer = threading.Thread(target=client.close)
                closer.start()
                time.sleep(0.05)
                release.set()
                closer.join(timeout=3)
                self.assertFalse(closer.is_alive())
                self.assertFalse(client._dispatcher.is_alive())
            finally:
                release.set()
                client.close()

    def test_windows_taskkill_failure_terminates_parent(self):
        client = object.__new__(ACPClient)
        client._stop_lock = threading.Lock()
        client._proc = MagicMock(pid=12345)
        client._proc.poll.return_value = None
        with patch.object(acp_client.sys, "platform", "win32"), \
             patch.object(acp_client.subprocess, "run", return_value=subprocess.CompletedProcess(
                 ["taskkill"], 1)):
            client._stop_process()
        client._proc.terminate.assert_called_once_with()

    def test_malformed_permission_defaults_to_reject_and_close_reaps_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root) as client:
                client.initialize()
                client.new_session(root)
                self.assertEqual(client.prompt("fake-session", "malformed-permission")["stopReason"],
                                 "end_turn")
                process = client._proc
            self.assertFalse((root / "allowed.txt").exists())
            self.assertIsNotNone(process.poll())

    def test_malformed_permission_never_reaches_explicit_allow_callback(self):
        malformed = (
            "malformed-permission", "malformed-session-type", "malformed-tool-call",
            "malformed-tool-id", "malformed-options", "malformed-option-item",
        )
        for content in malformed:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                callbacks = []
                def allow(params):
                    callbacks.append(params)
                    return "yes"
                with client_for(root, on_permission=allow) as client:
                    client.initialize()
                    client.new_session(root)
                    self.assertEqual(client.prompt("fake-session", content)["stopReason"],
                                     "end_turn")
                self.assertEqual(callbacks, [])
                self.assertFalse((root / "allowed.txt").exists())
                reply = next(message for message in transcript(root)
                             if message.get("id") == "permission-1")
                self.assertEqual(reply["result"]["outcome"], {"outcome": "cancelled"})

    def test_reused_agent_request_id_fails_before_a_second_permission_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = threading.Event(), threading.Event()
            callbacks = []
            def delayed_allow(params):
                callbacks.append(params["toolCall"]["toolCallId"])
                entered.set()
                release.wait(2)
                return "yes"
            with client_for(root, on_permission=delayed_allow) as client:
                client.initialize()
                client.new_session(root)
                def request(tool_id):
                    return {"jsonrpc": "2.0", "id": "reused-permission-id",
                            "method": "session/request_permission", "params": {
                                "sessionId": "fake-session",
                                "toolCall": {"toolCallId": tool_id},
                                "options": [
                                    {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                                    {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                                ],
                            }}
                client._ingest(request("first-tool"))
                self.assertTrue(entered.wait(1))
                client._ingest(request("second-tool"))
                release.set()
                self.assertEqual(callbacks, ["first-tool"])
                self.assertIsInstance(client._failure, ACPClosed)
                self.assert_agent_stopped(client)

    def test_ingress_denies_permission_if_review_action_would_be_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            callbacks = []
            with client_for(root, on_permission=lambda params: callbacks.append(params) or "yes") as client:
                client.initialize()
                client.new_session(root)
                client._ingest({"jsonrpc": "2.0", "id": "email-action",
                                "method": "session/request_permission", "params": {
                                    "sessionId": "fake-session",
                                    "toolCall": {
                                        "toolCallId": "tool-email", "kind": "execute",
                                        "rawInput": {"command": "printf private@example.test"},
                                    },
                                    "options": [
                                        {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                                        {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                                    ],
                                }})
                deadline = time.monotonic() + 1
                reply = None
                while time.monotonic() < deadline:
                    path = root / "fake-agent-requests.jsonl"
                    if path.exists():
                        reply = next((item for item in transcript(root)
                                      if item.get("id") == "email-action"), None)
                    if reply is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(reply)
                self.assertEqual(reply["result"]["outcome"], {"outcome": "cancelled"})
                self.assertEqual(callbacks, [])

    def test_cancel_pending_permission_prevents_late_allow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = threading.Event(), threading.Event()
            def delayed_allow(_params):
                entered.set()
                release.wait(2)
                return "yes"
            with client_for(root, on_permission=delayed_allow) as client:
                client.initialize()
                client.new_session(root)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(client.prompt, "fake-session", "write a file", timeout=2)
                    self.assertTrue(entered.wait(1))
                    client.cancel("fake-session")
                    release.set()
                    self.assertIn(future.result(timeout=2)["stopReason"], {"cancelled", "end_turn"})
            self.assertFalse((root / "allowed.txt").exists())
            replies = [message for message in transcript(root)
                       if message.get("id") == "permission-1"]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0]["result"]["outcome"], {"outcome": "cancelled"})

    def test_stderr_tail_is_bounded_after_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root, stderr_limit=16) as client:
                client.initialize()
            self.assertLessEqual(len(client.stderr_tail), 16)
            self.assertNotIn("example.test", client.stderr_tail)

    def test_update_flush_orders_prior_callbacks_and_has_a_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered, release = threading.Event(), threading.Event()
            observed = []
            def slow_update(_session, update):
                entered.set()
                release.wait(2)
                observed.append(update["content"]["text"])
            with client_for(root, on_update=slow_update) as client:
                client.initialize()
                client.new_session(root)
                client._ingest({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "fake-session", "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "from load"},
                    },
                }})
                self.assertTrue(entered.wait(1))
                with self.assertRaises(TimeoutError):
                    client.flush_updates(timeout=0.03)
                release.set()
                client.flush_updates(timeout=1)
                self.assertEqual(observed, ["from load"])

    @unittest.skipIf(sys.platform == "win32", "POSIX process group ownership test")
    def test_close_stops_descendant_after_adapter_parent_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "orphan_adapter.py"
            script.write_text(
                "import json, os, subprocess, sys, time\n"
                "request = json.loads(sys.stdin.readline())\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
                "stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr)\n"
                "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': request['id'], "
                "'result': {'protocolVersion': 1, 'agentCapabilities': {}}}) + '\\n')\n"
                "sys.stdout.flush()\n"
                "os._exit(0)\n",
                encoding="utf-8",
            )
            client = ACPClient([sys.executable, str(script)], cwd=root)
            try:
                self.assertEqual(client.initialize(timeout=2)["protocolVersion"], 1)
                client._proc.wait(timeout=1)
                closed = threading.Thread(target=client.close, daemon=True)
                closed.start()
                closed.join(timeout=4)
                self.assertFalse(closed.is_alive(), "close blocked on inherited pipe")
                self.assertFalse(client._reader.is_alive())
            finally:
                # If a regression leaves the inherited pipe open, stop only
                # this client-owned process group so the test cannot leak it.
                try:
                    os.killpg(client.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_agent_error_does_not_expose_account_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root) as client:
                client.initialize()
                client.new_session(root)
                with self.assertRaises(ACPError) as captured:
                    client.prompt("fake-session", "error")
            self.assertNotIn(SENTINEL, str(captured.exception))

    def test_resume_after_process_restart_and_request_multiplexing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root) as client:
                client.initialize()
                session = client.new_session(root)["sessionId"]
            with client_for(root) as client:
                client.initialize()
                client.resume_session(session, root)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    mode = executor.submit(client.set_mode, session, "plan")
                    model = executor.submit(client.set_config_option, session, "model", "sol")
                    self.assertEqual(mode.result(timeout=2), {"currentModeId": "plan"})
                    self.assertEqual(model.result(timeout=2)["configOptions"][0]["currentValue"], "sol")
                self.assertEqual(client.prompt(session, "resumed")["stopReason"], "end_turn")
                client.load_session(session, root)
            methods = [message.get("method") for message in transcript(root)]
            self.assertIn("session/resume", methods)
            self.assertIn("session/load", methods)


if __name__ == "__main__":
    unittest.main()
