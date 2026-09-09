"""Browser regressions for the shared appearance preferences."""

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
class AppearanceBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(viewport={"width": 1100, "height": 800})
        self.addCleanup(self.context.close)
        self.page_errors = []

    def _work(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        return page

    def _chat(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page.goto(
            f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message Chat")).to_be_enabled(timeout=5_000)
        return page

    def tearDown(self):
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    @staticmethod
    def _open_preferences(page):
        page.get_by_role("button", name="Preferences", exact=True).click()
        dialog = page.get_by_role("dialog", name="Preferences", exact=True)
        expect(dialog).to_be_visible()
        return dialog

    def test_work_choices_persist_and_are_available_in_chat(self):
        chat = self._chat()
        work = self._work()
        dialog = self._open_preferences(work)
        dialog.get_by_label("Darker", exact=True).check()
        dialog.get_by_label("Maximal", exact=True).check()
        dialog.get_by_label("Stronger", exact=True).check()
        expect(dialog.get_by_label("Darker", exact=True)).to_be_checked()
        expect(dialog.get_by_label("Maximal", exact=True)).to_be_checked()
        expect(dialog.get_by_label("Stronger", exact=True)).to_be_checked()
        chat_dialog = self._open_preferences(chat)
        expect(chat_dialog.get_by_label("Darker", exact=True)).to_be_checked()
        expect(chat_dialog.get_by_label("Maximal", exact=True)).to_be_checked()
        expect(chat_dialog.get_by_label("Stronger", exact=True)).to_be_checked()
        chat_dialog.get_by_role("button", name="Close", exact=True).click()

        # A reload must read the same stored selection, and a Chat change must
        # propagate back to the already-open Work document.
        work.reload(wait_until="domcontentloaded")
        expect(work.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        work_dialog = self._open_preferences(work)
        expect(work_dialog.get_by_label("Darker", exact=True)).to_be_checked()
        work_dialog.get_by_label("Original", exact=True).check()
        work_dialog.get_by_label("Minimal", exact=True).check()
        work_dialog.get_by_label("Standard", exact=True).check()
        chat_dialog = self._open_preferences(chat)
        expect(chat_dialog.get_by_label("Original", exact=True)).to_be_checked()
        expect(chat_dialog.get_by_label("Minimal", exact=True)).to_be_checked()
        expect(chat_dialog.get_by_label("Standard", exact=True)).to_be_checked()

    @staticmethod
    def _surface_metrics(page, selector):
        return page.locator(selector).first.evaluate("""node => {
            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');
            const parse = value => {
                // Chromium serializes color-mix() as color(srgb r g b / a),
                // which older canvas parsers treat as opaque black.
                const srgb = value.match(/^color\\(srgb\\s+([\\d.]+)\\s+([\\d.]+)\\s+([\\d.]+)(?:\\s+\\/\\s+([\\d.]+))?\\)$/);
                if (srgb) return [Number(srgb[1]), Number(srgb[2]), Number(srgb[3]), srgb[4] === undefined ? 1 : Number(srgb[4])];
                context.fillStyle = value;
                context.fillRect(0, 0, 1, 1);
                return [...context.getImageData(0, 0, 1, 1).data].map(value => value / 255);
            };
            const style = getComputedStyle(node);
            const text = parse(style.color);
            const background = parse(style.backgroundColor);
            const luminance = rgba => {
                const linear = rgba.slice(0, 3).map(value =>
                    value <= .03928 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
                return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
            };
            const foreground = luminance(text);
            const backdrop = luminance(background);
            return {
                alpha: background[3],
                luminance: backdrop,
                contrast: (Math.max(foreground, backdrop) + .05) /
                    (Math.min(foreground, backdrop) + .05),
            };
        }""")

    def test_tone_and_surface_modes_have_their_promised_alpha_and_contrast_in_work_and_chat(self):
        bright = {
            "active": True,
            "id": "appearance-bright",
            "version": "1",
            "background": False,
            "colors": {
                "ntp_background": "#f7f3ea",
                "ntp_section": "#f7f3ea",
                "ntp_text": "#171717",
                "frame": "#e4dac8",
                "toolbar": "#eee6d9",
            },
        }
        for page, message_selector, composer_selector in (
            (self._work(), ".welcome, .message", ".composer"),
            (self._chat(), ".chat-message", ".chat-composer"),
        ):
            page.evaluate("theme => applyBrowserTheme(theme)", bright)
            if message_selector == ".chat-message":
                page.evaluate("""() => {
                    const message = document.createElement('article');
                    message.className = 'chat-message';
                    message.innerHTML = '<div class="chat-message-body">Appearance fixture message</div>';
                    document.querySelector('.chat-messages').append(message);
                }""")
            dialog = self._open_preferences(page)
            dialog.get_by_label("Darker", exact=True).check()
            dialog.get_by_label("Minimal", exact=True).check()
            for selector in (".sidebar-group", message_selector, composer_selector):
                metrics = self._surface_metrics(page, selector)
                self.assertLessEqual(metrics["alpha"], .15, selector)
                self.assertGreaterEqual(metrics["contrast"], 4.5, selector)
            self.assertGreaterEqual(self._surface_metrics(page, "dialog")["alpha"], .95)
            dark_panel = self._surface_metrics(page, message_selector)
            self.assertLess(dark_panel["luminance"], .10)

            dialog.get_by_label("Maximal", exact=True).check()
            for selector in (".sidebar-group", message_selector, composer_selector):
                metrics = self._surface_metrics(page, selector)
                self.assertGreaterEqual(metrics["alpha"], .95, selector)
                self.assertGreaterEqual(metrics["contrast"], 4.5, selector)

            # Applying a fresh theme is a live update; it must not reset the
            # user's appearance choices.
            page.evaluate("theme => applyBrowserTheme(theme)", {
                **bright,
                "id": "appearance-bright-swap",
                "colors": {**bright["colors"], "frame": "#d8e7f1", "toolbar": "#c9ddea"},
            })
            expect(dialog.get_by_label("Darker", exact=True)).to_be_checked()
            expect(dialog.get_by_label("Maximal", exact=True)).to_be_checked()

    def test_darker_bright_theme_changes_surfaces_and_surface_modes_change_structure(self):
        page = self._work()
        bright = {
            "active": True,
            "id": "appearance-bright",
            "version": "1",
            "background": False,
            "colors": {
                "ntp_background": "#f7f3ea",
                "ntp_section": "#f7f3ea",
                "ntp_text": "#171717",
                "frame": "#e4dac8",
                "toolbar": "#eee6d9",
            },
        }
        page.evaluate("theme => applyBrowserTheme(theme)", bright)

        dialog = self._open_preferences(page)
        dialog.get_by_label("Original", exact=True).check()
        dialog.get_by_label("Balanced", exact=True).check()
        original = page.evaluate("""() => {
            const style = getComputedStyle(document.body);
            const sidebar = getComputedStyle(document.querySelector('.sidebar'));
            const message = getComputedStyle(document.querySelector('.welcome, .message'));
            return {
                sidebar: sidebar.backgroundColor,
                message: message.backgroundColor,
                body: style.backgroundColor,
            };
        }""")
        dialog.get_by_label("Darker", exact=True).check()
        darker = page.evaluate("""() => ({
            sidebar: getComputedStyle(document.querySelector('.sidebar')).backgroundColor,
            message: getComputedStyle(document.querySelector('.welcome, .message')).backgroundColor,
            body: getComputedStyle(document.body).backgroundColor,
        })""")
        self.assertNotEqual(darker, original)

        dialog.get_by_label("Minimal", exact=True).check()
        minimal = page.evaluate("""() => ({
            sidebar: getComputedStyle(document.querySelector('.sidebar')).backgroundColor,
            message: getComputedStyle(document.querySelector('.welcome, .message')).backgroundColor,
        })""")
        dialog.get_by_label("Maximal", exact=True).check()
        maximal = page.evaluate("""() => ({
            sidebar: getComputedStyle(document.querySelector('.sidebar')).backgroundColor,
            message: getComputedStyle(document.querySelector('.welcome, .message')).backgroundColor,
        })""")
        self.assertNotEqual(minimal, maximal)

    def test_invalid_stored_appearance_falls_back_to_readable_defaults(self):
        self.context.add_init_script("""
            const originalGetItem = Storage.prototype.getItem;
            Storage.prototype.getItem = function (key) {
                if (/appearance|preference/i.test(String(key))) return "{invalid";
                return originalGetItem.call(this, key);
            };
        """)
        page = self._work()
        dialog = self._open_preferences(page)
        expect(dialog.get_by_label("Original", exact=True)).to_be_checked()
        expect(dialog.get_by_label("Balanced", exact=True)).to_be_checked()
        expect(dialog.get_by_label("Standard", exact=True)).to_be_checked()

    def test_mobile_preferences_dialog_remains_usable_with_open_sidebar(self):
        self.context.set_default_timeout(5_000)
        page = self.context.new_page()
        page.set_viewport_size({"width": 320, "height": 568})
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        page.get_by_role("button", name="Open sidebar", exact=True).click()
        preferences = page.get_by_role("button", name="Preferences", exact=True)
        expect(preferences).to_be_visible()
        dialog = self._open_preferences(page)
        expect(dialog.get_by_label("Darker", exact=True)).to_be_visible()
        box = dialog.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreater(box["width"], 0)
        self.assertLessEqual(box["x"] + box["width"], 320)
