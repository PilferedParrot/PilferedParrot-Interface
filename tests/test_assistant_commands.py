"""Stored command actions use the ordinary provider run and preserve user drafts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_terminal_launch import _app


class AssistantCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = _app(self.temporary.name)
        self.chat = self.app.create_chat({"provider": "codex", "cwd": self.temporary.name})
        self.command = "  printf 'first\\n'\nprintf 'second\\n'  "
        with self.app.store.lock:
            chat = self.app.store.get(self.chat["id"])
            chat["draft"] = "Keep my unsent request"
            chat["messages"].append({
                "id": "stored-command", "role": "assistant",
                "content": "```shell\n" + self.command + "\n```",
            })
            self.app.store.save()
        self.payload = {"message_id": "stored-command", "block_index": 0,
                        "command": self.command, "request_id": "command-request-123"}

    def test_command_runs_as_normal_work_preserving_draft_and_model(self):
        with patch("pilferedparrot.web.threading.Thread.start"), \
             patch("pilferedparrot.web.launch_terminal") as terminal:
            result = self.app.run_assistant_command(self.chat["id"], self.payload)
        self.assertEqual(result["draft"], "Keep my unsent request")
        self.assertEqual(result["requested_provider"], "codex")
        self.assertEqual(result["requested_model"], self.chat["requested_model"])
        self.assertIn(self.command, result["messages"][-2]["content"])
        self.assertEqual(result["messages"][-2]["id"], self.payload["request_id"])
        self.assertTrue(result["messages"][-1]["pending"])
        terminal.assert_not_called()

    def test_retrying_same_request_does_not_start_another_run(self):
        with patch("pilferedparrot.web.threading.Thread.start") as start:
            first = self.app.run_assistant_command(self.chat["id"], self.payload)
            second = self.app.run_assistant_command(self.chat["id"], self.payload)
        self.assertEqual(first["messages"], second["messages"])
        start.assert_called_once()

    def test_new_command_request_is_rejected_while_session_is_running(self):
        with patch("pilferedparrot.web.threading.Thread.start") as start:
            self.app.run_assistant_command(self.chat["id"], self.payload)
            with self.assertRaisesRegex(ValueError, "already running"):
                self.app.run_assistant_command(self.chat["id"], {
                    **self.payload, "request_id": "another-command-request",
                })
        start.assert_called_once()

    def test_client_cannot_replace_the_reviewed_stored_command(self):
        with patch.object(self.app, "send_message") as send:
            with self.assertRaisesRegex(ValueError, "command changed"):
                self.app.run_assistant_command(self.chat["id"], {
                    **self.payload, "command": "echo different",
                })
            send.assert_not_called()

    def test_pending_user_and_non_shell_messages_cannot_be_executed(self):
        for changes in ({"role": "user"}, {"pending": True},
                        {"content": "```python\nprint('x')\n```"}):
            with self.subTest(changes=changes), self.app.store.lock:
                message = self.app.store.get(self.chat["id"])["messages"][0]
                original = dict(message)
                message.update(changes)
                with patch.object(self.app, "send_message") as send:
                    with self.assertRaises(ValueError):
                        self.app.run_assistant_command(self.chat["id"], self.payload)
                    send.assert_not_called()
                message.clear()
                message.update(original)

    def test_another_window_cannot_run_a_stored_command(self):
        with patch.object(self.app, "send_message") as send:
            with self.assertRaises((ValueError, KeyError)):
                self.app.run_assistant_command(self.chat["id"], self.payload,
                                              window_id="unrelated-window")
            send.assert_not_called()
