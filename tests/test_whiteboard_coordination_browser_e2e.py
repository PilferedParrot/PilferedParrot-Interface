import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get('PILFEREDPARROT_REQUIRE_PLAYWRIGHT') == '1': raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, 'Playwright required')
class WhiteboardCoordinationBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.fixture = PilferedParrotBrowserFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(self.fixture.browser_url)
        expect(self.page.locator('#prompt')).to_be_enabled()

    def _open_with_board(self, handler):
        self.page.route('**/api/whiteboard*', handler)
        self.page.locator('#whiteboardButton').click()

    def test_saved_independent_draft_reports_failed_comparison_read(self):
        self.page.route('**/api/whiteboard*', lambda route: route.fulfill(
            status=503, content_type='application/json', body='{"error":"Read unavailable"}',
        ) if route.request.method == 'GET' else route.continue_())
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardIndependent').click()
        self.page.locator('#whiteboardText').fill('Keep this saved idea')
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('independent draft is saved')
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('could not be loaded')
        expect(self.page.locator('#whiteboardText')).to_have_value('')
        notes = self.fixture.app.whiteboard_read()['messages']
        self.assertEqual([note['text'] for note in notes], ['Keep this saved idea'])

    def test_new_typing_survives_successful_post_and_reload(self):
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardText').fill('First contribution')
        self.page.evaluate('''() => {
          const original = api;
          api = async (path, options) => {
            const result = await original(path, options);
            if (path === '/api/whiteboard' && options?.method === 'POST') {
              const input = document.querySelector('#whiteboardText');
              input.value = 'My next contribution';
              input.dispatchEvent(new Event('input'));
            }
            return result;
          };
        }''')
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardText')).to_have_value('My next contribution')
        self.assertEqual(self.fixture.app.whiteboard_read()['messages'][0]['text'], 'First contribution')
        self.page.reload()
        self.page.locator('#whiteboardButton').click()
        expect(self.page.locator('#whiteboardText')).to_have_value('My next contribution')

    def test_independent_draft_waits_to_read_then_retains_on_error(self):
        calls = []

        def board(route):
            request = route.request
            calls.append((request.method, request.url, request.post_data))
            if request.method == 'POST':
                route.fulfill(status=201, content_type='application/json', body=json.dumps({'id': 'draft-1'}))
            else:
                route.fulfill(content_type='application/json', body=json.dumps({
                    'messages': [{'id': 'draft-1', 'kind': 'idea', 'title': 'First thought',
                                  'text': 'Draft body', 'basis': 'independent',
                                  'created_at': '2026-01-01T10:00:00Z'}],
                    'count': 1, 'has_more': False, 'next_before': None,
                }))

        self._open_with_board(board)
        self.page.locator('#whiteboardIndependent').click()
        if os.environ.get('PPI_SCREENSHOTS'):
            folder = Path(os.environ['PPI_SCREENSHOTS']); folder.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(folder / 'whiteboard-independent-entry.png'))
        self.assertEqual([call for call in calls if call[0] == 'GET'], [])
        self.page.locator('#whiteboardText').fill('Draft body')
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('independent draft is saved')
        posted = json.loads(next(call[2] for call in calls if call[0] == 'POST'))
        self.assertEqual(posted['basis'], 'independent')
        self.assertNotIn('thread=', next(call[1] for call in calls if call[0] == 'GET'))

        self.page.unroute('**/api/whiteboard*')
        self.page.route('**/api/whiteboard*', lambda route: route.fulfill(
            status=400, content_type='application/json', body='{"error":"Keep the draft"}',
        ))
        self.page.locator('#whiteboardText').fill('Draft survives a rejected post')
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('Keep the draft')
        expect(self.page.locator('#whiteboardText')).to_have_value('Draft survives a rejected post')

    def test_unfinished_informed_draft_keeps_its_basis_but_empty_composer_does_not(self):
        posts = []

        def board(route):
            if route.request.method == 'POST':
                posts.append(json.loads(route.request.post_data))
                route.fulfill(status=201, content_type='application/json', body=json.dumps({
                    'id': 'draft-%d' % len(posts),
                }))
            else:
                route.fulfill(content_type='application/json', body=json.dumps({
                    'messages': [], 'count': 0, 'has_more': False, 'next_before': None,
                }))

        self._open_with_board(board)
        self.page.locator('#whiteboardBrowse').click()
        expect(self.page.locator('#whiteboardModeNote')).to_contain_text('marked as informed')
        self.page.locator('#whiteboardText').fill('Keep the informed draft')
        self.page.reload()
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardIndependent').click()
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard') and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        self.assertEqual(posts[-1]['basis'], 'informed')
        expect(self.page.locator('#whiteboardText')).to_have_value('')

        self.page.reload()
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardIndependent').click()
        self.page.locator('#whiteboardText').fill('A fresh independent draft')
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard') and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        self.assertEqual(posts[-1]['basis'], 'independent')

    def test_search_paging_finding_and_request_reply_resolution(self):
        calls = []
        request = {
            'id': 'request-1', 'kind': 'request', 'title': 'Check migration',
            'text': 'Can someone verify the migration?', 'project': 'console',
            'topics': ['release', 'database'], 'evidence': 'CI failed on Windows',
            'status': 'open', 'effective_status': 'open', 'reply_count': 2,
            'created_at': '2026-01-02T10:00:00Z',
        }
        finding = {
            'id': 'finding-1', 'kind': 'finding', 'title': 'Cause found',
            'text': 'The stale cache selects the old schema.', 'project': 'console',
            'topics': ['database'], 'evidence': 'Reproduced locally',
            'applies_to': 'Windows launcher', 'created_at': '2026-01-02T11:00:00Z',
        }

        def board(route):
            parsed = urlparse(route.request.url)
            query = parse_qs(parsed.query)
            calls.append((route.request.method, query, route.request.post_data))
            if route.request.method == 'POST':
                route.fulfill(status=201, content_type='application/json', body='{"id":"update-1"}')
            elif query.get('before'):
                route.fulfill(content_type='application/json', body=json.dumps({
                    'messages': [{'id': 'older-1', 'kind': 'note', 'title': 'Older', 'text': 'Older note',
                                  'created_at': '2026-01-01T09:00:00Z'}],
                    'count': 1, 'has_more': False, 'next_before': None,
                }))
            else:
                route.fulfill(content_type='application/json', body=json.dumps({
                    'messages': [request, finding], 'count': 2, 'has_more': True,
                    'next_before': 'older-cursor',
                }))

        self._open_with_board(board)
        self.page.locator('#whiteboardBrowse').click()
        expect(self.page.locator('#whiteboardMessages')).to_contain_text('Cause found')
        expect(self.page.locator('#whiteboardMessages')).to_contain_text('Reproduced locally')
        if os.environ.get('PPI_SCREENSHOTS'):
            folder = Path(os.environ['PPI_SCREENSHOTS']); folder.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(folder / 'whiteboard-populated-desktop.png'))
        self.page.locator('#whiteboardSearch').fill('cache')
        self.page.locator('#whiteboardFilters summary').click()
        self.page.locator('#whiteboardProject').fill('console')
        self.page.locator('#whiteboardFilters').get_by_role('button', name='Apply filters').click()
        self.assertEqual(calls[-1][1]['query'], ['cache'])
        self.assertEqual(calls[-1][1]['project'], ['console'])
        self.page.locator('#whiteboardOlder').click()
        self.assertEqual(calls[-1][1]['before'], ['older-cursor'])
        expect(self.page.locator('#whiteboardMessages')).to_contain_text('Older note')

        card = self.page.locator('[data-note-id="request-1"]')
        card.get_by_role('button', name='Reply').click()
        expect(self.page.locator('#whiteboardReplyTo')).to_have_value('request-1')
        self.assertEqual(self.page.locator('#whiteboardComposeKind').input_value(), 'note')
        card.get_by_role('button', name='Resolve').click()
        update = json.loads(next(post for method, _query, post in reversed(calls) if method == 'POST'))
        self.assertEqual(update['reply_to'], 'request-1')
        self.assertEqual(update['status'], 'resolved')

    def test_whiteboard_mobile_controls_do_not_overflow(self):
        self._open_with_board(lambda route: route.fulfill(content_type='application/json', body=json.dumps({
            'messages': [], 'count': 0, 'has_more': False, 'next_before': None,
        })))
        self.page.set_viewport_size({'width': 320, 'height': 568})
        self.page.locator('#whiteboardBrowse').click()
        if os.environ.get('PPI_SCREENSHOTS'):
            folder = Path(os.environ['PPI_SCREENSHOTS']); folder.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(folder / 'whiteboard-narrow.png'))
        overflow = self.page.locator('#whiteboardDialog').evaluate(
            'node => node.scrollWidth <= node.clientWidth + 1',
        )
        self.assertTrue(overflow)

    def test_real_board_searches_old_note_and_persists_request_thread_status(self):
        # This uses the real isolated fixture store and HTTP handler.  The
        # mocked tests above cover transport failures and request parameters.
        self.fixture.app.whiteboard_post({
            'text': 'Needle finding body', 'kind': 'finding', 'title': 'Old finding',
            'project': 'real-project', 'evidence': 'seed evidence',
        })
        for number in range(24):
            self.fixture.app.whiteboard_post({
                'text': f'Recent note {number}', 'kind': 'note', 'project': 'real-project',
            })
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardBrowse').click()
        self.page.locator('#whiteboardSearch').fill('Needle finding')
        self.page.locator('#whiteboardFilters').get_by_role('button', name='Apply filters').click()
        expect(self.page.locator('#whiteboardMessages')).to_contain_text('Old finding')
        expect(self.page.locator('#whiteboardMessages')).to_contain_text('seed evidence')

        self.page.locator('#whiteboardComposeKind').select_option('request')
        self.page.locator('#whiteboardTitleInput').fill('Real request')
        self.page.locator('#whiteboardText').fill('Please validate the actual roundtrip.')
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard') and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        self.page.locator('#whiteboardSearch').fill('Real request')
        self.page.locator('#whiteboardFilters').get_by_role('button', name='Apply filters').click()
        request = self.page.locator('.whiteboard-note').filter(has_text='Real request')
        expect(request).to_have_count(1)
        request_id = request.get_attribute('data-note-id')
        request.get_by_role('button', name='Reply').click()
        expect(self.page.locator('#whiteboardComposeKind')).to_have_value('note')
        self.page.locator('#whiteboardText').fill('I will validate it.')
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard') and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard') and response.request.method == 'POST'):
            self.page.locator('[data-note-id="%s"]' % request_id).get_by_role('button', name='Claim').click()
        expect(self.page.locator('[data-note-id="%s"]' % request_id).get_by_role('button', name='Resolve')).to_be_visible()
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard') and response.request.method == 'POST'):
            self.page.locator('[data-note-id="%s"]' % request_id).get_by_role('button', name='Resolve').click()
        persisted = self.fixture.app.whiteboard_read({'thread': request_id})['messages']
        target = next(note for note in persisted if note['id'] == request_id)
        self.assertEqual(target['effective_status'], 'resolved', persisted)

    def test_real_independent_draft_is_saved_before_peer_read(self):
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardIndependent').click()
        self.page.locator('#whiteboardComposeKind').select_option('idea')
        self.page.locator('#whiteboardText').fill('An actual independent draft')
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('independent draft is saved')
        note = self.fixture.app.whiteboard_read({'query': 'actual independent'})['messages'][0]
        self.assertEqual(note['basis'], 'independent')
