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

    def test_open_continuation_can_be_copied_exactly_and_resolved(self):
        prompt = ('Continue the Alpha review in /tmp/alpha.\n'
                  'Verified: 3 focused checks passed.\n'
                  'Next: inspect the narrow view, then verify completion.')
        target = self.fixture.app.whiteboard_post({
            'text': prompt, 'kind': 'handoff', 'status': 'open',
            'topics': ['continuation'], 'project': 'Alpha',
        })
        other = self.fixture.app.whiteboard_post({
            'text': 'Continue the Bravo review.', 'kind': 'handoff', 'status': 'open',
            'topics': ['continuation'], 'project': 'Bravo',
        })
        closed = self.fixture.app.whiteboard_post({
            'text': 'Finished earlier work.', 'kind': 'handoff', 'status': 'open',
            'topics': ['continuation'], 'project': 'Alpha',
        })
        self.fixture.app.whiteboard_post({
            'text': 'All earlier checks passed.', 'kind': 'update',
            'reply_to': closed['id'], 'status': 'resolved',
        })
        self.fixture.app.whiteboard_post({
            'text': 'An unrelated open request.', 'kind': 'request', 'project': 'Alpha',
        })
        reads = []
        self.page.on('request', lambda request: reads.append(request.url)
                     if request.method == 'GET' and '/api/whiteboard?' in request.url else None)
        self.page.locator('#whiteboardButton').click()
        self.assertEqual(reads, [], 'opening Whiteboard must not read the shared feed')
        self.page.locator('#whiteboardOpenContinuations').click()
        expect(self.page.locator(f'[data-note-id="{target["id"]}"]')).to_be_visible()
        expect(self.page.locator(f'[data-note-id="{other["id"]}"]')).to_be_visible()
        expect(self.page.locator(f'[data-note-id="{closed["id"]}"]')).to_have_count(0)
        self.assertIn('kind=handoff', reads[-1])
        self.assertIn('topic=continuation', reads[-1])
        self.assertIn('status=open', reads[-1])
        self.assertNotIn('query=', reads[-1])
        self.page.locator('#whiteboardSearch').fill('stale search')
        self.page.locator('#whiteboardOpenContinuations').click()
        self.assertNotIn('query=', reads[-1])

        self.page.locator('#whiteboardFilters summary').click()
        self.page.locator('#whiteboardProject').fill('Alpha')
        self.page.locator('#whiteboardOpenContinuations').click()
        card = self.page.locator(f'[data-note-id="{target["id"]}"]')
        expect(card).to_be_visible()
        expect(self.page.locator(f'[data-note-id="{other["id"]}"]')).to_have_count(0)
        self.assertIn('project=Alpha', reads[-1])

        self.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        card.get_by_role('button', name='Copy next-session prompt').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('copied')
        copied = self.page.evaluate('navigator.clipboard.readText()')
        self.assertEqual(copied.replace('\r\n', '\n'), prompt)

        card.get_by_role('button', name='Resolve').click()
        expect(card).to_contain_text('resolved')
        persisted = self.fixture.app.whiteboard_read({'thread': target['id']})['messages']
        self.assertEqual(next(note for note in persisted if note['id'] == target['id'])
                         ['effective_status'], 'resolved')
        self.assertTrue(any(note['kind'] == 'update' and note['reply_to'] == target['id']
                            and note['status'] == 'resolved' for note in persisted))
        self.page.locator('#whiteboardProject').fill('Alpha')
        self.page.locator('#whiteboardOpenContinuations').click()
        expect(self.page.locator(f'[data-note-id="{target["id"]}"]')).to_have_count(0)

        self.page.locator('#whiteboardComposeKind').select_option('handoff')
        self.page.locator('#whiteboardText').fill('General Alpha handoff.')
        self.page.locator('#whiteboardForm .whiteboard-details summary').click()
        self.page.locator('#whiteboardComposeProject').fill('Alpha')
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard')
                                       and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        generic = self.fixture.app.whiteboard_read({
            'query': 'General Alpha handoff.', 'kind': 'handoff',
        })['messages']
        self.assertEqual(len(generic), 1)
        self.assertEqual(generic[0]['status'], '')
        self.assertNotIn('continuation', generic[0]['topics'])

        self.page.locator('#whiteboardComposeTopics').fill('a, b, c, d, e, f, g, h')
        self.page.get_by_role('button', name='Continuation starter').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('at most eight topics')
        expect(self.page.locator('#whiteboardComposeKind')).to_have_value('note')
        self.page.locator('#whiteboardComposeTopics').fill('review')
        self.page.get_by_role('button', name='Continuation starter').click()
        expect(self.page.locator('#whiteboardComposeKind')).to_have_value('handoff')
        expect(self.page.locator('#whiteboardComposeTopics')).to_have_value('review, continuation')
        self.assertIn('Original objective:', self.page.locator('#whiteboardText').input_value())
        self.page.locator('#whiteboardText').fill('Continue a new Alpha task.')
        self.page.locator('#whiteboardComposeProject').fill('Alpha')
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard')
                                       and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        created = self.fixture.app.whiteboard_read({
            'query': 'Continue a new Alpha task.', 'kind': 'handoff',
        })['messages']
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]['status'], 'open')
        self.assertIn('continuation', created[0]['topics'])
        self.assertIn('review', created[0]['topics'])

    def test_completed_work_reply_opens_reviewable_continuation_draft(self):
        reply = ('Task incomplete.\n\n### Next session prompt\n'
                 'Objective: finish the Alpha check.\n'
                 'Completed: the first check passed.\n'
                 'Next: run the second check and inspect its result.')
        self.page.evaluate('''text => {
          const chat = state.chats.find(item => item.id === state.activeId);
          chat.messages.push({id: 'manual-continuation-reply', role: 'assistant',
                              provider: 'codex', content: text, created_at: Date.now() / 1000,
                              activity: [{id: 'manual-tool-output', kind: 'tool_result',
                                          content: 'PRIVATE-TOOL-SAMPLE'}]});
          renderMessages();
        }''', reply)
        actions = self.page.locator('[data-draft-continuation="manual-continuation-reply"]')
        expect(actions).to_have_count(1)
        actions.focus()
        self.page.evaluate('renderMessages()')
        expect(actions).to_be_focused()
        selected_tool_text = self.page.evaluate('''() => {
          const article = document.querySelector('[data-draft-continuation="manual-continuation-reply"]')
            .closest('article.message');
          const work = article.querySelector('.work-log');
          work.open = true;
          const output = work.querySelector('.work-item > div');
          const range = document.createRange();
          range.selectNodeContents(output);
          window.getSelection().removeAllRanges();
          window.getSelection().addRange(range);
          return selectedWorkMessageText(article);
        }''')
        self.assertEqual(selected_tool_text, '', 'tool output is not a reply selection')
        self.page.evaluate('window.getSelection().removeAllRanges()')
        requests = []
        self.page.on('request', lambda request: requests.append((request.method, request.url))
                     if '/api/whiteboard' in request.url else None)
        actions.click()
        expect(self.page.locator('#whiteboardDialog')).to_be_visible()
        self.assertEqual(requests, [], 'drafting must not read or post the shared board')
        expect(self.page.locator('#whiteboardComposeKind')).to_have_value('handoff')
        expect(self.page.locator('#whiteboardComposeTopics')).to_have_value('continuation')
        expect(self.page.locator('#whiteboardComposeWorkspace')).to_have_value(
            str(self.fixture.project))
        expect(self.page.locator('#whiteboardComposeProject')).to_have_value(
            self.fixture.project.name)
        expect(self.page.locator('#whiteboardText')).to_have_value(reply)

        self.page.locator('#whiteboardText').fill('Continue the second Alpha check.')
        with self.page.expect_response(lambda response: response.url.endswith('/api/whiteboard')
                                       and response.request.method == 'POST'):
            self.page.locator('#whiteboardPost').click()
        saved = self.fixture.app.whiteboard_read({
            'query': 'Continue the second Alpha check.', 'kind': 'handoff',
        })['messages']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['status'], 'open')
        self.assertIn('continuation', saved[0]['topics'])
        self.assertEqual(saved[0]['workspace'], str(self.fixture.project))

        self.page.locator('#whiteboardText').fill('Keep this unsent draft')
        self.page.locator('#whiteboardClose').click()
        actions.click()
        expect(self.page.locator('#whiteboardText')).to_have_value('Keep this unsent draft')
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('preserved')

    def test_overlong_work_reply_uses_starter_without_truncation(self):
        self.page.evaluate('''() => {
          const chat = state.chats.find(item => item.id === state.activeId);
          chat.messages.push({id: 'long-continuation-reply', role: 'assistant',
                              provider: 'codex', content: 'x'.repeat(2001),
                              created_at: Date.now() / 1000});
          renderMessages();
        }''')
        self.page.locator('[data-draft-continuation="long-continuation-reply"]').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('exceeds 2,000')
        self.assertIn('Original objective:', self.page.locator('#whiteboardText').input_value())
        self.assertNotIn('x' * 100, self.page.locator('#whiteboardText').input_value())
        self.assertEqual(self.fixture.app.whiteboard_read()['messages'], [])

    def test_selected_reply_text_prefills_continuation_draft(self):
        self.page.evaluate('''() => {
          const chat = state.chats.find(item => item.id === state.activeId);
          chat.messages.push({id: 'selected-continuation-reply', role: 'assistant',
                              provider: 'codex', content: 'Summary paragraph.\\n\\nNext session prompt: finish the selected check.',
                              created_at: Date.now() / 1000});
          renderMessages();
          const article = document.querySelector('[data-draft-continuation="selected-continuation-reply"]')
            .closest('article.message');
          const paragraph = article.querySelector('.work-reply p:last-child');
          const range = document.createRange();
          range.selectNodeContents(paragraph);
          window.getSelection().removeAllRanges();
          window.getSelection().addRange(range);
        }''')
        self.page.locator('[data-draft-continuation="selected-continuation-reply"]').click()
        expect(self.page.locator('#whiteboardText')).to_have_value(
            'Next session prompt: finish the selected check.')
        self.assertEqual(self.fixture.app.whiteboard_read()['messages'], [])

    def test_real_independent_draft_is_saved_before_peer_read(self):
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardIndependent').click()
        self.page.locator('#whiteboardComposeKind').select_option('idea')
        self.page.locator('#whiteboardText').fill('An actual independent draft')
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardStatus')).to_contain_text('independent draft is saved')
        note = self.fixture.app.whiteboard_read({'query': 'actual independent'})['messages'][0]
        self.assertEqual(note['basis'], 'independent')
