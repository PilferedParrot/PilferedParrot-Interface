"""Authorization and consent timing regressions at the real HTTP boundary."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pilferedparrot.feedback import FeedbackStore
from pilferedparrot import web_server
from test_web_server import FakeApp, bare_handler


class FeedbackRouteTests(unittest.TestCase):
    def handler(self, app, path, *, token="dashboard-token", origin="http://127.0.0.1:8765"):
        original = app.capability_context
        app.capability_context = lambda supplied: (
            {"scope": "chat", "provider": "codex", "window_id": "chat"}
            if supplied == "chat-token" else original(supplied)
        )
        handler = bare_handler(web_server.make_handler(app), path=path)
        if token:
            handler.headers["X-PilferedParrot-Capability"] = token
        if origin:
            handler.headers["Origin"] = origin
        handler._json = MagicMock()
        handler._read_json = MagicMock(return_value={})
        return handler

    def test_reads_require_a_window_capability(self):
        for token, allowed in ((None, False), ("wrong", False), ("chat-token", True), ("dashboard-token", True)):
            with self.subTest(token=token):
                app = FakeApp()
                app.feedback_status = MagicMock(return_value={})
                handler = self.handler(app, "/api/feedback", token=token)
                handler.do_GET()
                self.assertEqual(app.feedback_status.called, allowed)
                if not allowed:
                    self.assertEqual(handler._json.call_args.args[1], 403)

    def test_all_actions_require_same_origin_and_valid_work_or_chat_capability(self):
        for action in ("consent", "report", "clear", "reset"):
            for token, origin, allowed in (
                (None, "http://127.0.0.1:8765", False),
                ("wrong", "http://127.0.0.1:8765", False),
                ("dashboard-token", None, False),
                ("dashboard-token", "http://evil.example", False),
                ("chat-token", "http://127.0.0.1:8765", True),
                ("dashboard-token", "http://127.0.0.1:8765", True),
            ):
                with self.subTest(action=action, token=token, origin=origin):
                    app = FakeApp()
                    app.feedback_action = MagicMock(return_value={})
                    handler = self.handler(app, "/api/feedback/" + action, token=token, origin=origin)
                    handler.do_POST()
                    self.assertEqual(app.feedback_action.called, allowed)
                    self.assertEqual(handler._read_json.called, allowed)
                    if allowed:
                        app.feedback_action.assert_called_once_with(action, {})
                    else:
                        self.assertEqual(handler._json.call_args.args[1], 403)

    def test_action_started_before_consent_cannot_be_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FeedbackStore(Path(directory) / "feedback.sqlite3")
            app = FakeApp()
            app.feedback_snapshot = store.snapshot
            app.record_feedback = store.record
            def enable_during_action(*args, **kwargs):
                store.set_consent({"category": "usage", "enabled": True, "policy_version": 1})
                return {"id": "private-session-id"}
            app.create_chat = enable_during_action
            handler = self.handler(app, "/api/chats")
            handler.do_POST()
            self.assertEqual(store.report()["counts"], [])
            app.create_chat = lambda *args, **kwargs: {"id": "private-session-id"}
            handler = self.handler(app, "/api/chats")
            handler.do_POST()
            store.close()
            self.assertEqual(store.report()["counts"], [
                {"category": "usage", "event": "new_session", "surface": "work", "count": 1},
            ])
            self.assertNotIn("private-session-id", str(store.report()))

    def test_feedback_failure_never_changes_successful_response(self):
        app = FakeApp()
        app.create_chat = MagicMock(return_value={"id": "session"})
        app.feedback_snapshot = MagicMock(side_effect=OSError("locked"))
        app.record_feedback = MagicMock(side_effect=OSError("disk full"))
        handler = self.handler(app, "/api/chats")
        handler.do_POST()
        handler._json.assert_called_once_with({"id": "session"}, 201)
