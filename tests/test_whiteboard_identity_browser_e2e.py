import json
import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get('PILFEREDPARROT_REQUIRE_PLAYWRIGHT') == '1':
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture
from pilferedparrot.whiteboard import Whiteboard


@unittest.skipUnless(sync_playwright, 'Playwright required')
class WhiteboardIdentityBrowserTests(unittest.TestCase):
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

    def _show_notes(self, notes):
        payload = json.dumps({
            'messages': notes,
            'count': len(notes),
            'has_more': False,
            'next_before': None,
        })
        self.page.route(
            '**/api/whiteboard*',
            lambda route: route.fulfill(content_type='application/json', body=payload),
        )
        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardBrowse').click()

    @staticmethod
    def _note(note_id, author, identity=None):
        note = {
            'id': note_id,
            'kind': 'finding',
            'title': f'Finding {note_id}',
            'text': f'Body {note_id}',
            'author': author,
            'created_at': '2026-09-18T12:00:00Z',
        }
        if identity is not None:
            note['identity'] = identity
        return note

    def test_runtime_identity_shows_each_workers_model_and_reasoning(self):
        self._show_notes([
            self._note('lead', 'lead/job-1', {
                'provider': 'codex',
                'model': 'gpt-5.2-codex',
                'reasoning_effort': 'high',
                'source': 'runtime',
                'model_source': 'configured',
                'reasoning_source': 'configured',
            }),
            self._note('worker', 'worker/job-2', {
                'provider': 'claude',
                'model': 'claude-sonnet-4-5',
                'reasoning_effort': 'medium',
                'source': 'runtime',
                'model_source': 'reported',
                'reasoning_source': 'reported',
            }),
        ])

        lead = self.page.locator('[data-note-id="lead"]')
        expect(lead.locator('.whiteboard-note-meta')).to_contain_text('lead/job-1')
        expect(lead.locator('.whiteboard-note-identity')).to_have_text(
            'Model: gpt-5.2-codex · Reasoning: high'
        )
        expect(lead.locator('.whiteboard-note-identity-details')).to_have_text(
            'Provider: codex · Runtime identity · Model/reasoning: runtime configured'
        )
        worker = self.page.locator('[data-note-id="worker"]')
        expect(worker.locator('.whiteboard-note-meta')).to_contain_text('worker/job-2')
        expect(worker.locator('.whiteboard-note-identity')).to_have_text(
            'Model: claude-sonnet-4-5 · Reasoning: medium'
        )
        expect(worker.locator('.whiteboard-note-identity-details')).to_have_text(
            'Provider: claude · Runtime identity · Model/reasoning: provider reported'
        )

    def test_unknown_and_legacy_identity_are_explicit(self):
        self._show_notes([
            self._note('unknown', 'worker/unknown', {
                'provider': '',
                'model': 'unknown',
                'reasoning_effort': '',
                'source': 'unknown',
                'model_source': 'unknown',
                'reasoning_source': 'unknown',
            }),
            self._note('legacy', 'legacy/job'),
        ])

        expect(
            self.page.locator('[data-note-id="unknown"] .whiteboard-note-identity')
        ).to_have_text(
            'Model: unknown · Reasoning: unknown'
        )
        expect(
            self.page.locator(
                '[data-note-id="unknown"] .whiteboard-note-identity-details'
            )
        ).to_have_text(
            'Provider: unknown · Identity source unknown · '
            'Model/reasoning: source unknown'
        )
        expect(
            self.page.locator('[data-note-id="legacy"] .whiteboard-note-identity')
        ).to_have_text('Identity unavailable')

    def test_self_reported_human_and_unsafe_identity_values_render_as_text(self):
        unsafe = '<img src=x onerror="window.identityInjected=true">'
        self._show_notes([
            self._note('self-reported', 'worker/job-3', {
                'provider': unsafe,
                'model': '<script>bad()</script>',
                'reasoning_effort': 'max & careful',
                'source': 'self-reported',
                'model_source': 'reported',
                'reasoning_source': 'reported',
            }),
            self._note('human', 'Chris', {
                'provider': '',
                'model': '',
                'reasoning_effort': '',
                'source': 'user',
                'model_source': 'not-applicable',
                'reasoning_source': 'not-applicable',
            }),
        ])

        reported = self.page.locator(
            '[data-note-id="self-reported"] .whiteboard-note-identity'
        )
        expect(reported).to_have_text(
            'Model: <script>bad()</script> · Reasoning: max & careful'
        )
        details = self.page.locator(
            '[data-note-id="self-reported"] .whiteboard-note-identity-details'
        )
        expect(details).to_contain_text(f'Provider: {unsafe}')
        expect(details).to_contain_text('Self-reported identity')
        expect(details).to_contain_text('Model/reasoning: provider reported')
        self.assertEqual(self.page.locator('#whiteboardMessages img').count(), 0)
        self.assertEqual(self.page.locator('#whiteboardMessages script').count(), 0)
        self.assertIsNone(self.page.evaluate('window.identityInjected'))

        human = self.page.locator('[data-note-id="human"]')
        expect(human.locator('.whiteboard-note-meta')).to_contain_text('Chris')
        expect(human.locator('.whiteboard-note-identity')).to_have_text('User')
        expect(human.locator('.whiteboard-note-identity')).not_to_contain_text('Model:')

    def test_real_store_and_http_round_trip_agent_and_user_identity(self):
        agent = Whiteboard(self.fixture.config).post(
            'Integrated agent note',
            author='worker/integration',
            identity={
                'provider': 'codex',
                'model': 'gpt-5.3-codex',
                'reasoning_effort': 'xhigh',
                'source': 'runtime',
                'model_source': 'configured',
                'reasoning_source': 'configured',
            },
            kind='finding',
            title='Integrated identity',
        )

        self.page.locator('#whiteboardButton').click()
        self.page.locator('#whiteboardBrowse').click()
        agent_card = self.page.locator(f'[data-note-id="{agent["id"]}"]')
        expect(agent_card.locator('.whiteboard-note-meta')).to_contain_text(
            'worker/integration'
        )
        expect(agent_card.locator('.whiteboard-note-identity')).to_have_text(
            'Model: gpt-5.3-codex · Reasoning: xhigh'
        )
        expect(agent_card.locator('.whiteboard-note-identity-details')).to_have_text(
            'Provider: codex · Runtime identity · Model/reasoning: runtime configured'
        )

        self.page.locator('#whiteboardText').fill('Integrated user note')
        with self.page.expect_response(
            lambda response: response.url.endswith('/api/whiteboard')
            and response.request.method == 'POST'
        ):
            self.page.locator('#whiteboardPost').click()
        user_card = self.page.locator('.whiteboard-note').filter(
            has_text='Integrated user note'
        )
        expect(user_card).to_have_count(1)
        expect(user_card.locator('.whiteboard-note-meta')).to_contain_text('User')
        expect(user_card.locator('.whiteboard-note-identity')).to_have_text('User')
        expect(user_card.locator('.whiteboard-note-identity-details')).to_have_count(0)
        persisted = self.fixture.app.whiteboard_read({
            'query': 'Integrated user note',
        })['messages']
        self.assertEqual(persisted[0]['identity']['source'], 'user')


if __name__ == '__main__':
    unittest.main()
