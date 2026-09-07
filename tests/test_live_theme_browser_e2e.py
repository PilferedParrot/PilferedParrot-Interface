"""Browser regressions for live Chrome theme updates and pane resizing."""

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
class LiveThemeBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(viewport={"width": 1200, "height": 800})
        self.addCleanup(self.context.close)

    @staticmethod
    def _solid_png(color=(255, 255, 255), *, width=4, height=2):
        """Small valid RGB PNG with a deterministic color for pixel assertions."""
        rows = b"".join(b"\0" + bytes(color) * width for _ in range(height))

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data)))

        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    @staticmethod
    def _png_bytes():
        """Small valid RGB PNG so image tests exercise decoding too."""
        width, height = 4, 2
        rows = b"".join(
            b"\0" + (b"\xff\xff\xff" * width if row == 0 else b"\x00\x00\x00" * width)
            for row in range(height)
        )

        def chunk(kind, data):
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data)))

        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    @staticmethod
    def _screenshot_pixel(png):
        """Read a 1x1 Chromium PNG; all first-pixel filter predictors are zero."""
        offset = 8
        idat = bytearray()
        while offset < len(png):
            length = struct.unpack(">I", png[offset:offset + 4])[0]
            kind = png[offset + 4:offset + 8]
            data = png[offset + 8:offset + 8 + length]
            offset += length + 12
            if kind == b"IHDR":
                width, height, depth, color_type, _, _, interlace = struct.unpack(
                    ">IIBBBBB", data,
                )
                assert (width, height, depth, interlace) == (1, 1, 8, 0)
                assert color_type in (2, 6)
            elif kind == b"IDAT":
                idat.extend(data)
        raw = zlib.decompress(bytes(idat))
        assert raw[0] in range(5)
        return tuple(raw[1:4])

    @staticmethod
    def _theme(name, color, *, background=False):
        theme = {
            "active": True,
            "id": name,
            "version": "1",
            "colors": {
                "ntp_background": color,
                "ntp_text": "#ffffff",
                "ntp_section": color,
                "frame": color,
                "toolbar": color,
            },
            "background": background,
        }
        if background:
            theme.update({
                "background_url": "/api/browser/theme/background?v=live-1",
                "background_alignment": "left top",
                "background_repeat": "no-repeat",
            })
        return theme

    @staticmethod
    def _assert_background_decodes(page):
        width = page.evaluate("""async () => {
            const image = new Image();
            image.src = themeBackgroundObjectUrl;
            await image.decode();
            return image.naturalWidth;
        }""")
        return width

    def _work_page(self, theme, *, route_background=False):
        self.fixture.app.browser_theme = lambda: theme
        page = self.context.new_page()
        if route_background:
            self._route_background(page)
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
        return page

    def _chat_page(self, theme, *, route_background=False):
        self.fixture.app.browser_theme = lambda: theme
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page = self.context.new_page()
        if route_background:
            self._route_background(page)
        page.goto(
            f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(page.locator("#chatPrompt")).to_be_enabled(timeout=5_000)
        return page

    def _route_background(self, page):
        page.route(
            "**/api/browser/theme/background*",
            lambda route: route.fulfill(
                status=200, content_type="image/png", body=self._png_bytes(),
            ),
        )

    def _assert_continuous_theme_surfaces(self, page, *, header):
        """The root canvas owns artwork; structural chrome must let it show through."""
        surfaces = page.evaluate(
            """headerSelector => {
                const style = selector => getComputedStyle(document.querySelector(selector));
                const alpha = value => {
                    const slash = value.lastIndexOf('/');
                    if (slash >= 0) {
                        const mixedAlpha = Number(value.slice(slash + 1).replace(')', '').trim());
                        if (Number.isFinite(mixedAlpha)) return mixedAlpha;
                    }
                    const match = value.match(/rgba?\\(([^)]+)\\)/);
                    if (!match) return 1;
                    const channels = match[1].split(',').map(part => part.trim());
                    return channels.length === 4 ? Number(channels[3]) : 1;
                };
                const pseudo = getComputedStyle(document.querySelector('#sidebarResizer, #chatResizer'), '::after');
                return {
                    bodyImage: style('body').backgroundImage,
                    bodyColor: style('body').backgroundColor,
                    shellColor: style('.shell, .chat-window').backgroundColor,
                    paneColor: style('.main, .chat-window-conversation').backgroundColor,
                    headerAlpha: alpha(style(headerSelector).backgroundColor),
                    titlebarAlpha: alpha(style('#nativeTitlebar').backgroundColor),
                    dividerColor: style('#sidebarResizer, #chatResizer').backgroundColor,
                    dividerPillColor: pseudo.backgroundColor,
                };
            }""",
            header,
        )
        self.assertNotEqual(surfaces["bodyImage"], "none")
        self.assertIn("0, 0, 0, 0", surfaces["shellColor"])
        self.assertIn("0, 0, 0, 0", surfaces["paneColor"])
        self.assertEqual(surfaces["headerAlpha"], 1)
        self.assertEqual(surfaces["titlebarAlpha"], 1)
        self.assertIn("0, 0, 0, 0", surfaces["dividerColor"])
        self.assertIn("0, 0, 0, 0", surfaces["dividerPillColor"])

    def test_theme_artwork_continues_through_native_and_browser_work_chat_chrome(self):
        for kind in ("work", "chat"):
            with self.subTest(kind=kind):
                theme = self._theme(f"continuous-{kind}", "#223344", background=True)
                page = (self._work_page(theme, route_background=True)
                        if kind == "work" else self._chat_page(theme, route_background=True))
                page.wait_for_function(
                    """() => document.body.classList.contains('chrome-theme') &&
                        getComputedStyle(document.body).backgroundImage !== 'none'""",
                    timeout=5_000,
                )
                header = ".topbar" if kind == "work" else ".chat-header"
                self._assert_continuous_theme_surfaces(page, header=header)

                page.evaluate("""() => {
                    document.body.classList.add('native-window');
                    document.querySelector('#nativeTitlebar').hidden = false;
                }""")
                self._assert_continuous_theme_surfaces(page, header=header)

                if kind == "chat":
                    handle = page.locator("#chatResizer")
                    before = int(handle.get_attribute("aria-valuenow"))
                    box = handle.bounding_box()
                    self.assertIsNotNone(box)
                    page.mouse.move(box["x"] + 3, box["y"] + box["height"] / 2)
                    page.mouse.down()
                    page.mouse.move(box["x"] + 70, box["y"] + box["height"] / 2)
                    page.mouse.up()
                    pointer_width = int(handle.get_attribute("aria-valuenow"))
                    self.assertGreater(pointer_width, before)
                    handle.focus()
                    page.keyboard.press("ArrowRight")
                    self.assertGreater(int(handle.get_attribute("aria-valuenow")), pointer_width)
                    self.assertNotEqual(
                        page.evaluate("() => getComputedStyle(document.body).backgroundImage"), "none",
                    )

                # A color-only theme keeps its color fallback when artwork is removed.
                theme.clear()
                theme.update(self._theme(f"color-only-{kind}", "#334455"))
                page.wait_for_function(
                    "() => document.body.dataset.chromeTheme === 'color-only-%s:1'" % kind,
                    timeout=5_000,
                )
                fallback = page.evaluate("() => getComputedStyle(document.body).backgroundImage")
                self.assertTrue(fallback.startswith("none"), fallback)
                self.assertIn("51, 68, 85", page.evaluate("() => getComputedStyle(document.body).backgroundColor"))
                # Keep Work registered until context cleanup so its window-close
                # grace timer cannot shut down the fixture during Chat checks.

    def test_original_theme_assets_are_native_size_opaque_and_removed_live(self):
        assets = {
            "/theme-frame.png": self._solid_png((21, 71, 121), width=7, height=4),
            "/theme-overlay.png": self._solid_png((211, 47, 103), width=5, height=3),
            "/theme-toolbar.png": self._solid_png((43, 157, 94), width=6, height=4),
            "/theme-background.png": self._solid_png((238, 171, 42), width=9, height=7),
        }
        for kind in ("work", "chat"):
            with self.subTest(kind=kind):
                theme = self._theme(f"asset-fidelity-{kind}", "#f0e0d0", background=True)
                theme.update({
                    "frame_url": "/api/browser/theme/image/theme_frame",
                    "frame_overlay_url": "/api/browser/theme/image/theme_frame_overlay",
                    "toolbar_url": "/api/browser/theme/image/theme_toolbar",
                    "background_url": "/theme-background.png",
                    "background_alignment": "left top",
                    "background_repeat": "repeat-x",
                    "colors": {
                        **theme["colors"], "frame": "#f0e0d0", "toolbar": "#e0d0c0",
                        "tab_background_text": "#fafafa", "toolbar_text": "#fefefe",
                    },
                })
                capability = (
                    self.fixture.app.issue_capability("chat", provider="codex")
                    if kind == "chat" else None
                )
                page = self.context.new_page()

                def route_asset(route):
                    name = route.request.url.split("/api/browser/theme/image/", 1)[-1].split("?", 1)[0]
                    path = {
                        "theme_frame": "/theme-frame.png",
                        "theme_frame_overlay": "/theme-overlay.png",
                        "theme_toolbar": "/theme-toolbar.png",
                    }[name]
                    route.fulfill(status=200, content_type="image/png", body=assets[path])

                # The fixture's normal background endpoint is separate from the
                # image endpoint used by frame/toolbar artwork.
                page.route("**/api/browser/theme/image/*", route_asset)
                page.route(
                    "**/theme-*.png",
                    lambda route: route.fulfill(
                        status=200, content_type="image/png",
                        body=assets["/theme-background.png"],
                    ),
                )
                self.fixture.app.browser_theme = lambda theme=theme: theme
                if kind == "work":
                    page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
                    expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
                else:
                    page.goto(
                        f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
                        wait_until="domcontentloaded",
                    )
                    expect(page.locator("#chatPrompt")).to_be_enabled(timeout=5_000)
                page.wait_for_function(
                    "() => document.body.dataset.chromeTheme === 'asset-fidelity-%s:1'" % kind,
                    timeout=5_000,
                )
                header = ".topbar" if kind == "work" else ".chat-header"
                styles = page.evaluate(
                    """header => {
                        const root = getComputedStyle(document.documentElement);
                        const titlebar = getComputedStyle(document.querySelector('#nativeTitlebar'));
                        const bar = getComputedStyle(document.querySelector(header));
                        return {
                            frame: root.getPropertyValue('--chrome-theme-frame').trim(),
                            toolbar: root.getPropertyValue('--chrome-theme-toolbar').trim(),
                            bodyImage: root.getPropertyValue('--chrome-theme-background-image').trim(),
                            titleImage: titlebar.backgroundImage,
                            titleRepeat: titlebar.backgroundRepeat,
                            titleSize: titlebar.backgroundSize,
                            barImage: bar.backgroundImage,
                            barRepeat: bar.backgroundRepeat,
                            barSize: bar.backgroundSize,
                            barColor: bar.backgroundColor,
                            titleColor: titlebar.color,
                            barTextColor: bar.color,
                        };
                    }""",
                    header,
                )
                self.assertEqual(styles["frame"], "#f0e0d0")
                self.assertEqual(styles["toolbar"], "#e0d0c0")
                self.assertIn("url", styles["bodyImage"])
                self.assertIn("no-repeat, repeat-x", styles["titleRepeat"])
                self.assertIn("auto", styles["titleSize"])
                self.assertEqual(styles["barRepeat"], "repeat-x")
                self.assertIn("auto", styles["barSize"])
                self.assertIn("rgb(224, 208, 192)", styles["barColor"])
                self.assertIn("rgb(250, 250, 250)", styles["titleColor"])
                self.assertIn("rgb(254, 254, 254)", styles["barTextColor"])

                page.evaluate("""() => {
                    document.body.classList.add('native-window');
                    document.querySelector('#nativeTitlebar').hidden = false;
                }""")
                page.wait_for_timeout(100)
                box = page.locator(header).bounding_box()
                self.assertIsNotNone(box)
                points = [(2, 2), (12, 2), (2, 8),
                          (int(box["x"]) + 12, int(box["y"]) + 2),
                          (int(box["x"]) + 2, int(box["y"]) + 6)]
                pixels = [self._screenshot_pixel(
                    page.screenshot(clip={"x": x, "y": y, "width": 1, "height": 1}),
                ) for x, y in points]
                self.assertEqual(pixels[0][:3], (211, 47, 103))
                self.assertEqual(pixels[1][:3], (21, 71, 121))
                self.assertEqual(pixels[2][:3], (240, 224, 208))
                self.assertEqual(
                    pixels[3][:3],
                    (43, 157, 94),
                )
                self.assertEqual(
                    pixels[4][:3],
                    (224, 208, 192),
                )

                theme.clear()
                theme.update(self._theme(f"color-only-{kind}", "#334455"))
                page.wait_for_function(
                    "() => document.body.dataset.chromeTheme === 'color-only-%s:1'" % kind,
                    timeout=10_000,
                )
                cleared = page.evaluate(
                    """header => {
                        const root = getComputedStyle(document.documentElement);
                        return {
                            body: root.getPropertyValue('--chrome-theme-background-image').trim(),
                            title: getComputedStyle(document.querySelector('#nativeTitlebar')).backgroundImage,
                            bar: getComputedStyle(document.querySelector(header)).backgroundImage,
                        };
                    }""",
                    header,
                )
                self.assertNotIn("url(", cleared["body"])
                self.assertNotIn("url(", cleared["title"])
                self.assertNotIn("url(", cleared["bar"])
                theme.clear()
                theme.update({"active": False})
                page.wait_for_function("() => !document.body.classList.contains('chrome-theme')", timeout=5_000)
                self.assertEqual(
                    page.evaluate("header => getComputedStyle(document.querySelector(header)).backgroundImage", header),
                    "none",
                )
                # Keep Work open until context cleanup: closing the final Work
                # window starts the production server's shutdown grace period.

    def test_live_theme_update_and_removal_preserve_work_draft_without_reload(self):
        theme = self._theme("first-live", "#112233")
        page = self._work_page(theme)
        page.locator("#prompt").fill("draft survives a live theme change")

        theme.clear()
        theme.update(self._theme("second-live", "#ddeeff"))
        page.wait_for_function("() => document.body.dataset.chromeTheme === 'second-live:1'", timeout=5_000)
        expect(page.locator("#prompt")).to_have_value("draft survives a live theme change")

        theme.clear()
        theme.update({"active": False})
        page.wait_for_function("() => !document.body.classList.contains('chrome-theme')", timeout=5_000)
        expect(page.locator("#prompt")).to_have_value("draft survives a live theme change")

    def test_live_theme_update_and_removal_preserve_chat_draft_without_reload(self):
        theme = self._theme("chat-first-live", "#112233")
        page = self._chat_page(theme)
        page.locator("#chatPrompt").fill("chat draft survives a live theme change")

        theme.clear()
        theme.update(self._theme("chat-second-live", "#ddeeff"))
        page.wait_for_function("() => document.body.dataset.chromeTheme === 'chat-second-live:1'", timeout=5_000)
        expect(page.locator("#chatPrompt")).to_have_value("chat draft survives a live theme change")

        theme.clear()
        theme.update({"active": False})
        page.wait_for_function("() => !document.body.classList.contains('chrome-theme')", timeout=5_000)
        expect(page.locator("#chatPrompt")).to_have_value("chat draft survives a live theme change")

    def test_unchanged_theme_descriptor_does_not_refetch_background_image(self):
        for kind in ("work", "chat"):
            with self.subTest(kind=kind):
                theme = self._theme(f"stable-{kind}", "#334455", background=True)
                image_requests = []
                self.fixture.app.browser_theme = lambda theme=theme: theme
                page = self.context.new_page()
                page.route(
                    "**/api/browser/theme/background*",
                    lambda route: route.fulfill(
                        status=200, content_type="image/png", body=self._png_bytes(),
                    ),
                )
                page.on("request", lambda request: image_requests.append(request.url)
                        if "/api/browser/theme/background" in request.url else None)
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
                page.wait_for_function(
                    "() => document.documentElement.style.getPropertyValue('--chrome-theme-background-image')",
                    timeout=5_000,
                )
                self.assertEqual(self._assert_background_decodes(page), 4)
                initial_count = len(image_requests)
                self.assertEqual(initial_count, 1)
                page.wait_for_timeout(1_000)
                settled_count = len(image_requests)
                page.wait_for_timeout(2_500)
                self.assertEqual(len(image_requests), settled_count)
                self.assertEqual(settled_count, initial_count)
                page.close()

    def test_failed_theme_background_is_retried_and_eventually_applied(self):
        for kind in ("work", "chat"):
            with self.subTest(kind=kind):
                theme = self._theme(f"retry-{kind}", "#445566", background=True)
                attempts = []
                self.fixture.app.browser_theme = lambda theme=theme: theme
                page = self.context.new_page()

                def background(route):
                    attempts.append(route.request.url)
                    if len(attempts) == 1:
                        route.fulfill(status=503, body="temporary failure")
                    else:
                        route.fulfill(
                            status=200, content_type="image/png", body=self._png_bytes(),
                        )

                page.route("**/api/browser/theme/background*", background)
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
                page.wait_for_function(
                    "() => document.documentElement.style.getPropertyValue('--chrome-theme-background-image')",
                    timeout=5_000,
                )
                self.assertGreaterEqual(len(attempts), 2)
                self.assertEqual(self._assert_background_decodes(page), 4)
                self.assertEqual(
                    page.locator("body").get_attribute("data-chrome-theme"), f"retry-{kind}:1",
                )
                page.close()

    def test_low_contrast_theme_is_corrected_for_work_controls_and_content(self):
        theme = self._theme("legible-live", "#ffff00", background=True)
        theme["colors"].update({
            "ntp_text": "#ffff00",
            "ntp_section": "#ffff00",
            "ntp_link": "#ffff00",
            "frame": "#ffff00",
            "toolbar": "#ffff00",
        })
        self.fixture.app.browser_theme = lambda: theme
        page = self.context.new_page()
        page.route(
            "**/api/browser/theme/background*",
            lambda route: route.fulfill(
                status=200, content_type="image/png", body=self._png_bytes(),
            ),
        )
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
        page.wait_for_function("() => document.body.dataset.chromeTheme === 'legible-live:1'", timeout=5_000)
        self.assertEqual(self._assert_background_decodes(page), 4)
        page.locator("#welcome p").evaluate(
            "node => { node.insertAdjacentHTML('beforeend', ' <a href=\"/readable\">theme link</a>'); }"
        )
        ratios = page.evaluate(
            """() => {
                const rgb = value => {
                    const match = value.match(/rgba?\\(([^)]+)\\)/);
                    if (!match) return null;
                    const values = match[1].split(',').map(Number);
                    return values.slice(0, 3);
                };
                const luminance = value => {
                    const channels = rgb(value).map(channel => channel / 255).map(channel =>
                        channel <= .03928 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
                    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
                };
                const ratio = (node, pseudo = null) => {
                    const foreground = getComputedStyle(node, pseudo).color;
                    let backgroundNode = node;
                    let background = null;
                    while (backgroundNode && !background) {
                        const candidate = getComputedStyle(backgroundNode).backgroundColor;
                        if (candidate && !candidate.includes('transparent') && !candidate.endsWith(', 0)')) {
                            background = candidate;
                        }
                        backgroundNode = backgroundNode.parentElement;
                    }
                    if (!background) background = getComputedStyle(document.body).backgroundColor;
                    const foregroundL = luminance(foreground);
                    const backgroundL = luminance(background);
                    return (Math.max(foregroundL, backgroundL) + .05) /
                        (Math.min(foregroundL, backgroundL) + .05);
                };
                const selectors = {
                    prompt: '#prompt',
                    sidebarHeading: '#technicalHistoryHeading',
                    sessionRow: '#chatList .chat-item',
                    disclosure: '#preferencesDetails summary',
                    link: '#welcome a',
                };
                const result = Object.fromEntries(Object.entries(selectors).map(([name, selector]) =>
                    [name, ratio(document.querySelector(selector))]));
                result.placeholder = ratio(document.querySelector("#prompt"), "::placeholder");
                return result;
            }"""
        )
        for name, contrast in ratios.items():
            self.assertGreaterEqual(contrast, 4.5, name)

    def test_work_resizer_pointer_keyboard_persists_and_mobile_hides_without_overflow(self):
        theme = self._theme("resize-live", "#223344", background=True)
        page = self.context.new_page()
        page.route(
            "**/api/browser/theme/background*",
            lambda route: route.fulfill(
                status=200, content_type="image/png", body=self._png_bytes(),
            ),
        )
        self.fixture.app.browser_theme = lambda: theme
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
        page.wait_for_function(
            "() => document.documentElement.style.getPropertyValue('--chrome-theme-background-image')",
            timeout=5_000,
        )
        shell = page.locator(".shell")
        sidebar = page.locator("#sidebar")
        main = page.locator(".main")
        handle = page.locator("#sidebarResizer")
        expect(handle).to_be_visible()
        before_image = page.locator("body").evaluate(
            "node => getComputedStyle(node).backgroundImage"
        )
        before_position = page.locator("body").evaluate(
            "node => getComputedStyle(node).backgroundPosition"
        )
        before_main_left = main.bounding_box()["x"]
        box = handle.bounding_box()
        self.assertIsNotNone(box)
        page.mouse.move(box["x"] + 3, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + 83, box["y"] + box["height"] / 2)
        page.mouse.up()
        pointer_width = page.locator("#sidebarResizer").get_attribute("aria-valuenow")
        self.assertGreater(int(pointer_width), 290)
        self.assertAlmostEqual(sidebar.bounding_box()["width"], int(pointer_width), delta=1)
        self.assertGreater(main.bounding_box()["x"], before_main_left)
        self.assertEqual(page.locator("body").evaluate("node => getComputedStyle(node).backgroundImage"), before_image)
        self.assertEqual(page.locator("body").evaluate("node => getComputedStyle(node).backgroundPosition"), before_position)
        self.assertIn("0, 0, 0, 0", sidebar.evaluate("node => getComputedStyle(node).backgroundColor"))

        handle.focus()
        page.keyboard.press("ArrowRight")
        keyboard_width = page.locator("#sidebarResizer").get_attribute("aria-valuenow")
        self.assertGreater(int(keyboard_width), int(pointer_width))
        stored = page.evaluate("JSON.parse(localStorage.getItem('pilferedparrot-pane-widths')).sidebar")
        self.assertEqual(stored, int(keyboard_width))
        handle.focus()
        page.keyboard.press("Home")
        self.assertEqual(int(handle.get_attribute("aria-valuenow")), 220)
        handle.focus()
        page.keyboard.press("End")
        self.assertEqual(int(handle.get_attribute("aria-valuenow")), 480)

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#prompt")).to_be_enabled(timeout=5_000)
        self.assertEqual(page.locator("#sidebarResizer").get_attribute("aria-valuenow"), "480")

        page.set_viewport_size({"width": 600, "height": 800})
        expect(page.locator("#sidebarResizer")).to_be_hidden()
        metrics = page.evaluate(
            """() => ({
                viewport: document.documentElement.clientWidth,
                documentWidth: document.documentElement.scrollWidth,
                bodyWidth: document.body.scrollWidth,
            })"""
        )
        self.assertLessEqual(metrics["documentWidth"], metrics["viewport"])
        self.assertLessEqual(metrics["bodyWidth"], metrics["viewport"])


if __name__ == "__main__":
    unittest.main()
