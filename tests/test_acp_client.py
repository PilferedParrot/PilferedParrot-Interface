"""ACP transport tests against a deterministic stdio agent."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pilferedparrot.acp_client import ACPClient, ACPClosed, ACPError


FIXTURES = Path(__file__).parent / "fixtures" / "acp"
CONTRACT = json.loads((FIXTURES / "v1_contract.json").read_text(encoding="utf-8"))
SENTINEL = "private.person@example.test"


def client_for(root: Path, **kwargs) -> ACPClient:
    env = {**os.environ, "FAKE_ACP_SECRET": SENTINEL}
    return ACPClient([sys.executable, str(FIXTURES / "fake_agent.py")],
                     cwd=root, env=env, **kwargs)


def transcript(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "fake-agent-requests.jsonl").read_text().splitlines()]


class ACPClientTests(unittest.TestCase):
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
            self.assertTrue(any(update["sessionUpdate"] == "agent_message_chunk"
                                for _, update in updates))
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
            with client_for(root, on_update=lambda _session, update: updates.append(update),
                            on_permission=lambda params: "yes") as client:
                client.initialize()
                client.new_session(root)
                client.set_config_option("fake-session", "model", "sol")
                client.set_mode("fake-session", "plan")
                self.assertEqual(client.prompt("fake-session", "write a file")["stopReason"], "end_turn")
            self.assertEqual((root / "allowed.txt").read_text(), "changed")
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

    def test_stderr_tail_is_bounded_after_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with client_for(root, stderr_limit=16) as client:
                client.initialize()
            self.assertLessEqual(len(client.stderr_tail), 16)
            self.assertNotIn("example.test", client.stderr_tail)

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
                    self.assertEqual(mode.result(timeout=2), {})
                    self.assertEqual(model.result(timeout=2)["configOptions"][0]["currentValue"], "sol")
                self.assertEqual(client.prompt(session, "resumed")["stopReason"], "end_turn")
                client.load_session(session, root)
            methods = [message.get("method") for message in transcript(root)]
            self.assertIn("session/resume", methods)
            self.assertIn("session/load", methods)


if __name__ == "__main__":
    unittest.main()
