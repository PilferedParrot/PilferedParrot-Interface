"""Offline coverage for runtime-attributed native whiteboard posting."""
from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilferedparrot.config import DEFAULTS
from pilferedparrot.dispatch import RunResult, _provider_process_environment, capture_dispatch
from pilferedparrot.model import Conversation
from pilferedparrot.whiteboard import Whiteboard
from pilferedparrot.whiteboard_native import (
    _codex_runtime, _command, _read_descriptor, _read_payload, _render_command,
    _write_descriptor,
    cleanup_native_whiteboard_discovery, main, native_whiteboard_discovery,
    post_native,
)


class NativeWhiteboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "root with spaces"
        self.root.mkdir()
        self.board = self.root / "board with spaces"
        self.codex_home = self.root / "codex home"
        self.config = deepcopy(DEFAULTS)
        self.config["_whiteboard_directory"] = str(self.board)
        self.config["codex"]["config_path"] = str(self.codex_home / "config.toml")

    def _descriptor(self, provider: str = "codex") -> Path:
        conversation = Conversation(provider=provider)
        instruction = native_whiteboard_discovery(conversation, self.config)
        self.assertIn("Native whiteboard posting", instruction)
        return Path(conversation._native_whiteboard_descriptor)

    def _session(
        self, thread_id: str, model: str, effort: str, *, parent: str | None = None,
        prefix: str = "rollout",
    ) -> Path:
        directory = self.codex_home / "sessions" / "2026" / "09" / "18"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{prefix}-{thread_id}.jsonl"
        events = [
            {"type": "session_meta", "payload": {
                "id": thread_id, "parent_thread_id": parent,
                "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
                if parent else "exec",
            }},
            {"type": "turn_context", "payload": {"model": model, "effort": effort}},
        ]
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        return path

    def test_parent_and_child_use_their_own_latest_turn_context(self) -> None:
        descriptor = _read_descriptor(self._descriptor())
        parent = "01a00000-0000-7000-8000-000000000001"
        child = "01a00000-0000-7000-8000-000000000002"
        self._session(parent, "gpt-lead", "ultra")
        child_path = self._session(child, "gpt-worker", "high", parent=parent)
        with child_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({
                "type": "turn_context",
                "payload": {"model": "gpt-worker-new", "effort": "medium"},
            }) + "\n")

        self.assertEqual(
            _codex_runtime(descriptor, {"CODEX_THREAD_ID": parent}),
            ("gpt-lead", "ultra"),
        )
        self.assertEqual(
            _codex_runtime(descriptor, {"CODEX_THREAD_ID": child}),
            ("gpt-worker-new", "medium"),
        )

    def test_concurrent_session_ids_do_not_cross_attribute(self) -> None:
        descriptor_path = self._descriptor()
        first = "01a00000-0000-7000-8000-000000000011"
        second = "01a00000-0000-7000-8000-000000000012"
        self._session(first, "first-model", "low", prefix="same-time-a")
        self._session(second, "second-model", "high", prefix="same-time-b")
        observed: list[dict[str, object]] = []

        def record(_self, _text, _author, **kwargs):
            observed.append(kwargs["identity"])
            return {"id": f"note-{len(observed)}"}

        def runtime_identity(_config, provider, **reported):
            return {
                "provider": provider,
                "model": reported["reported_model"],
                "reasoning_effort": reported["reported_reasoning_effort"],
                "source": "runtime", "model_source": "reported",
                "reasoning_source": "reported",
            }

        identity_module = MagicMock(runtime_identity=runtime_identity)
        with patch.dict("sys.modules", {"pilferedparrot.whiteboard_identity": identity_module}), \
                patch.object(Whiteboard, "post", new=record):
            post_native(
                descriptor_path, {"text": "one", "author": "worker-one"},
                environment={"CODEX_THREAD_ID": first},
            )
            post_native(
                descriptor_path, {"text": "two", "author": "worker-two"},
                environment={"CODEX_THREAD_ID": second},
            )
        self.assertEqual(
            [(item["model"], item["reasoning_effort"]) for item in observed],
            [("first-model", "low"), ("second-model", "high")],
        )

    def test_unknown_session_never_falls_back_to_configured_parent(self) -> None:
        descriptor_path = self._descriptor()
        self.config["codex"]["model"] = "configured-parent"
        recorded: dict[str, object] = {}

        def record(_self, _text, _author, **kwargs):
            recorded.update(kwargs["identity"])
            return {"id": "unknown-note"}

        with patch.object(Whiteboard, "post", new=record):
            post_native(
                descriptor_path, {"text": "unknown", "author": "worker"},
                environment={"CODEX_THREAD_ID": "missing-session-id"},
            )
        self.assertEqual(recorded["provider"], "codex")
        self.assertEqual(recorded["model"], "")
        self.assertEqual(recorded["reasoning_effort"], "")
        self.assertEqual(recorded["source"], "unknown")

    def test_provider_launch_drops_enclosing_codex_identity(self) -> None:
        inherited = {
            "PATH": "/bin", "CODEX_THREAD_ID": "outer-thread",
            "CODEX_SESSION_ID": "outer-session",
        }
        with patch("pilferedparrot.dispatch.provider_environment", return_value=inherited):
            environment = _provider_process_environment()
        self.assertEqual(environment, {"PATH": "/bin"})

    def test_payload_cannot_supply_or_spoof_identity(self) -> None:
        for field in ("identity", "model", "reasoning_effort", "model_source"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "cannot set"):
                    _read_payload(io.StringIO(json.dumps({
                        "text": "note", "author": "worker", field: "spoofed",
                    })))
                with self.assertRaisesRegex(ValueError, "cannot set"):
                    post_native(
                        self._descriptor(),
                        {"text": "note", "author": "worker", field: "spoofed"},
                        environment={},
                    )
        with self.assertRaisesRegex(ValueError, "one JSON object"):
            _read_payload(io.StringIO("[" * 2_000 + "0" + "]" * 2_000))

    def test_read_only_native_context_does_not_advertise_or_allow_posting(self) -> None:
        self.config["codex"]["sandbox"] = "read-only"
        conversation = Conversation(provider="codex")
        self.assertEqual(native_whiteboard_discovery(conversation, self.config), "")
        self.assertFalse(hasattr(conversation, "_native_whiteboard_descriptor"))
        descriptor = _write_descriptor(self.config, "codex", read_only=True)
        with self.assertRaisesRegex(PermissionError, "read-only"):
            post_native(descriptor, {"text": "no", "author": "worker"}, environment={})

    def test_paths_with_spaces_are_quoted_and_descriptor_is_cleaned_up(self) -> None:
        conversation = Conversation(provider="codex")
        instruction = native_whiteboard_discovery(conversation, self.config)
        descriptor = Path(conversation._native_whiteboard_descriptor)
        command_text = instruction.split("stdin: ", 1)[1].split(". Required field", 1)[0]
        command = shlex.split(command_text) if os.name != "nt" else command_text
        if os.name != "nt":
            self.assertEqual(Path(command[0]), Path(os.sys.executable).resolve())
            self.assertEqual(Path(command[-1]), descriptor)
        self.assertTrue(descriptor.is_file())
        cleanup_native_whiteboard_discovery(conversation)
        self.assertFalse(descriptor.exists())

    def test_windows_and_frozen_commands_use_powershell_call_and_literal_quoting(self) -> None:
        descriptor = self.root / "descriptor with spaces.json"
        with patch("pilferedparrot.whiteboard_native.os.name", "nt"):
            command = [r"C:\Program Files\Parrot\parrot.exe", "--whiteboard-post", "C:\\Chris's board\\context.json"]
            self.assertEqual(_render_command(command),
                             "& 'C:\\Program Files\\Parrot\\parrot.exe' '--whiteboard-post' 'C:\\Chris''s board\\context.json'")
        with patch("pilferedparrot.whiteboard_native.sys.frozen", True, create=True), \
                patch("pilferedparrot.whiteboard_native.sys.executable", str(self.root / "parrot.exe")):
            command = _command(descriptor)
        self.assertEqual(command[1:], ["--whiteboard-post", str(descriptor)])

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell required')
    def test_windows_powershell_pipeline_posts_through_space_paths(self) -> None:
        descriptor = self._descriptor('claude')
        command = _render_command(_command(descriptor))
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             "'{\"text\":\"PowerShell posting\"}' | " + command],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)['posted'])

    def test_descriptor_honors_codex_home_environment_override(self) -> None:
        override = self.root / "actual codex home"
        with patch.dict(os.environ, {"CODEX_HOME": str(override)}):
            descriptor = _read_descriptor(self._descriptor())
        self.assertEqual(descriptor["codex_home"], str(override.resolve()))

    def test_absolute_helper_script_posts_runtime_identity_with_space_paths(self) -> None:
        conversation = Conversation(provider="codex")
        native_whiteboard_discovery(conversation, self.config)
        descriptor = Path(conversation._native_whiteboard_descriptor)
        thread_id = "01a00000-0000-7000-8000-000000000031"
        self._session(thread_id, "runtime-model", "xhigh")
        environment = dict(os.environ)
        environment["CODEX_THREAD_ID"] = thread_id
        completed = subprocess.run(
            _command(descriptor),
            input=json.dumps({"text": "runtime note", "author": "native worker"}),
            text=True, capture_output=True, env=environment, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertTrue(receipt["posted"])
        note = Whiteboard(self.config).read(query="runtime note")["messages"][0]
        self.assertEqual(note["identity"]["model"], "runtime-model")
        self.assertEqual(note["identity"]["reasoning_effort"], "xhigh")
        self.assertEqual(note["identity"]["source"], "runtime")

    def test_each_invocation_gets_current_config_and_a_new_descriptor(self) -> None:
        conversation = Conversation(provider="codex")
        native_whiteboard_discovery(conversation, self.config)
        first = Path(conversation._native_whiteboard_descriptor)
        updated_board = self.root / "updated board"
        self.config["_whiteboard_directory"] = str(updated_board)
        native_whiteboard_discovery(conversation, self.config)
        second = Path(conversation._native_whiteboard_descriptor)
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())
        self.assertEqual(_read_descriptor(second)["whiteboard_directory"], str(updated_board.resolve()))

    def test_dispatch_includes_a_fresh_native_instruction_on_resumed_turns(self) -> None:
        conversation = Conversation(provider="codex", provider_session_id="existing-session")
        adapter = MagicMock()
        adapter.capabilities.resume = True
        adapter.resume.return_value = RunResult("done", 0, "existing-session")
        with patch("pilferedparrot.adapters.adapter_for", return_value=adapter):
            capture_dispatch("codex", "first", self.root, conversation, self.config)
            capture_dispatch("codex", "second", self.root, conversation, self.config)
        prompts = [call.args[0] for call in adapter.resume.call_args_list]
        self.assertTrue(all("Native whiteboard posting" in prompt for prompt in prompts))
        self.assertNotEqual(prompts[0], prompts[1])

    def test_adapter_setup_failure_removes_native_descriptor(self) -> None:
        conversation = Conversation(provider='codex')
        with patch('pilferedparrot.adapters.adapter_for', side_effect=ValueError('adapter setup failed')):
            with self.assertRaisesRegex(ValueError, 'adapter setup failed'):
                capture_dispatch('codex', 'first', self.root, conversation, self.config)
        self.assertFalse(hasattr(conversation, '_native_whiteboard_descriptor'))
        self.assertEqual(list((self.board / '.native-contexts').glob('*.json')), [])
        self.assertFalse(hasattr(conversation, "_native_whiteboard_descriptor"))

    def test_helper_main_returns_bounded_json_error_and_receipt(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stdin", io.StringIO('{"identity":"spoof"}')), redirect_stderr(stderr):
            self.assertEqual(main([str(self._descriptor())]), 1)
        self.assertFalse(json.loads(stderr.getvalue())["posted"])

        stdout = io.StringIO()
        receipt = {"id": "note-1", "identity": {"source": "unknown"}}
        with patch("sys.stdin", io.StringIO('{"text":"ok","author":"worker"}')), \
                patch("pilferedparrot.whiteboard_native.post_native", return_value=receipt), \
                redirect_stdout(stdout):
            self.assertEqual(main([str(self._descriptor())]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["id"], "note-1")

    def test_oversized_lines_and_symlinked_session_files_are_ignored(self) -> None:
        descriptor = _read_descriptor(self._descriptor())
        thread_id = "01a00000-0000-7000-8000-000000000021"
        path = self._session(thread_id, "kept-model", "high")
        original = path.read_bytes()
        deeply_nested = b"[" * 2_000 + b"0" + b"]" * 2_000 + b"\n"
        path.write_bytes(b"x" * 300_000 + b"\n" + deeply_nested + original)
        self.assertEqual(
            _codex_runtime(descriptor, {"CODEX_THREAD_ID": thread_id}),
            ("kept-model", "high"),
        )
        if hasattr(os, "symlink"):
            linked_id = "01a00000-0000-7000-8000-000000000022"
            target = self.root / "outside.jsonl"
            target.write_text(json.dumps({
                "type": "session_meta", "payload": {"id": linked_id},
            }) + "\n", encoding="utf-8")
            link = path.parent / f"rollout-{linked_id}.jsonl"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks unavailable")
            self.assertEqual(
                _codex_runtime(descriptor, {"CODEX_THREAD_ID": linked_id}),
                (None, None),
            )


if __name__ == "__main__":
    unittest.main()
