"""Browser regressions for balanced imported Chrome theme colors."""

from __future__ import annotations

import os
import struct
import unittest
import zlib

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class ThemeBalanceBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(viewport={"width": 1200, "height": 800})
        self.addCleanup(self.context.close)

    @staticmethod
    def _theme(name, *, background="#25384c", text="#f4f7fb", frame="#182838",
               frame_text="#f4f7fb", toolbar="#315878", toolbar_text="#f4f7fb",
               section="#304a62", section_text="#f4f7fb"):
        theme = {
            "active": True,
            "id": name,
            "version": "1",
            "colors": {
                "ntp_background": background,
                "ntp_text": text,
                "ntp_section": section,
                "ntp_section_text": section_text,
                "ntp_link": toolbar_text,
                "frame": frame,
                "tab_background_text": frame_text,
                "toolbar": toolbar,
                "toolbar_text": toolbar_text,
            },
        }
        return theme

    @staticmethod
    def _png_bytes():
        rows = b"\0" + b"\xff\xff\xff" * 4

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data)))

        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 1, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    def _page(self, theme, kind):
        self.fixture.app.browser_theme = lambda theme=theme: theme
        page = self.context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page._theme_balance_page_errors = page_errors
        if kind == "work":
            page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
            expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
        else:
            capability = self.fixture.app.issue_capability("chat", provider="codex")
            page.goto(
                f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
                wait_until="domcontentloaded",
            )
            expect(page.locator("#chatPrompt")).to_be_enabled(timeout=5_000)
        page.wait_for_function("() => document.body.classList.contains('chrome-theme')")
        return page

    def _surface_styles(self, page, kind):
        return page.evaluate(
            """kind => {
                const root = getComputedStyle(document.documentElement);
                const selector = kind === 'work' ? '.sidebar-group' : '.chat-window-sidebar .sidebar-group';
                const messageSelector = kind === 'work' ? '.message' : '.chat-message';
                const message = document.querySelector(messageSelector);
                const composer = document.querySelector(kind === 'work' ? '.composer' : '.chat-composer');
                const alpha = value => {
                    const slash = value.lastIndexOf('/');
                    if (slash >= 0) {
                        const n = Number(value.slice(slash + 1).replace(/[) ]/g, ''));
                        if (Number.isFinite(n)) return n;
                    }
                    const match = value.match(/rgba?\\(([^)]+)\\)/);
                    if (!match) return 1;
                    const parts = match[1].split(',').map(part => part.trim());
                    return parts.length === 4 ? Number(parts[3]) : 1;
                };
                const color = (node, pseudo = null) => getComputedStyle(node, pseudo).backgroundColor;
                const rgb = value => {
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    ctx.fillStyle = value;
                    ctx.fillRect(0, 0, 1, 1);
                    return [...ctx.getImageData(0, 0, 1, 1).data.slice(0, 3)];
                };
                const luminance = value => rgb(value).map(channel => channel / 255)
                    .map(channel => channel <= .03928 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4)
                    .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
                const ratio = (foreground, background) => {
                    const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
                    return (light + .05) / (dark + .05);
                };
                const text = root.getPropertyValue('--chrome-theme-text').trim();
                const background = root.getPropertyValue('--chrome-theme-background').trim();
                const toolbar = root.getPropertyValue('--chrome-theme-toolbar').trim();
                const toolbarText = root.getPropertyValue('--chrome-theme-toolbar-text').trim();
                const dialog = document.querySelector('#preferencesDialog');
                const field = dialog.querySelector('input, select, textarea');
                return {
                    vars: Object.fromEntries(['background', 'text', 'frame', 'frame-text', 'toolbar',
                        'toolbar-text', 'section', 'panel-text'].map(name =>
                        [name, root.getPropertyValue(`--chrome-theme-${name}`).trim()])),
                    textContrast: ratio(text, background), toolbarContrast: ratio(toolbarText, toolbar),
                    sidebarAlpha: alpha(color(document.querySelector(selector))),
                    messageAlpha: alpha(color(message)),
                    composerAlpha: alpha(color(composer)),
                    dialogAlpha: alpha(color(dialog)),
                    fieldAlpha: alpha(color(field)),
                    codeAlpha: alpha(color(document.querySelector(`${messageSelector} pre`))),
                    dialogColor: color(dialog), fieldColor: color(field), codeColor: color(document.querySelector(`${messageSelector} pre`)),
                };
            }""",
            kind,
        )

    def _seed_content(self, page, kind):
        page.evaluate(
            """kind => {
                const message = document.createElement('article');
                message.className = kind === 'work' ? 'message' : 'chat-message';
                const body = document.createElement('div');
                body.className = kind === 'work' ? 'message-content' : 'chat-message-body';
                body.innerHTML = '<p>Theme balance sample</p><pre><code>const sample = true;</code></pre>';
                message.append(body);
                document.querySelector(kind === 'work' ? '#messages' : '#chatMessages').append(message);
                const dialog = document.querySelector('#preferencesDialog');
                const input = document.createElement('input');
                input.value = 'solid field';
                dialog.querySelector('.dialog-card').append(input);
                dialog.showModal();
            }""",
            kind,
        )

    def test_readable_authored_colors_and_surface_alpha_hold_in_work_and_chat(self):
        themes = {
            "dark": self._theme("balanced-dark"),
            "light": self._theme(
                "balanced-light", background="#f4efe5", text="#172333", frame="#d6c6ad",
                frame_text="#172333", toolbar="#d9b36c", toolbar_text="#172333",
                section="#eee2ce", section_text="#172333",
            ),
            "vivid": self._theme(
                "balanced-vivid", background="#24314f", text="#f8f0ff", frame="#592b68",
                frame_text="#fff0ff", toolbar="#d94b9b", toolbar_text="#190c1e",
                section="#44234f", section_text="#fff0ff",
            ),
        }
        for palette, theme in themes.items():
            for kind in ("work", "chat"):
                with self.subTest(palette=palette, kind=kind):
                    page = self._page(theme, kind)
                    self._seed_content(page, kind)
                    styles = self._surface_styles(page, kind)
                    self.assertEqual(page._theme_balance_page_errors, [])
                # Readable authored values stay authored after CSS color conversion.
                    self.assertEqual(styles["vars"]["text"], theme["colors"]["ntp_text"])
                    self.assertEqual(styles["vars"]["toolbar"], theme["colors"]["toolbar"])
                    self.assertGreaterEqual(styles["textContrast"], 4.5)
                    self.assertGreaterEqual(styles["toolbarContrast"], 4.5)
                    self.assertGreaterEqual(styles["sidebarAlpha"], .68)
                    self.assertLessEqual(styles["sidebarAlpha"], .78)
                    self.assertGreaterEqual(styles["messageAlpha"], .84)
                    self.assertLessEqual(styles["messageAlpha"], .92)
                    self.assertGreaterEqual(styles["composerAlpha"], .87)
                    self.assertLessEqual(styles["composerAlpha"], .93)
                    self.assertEqual(styles["dialogAlpha"], 1)
                    self.assertEqual(styles["fieldAlpha"], 1)
                    self.assertEqual(styles["codeAlpha"], 1)
                    self.assertTrue(styles["dialogColor"])
                    self.assertTrue(styles["fieldColor"])
                    self.assertTrue(styles["codeColor"])

    def test_low_contrast_vivid_and_invalid_colors_are_corrected_without_crashing(self):
        theme = self._theme(
            "vivid-invalid", background="#ff00aa", text="#ff00aa", frame="#00ffff",
            frame_text="#00ffff", toolbar="#ffff00", toolbar_text="#ffff00",
            section="#ff00aa", section_text="#ff00aa",
        )
        theme["colors"].update({"toolbar": "not-a-color", "ntp_section_text": "#nope"})
        for kind in ("work", "chat"):
            with self.subTest(kind=kind):
                page = self._page(theme, kind)
                self._seed_content(page, kind)
                styles = self._surface_styles(page, kind)
                self.assertTrue(styles["vars"]["text"])
                self.assertTrue(styles["vars"]["toolbar"])
                self.assertGreaterEqual(styles["textContrast"], 4.5)
                self.assertGreaterEqual(styles["toolbarContrast"], 4.5)
                # A failing vivid foreground is blended toward a pole while retaining hue;
                # pure black/white substitution would make all channels equal.
                text = styles["vars"]["text"].lower()
                self.assertNotIn(text, {"#000000", "#ffffff", "rgb(0, 0, 0)", "rgb(255, 255, 255)"})
                vivid_rgb = page.evaluate("""() => {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--chrome-theme-text');
                    ctx.fillRect(0, 0, 1, 1);
                    return [...ctx.getImageData(0, 0, 1, 1).data.slice(0, 3)];
                }""")
                self.assertGreater(vivid_rgb[0], vivid_rgb[1])
                self.assertGreater(vivid_rgb[2], vivid_rgb[1])
                self.assertFalse(styles["dialogColor"] == "rgba(0, 0, 0, 0)")
                self.assertFalse(styles["fieldColor"] == "rgba(0, 0, 0, 0)")
                self.assertFalse(styles["codeColor"] == "rgba(0, 0, 0, 0)")
                self.assertEqual(page.locator("body").get_attribute("data-chrome-theme"), "vivid-invalid:1")

    def test_artwork_position_scale_and_no_theme_default_remain_stable(self):
        theme = self._theme("artwork-stable")
        theme.update({
            "background": True,
            "background_url": "/api/browser/theme/background?v=balance",
            "background_alignment": "left top",
            "background_repeat": "repeat-x",
        })
        self.fixture.app.browser_theme = lambda: theme
        page = self.context.new_page()
        page.route("**/api/browser/theme/background*", lambda route: route.fulfill(
            status=200, content_type="image/png", body=self._png_bytes(),
        ))
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
        artwork = page.evaluate("""() => {
            const style = getComputedStyle(document.documentElement);
            return {image: style.getPropertyValue('--chrome-theme-background-image'),
                position: style.getPropertyValue('--chrome-theme-background-position'),
                repeat: style.getPropertyValue('--chrome-theme-background-repeat'),
                size: getComputedStyle(document.body).backgroundSize};
        }""")
        self.assertIn("url(", artwork["image"])
        self.assertEqual(artwork["position"], "left top")
        self.assertEqual(artwork["repeat"], "repeat-x")
        self.assertIn("auto", artwork["size"])

        theme.clear()
        theme.update({"active": False})
        page.wait_for_function("() => !document.body.classList.contains('chrome-theme')")
        self.assertEqual(page.evaluate("() => getComputedStyle(document.body).backgroundImage"), "none")
        self.assertEqual(page.evaluate("() => getComputedStyle(document.querySelector('.main')).backgroundImage"), "none")
