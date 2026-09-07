"""Browser coverage for responsive sidebar accessibility state."""

from __future__ import annotations

import os
import re
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class SidebarBrowserEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.fixture = PilferedParrotBrowserFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(self.context.close)
        self.page_errors = []

    def tearDown(self):
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _load_work(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        return page

    def _load_chat(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page.goto(
            f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message Chat")).to_be_enabled(timeout=5_000)
        return page

    def test_header_branding_stays_centered_without_obscuring_controls(self):
        for mode, load, header, sidebar, title in (
            ("work", self._load_work, ".topbar", "#sidebar", "#chatTitle"),
            ("chat", self._load_chat, ".chat-header", "#chatWindowSidebar", "#chatThreadTitle"),
        ):
            page = load()
            self.assertEqual(page.locator(f'{sidebar} img[src="/pilferedparrot-icon.png"]').count(), 0)
            page.locator(title).evaluate("node => node.textContent = 'A long session title '.repeat(20)")
            for native in (False, True):
                page.evaluate("""native => {
                    document.body.classList.toggle('native-window', native);
                    document.querySelector('#nativeTitlebar').hidden = !native;
                }""", native)
                brand = ".native-titlebar-brand" if native else ".header-brand"
                bar = "#nativeTitlebar" if native else header
                for width in (320, 390, 600, 900, 1280, 1920):
                    with self.subTest(mode=mode, native=native, width=width):
                        page.set_viewport_size({"width": width, "height": 844})
                        expect(page.locator(brand)).to_be_visible()
                        metrics = page.evaluate("""({brand, bar}) => {
                            const b = document.querySelector(brand).getBoundingClientRect();
                            const h = document.querySelector(bar).getBoundingClientRect();
                            const overlaps = [...document.querySelectorAll(`${bar} button, ${bar} .chat-meta, ${bar} #chatThreadTitle`)].filter(node => {
                                const r = node.getBoundingClientRect();
                                return r.width && r.height && r.left < b.right && r.right > b.left &&
                                    r.top < b.bottom && r.bottom > b.top;
                            }).map(node => node.id || node.getAttribute('aria-label'));
                            return {offset: Math.abs((b.left + b.right - h.left - h.right) / 2),
                                left: b.left, right: b.right, overlaps,
                                scroll: document.documentElement.scrollWidth};
                        }""", {"brand": brand, "bar": bar})
                        self.assertLessEqual(metrics["offset"], 1)
                        self.assertGreaterEqual(metrics["left"], 0)
                        self.assertLessEqual(metrics["right"], width)
                        self.assertEqual(metrics["overlaps"], [])
                        self.assertLessEqual(metrics["scroll"], width)
                if native:
                    expect(page.locator(".header-brand")).to_be_hidden()
            page.close()

    def _assert_resize_state(self, page, *, sidebar, toggle, close, conversation, input_selector):
        page.locator(toggle).click()
        expect(page.locator(sidebar)).to_have_class(re.compile(r"\bopen\b"))
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "true")
        self.assertTrue(page.locator(conversation).evaluate("node => node.inert"))

        # The open mobile drawer remains visible across the breakpoint, but the
        # conversation must become usable as soon as the mobile modal behavior ends.
        page.set_viewport_size({"width": 900, "height": 844})
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "false")
        self.assertFalse(page.locator(conversation).evaluate("node => node.inert"))
        page.locator(input_selector).focus()
        self.assertEqual(page.evaluate("document.activeElement.id"), input_selector.removeprefix("#"))

        # Returning to narrow layout restores the modal semantics. Setting a
        # focused conversation inert may naturally blur it; the resize handler
        # itself must not move focus to a sidebar control.
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "true")
        self.assertTrue(page.locator(conversation).evaluate("node => node.inert"))
        page.locator(close).click()
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "false")
        self.assertEqual(page.evaluate("document.activeElement.id"), toggle.removeprefix("#"))
        self.assertFalse(page.locator(conversation).evaluate("node => node.inert"))

    def test_work_sidebar_inert_tracks_breakpoint_and_close_focus(self):
        page = self._load_work()
        self._assert_resize_state(
            page,
            sidebar="#sidebar",
            toggle="#openSidebar",
            close="#closeSidebar",
            conversation=".main",
            input_selector="#prompt",
        )

    def test_chat_sidebar_inert_tracks_breakpoint_and_close_focus(self):
        page = self._load_chat()
        self._assert_resize_state(
            page,
            sidebar=".chat-window-sidebar",
            toggle="#toggleChatSidebar",
            close="#closeChatSidebar",
            conversation=".chat-window-conversation",
            input_selector="#chatPrompt",
        )

    def test_project_required_launch_cannot_submit_until_a_folder_is_chosen(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        create_requests = []
        page.on(
            "request",
            lambda request: create_requests.append(request.url)
            if request.method == "POST" and (
                request.url.endswith("/api/chats")
                or ("/api/chats/" in request.url and request.url.endswith("/messages"))
            )
            else None,
        )
        capability = self.fixture.app.dashboard_capability
        page.goto(
            f"{self.fixture.base_url}/#capability={capability}&provider=codex&pick=1",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        dialog = page.locator("#projectDialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()

        page.get_by_role("textbox", name="Message").fill("must choose a folder")
        page.get_by_role("button", name="Open sidebar").click()
        page.get_by_role("button", name="Start a new work session").click()
        expect(dialog).to_be_visible()
        self.assertEqual(create_requests, [])
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()
        page.get_by_role("button", name="Close sidebar").click()

        page.get_by_role("button", name="Send").click()
        expect(dialog).to_be_visible()
        self.assertEqual(create_requests, [])

        page.locator("#projectInput").fill(str(self.fixture.project))
        dialog.get_by_role("button", name="Use folder").click()
        expect(dialog).to_be_hidden()
        expect(page.locator("#chatTitle")).to_be_visible()
        expect(page.get_by_role("textbox", name="Message")).to_have_value("must choose a folder")
        page.get_by_role("button", name="Send").click()
        expect(page.get_by_text("Fake provider completed: must choose a folder", exact=True)).to_be_visible(timeout=5_000)
