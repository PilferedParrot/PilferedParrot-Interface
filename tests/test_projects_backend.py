"""Project workroom persistence and capability-scoped HTTP controls."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import MagicMock

from pilferedparrot.config import load_config
from pilferedparrot.web import PilferedParrotApp
from pilferedparrot.web_server import BrowserHTTPServer, make_handler


def app_for(root: Path) -> PilferedParrotApp:
    config = load_config(root / "missing-config.json")
    config["web"]["port"] = 0
    config["web"]["chat_store"] = str(root / "chats.json")
    config["ledger"] = str(root / "runs.jsonl")
    return PilferedParrotApp(config, root)


class ProjectWorkroomTests(unittest.TestCase):
    def test_first_message_rejects_another_project_without_mutating_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            first.mkdir(); second.mkdir()
            app = app_for(root)
            chat = app.create_chat({"cwd": str(first)}, window_id="main",
                                   window_provider="codex")
            app.select_project({"cwd": str(second)}, window_id="main",
                               window_provider="codex")
            with self.assertRaisesRegex(ValueError, "belongs to another project"):
                app.send_message(chat["id"], {
                    "content": "Do work in the wrong folder", "cwd": str(second),
                    "request_id": "wrong-project-request",
                }, window_id="main", window_provider="codex")
            stored = app.store.get(chat["id"])
            self.assertEqual(stored["cwd"], str(first))
            self.assertEqual(stored["messages"], [])
            self.assertEqual(app.state(window_id="main", window_provider="codex")
                             ["selected_project"], str(second))

    def test_ack_only_draft_persists_without_returning_session_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = app_for(root)
            chat = app.create_chat({"cwd": str(root)}, window_id="main",
                                   window_provider="codex")
            server = BrowserHTTPServer(("127.0.0.1", 0), make_handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                origin = f"http://127.0.0.1:{server.server_address[1]}"
                request = Request(
                    f"{origin}/api/chats/{chat['id']}/draft",
                    data=json.dumps({"draft": "private unsent text", "ack_only": True}).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": origin,
                        "X-PilferedParrot-Capability": app.dashboard_capability,
                    }, method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    reply = json.load(response)
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()
                app.shutdown()
                self.assertFalse(thread.is_alive(), "draft API test server did not stop")
            self.assertEqual(reply, {"id": chat["id"], "draft_saved": True})
            self.assertNotIn("private unsent text", str(reply))
            self.assertEqual(app.store.get(chat["id"])["draft"], "private unsent text")
            self.assertEqual(app_for(root).store.get(chat["id"])["draft"],
                             "private unsent text")

    def test_selection_pin_and_create_survive_restart_without_changing_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            first.mkdir(); second.mkdir()
            alias = root / "alias"
            try:
                alias.symlink_to(first, target_is_directory=True)
            except (OSError, NotImplementedError):
                # Windows accounts without symlink privilege still exercise
                # persistence and scoped selection with the canonical path.
                alias = first
            app = app_for(root)
            chat = app.create_chat({"cwd": str(second)}, window_id="provider-codex",
                                   window_provider="codex")
            app.set_draft(chat["id"], {"draft": "keep my text"}, window_id="provider-codex")
            state = app.state(window_id="provider-codex", window_provider="codex")
            self.assertEqual(state["selected_project"], str(second))
            self.assertEqual(state["chats"][0]["project_cwd"], str(second))
            selected = app.select_project({"cwd": str(alias)}, window_id="provider-codex",
                                          window_provider="codex")
            self.assertEqual(selected["selected_project"], str(first))
            pinned = app.pin_project({"cwd": str(alias), "pinned": True},
                                     window_id="provider-codex", window_provider="codex")
            self.assertTrue(next(p for p in pinned["projects"] if p["cwd"] == str(first))["pinned"])
            restarted = app_for(root)
            state = restarted.state(window_id="provider-codex", window_provider="codex")
            self.assertEqual(state["selected_project"], str(first))
            self.assertEqual(state["chats"][0]["draft"], "keep my text")
            self.assertEqual(state["chats"][0]["cwd"], str(second))
            self.assertEqual(next(p for p in state["projects"] if p["cwd"] == str(first))["session_count"], 0)
            self.assertNotIn("project_workrooms", state["preferences"])
            new_chat = restarted.create_chat({}, window_id="provider-codex",
                                             window_provider="codex")
            self.assertEqual(new_chat["project_cwd"], state["selected_project"])
            self.assertEqual(restarted.state(window_id="provider-codex",
                                             window_provider="codex")["selected_project"], str(first))

    def test_provider_window_cannot_see_another_windows_project_or_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_dir, claude_dir = root / "codex-work", root / "claude-work"
            codex_dir.mkdir(); claude_dir.mkdir()
            app = app_for(root)
            codex_chat = app.create_chat({"cwd": str(codex_dir)}, window_id="provider-codex",
                                         window_provider="codex")
            claude_chat = app.create_chat({"cwd": str(claude_dir)}, window_id="provider-claude",
                                          window_provider="claude")
            app.pin_project({"cwd": str(claude_dir), "pinned": True},
                            window_id="provider-claude", window_provider="claude")
            codex = app.state(window_id="provider-codex", window_provider="codex")
            self.assertEqual([c["id"] for c in codex["chats"]], [codex_chat["id"]])
            self.assertNotIn(claude_chat["id"], str(codex))
            self.assertNotIn(str(claude_dir), {p["cwd"] for p in codex["projects"]})
            main = app.state(window_id="main", window_provider="codex")
            self.assertEqual({p["cwd"] for p in main["projects"] if p["session_count"]},
                             {str(codex_dir), str(claude_dir)})
            self.assertNotIn(codex_chat["id"], str(main))
            self.assertNotIn(claude_chat["id"], str(main))

    def test_invalid_project_actions_leave_selection_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = app_for(root)
            app.select_project({"cwd": str(root)}, window_id="provider-codex",
                               window_provider="codex")
            with self.assertRaises(ValueError):
                app.pin_project({"cwd": str(root), "pinned": 1},
                                window_id="provider-codex", window_provider="codex")
            with self.assertRaises(ValueError):
                app.select_project({"cwd": str(root / "missing")},
                                   window_id="provider-codex", window_provider="codex")
            self.assertEqual(app.state(window_id="provider-codex",
                                       window_provider="codex")["selected_project"], str(root))

    def test_recent_and_pinned_roots_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = app_for(root)
            for index in range(30):
                path = root / f"project-{index}"
                path.mkdir()
                app.select_project({"cwd": str(path)}, window_id="provider-codex",
                                   window_provider="codex")
                app.pin_project({"cwd": str(path), "pinned": True},
                                window_id="provider-codex", window_provider="codex")
            restarted = app_for(root)
            state = restarted.state(window_id="provider-codex", window_provider="codex")
            self.assertEqual(len(state["projects"]), 24)
            self.assertEqual(state["selected_project"], str(root / "project-29"))
            self.assertTrue(all(project["pinned"] for project in state["projects"]))


class ProjectHTTPTests(unittest.TestCase):
    def _handler(self, app, path: str, *, token: str, origin: str | None):
        handler = object.__new__(make_handler(app))
        handler.path = path
        handler.server = MagicMock(server_address=("127.0.0.1", 8765))
        handler.client_address = ("127.0.0.1", 43210)
        handler.headers = {"Host": "127.0.0.1:8765", "X-PilferedParrot-Capability": token}
        if origin is not None:
            handler.headers["Origin"] = origin
        handler._read_json = MagicMock(return_value={"cwd": "/tmp/project"})
        handler._json = MagicMock()
        return handler

    def test_project_controls_reject_wrong_capability_or_origin(self):
        app = MagicMock()
        app.config = {"web": {"host": "127.0.0.1", "port": 8765}}
        app.default_provider = "codex"
        app.capability_context.side_effect = lambda token: {
            "good": {"scope": "dashboard", "window_id": "win-a", "provider": "codex"},
            "chat": {"scope": "chat", "window_id": "chat", "provider": "codex"},
        }.get(token)
        for path in ("/api/projects/select", "/api/projects/pin"):
            for token, origin in (("bad", "http://127.0.0.1:8765"),
                                  ("chat", "http://127.0.0.1:8765"),
                                  ("good", None), ("good", "http://127.0.0.1:9999")):
                with self.subTest(path=path, token=token, origin=origin):
                    handler = self._handler(app, path, token=token, origin=origin)
                    handler.do_POST()
                    handler._read_json.assert_not_called()
                    handler._json.assert_called_once_with(
                        {"error": "local control authorization failed"}, HTTPStatus.FORBIDDEN)
        app.select_project.assert_not_called()
        app.pin_project.assert_not_called()

    def test_project_control_derives_window_and_provider_from_capability(self):
        app = MagicMock()
        app.config = {"web": {"host": "127.0.0.1", "port": 8765}}
        app.default_provider = "codex"
        app.capability_context.return_value = {
            "scope": "dashboard", "window_id": "ephemeral", "history_id": "provider-codex",
            "provider": "codex",
        }
        app.select_project.return_value = {"selected_project": "/tmp/project", "projects": []}
        handler = self._handler(app, "/api/projects/select", token="good",
                                origin="http://127.0.0.1:8765")
        handler.do_POST()
        app.select_project.assert_called_once_with(
            {"cwd": "/tmp/project"}, window_id="provider-codex", window_provider="codex")
        handler._json.assert_called_once_with(app.select_project.return_value)


if __name__ == "__main__":
    unittest.main()
