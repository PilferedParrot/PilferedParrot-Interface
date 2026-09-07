from unittest import TestCase
from unittest.mock import MagicMock
from pilferedparrot import web_server
from test_web_server import FakeApp, bare_handler


class WhiteboardRouteTests(TestCase):
    def test_board_requires_dashboard_and_images_allow_both_window_scopes(self):
        for scope in (None, 'chat', 'dashboard'):
            for path, method, allowed in (
                ('/api/whiteboard', 'whiteboard_read', scope == 'dashboard'),
                (
                    '/api/browser/theme/image/theme_frame'
                    '?v=abcdefghijklmnopabcdefghijklmnop-1.0',
                    'chrome_theme_image', scope in {'chat', 'dashboard'},
                ),
            ):
                with self.subTest(scope=scope, path=path):
                    app = FakeApp()
                    setattr(app, method, MagicMock(return_value=(b'png', 'image/png') if method == 'chrome_theme_image' else {}))
                    handler = bare_handler(web_server.make_handler(app), path=path)
                    handler._request_capability_scope = lambda: scope
                    handler._json = MagicMock(); handler._binary = MagicMock()
                    handler.do_GET()
                    self.assertEqual(getattr(app, method).called, allowed)
                    if not allowed: self.assertEqual(handler._json.call_args.args[1], 403)

    def test_whiteboard_post_requires_origin_and_dashboard_before_mutation(self):
        app = FakeApp(); app.whiteboard_post = MagicMock()
        handler = bare_handler(web_server.make_handler(app), path='/api/whiteboard')
        handler._control_allowed = MagicMock(return_value=False)
        handler._json = MagicMock()
        handler.do_POST()
        handler._control_allowed.assert_called_once_with('dashboard')
        app.whiteboard_post.assert_not_called()
