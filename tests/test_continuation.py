"""Offline regressions for the provider continuation contract."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pilferedparrot.config import DEFAULTS
from pilferedparrot.continuation import continuation_rule
from pilferedparrot.dispatch import RunResult, capture_dispatch, dispatch
from pilferedparrot.model import Conversation
from pilferedparrot.whiteboard import Whiteboard


MARKER = "[Incomplete work handoff]"
NEXT_PROMPT = "Next session prompt"
NATIVE_PROVIDERS = ("codex", "claude", "gemini", "antigravity")


class _RecordingAdapter:
    capabilities = SimpleNamespace(resume=True)

    def __init__(self, text: str = "provider result") -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def run(self, prompt, _cwd, _conversation, *_args):
        self.calls.append(("run", prompt))
        return RunResult(self.text, 0)

    def resume(self, prompt, _cwd, _conversation, *_args):
        self.calls.append(("resume", prompt))
        return RunResult(self.text, 0)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.body.encode("utf-8")


class ContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = deepcopy(DEFAULTS)
        self.config["whiteboard"] = {"directory": str(self.root / "board")}
        self.config["custom"] = dict(
            self.config["qwen"], adapter="openai_compatible",
            base_url="http://127.0.0.1:1234/v1", model="configured-model",
        )

    def assert_rule_is_last(self, prompt: str) -> None:
        self.assertIn(MARKER, prompt)
        marker = prompt.rindex(MARKER)
        for discovery in ("[Shared model whiteboard]", "[Native whiteboard posting]"):
            if discovery in prompt:
                self.assertLess(prompt.rindex(discovery), marker)

    def test_rule_states_completion_verification_handoff_and_scope_boundaries(self) -> None:
        for provider in (*NATIVE_PROVIDERS, "qwen", "custom"):
            with self.subTest(provider=provider):
                rule = continuation_rule(provider, self.config)
                lowered = rule.lower()
                self.assertIn(MARKER, rule)
                self.assertIn(NEXT_PROMPT, rule)
                self.assertIn("full intended outcome", lowered)
                self.assertIn("accepted follow-ups", lowered)
                self.assertIn("verif", lowered)
                self.assertIn("stop early", lowered)
                self.assertIn("cancel", lowered)
                self.assertIn("replac", lowered)
                self.assertIn("delegated", lowered)
                self.assertIn("assigned", lowered)
                if provider in NATIVE_PROVIDERS:
                    self.assertIn("native", lowered)
                    self.assertIn("helper", lowered)
                else:
                    self.assertIn("whiteboard_post", rule)

    def test_capture_dispatch_delivers_rule_last_for_every_provider_and_resume_state(self) -> None:
        for provider in (*NATIVE_PROVIDERS, "qwen", "custom"):
            for resumed in (False, True):
                with self.subTest(provider=provider, resumed=resumed):
                    adapter = _RecordingAdapter()
                    conversation = Conversation(
                        provider_session_id="existing-session" if resumed else None,
                        whiteboard_discovered=resumed,
                    )
                    with patch("pilferedparrot.adapters.adapter_for", return_value=adapter):
                        result = capture_dispatch(
                            provider, "perform the task", self.root, conversation, self.config,
                        )
                    self.assertEqual(result.text, "provider result")
                    self.assertEqual(adapter.calls[0][0], "resume" if resumed else "run")
                    prompt = adapter.calls[0][1]
                    self.assert_rule_is_last(prompt)
                    self.assertEqual(prompt.count(MARKER), 1)
                    if provider in NATIVE_PROVIDERS:
                        self.assertIn("[Native whiteboard posting]", prompt)
                    else:
                        self.assertNotIn("[Native whiteboard posting]", prompt)
                    if resumed:
                        self.assertNotIn("[Shared model whiteboard]", prompt)
                    else:
                        self.assertIn("[Shared model whiteboard]", prompt)

    def test_cli_dispatch_delivers_rule_last_for_new_and_existing_conversations(self) -> None:
        for provider in (*NATIVE_PROVIDERS, "qwen", "custom"):
            for resumed in (False, True):
                with self.subTest(provider=provider, resumed=resumed):
                    adapter = _RecordingAdapter()
                    conversation = Conversation(
                        provider_session_id="existing-session" if resumed else None,
                        whiteboard_discovered=resumed,
                    )
                    with patch("pilferedparrot.adapters.adapter_for", return_value=adapter), \
                            redirect_stdout(io.StringIO()):
                        exit_code = dispatch(
                            provider, "perform the task", self.root, conversation, self.config,
                        )
                    self.assertEqual(exit_code, 0)
                    self.assertEqual(adapter.calls[0][0], "run")
                    self.assert_rule_is_last(adapter.calls[0][1])

    def test_plan_and_read_only_modes_require_final_reply_without_posting(self) -> None:
        cases = (
            ("codex", "sandbox", "read-only"),
            ("claude", "permission_mode", "plan"),
            ("gemini", "approval_mode", "plan"),
            ("antigravity", "mode", "plan"),
            ("qwen", "read_only", True),
            ("custom", "read_only", True),
        )
        for provider, field, value in cases:
            with self.subTest(provider=provider, field=field):
                config = deepcopy(self.config)
                config[provider][field] = value
                rule = continuation_rule(provider, config)
                self.assertIn(MARKER, rule)
                self.assertIn(NEXT_PROMPT, rule)
                self.assertIn("final reply", rule.lower())
                self.assertNotIn("whiteboard_post", rule)
                self.assertNotIn("native whiteboard", rule.lower())

                adapter = _RecordingAdapter()
                conversation = Conversation(whiteboard_discovered=True)
                with patch("pilferedparrot.adapters.adapter_for", return_value=adapter):
                    capture_dispatch(provider, "inspect", self.root, conversation, config)
                prompt = adapter.calls[0][1]
                self.assert_rule_is_last(prompt)
                self.assertNotIn("[Native whiteboard posting]", prompt)

    def test_bounded_worker_rule_limits_handoff_to_the_assigned_contract(self) -> None:
        config = deepcopy(self.config)
        config["_harness"] = {"bounded": True}
        rule = continuation_rule("codex", config).lower()
        self.assertIn("bounded worker assignment", rule)
        self.assertIn("assigned contract", rule)
        self.assertIn("stop conditions", rule)
        self.assertIn("return unfinished work to the lead", rule)

    def test_rule_failure_cleans_up_native_posting_context_in_both_entry_points(self) -> None:
        for entry_point in (capture_dispatch, dispatch):
            with self.subTest(entry_point=entry_point.__name__):
                conversation = Conversation()
                with patch("pilferedparrot.continuation.continuation_rule",
                           side_effect=RuntimeError("rule unavailable")), \
                        patch("pilferedparrot.adapters.adapter_for") as adapter:
                    with self.assertRaisesRegex(RuntimeError, "rule unavailable"):
                        entry_point("codex", "task", self.root, conversation, self.config)
                adapter.assert_not_called()
                self.assertFalse(hasattr(conversation, "_native_whiteboard_descriptor"))
                self.assertEqual(list((self.root / "board" / ".native-contexts").glob("*.json")), [])

    def test_compatible_tool_loop_persists_open_handoff_with_runtime_identity(self) -> None:
        handoff = "Continue the unfinished parser work and run its focused test."
        final = (
            "The parser work remains incomplete.\n\n"
            "Next session prompt\n```text\nContinue the parser work from the saved handoff.\n```"
        )
        responses = [
            {
                "model": "reported-worker-model",
                "choices": [{"message": {
                    "role": "assistant", "content": "Saving a continuation handoff.",
                    "tool_calls": [{
                        "id": "handoff-1", "type": "function", "function": {
                            "name": "whiteboard_post",
                            "arguments": json.dumps({
                                "text": handoff, "kind": "handoff", "status": "open",
                                "topics": ["continuation"],
                            }),
                        },
                    }],
                }}],
            },
            {"choices": [{"message": {"role": "assistant", "content": final}}]},
        ]
        requests: list[dict[str, object]] = []

        def complete(_config, provider, request, **_kwargs):
            self.assertEqual(provider, "custom")
            requests.append(json.loads(request.data.decode("utf-8")))
            return _Response(responses.pop(0))

        conversation = Conversation()
        with patch("pilferedparrot.qwen.open_compatible_url", side_effect=complete), \
                redirect_stdout(io.StringIO()):
            result = capture_dispatch(
                "custom", "implement the parser", self.root, conversation, self.config,
            )

        self.assertEqual(result.text, final)
        first_user_prompt = requests[0]["messages"][-1]["content"]
        self.assert_rule_is_last(first_user_prompt)
        self.assertEqual(requests[1]["messages"][-1]["name"], "whiteboard_post")

        # A new Whiteboard object proves the handoff was persisted rather than
        # retained in the toolbox instance that executed the tool call.
        notes = Whiteboard(deepcopy(self.config)).read(
            kind="handoff", status="open", topic="continuation",
        )["messages"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["text"], handoff)
        self.assertEqual(notes[0]["topics"], ["continuation"])
        self.assertEqual(notes[0]["effective_status"], "open")
        self.assertEqual(notes[0]["identity"]["source"], "runtime")
        self.assertEqual(notes[0]["identity"]["provider"], "custom")
        self.assertEqual(notes[0]["identity"]["model"], "reported-worker-model")
        self.assertEqual(notes[0]["identity"]["model_source"], "reported")

    def test_completed_compatible_result_is_unchanged_and_does_not_create_handoff(self) -> None:
        completed = "Implemented the full request and verified the focused tests."
        requests: list[dict[str, object]] = []

        def complete(_config, _provider, request, **_kwargs):
            requests.append(json.loads(request.data.decode("utf-8")))
            return _Response({
                "model": "completed-worker",
                "choices": [{"message": {"role": "assistant", "content": completed}}],
            })

        with patch("pilferedparrot.qwen.open_compatible_url", side_effect=complete), \
                redirect_stdout(io.StringIO()):
            result = capture_dispatch(
                "custom", "finish all requested work", self.root, Conversation(), self.config,
            )

        self.assertEqual(result.text, completed)
        self.assertEqual(len(requests), 1)
        self.assert_rule_is_last(requests[0]["messages"][-1]["content"])
        self.assertEqual(Whiteboard(self.config).read()["messages"], [])


if __name__ == "__main__":
    unittest.main()
