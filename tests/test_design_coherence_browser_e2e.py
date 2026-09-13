"""Work and independently opened Chat use the same rendered surface components."""
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
class DesignCoherenceBrowserEndToEndTests(unittest.TestCase):
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
        self.contexts = []
        self.errors = []

    def tearDown(self):
        for context in self.contexts:
            context.close()
        self.assertEqual(self.errors, [])

    def _window(self, kind):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        self.contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        url = self.fixture.browser_url if kind == "work" else f"{self.fixture.base_url}/chat#capability={capability}&provider=codex"
        page.goto(url, wait_until="domcontentloaded")
        prompt = page.locator("#prompt" if kind == "work" else "#chatPrompt")
        expect(prompt).to_be_enabled(timeout=5_000)
        prompt.fill("Compare the shared interface components.")
        prompt.press("Enter")
        expect(page.locator(".message.assistant" if kind == "work" else ".chat-message.assistant")).to_contain_text("Fake provider completed:")
        return page

    @staticmethod
    def _components(page, kind):
        selectors = {
            "header": ".topbar" if kind == "work" else ".chat-window-conversation .chat-header",
            "group": ".sidebar-group",
            "history": ".history-group",
            "selected": ".chat-item.active",
            "message": ".message.user" if kind == "work" else ".chat-message.user",
            "message_heading": ".message-head" if kind == "work" else ".chat-message-head",
            "body": ".message-content" if kind == "work" else ".chat-message-body",
            "composer": ".composer" if kind == "work" else ".chat-composer",
            "model": "#modelSelect" if kind == "work" else "#chatModelSelect",
            "reasoning": "#reasoningSelect" if kind == "work" else "#chatReasoningSelect",
            "prompt": "#prompt" if kind == "work" else "#chatPrompt",
            "preferences": "#preferencesButton",
        }
        return page.evaluate("""selectors => Object.fromEntries(Object.entries(selectors).map(([key, selector]) => {
            const style = getComputedStyle(document.querySelector(selector));
            const properties = ['backgroundColor', 'color', 'borderTopColor', 'borderTopWidth',
                'borderRadius', 'fontSize', 'fontWeight', 'lineHeight', 'backdropFilter',
                'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight'];
            return [key, Object.fromEntries(properties.map(property => [property, style[property]]))];
        }))""", selectors)

    def test_independent_windows_share_colors_and_component_geometry_in_every_surface_mode(self):
        theme = {"active": True, "id": "coherent-blue", "version": "1", "colors": {
            "frame": "#010203", "toolbar": "#275068", "ntp_background": "#275068",
            "ntp_text": "#ffffff", "ntp_link": "#ffffff", "tab_background_text": "#ffffff",
        }}
        self.fixture.app.browser_theme = lambda: theme
        work, chat = self._window("work"), self._window("chat")
        for palette in ("blue", "light", "default"):
            if palette == "light":
                theme.update({"id": "coherent-light", "colors": {
                    "ntp_background": "#eef5fa", "ntp_section": "#d7e7f2", "ntp_text": "#142e43",
                    "ntp_section_text": "#142e43", "toolbar": "#b4d4e8", "frame": "#aacce0",
                }})
            elif palette == "default":
                theme.update({"active": False})
            for page in (work, chat):
                page.evaluate("theme => applyBrowserTheme(theme)", theme)
            for surface in ("minimal", "balanced", "maximal"):
                self.fixture.app.set_appearance_preferences({"surface": surface})
                for page in (work, chat):
                    page.evaluate("() => __pilferedParrotAppearanceSync.refresh()")
                    expect(page.locator("body")).to_have_attribute("data-appearance-surface", surface)
                for width in (1280, 700, 390):
                    with self.subTest(palette=palette, surface=surface, width=width):
                        for page, header in ((work, ".topbar"), (chat, ".chat-window-conversation .chat-header")):
                            page.set_viewport_size({"width": width, "height": 900})
                            expect(page.locator(header)).to_have_css("padding-left", "20px" if width == 1280 else "12px")
                            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        work_components = self._components(work, "work")
                        chat_components = self._components(chat, "chat")
                        for component in work_components:
                            self.assertEqual(work_components[component], chat_components[component], component)
                        # Model and reasoning belong to the same family of controls.
                        for controls in (work_components, chat_components):
                            self.assertEqual(controls["model"], controls["reasoning"])
                        for page, toggle in ((work, "#openSidebar"), (chat, "#toggleChatSidebar")):
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                            if width <= 760:
                                expect(page.locator(toggle)).to_be_visible()
                            else:
                                expect(page.locator(toggle)).to_be_hidden()
                        self.assertEqual(work.locator(".avatar").count(), 0)

    def test_model_and_history_fills_remain_blue_and_translucent(self):
        self.fixture.app.browser_theme = lambda: {"active": True, "id": "blue-controls", "version": "1",
            "colors": {"ntp_background": "#275068", "frame": "#010203", "ntp_text": "#ffffff"}}
        for kind in ("work", "chat"):
            page = self._window(kind)
            colors = page.evaluate("""() => {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                return ['.composer-setting select', '.chat-item.active'].map(selector => {
                    ctx.clearRect(0, 0, 1, 1);
                    ctx.fillStyle = getComputedStyle(document.querySelector(selector)).backgroundColor;
                    ctx.fillRect(0, 0, 1, 1);
                    return [...ctx.getImageData(0, 0, 1, 1).data];
                });
            }""")
            for red, green, blue, alpha in colors:
                self.assertGreater(blue, red + 20)
                self.assertGreater(green, red)
                self.assertGreater(alpha, 60)
                self.assertLess(alpha, 220)
