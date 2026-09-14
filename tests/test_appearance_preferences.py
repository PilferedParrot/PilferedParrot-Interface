"""Coverage for application-wide persisted appearance preferences."""

from __future__ import annotations

import json
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilferedparrot import web_server
from pilferedparrot.web_persistence import PersistentChatStore


class AppearancePreferencePersistenceTests(unittest.TestCase):
    @staticmethod
    def _usage(*_args, **_kwargs):
        return {"percent": 0, "used_tokens": 0, "limit_tokens": 1}

    def test_invalid_persisted_appearance_normalizes_each_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            path.write_text(json.dumps({
                "version": 8, "chats": [], "preferences": {
                    "appearance": {"tone": "darker", "surface": "bad", "readability": []},
                },
            }), encoding="utf-8")

            store = PersistentChatStore(path, context_usage=self._usage)

            self.assertEqual(store.appearance_preferences(), {
                "tone": "darker", "surface": "minimal", "readability": "standard",
            })
            self.assertEqual(store.preferences_public()["appearance"], store.appearance_preferences())

    def test_partial_updates_merge_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = PersistentChatStore(path, context_usage=self._usage)

            self.assertEqual(store.set_appearance_preferences({"tone": "darker"}), {
                "tone": "darker", "surface": "minimal", "readability": "standard",
            })
            self.assertEqual(store.set_appearance_preferences({"surface": "maximal"}), {
                "tone": "darker", "surface": "maximal", "readability": "standard",
            })
            restarted = PersistentChatStore(path, context_usage=self._usage)
            self.assertEqual(restarted.appearance_preferences(), {
                "tone": "darker", "surface": "maximal", "readability": "standard",
            })

    def test_fresh_store_uses_new_appearance_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersistentChatStore(Path(directory) / "chats.json", context_usage=self._usage)

            self.assertEqual(store.appearance_preferences(), {
                "tone": "darker", "surface": "minimal", "readability": "standard",
            })

    def test_existing_saved_appearance_choices_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = PersistentChatStore(path, context_usage=self._usage)
            expected = {"tone": "original", "surface": "balanced", "readability": "standard"}

            self.assertEqual(store.set_appearance_preferences(expected), expected)
            restarted = PersistentChatStore(path, context_usage=self._usage)
            self.assertEqual(restarted.appearance_preferences(), expected)

    def test_invalid_update_is_rejected_without_changing_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersistentChatStore(Path(directory) / "chats.json", context_usage=self._usage)
            store.set_appearance_preferences({
                "tone": "original", "surface": "balanced", "readability": "standard",
            })
            before = store.appearance_preferences()

            for payload in ({}, {"tone": "original", "unknown": "value"}, {"surface": []}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    store.set_appearance_preferences(payload)
                self.assertEqual(store.appearance_preferences(), before)

    def test_save_failure_rolls_back_memory_and_a_later_update_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.json"
            store = PersistentChatStore(path, context_usage=self._usage)
            before = store.set_appearance_preferences({
                "tone": "original", "surface": "balanced", "readability": "standard",
            })
            persisted_before = json.loads(path.read_text(encoding="utf-8"))

            with patch.object(store, "save", side_effect=OSError("disk unavailable")):
                with self.assertRaisesRegex(OSError, "disk unavailable"):
                    store.set_appearance_preferences({"surface": "maximal"})

            self.assertEqual(store.appearance_preferences(), before)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), persisted_before)
            self.assertEqual(store.set_appearance_preferences({"surface": "maximal"}), {
                "tone": "original", "surface": "maximal", "readability": "standard",
            })


class AppearanceRouteApp:
    def __init__(self):
        self.config = {"web": {"host": "127.0.0.1", "port": 8765}}
        self.default_provider = "codex"
        self.dashboard_capability = "dashboard-token"
        self.appearance_preferences = MagicMock(return_value={
            "tone": "original", "surface": "balanced", "readability": "standard",
        })
        self.set_appearance_preferences = MagicMock(return_value={
            "tone": "darker", "surface": "balanced", "readability": "standard",
        })
        self.set_provider_preferences = MagicMock()

    def capability_context(self, supplied):
        if supplied == "dashboard-token":
            return {"scope": "dashboard", "window_id": "main", "provider": "codex"}
        if supplied == "chat-token":
            return {"scope": "chat", "window_id": "chat", "provider": "codex"}
        return None


