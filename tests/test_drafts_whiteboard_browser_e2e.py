import os
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get('PILFEREDPARROT_REQUIRE_PLAYWRIGHT') == '1': raise
    expect = sync_playwright = None
from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, 'Playwright required')
class DraftWhiteboardBrowserTests(unittest.TestCase):
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
        self.prompt = self.page.locator('#prompt')
        expect(self.prompt).to_be_enabled()

    def test_draft_survives_navigation_reload_and_server_restart(self):
        first = self.page.evaluate('state.activeId')
        self.prompt.fill('  A draft with whitespace\nand a second line  ')
        self.page.locator('#newWorkSession').click()
        self.page.wait_for_function('(first) => state.activeId !== first', arg=first)
        second = self.page.evaluate('state.activeId')
        self.prompt.fill('another session draft')
        self.page.locator(f'[data-chat="{first}"]').click()
        expect(self.prompt).to_have_value('  A draft with whitespace\nand a second line  ')
        self.page.reload()
        expect(self.prompt).to_have_value('  A draft with whitespace\nand a second line  ')
        self.page.locator(f'[data-chat="{second}"]').click()
        expect(self.prompt).to_have_value('another session draft')
        self.page.evaluate('flushDraft(state.activeId)')
        from pilferedparrot.web import ChatStore
        persisted = ChatStore(Path(self.fixture.app.config['web']['chat_store']))
        self.assertEqual(persisted.get(first)['draft'], '  A draft with whitespace\nand a second line  ')
        self.assertEqual(persisted.get(second)['draft'], 'another session draft')

    def test_failed_submission_preserves_and_success_keeps_new_typing(self):
        self.page.route('**/api/chats/*/messages', lambda route: route.fulfill(status=400, content_type='application/json', body='{"error":"Rejected"}'))
        self.prompt.fill('  Keep this draft  ')
        self.page.locator('#sendButton').click()
        expect(self.page.locator('#toast')).to_contain_text('Rejected')
        expect(self.prompt).to_have_value('  Keep this draft  ')
        self.page.unroute('**/api/chats/*/messages')
        self.page.evaluate('''() => {
          const original = api;
          api = async (path, options) => {
            const result = await original(path, options);
            if (path.endsWith('/messages')) {
              document.querySelector('#prompt').value = 'My next draft';
              document.querySelector('#prompt').dispatchEvent(new Event('input'));
            }
            return result;
          };
        }''')
        self.page.locator('#sendButton').click()
        expect(self.prompt).to_have_value('My next draft')
        self.page.evaluate('flushDraft(state.activeId)')
        self.page.reload()
        expect(self.prompt).to_have_value('My next draft')

    def test_whiteboard_is_shared_plain_text_and_persistent(self):
        self.page.locator('#whiteboardButton').click()
        expect(self.page.locator('#whiteboardStatus')).to_have_text('No messages yet.')
        note = '<script>bad()</script> useful note'
        self.page.locator('#whiteboardText').fill(note)
        self.page.locator('#whiteboardPost').click()
        expect(self.page.locator('#whiteboardMessages')).to_contain_text(note)
        self.assertEqual(self.page.locator('#whiteboardMessages script').count(), 0)
        self.page.locator('#whiteboardClose').click()
        self.page.reload()
        self.page.locator('#whiteboardButton').click()
        expect(self.page.locator('#whiteboardMessages')).to_contain_text(note)

    def test_bright_theme_preserves_art_colors_and_readable_composer(self):
        import struct, zlib
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        rows = b''.join(b'\0' + bytes((255, 190, 35)) * 120 + bytes((210, 40, 135)) * 120 for _ in range(160))
        art = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 240, 160, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
        self.page.route('**/api/browser/theme/background', lambda route: route.fulfill(body=art, content_type='image/png'))
        self.page.evaluate('''() => applyBrowserTheme({
          active: true, name: 'Bright fixture', colors: {
            ntp_background: '#f5efdc', ntp_text: '#181020', ntp_section: '#fffaf0',
            frame: '#d42a8c', toolbar: '#ffc442', ntp_link: '#60309a'
          }, background: true, background_url: '/api/browser/theme/background',
          background_alignment: 'right bottom', background_repeat: 'repeat'
        })''')
        palette = self.page.evaluate('''() => {
          const main = getComputedStyle(document.querySelector('.shell'));
          const composer = getComputedStyle(document.querySelector('.composer'));
          const prompt = getComputedStyle(document.querySelector('#prompt'));
          return {background: main.backgroundColor, image: main.backgroundImage,
            size: main.backgroundSize, position: main.backgroundPosition,
            panel: composer.backgroundColor, text: prompt.color,
            toolbar: getComputedStyle(document.querySelector('.topbar')).backgroundColor};
        }''')
        decoded = self.page.evaluate("""async () => {
          const image = new Image();
          image.src = themeBackgroundObjectUrl;
          await image.decode();
          return image.naturalWidth;
        }""")
        self.assertEqual(decoded, 240)
        self.assertEqual(palette['background'], 'rgb(245, 239, 220)')
        self.assertIn('blob:', palette['image'])
        self.assertNotIn('gradient', palette['image'])
        self.assertNotIn('cover', palette['size'])
        self.assertIn('100% 100%', palette['position'])
        self.assertEqual(palette['toolbar'], 'rgb(255, 196, 66)')
        self.assertEqual(palette['panel'], 'rgb(255, 250, 240)')
        self.assertEqual(palette['text'], 'rgb(24, 16, 32)')
        if os.environ.get('PPI_SCREENSHOTS'):
            folder = Path(os.environ['PPI_SCREENSHOTS']); folder.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(folder / 'bright-theme.png'))
        self.page.evaluate('applyBrowserTheme({active:false})')
        self.assertFalse(self.page.locator('body').evaluate("node => node.classList.contains('chrome-theme')"))

    def test_out_of_order_theme_images_leave_the_newest_theme_applied(self):
        result = self.page.evaluate('''async () => {
          const originalFetch = window.fetch;
          const originalCreateObjectURL = URL.createObjectURL;
          const originalRevokeObjectURL = URL.revokeObjectURL;
          const requests = new Map();
          const blobs = new WeakMap();
          const revoked = [];
          window.fetch = (url) => new Promise((resolve) => requests.set(url, resolve));
          URL.createObjectURL = (blob) => `blob:${blobs.get(blob)}`;
          URL.revokeObjectURL = (url) => revoked.push(url);
          const response = (name) => ({
            ok: true,
            blob: async () => {
              const blob = new Blob([name]);
              blobs.set(blob, name);
              return blob;
            },
          });
          const theme = (id, color) => ({
            active: true, id, version: '1',
            colors: { ntp_background: color, frame: color, toolbar: color },
            background: true, background_url: `/theme-${id}.png`,
          });
          try {
            const first = applyBrowserTheme(theme('old', '#112233'));
            while (!requests.has('/theme-old.png')) await new Promise((resolve) => setTimeout(resolve, 0));
            const second = applyBrowserTheme(theme('new', '#ddeeff'));
            while (!requests.has('/theme-new.png')) await new Promise((resolve) => setTimeout(resolve, 0));
            requests.get('/theme-new.png')(response('new'));
            await second;
            const afterNew = {
              theme: document.body.dataset.chromeTheme,
              background: getComputedStyle(document.querySelector('.shell')).backgroundColor,
              image: getComputedStyle(document.querySelector('.shell')).backgroundImage,
            };
            requests.get('/theme-old.png')(response('old'));
            await first;
            const afterOld = {
              theme: document.body.dataset.chromeTheme,
              background: getComputedStyle(document.querySelector('.shell')).backgroundColor,
              image: getComputedStyle(document.querySelector('.shell')).backgroundImage,
            };
            return { afterNew, afterOld, revoked };
          } finally {
            window.fetch = originalFetch;
            URL.createObjectURL = originalCreateObjectURL;
            URL.revokeObjectURL = originalRevokeObjectURL;
          }
        }''')
        self.assertEqual(result['afterNew']['theme'], 'new:1')
        self.assertEqual(result['afterOld'], result['afterNew'])
        self.assertIn('rgb(221, 238, 255)', result['afterNew']['background'])
        self.assertIn('blob:new', result['afterNew']['image'])
        self.assertNotIn('blob:old', result['afterNew']['image'])

    def test_theme_colors_wait_for_matching_artwork_before_commit(self):
        result = self.page.evaluate('''async () => {
          const originalFetch = window.fetch;
          const originalCreateObjectURL = URL.createObjectURL;
          const originalRevokeObjectURL = URL.revokeObjectURL;
          const requests = new Map();
          const blobs = new WeakMap();
          window.fetch = (url) => new Promise((resolve) => requests.set(url, resolve));
          URL.createObjectURL = (blob) => `blob:${blobs.get(blob)}`;
          URL.revokeObjectURL = () => {};
          const response = (name) => ({
            ok: true,
            blob: async () => {
              const blob = new Blob([name]);
              blobs.set(blob, name);
              return blob;
            },
          });
          const theme = (id, color) => ({
            active: true, id, version: '1',
            colors: { ntp_background: color, frame: color, toolbar: color },
            background: true, background_url: `/theme-${id}.png`,
          });
          const palette = () => ({
            theme: document.body.dataset.chromeTheme,
            background: getComputedStyle(document.querySelector('.shell')).backgroundColor,
            image: getComputedStyle(document.querySelector('.shell')).backgroundImage,
          });
          try {
            const first = applyBrowserTheme(theme('old-atomic', '#112233'));
            while (!requests.has('/theme-old-atomic.png')) await new Promise((resolve) => setTimeout(resolve, 0));
            requests.get('/theme-old-atomic.png')(response('old'));
            await first;
            const second = applyBrowserTheme(theme('new-atomic', '#ddeeff'));
            while (!requests.has('/theme-new-atomic.png')) await new Promise((resolve) => setTimeout(resolve, 0));
            const whilePending = palette();
            requests.get('/theme-new-atomic.png')(response('new'));
            await second;
            return { whilePending, committed: palette() };
          } finally {
            window.fetch = originalFetch;
            URL.createObjectURL = originalCreateObjectURL;
            URL.revokeObjectURL = originalRevokeObjectURL;
          }
        }''')
        self.assertEqual(result['whilePending']['theme'], 'old-atomic:1')
        self.assertIn('rgb(17, 34, 51)', result['whilePending']['background'])
        self.assertIn('blob:old', result['whilePending']['image'])
        self.assertEqual(result['committed']['theme'], 'new-atomic:1')
        self.assertIn('rgb(221, 238, 255)', result['committed']['background'])
        self.assertIn('blob:new', result['committed']['image'])

    def test_recovered_submission_does_not_restore_already_sent_draft(self):
        self.page.evaluate('''() => {
          const original = api;
          let lost = false;
          api = async (path, options) => {
            const result = await original(path, options);
            if (path.endsWith('/messages') && !lost) {
              lost = true;
              throw new Error('Simulated lost response after acceptance');
            }
            return result;
          };
        }''')
        self.prompt.fill('Send exactly once')
        self.page.locator('#sendButton').click()
        expect(self.page.locator('#toast')).to_contain_text('Connection recovered')
        self.page.evaluate('flushDraft(state.activeId)')
        self.page.reload()
        expect(self.prompt).to_have_value('')
        self.assertEqual(len(self.fixture.provider.requests), 1)
