"""Browser coverage for expanding overflowing Markdown tables and code."""

from __future__ import annotations

import os
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class ExpandedContentBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 760}, reduced_motion="reduce")
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(error))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def _load(self, kind, width=1280):
        self.page.goto("about:blank")
        self.page.set_viewport_size({"width": width, "height": 760})
        if kind == "work":
            capability = self.fixture.app.issue_capability("dashboard", window_id="main", provider="codex")
        else:
            capability = self.fixture.app.issue_capability("chat", provider="codex")
        path = "/" if kind == "work" else "/chat"
        self.page.goto(f"{self.fixture.base_url}{path}#capability={capability}&provider=codex", wait_until="domcontentloaded")
        name = "Message" if kind == "work" else "Message Chat"
        expect(self.page.get_by_role("textbox", name=name)).to_be_enabled(timeout=5_000)
        return "#messages" if kind == "work" else "#chatMessages"

    def _add(self, root, markdown, kind="work"):
        classes = ("message", "message-content") if kind == "work" else ("chat-message", "chat-message-body")
        self.page.locator(root).evaluate("""(root, args) => {
            const message = document.createElement('article');
            message.className = args.classes[0];
            const options = {commandTarget: {messageId: 'viewer-test'}, shellLanguages: new Set(['bash'])};
            const rendered = '<div class="' + args.classes[1] + '">' +
              PilferedParrotMarkdown.render(args.markdown, options) + '</div>';
            message.innerHTML = args.work ? '<div class="avatar" aria-hidden="true"></div><div class="message-body">' + rendered + '</div>' : rendered;
            root.append(message);
        }""", {"classes": classes, "markdown": markdown, "work": kind == "work"})
        self.page.wait_for_timeout(80)

    def test_controls_follow_overflow_and_stay_outside_table_scroll_in_work_and_chat(self):
        for kind in ("work", "chat"):
            with self.subTest(kind=kind, width="desktop-and-narrow"):
                root = self._load(kind)
                table = "| " + " | ".join("column-%d-abcdef" % item for item in range(5)) + " |\n|" + "|".join("---" for _ in range(5)) + "|\n|" + "|".join("value" for _ in range(5)) + "|"
                self._add(root, table + "\n\n```text\nshort\n```", kind)
                self.page.locator(".table-scroll table").first.evaluate("node => { node.style.width = '400px'; }")
                self.page.evaluate("() => dispatchEvent(new Event('resize'))")
                buttons = self.page.locator(".markdown-expand-button")
                expect(buttons.nth(0)).to_be_hidden()
                expect(buttons.nth(1)).to_be_hidden()
                self.page.set_viewport_size({"width": 390, "height": 760})
                expect(buttons.nth(0)).to_be_visible()
                table = self.page.locator(".table-scroll").first
                before = buttons.nth(0).evaluate("node => { const a = node.getBoundingClientRect(), b = node.closest('.markdown-expand-region').getBoundingClientRect(); return [a.left - b.left, a.top - b.top]; }")
                table.evaluate("node => { node.scrollLeft = 200; }")
                after = buttons.nth(0).evaluate("node => { const a = node.getBoundingClientRect(), b = node.closest('.markdown-expand-region').getBoundingClientRect(); return [a.left - b.left, a.top - b.top]; }")
                self.assertGreater(table.evaluate("node => node.scrollLeft"), 0)
                # The toolbar is outside the scroll box; a scrollbar appearing can
                # move layout by a few pixels, but it must not move with scrollLeft.
                self.assertAlmostEqual(before[0], after[0], delta=20)
                self.assertAlmostEqual(before[1], after[1], delta=20)
                self.assertFalse(buttons.nth(0).evaluate("node => Boolean(node.closest('.table-scroll'))"))
                self.assertEqual(buttons.nth(0).evaluate("node => node.getBoundingClientRect().width"), 44)

    def test_live_content_and_container_resize_update_controls(self):
        root = self._load("work")
        table = "| " + " | ".join("column-%d-abcdef" % item for item in range(5)) + " |\n|" + "|".join("---" for _ in range(5)) + "|\n|" + "|".join("value" for _ in range(5)) + "|"
        self._add(root, table)
        self.page.locator(".table-scroll table").first.evaluate("node => { node.style.width = '400px'; }")
        self.page.evaluate("() => dispatchEvent(new Event('resize'))")
        button = self.page.locator(".markdown-expand-button")
        expect(button).to_be_hidden()
        self.page.locator(".message-content").evaluate("node => { node.style.width = '300px'; }")
        expect(button).to_be_visible()
        self.page.locator(".message-content").evaluate("node => { node.style.width = '760px'; }")
        expect(button).to_be_hidden()
        self.page.locator(root).evaluate("""root => {
            root.querySelector('.message-content').insertAdjacentHTML('beforeend',
              '<div class="code-block"><pre>' + 'x'.repeat(1200) + '</pre></div>');
        }""")
        expect(self.page.locator(".markdown-expand-button").nth(1)).to_be_visible()

    def test_dialog_is_full_viewport_read_only_and_restores_focus(self):
        root = self._load("chat", 390)
        self._add(root, "```bash\n" + "x" * 1200 + "\n```", "chat")
        button = self.page.locator(".markdown-expand-button")
        expect(button).to_be_visible()
        self.page.evaluate("""() => Object.defineProperty(document.documentElement, 'requestFullscreen', {
            configurable: true, value: () => Promise.reject(new Error('blocked for resize test'))
        })""")
        button.click()
        dialog = self.page.locator("#markdownExpansionDialog")
        expect(dialog).to_be_visible()
        self.assertTrue(dialog.evaluate("node => node.open"))
        self.assertTrue(dialog.locator(".markdown-expansion-close").evaluate("""node => {
            const rect = node.getBoundingClientRect();
            return document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) === node;
        }"""))
        self.assertEqual(dialog.locator(".run-command").count(), 0)
        self.assertEqual(dialog.locator(".markdown-expand-button").count(), 0)
        self.assertTrue(dialog.locator(".markdown-expansion-rendered").evaluate("node => getComputedStyle(node.querySelector('pre')).borderTopStyle !== 'none'"))
        self.page.set_viewport_size({"width": 640, "height": 500})
        metrics = dialog.evaluate("node => { const r = node.getBoundingClientRect(); return [r.width, r.height, innerWidth, innerHeight]; }")
        self.assertEqual(metrics, [640, 500, 640, 500])
        self.page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        expect(button).to_be_focused()

    def test_fullscreen_request_and_rejected_request_keep_a_usable_viewer(self):
        root = self._load("work", 390)
        self._add(root, "```text\n" + "x" * 1200 + "\n```")
        button = self.page.locator(".markdown-expand-button")
        button.click()
        dialog = self.page.locator("#markdownExpansionDialog")
        expect(dialog).to_be_visible()
        self.assertTrue(dialog.locator(".markdown-expansion-close").evaluate("""node => {
            const r = node.getBoundingClientRect();
            return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === node;
        }"""))
        self.page.wait_for_function("() => document.fullscreenElement === document.documentElement")
        dialog.locator(".markdown-expansion-close").click()
        self.page.wait_for_function("() => !document.fullscreenElement")
        expect(dialog).to_be_hidden()

        self.page.evaluate("""() => Object.defineProperty(document.documentElement, 'requestFullscreen', {
            configurable: true, value: () => Promise.reject(new Error('blocked for test'))
        })""")
        button.click()
        expect(dialog).to_be_visible()
        self.assertFalse(self.page.evaluate("() => Boolean(document.fullscreenElement)"))
        dialog.locator(".markdown-expansion-close").click()


    def test_expanded_table_uses_the_theme_and_preserves_existing_fullscreen(self):
        for kind in ("work", "chat"):
            for name, panel, text in (("light", "#eee2ce", "#172333"),
                                      ("dark", "#304a62", "#f4f7fb")):
                with self.subTest(kind=kind, theme=name):
                    self.fixture.app.browser_theme = lambda: {
                        "active": True, "id": name, "version": "1",
                        "colors": {"ntp_background": panel, "ntp_section": panel,
                                   "ntp_text": text, "ntp_section_text": text},
                    }
                    root = self._load(kind, 390)
                    self._add(root, "| " + " | ".join(["Long column heading"] * 8) +
                              " |\n|" + "|".join(["---"] * 8) + "|\n" +
                              ("|" + "|".join(["sample value"] * 8) + "|\n") * 40, kind)
                    self.page.wait_for_function("() => document.body.classList.contains('chrome-theme')")
                    # A user may have put the application in fullscreen already.
                    self.page.evaluate("() => document.documentElement.requestFullscreen()")
                    button = self.page.locator(".markdown-expand-button")
                    button.click()
                    dialog = self.page.locator("#markdownExpansionDialog")
                    expect(dialog).to_be_visible()
                    colors = dialog.evaluate("""node => {
                        const rgb = value => { const probe = document.createElement('span');
                            probe.style.color = value; document.body.append(probe);
                            const result = getComputedStyle(probe).color; probe.remove(); return result; };
                        const style = getComputedStyle(node);
                        return [style.backgroundColor, style.color,
                            rgb(style.getPropertyValue('--panel')),
                            rgb(style.getPropertyValue('--chrome-theme-panel-text'))];
                    }""")
                    self.assertEqual(colors[:2], colors[2:])
                    expect(dialog.locator('th')).to_have_count(8)
                    expect(dialog.locator('td')).to_have_count(320)
                    self.assertEqual(dialog.locator('.markdown-expand-button, .run-command').count(), 0)
                    self.assertTrue(dialog.locator('td').first.evaluate(
                        "node => getComputedStyle(node).borderTopStyle === 'solid'"))
                    self.assertTrue(dialog.locator('th').first.evaluate("""node => {
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        return range.getClientRects().length > 1;
                    }"""))
                    close_position = dialog.locator('.markdown-expansion-close').bounding_box()
                    offsets = dialog.locator('.markdown-expansion-content').evaluate("""node => {
                        node.scrollTop = 500; node.scrollLeft = 200;
                        return [node.scrollTop, node.scrollLeft];
                    }""")
                    self.assertGreater(offsets[0], 0)
                    self.assertEqual(offsets[1], 0)
                    self.assertEqual(dialog.locator('.markdown-expansion-close').bounding_box(), close_position)
                    dialog.locator('.markdown-expansion-close').click()
                    expect(dialog).to_be_hidden()
                    expect(button).to_be_focused()
                    self.assertTrue(self.page.evaluate('!!document.fullscreenElement'))
                    self.page.evaluate('() => document.exitFullscreen()')

if __name__ == "__main__":
    unittest.main()
