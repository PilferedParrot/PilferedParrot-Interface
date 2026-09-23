from unittest import TestCase
from unittest.mock import MagicMock
import tempfile
from pathlib import Path
from pilferedparrot import web_server
from test_web_server import FakeApp, bare_handler


class WhiteboardRouteTests(TestCase):
    def test_search_filters_reach_board(self):
        app = FakeApp()
        app.whiteboard_read = MagicMock(return_value={"messages": [], "count": 0})
        handler = bare_handler(web_server.make_handler(app), path=(
            '/api/whiteboard?query=save+failure&project=Pilfered+Parrot'
            '&topic=appearance&kind=finding&before=older-note&limit=5'
        ))
        handler._request_capability_scope = lambda: 'dashboard'
        handler._json = MagicMock()
        handler.do_GET()
        app.whiteboard_read.assert_called_once_with({
            'query': 'save failure', 'project': 'Pilfered Parrot',
            'topic': 'appearance', 'kind': 'finding', 'before': 'older-note', 'limit': '5',
        })

    def test_invalid_or_repeated_filters_fail_before_board_access(self):
        for query in ('query=one&query=two', 'file=../../private'):
            app = FakeApp()
            app.whiteboard_read = MagicMock()
            handler = bare_handler(web_server.make_handler(app), path='/api/whiteboard?' + query)
            handler._request_capability_scope = lambda: 'dashboard'
            handler._json = MagicMock()
            handler.do_GET()
            self.assertEqual(handler._json.call_args.args[1], 400)
            app.whiteboard_read.assert_not_called()

    def test_application_preserves_metadata_and_appends_request_updates(self):
        from pilferedparrot.web import PilferedParrotApp
        with tempfile.TemporaryDirectory() as directory:
            app = object.__new__(PilferedParrotApp)
            app.config = {'whiteboard': {'directory': directory}}
            request = app.whiteboard_post({
                'text': 'Can someone reproduce the failed save?', 'kind': 'request',
                'title': 'Reproduce failed save', 'project': 'PPI', 'topics': ['appearance'],
            })
            reply = app.whiteboard_post({
                'text': 'Reproduced with the isolated fixture.', 'kind': 'finding',
                'reply_to': request['id'], 'evidence': 'tests/reproduce_save.py',
                'applies_to': 'Isolated fixture only',
            })
            app.whiteboard_post({'text': 'Question answered.', 'kind': 'update',
                                 'reply_to': request['id'], 'status': 'resolved'})
            result = app.whiteboard_read({'thread': request['id']})
            self.assertEqual({m['id'] for m in result['messages']} & {reply['id']}, {reply['id']})
            root = next(m for m in result['messages'] if m['id'] == request['id'])
            self.assertEqual(root['effective_status'], 'resolved')
            self.assertEqual(len(list(Path(directory).glob('*.txt'))), 3)
            self.assertTrue(all(m['author'] == 'User' for m in result['messages']))
            with self.assertRaises(ValueError):
                app.whiteboard_post({'text': 'spoofed', 'author': 'Someone else'})
            with self.assertRaises(ValueError):
                app.whiteboard_read({'arbitrary': 'file'})

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
