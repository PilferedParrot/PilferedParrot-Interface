"""Provider-neutral ACP turn facade tests using the existing fake stdio agent."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pilferedparrot.acp_client import ACPError
from pilferedparrot.acp_engine import ACPCancelled, ACPWorkResult, run_acp_turn


FIXTURE = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"


def _options(*, model: str = "sonnet", include_effort: bool = True) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = [{
        "id": "model", "name": "Model", "category": "model", "currentValue": model,
        "options": [{"value": "sonnet", "name": "Sonnet"},
                    {"value": "haiku", "name": "Haiku"}],
    }]
    if include_effort:
        result.append({
            "id": "effort", "name": "Effort", "category": "thought_level",
            "currentValue": "low",
            "options": [{"value": "low", "name": "Low"},
                        {"value": "high", "name": "High"}],
        })
    return result


class FakeACPClient:
    """Small injectable transport double for facade lifecycle edge cases."""

    def __init__(
        self, _argv, *, cwd, on_update=None, on_permission=None, env=None,
        capabilities=None, options=None, load_error=None, stop_reason="end_turn",
        hold_prompt=False, emit_early_update=False, emit_load_replay=False,
        update_burst=0, flush_error=None, ignore_cancel=False,
        ignore_model_value=False, hold_flush=False, permission_session_override=None,
        permission_on_load=False, hold_initialize=False, mode_ack=None,
        ignore_effort_value=False, message_burst=0,
    ):
        self.cwd = cwd
        self.on_update = on_update
        self.on_permission = on_permission
        self.env = env
        self.agent_capabilities = capabilities or {
            "loadSession": True, "sessionCapabilities": {"resume": {}},
        }
        self.options = copy.deepcopy(options if options is not None else _options())
        self.load_error = load_error
        self.stop_reason = stop_reason
        self.hold_prompt = hold_prompt
        self.emit_early_update = emit_early_update
        self.emit_load_replay = emit_load_replay
        self.update_burst = update_burst
        self.flush_error = flush_error
        self.ignore_cancel = ignore_cancel
        self.ignore_model_value = ignore_model_value
        self.hold_flush = hold_flush
        self.permission_session_override = permission_session_override
        self.permission_on_load = permission_on_load
        self.hold_initialize = hold_initialize
        self.mode_ack = mode_ack
        self.ignore_effort_value = ignore_effort_value
        self.message_burst = message_burst
        self.prompt_started = threading.Event()
        self.cancelled = threading.Event()
        self.closed_event = threading.Event()
        self.flush_started = threading.Event()
        self.initialize_started = threading.Event()
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False

    def initialize(self, *, timeout=120):
        self.calls.append(("initialize", timeout))
        if self.hold_initialize:
            self.initialize_started.set()
            self.closed_event.wait(timeout=timeout)
        return {"protocolVersion": 1, "agentCapabilities": self.agent_capabilities}

    def new_session(self, cwd, *, timeout=120):
        self.calls.append(("new", cwd, timeout))
        if self.emit_early_update and self.on_update is not None:
            self.on_update("fake-session", {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "early "},
            })
        return {
            "sessionId": "fake-session", "configOptions": copy.deepcopy(self.options),
            "modes": {"currentModeId": "default", "availableModes": [
                {"id": "default", "name": "Default"}, {"id": "plan", "name": "Plan"},
            ]},
        }

    def load_session(self, session_id, cwd, *, timeout=120):
        self.calls.append(("load", session_id, cwd, timeout))
        if self.load_error is not None:
            raise self.load_error
        if self.emit_load_replay and self.on_update is not None:
            self.on_update(session_id, {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "old assistant replay"},
            })
            self.on_update(session_id, {
                "sessionUpdate": "config_option_update",
                "configOptions": copy.deepcopy(self.options),
            })
        if self.permission_on_load and self.on_permission is not None:
            self.on_permission({"sessionId": session_id, "options": []})
        return {"configOptions": copy.deepcopy(self.options), "modes": {
            "currentModeId": "default", "availableModes": [
                {"id": "default", "name": "Default"}, {"id": "plan", "name": "Plan"},
            ],
        }}

    def resume_session(self, session_id, cwd, *, timeout=120):
        self.calls.append(("resume", session_id, cwd, timeout))
        return {"configOptions": copy.deepcopy(self.options)}

    def set_mode(self, session_id, mode, *, timeout=30):
        self.calls.append(("mode", session_id, mode, timeout))
        return {"currentModeId": self.mode_ack if self.mode_ack is not None else mode}

    def set_config_option(self, session_id, option_id, value, *, timeout=30):
        self.calls.append(("option", session_id, option_id, value, timeout))
        if option_id == "model" and self.ignore_model_value:
            pass
        elif option_id == "model" and value == "haiku":
            self.options = _options(model="haiku", include_effort=False)
        elif option_id == "model":
            self.options = _options(model=str(value), include_effort=True)
        elif option_id == "effort":
            if not self.ignore_effort_value:
                for option in self.options:
                    if option.get("id") == "effort":
                        option["currentValue"] = value
        update = {
            "sessionUpdate": "config_option_update",
            "configOptions": copy.deepcopy(self.options),
        }
        if self.on_update is not None:
            self.on_update("fake-session", update)
        return {"configOptions": copy.deepcopy(self.options)}

    def prompt(self, session_id, prompt, *, timeout=600):
        self.calls.append(("prompt", session_id, prompt, timeout))
        if self.on_permission is not None and self.permission_session_override is not None:
            self.on_permission({"sessionId": self.permission_session_override, "options": []})
        if self.on_update is not None:
            self.on_update(session_id, {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "fake answer"},
            })
            for _ in range(self.message_burst):
                self.on_update(session_id, {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "chunk!"},
                })
            for index in range(self.update_burst):
                self.on_update(session_id, {
                    "sessionUpdate": "tool_call_update", "toolCallId": f"tool-{index}",
                })
            self.on_update(session_id, {
                "sessionUpdate": "usage_update", "used": 12, "size": 100, "cost": 0.01,
            })
        if self.hold_prompt:
            self.prompt_started.set()
            if self.ignore_cancel:
                self.closed_event.wait(timeout=timeout)
                return {"stopReason": self.stop_reason}
            self.cancelled.wait(timeout=timeout)
            if self.cancelled.is_set():
                return {"stopReason": "cancelled"}
        return {"stopReason": self.stop_reason, "_meta": {"quota": {"token_count": {
            "inputTokens": 20, "cachedInputTokens": 4, "outputTokens": 2,
            "reasoningOutputTokens": 3,
        }}}}

    def cancel(self, session_id):
        self.calls.append(("cancel", session_id))
        if not self.ignore_cancel:
            self.cancelled.set()

    def close(self):
        self.closed = True
        self.closed_event.set()
        self.cancelled.set()

    def flush_updates(self, *, timeout=5):
        self.calls.append(("flush", timeout))
        if self.hold_flush:
            self.flush_started.set()
            self.closed_event.wait(timeout=timeout + 1)
        if self.flush_error is not None:
            raise self.flush_error


class ACPWorkEngineTests(unittest.TestCase):
    def test_fake_agent_defaults_to_reject_and_returns_ordered_redacted_updates(self):
        sentinel = "configured-secret-marker"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            observed: list[dict[str, Any]] = []

            def redact(update):
                def visit(value):
                    if isinstance(value, dict):
                        return {key: visit(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [visit(item) for item in value]
                    if isinstance(value, str):
                        return value.replace(sentinel, "[redacted]")
                    return value
                return visit(update)

            result = run_acp_turn(
                [sys.executable, str(FIXTURE)], cwd=root, prompt=sentinel,
                sanitize_update=redact,
                on_update=lambda _session_id, update: observed.append(update),
            )

            self.assertIsInstance(result, ACPWorkResult)
            self.assertTrue(result.succeeded)
            self.assertEqual(result.stop_reason, "end_turn")
            self.assertEqual(result.session_id, "fake-session")
            self.assertIn("[redacted]", result.text)
            self.assertEqual(
                [item.get("sessionUpdate") for item in result.updates],
                ["agent_message_chunk"],
            )
            self.assertEqual(observed, list(result.updates))
            self.assertNotIn(sentinel, json.dumps(result.updates))
            self.assertNotIn(sentinel, result.text)
            self.assertFalse((root / "allowed.txt").exists(), "permission must default-deny")
            self.assertEqual(result.model, "luna")

    def test_model_switch_refreshes_dynamic_options_before_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            clients: list[FakeACPClient] = []
            updates: list[dict[str, Any]] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, **kwargs)
                clients.append(client)
                return client

            result = run_acp_turn(
                ["fake-agent"], cwd=root, prompt="test", model="haiku", effort="high",
                env={"PATH": "/safe/bin", "API_KEY": "sentinel"},
                client_factory=factory, on_update=lambda _sid, item: updates.append(item),
            )

            client = clients[0]
            self.assertEqual(client.env, {"PATH": "/safe/bin", "API_KEY": "sentinel"})
            option_calls = [call for call in client.calls if call[0] == "option"]
            self.assertEqual([(call[2], call[3]) for call in option_calls], [("model", "haiku")])
            self.assertEqual(result.model, "haiku")
            self.assertIsNone(result.effort)
            self.assertEqual([option["id"] for option in result.config_options], ["model"])
            self.assertEqual(
                [item.get("sessionUpdate") for item in updates],
                ["agent_message_chunk", "usage_update"],
            )
            self.assertEqual(result.usage["used"], 12)
            self.assertEqual(result.usage["input_tokens"], 20)
            self.assertEqual(result.usage["cached_input_tokens"], 4)
            self.assertEqual(result.usage["output_tokens"], 2)
            self.assertEqual(result.usage["reasoning_output_tokens"], 3)
            self.assertTrue(client.closed)

    def test_updates_before_session_new_response_are_not_current_turn_output(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, emit_early_update=True, **kwargs)
                clients.append(client)
                return client

            result = run_acp_turn(
                ["fake-agent"], cwd=Path(directory).resolve(), prompt="test",
                client_factory=factory,
            )

            self.assertEqual(result.text, "fake answer")
            self.assertEqual(
                [item.get("sessionUpdate") for item in result.updates],
                ["agent_message_chunk", "usage_update"],
            )
            self.assertTrue(clients[0].closed)

    def test_load_replay_is_not_returned_as_current_turn_output(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []
            emitted: list[dict[str, Any]] = []
            sanitized_text: list[str] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, emit_load_replay=True, **kwargs)
                clients.append(client)
                return client

            def stateful_sanitizer(update):
                if update.get("sessionUpdate") == "agent_message_chunk":
                    sanitized_text.append(update.get("content", {}).get("text", ""))
                return copy.deepcopy(update)

            result = run_acp_turn(
                ["fake-agent"], cwd=Path(directory).resolve(), prompt="next turn",
                session_id="prior-session", client_factory=factory,
                sanitize_update=stateful_sanitizer,
                on_update=lambda _sid, update: emitted.append(update),
            )

            self.assertEqual(result.text, "fake answer")
            self.assertEqual(
                [item.get("sessionUpdate") for item in result.updates],
                ["agent_message_chunk", "usage_update"],
            )
            self.assertEqual(emitted, list(result.updates))
            self.assertNotIn("old assistant replay", json.dumps(result.updates))
            self.assertEqual(sanitized_text, ["fake answer"])
            self.assertTrue(clients[0].closed)

    def test_collected_updates_are_bounded_but_stream_callback_receives_all(self):
        with tempfile.TemporaryDirectory() as directory:
            streamed: list[dict[str, Any]] = []

            def factory(*args, **kwargs):
                return FakeACPClient(*args, update_burst=5, **kwargs)

            result = run_acp_turn(
                ["fake-agent"], cwd=Path(directory).resolve(), prompt="many updates",
                max_collected_updates=3, client_factory=factory,
                on_update=lambda _sid, update: streamed.append(update),
            )

            self.assertEqual(len(streamed), 7)
            self.assertEqual(len(result.updates), 3)
            self.assertTrue(result.updates_truncated)
            self.assertEqual(
                [item.get("sessionUpdate") for item in result.updates],
                ["tool_call_update", "tool_call_update", "usage_update"],
            )

    def test_assistant_text_is_capped_with_marker_and_stream_keeps_all_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            streamed: list[dict[str, Any]] = []

            def factory(*args, **kwargs):
                return FakeACPClient(*args, message_burst=8, **kwargs)

            result = run_acp_turn(
                ["fake-agent"], cwd=Path(directory).resolve(), prompt="large response",
                max_text_chars=24, client_factory=factory,
                on_update=lambda _sid, update: streamed.append(update),
            )

            self.assertTrue(result.text_truncated)
            self.assertEqual(len(result.text), 24)
            self.assertTrue(result.text.endswith("\n[output truncated]"))
            self.assertEqual(result.text[:5], "fake ")
            self.assertEqual(
                sum(item.get("sessionUpdate") == "agent_message_chunk" for item in streamed),
                9,
            )

    def test_flush_timeout_propagates_and_closes_client(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, flush_error=TimeoutError("flush timed out"), **kwargs)
                clients.append(client)
                return client

            with self.assertRaisesRegex(TimeoutError, "flush timed out"):
                run_acp_turn(
                    ["fake-agent"], cwd=Path(directory).resolve(), prompt="continue",
                    option_timeout=20, client_factory=factory,
                )

            self.assertEqual(
                [call for call in clients[0].calls if call[0] == "flush"],
                [("flush", 5)],
            )
            self.assertNotIn("prompt", [call[0] for call in clients[0].calls])
            self.assertTrue(clients[0].closed)

    def test_cancel_during_flush_does_not_dispatch_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []
            created = threading.Event()

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, hold_flush=True, **kwargs)
                clients.append(client)
                created.set()
                return client

            cancelled = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_acp_turn, ["fake-agent"], cwd=Path(directory).resolve(),
                    prompt="cancel during flush", cancel_event=cancelled,
                    cancel_grace=0.1, client_factory=factory,
                )
                self.assertTrue(created.wait(2))
                self.assertTrue(clients[0].flush_started.wait(2))
                cancelled.set()
                with self.assertRaises(ACPCancelled):
                    future.result(timeout=2)

            methods = [call[0] for call in clients[0].calls]
            self.assertIn("cancel", methods)
            self.assertNotIn("prompt", methods)
            self.assertTrue(clients[0].closed)

    def test_cancellation_watcher_covers_initialize(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []
            created = threading.Event()

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, hold_initialize=True, **kwargs)
                clients.append(client)
                created.set()
                return client

            cancelled = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_acp_turn, ["fake-agent"], cwd=Path(directory).resolve(),
                    prompt="cancel initialize", cancel_event=cancelled,
                    cancel_grace=0.1, client_factory=factory,
                )
                self.assertTrue(created.wait(2))
                self.assertTrue(clients[0].initialize_started.wait(2))
                cancelled.set()
                with self.assertRaises(ACPCancelled):
                    future.result(timeout=2)

            methods = [call[0] for call in clients[0].calls]
            self.assertNotIn("new", methods)
            self.assertNotIn("prompt", methods)
            self.assertTrue(clients[0].closed)

    def test_ignored_cancel_forces_close_and_never_returns_success(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []
            created = threading.Event()

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, hold_prompt=True, ignore_cancel=True, **kwargs)
                clients.append(client)
                created.set()
                return client

            cancelled = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_acp_turn, ["fake-agent"], cwd=Path(directory).resolve(),
                    prompt="ignore cancellation", cancel_event=cancelled,
                    cancel_grace=0.1, client_factory=factory,
                )
                self.assertTrue(created.wait(2))
                self.assertTrue(clients[0].prompt_started.wait(2))
                cancelled.set()
                with self.assertRaises(ACPCancelled):
                    future.result(timeout=2)

            methods = [call[0] for call in clients[0].calls]
            self.assertIn("cancel", methods)
            self.assertTrue(clients[0].closed)

    def test_model_must_be_acknowledged_in_returned_options(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, ignore_model_value=True, **kwargs)
                clients.append(client)
                return client

            with self.assertRaisesRegex(ACPError, "did not apply the requested model"):
                run_acp_turn(
                    ["fake-agent"], cwd=Path(directory).resolve(), prompt="model check",
                    model="haiku", client_factory=factory,
                )
            self.assertNotIn("prompt", [call[0] for call in clients[0].calls])
            self.assertTrue(clients[0].closed)

    def test_mode_must_be_acknowledged_before_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, mode_ack="default", **kwargs)
                clients.append(client)
                return client

            with self.assertRaisesRegex(ACPError, "did not apply the requested mode"):
                run_acp_turn(
                    ["fake-agent"], cwd=Path(directory).resolve(), prompt="mode check",
                    mode="plan", client_factory=factory,
                )
            self.assertNotIn("prompt", [call[0] for call in clients[0].calls])
            self.assertTrue(clients[0].closed)

    def test_effort_must_be_acknowledged_when_advertised(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, ignore_effort_value=True, **kwargs)
                clients.append(client)
                return client

            with self.assertRaisesRegex(ACPError, "did not apply the requested effort"):
                run_acp_turn(
                    ["fake-agent"], cwd=Path(directory).resolve(), prompt="effort check",
                    effort="high", client_factory=factory,
                )
            self.assertNotIn("prompt", [call[0] for call in clients[0].calls])
            self.assertTrue(clients[0].closed)

    def test_permission_callback_rejects_pre_turn_and_wrong_session_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            clients: list[FakeACPClient] = []
            seen: list[dict[str, Any]] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(
                    *args, permission_on_load=True,
                    permission_session_override="another-session", **kwargs,
                )
                clients.append(client)
                return client

            result = run_acp_turn(
                ["fake-agent"], cwd=Path(directory).resolve(), prompt="permissions",
                session_id="prior-session", on_permission=lambda params: seen.append(params) or "yes",
                client_factory=factory,
            )
            self.assertTrue(result.succeeded)
            self.assertEqual(seen, [])
            self.assertTrue(clients[0].closed)

    def test_fake_agent_cancel_keeps_update_order_and_redacts_before_collection(self):
        secret = "working on wait-cancel"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cancelled = threading.Event()
            observed: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_acp_turn, [sys.executable, str(FIXTURE)], cwd=root,
                    prompt="wait-cancel", env={"PATH": os.environ.get("PATH", ""),
                                              "FAKE_ACP_SECRET": "configured-secret"},
                    cancel_event=cancelled,
                    sanitize_update=lambda update: json.loads(
                        json.dumps(update).replace(secret, "[redacted]")
                    ),
                    on_update=lambda _sid, update: observed.append(update),
                )
                request_log = root / "fake-agent-requests.jsonl"
                deadline = time.monotonic() + 3
                prompt_seen = False
                while time.monotonic() < deadline:
                    if request_log.exists():
                        prompt_seen = any(
                            '"method":"session/prompt"' in line
                            for line in request_log.read_text(encoding="utf-8").splitlines()
                        )
                        if prompt_seen:
                            break
                    time.sleep(0.01)
                self.assertTrue(prompt_seen, "fake agent did not receive prompt")
                cancelled.set()
                with self.assertRaises(ACPCancelled):
                    future.result(timeout=3)

            self.assertEqual(
                [item.get("sessionUpdate") for item in observed],
                ["agent_message_chunk"],
            )
            self.assertEqual(observed[0]["content"]["text"], "[redacted]")
            self.assertNotIn(secret, json.dumps(observed))

    def test_existing_session_load_failure_does_not_start_a_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, load_error=ACPError("session unavailable"), **kwargs)
                clients.append(client)
                return client

            with self.assertRaisesRegex(ACPError, "session unavailable"):
                run_acp_turn(
                    ["fake-agent"], cwd=root, prompt="continue", session_id="prior-session",
                    client_factory=factory,
                )
            methods = [call[0] for call in clients[0].calls]
            self.assertIn("load", methods)
            self.assertNotIn("new", methods)
            self.assertNotIn("prompt", methods)
            self.assertTrue(clients[0].closed)

    def test_resume_is_used_only_when_load_is_not_advertised(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(
                    *args, capabilities={"loadSession": False, "sessionCapabilities": {"resume": {}}},
                    **kwargs,
                )
                clients.append(client)
                return client

            result = run_acp_turn(
                ["fake-agent"], cwd=root, prompt="continue", session_id="prior-session",
                client_factory=factory,
            )
            methods = [call[0] for call in clients[0].calls]
            self.assertIn("resume", methods)
            self.assertNotIn("new", methods)
            self.assertEqual(result.session_id, "prior-session")

    def test_cancel_watcher_cancels_prompt_and_non_end_turn_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            clients: list[FakeACPClient] = []

            def factory(*args, **kwargs):
                client = FakeACPClient(*args, hold_prompt=True, **kwargs)
                clients.append(client)
                return client

            cancelled = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    run_acp_turn, ["fake-agent"], cwd=root, prompt="wait",
                    cancel_event=cancelled, client_factory=factory,
                )
                deadline = time.monotonic() + 2
                while not clients and time.monotonic() < deadline:
                    threading.Event().wait(0.005)
                self.assertTrue(clients, "client factory did not run")
                self.assertTrue(clients[0].prompt_started.wait(2))
                cancelled.set()
                with self.assertRaises(ACPCancelled):
                    future.result(timeout=2)

            self.assertTrue(any(call[0] == "cancel" for call in clients[0].calls))
            self.assertTrue(clients[0].closed)

        with tempfile.TemporaryDirectory() as directory:
            client_holder: list[FakeACPClient] = []
            def factory(*args, **kwargs):
                client = FakeACPClient(*args, stop_reason="max_tokens", **kwargs)
                client_holder.append(client)
                return client
            result = run_acp_turn(
                ["fake-agent"], cwd=Path(directory).resolve(), prompt="limited",
                client_factory=factory,
            )
            self.assertEqual(result.stop_reason, "max_tokens")
            self.assertFalse(result.succeeded)
            self.assertTrue(client_holder[0].closed)


if __name__ == "__main__":
    unittest.main()