def route_handler(app, *, path, capability=None, origin=None):
    handler = object.__new__(web_server.make_handler(app))
    handler.path = path
    handler.server = MagicMock(server_address=("127.0.0.1", 8765))
    handler.client_address = ("127.0.0.1", 43210)
    handler.headers = {"Host": "127.0.0.1:8765"}
    if capability is not None:
        handler.headers["X-PilferedParrot-Capability"] = capability
    if origin is not None:
        handler.headers["Origin"] = origin
    handler._json = MagicMock()
    return handler


class AppearanceRouteTests(unittest.TestCase):
    def test_dashboard_and_chat_capabilities_can_read_appearance(self):
        for capability in ("dashboard-token", "chat-token"):
            with self.subTest(capability=capability):
                app = AppearanceRouteApp()
                handler = route_handler(app, path="/api/preferences/appearance", capability=capability)

                handler.do_GET()

                app.appearance_preferences.assert_called_once_with()
                handler._json.assert_called_once_with(app.appearance_preferences.return_value)

    def test_dashboard_and_chat_capabilities_can_write_partial_appearance(self):
        for capability in ("dashboard-token", "chat-token"):
            with self.subTest(capability=capability):
                app = AppearanceRouteApp()
                handler = route_handler(
                    app, path="/api/preferences/appearance", capability=capability,
                    origin="http://127.0.0.1:8765",
                )
                handler._read_json = MagicMock(return_value={"tone": "darker"})

                handler.do_POST()

                app.set_appearance_preferences.assert_called_once_with({"tone": "darker"})
                handler._json.assert_called_once_with(app.set_appearance_preferences.return_value)

    def test_appearance_post_rejects_missing_wrong_or_cross_origin_capability(self):
        for capability, origin in (
            (None, "http://127.0.0.1:8765"),
            ("wrong-token", "http://127.0.0.1:8765"),
            ("dashboard-token", "http://127.0.0.1:9999"),
        ):
            with self.subTest(capability=capability, origin=origin):
                app = AppearanceRouteApp()
                handler = route_handler(
                    app, path="/api/preferences/appearance", capability=capability, origin=origin,
                )
                handler._read_json = MagicMock(return_value={"tone": "darker"})

                handler.do_POST()

                app.set_appearance_preferences.assert_not_called()
                handler._read_json.assert_not_called()
                handler._json.assert_called_once_with(
                    {"error": "local control authorization failed"}, HTTPStatus.FORBIDDEN,
                )

    def test_appearance_get_rejects_missing_wrong_or_cross_origin_capability(self):
        for capability, origin in (
            (None, None),
            ("wrong-token", None),
            ("dashboard-token", "http://127.0.0.1:9999"),
        ):
            with self.subTest(capability=capability, origin=origin):
                app = AppearanceRouteApp()
                handler = route_handler(
                    app, path="/api/preferences/appearance", capability=capability, origin=origin,
                )

                handler.do_GET()

                app.appearance_preferences.assert_not_called()
                handler._json.assert_called_once_with(
                    {"error": "window authorization failed"}, HTTPStatus.FORBIDDEN,
                )

    def test_chat_capability_cannot_write_ordinary_work_preferences(self):
        app = AppearanceRouteApp()
        handler = route_handler(
            app, path="/api/preferences/provider", capability="chat-token",
            origin="http://127.0.0.1:8765",
        )

        handler.do_POST()

        app.set_provider_preferences.assert_not_called()
        handler._json.assert_called_once_with(
            {"error": "local control authorization failed"}, HTTPStatus.FORBIDDEN,
        )


if __name__ == "__main__":
    unittest.main()
