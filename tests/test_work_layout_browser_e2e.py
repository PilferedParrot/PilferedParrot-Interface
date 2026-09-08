"""Browser coverage for Work's responsive layout."""

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
class WorkLayoutBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(
            viewport={"width": 320, "height": 568}, reduced_motion="reduce",
        )
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page_errors = []
        self.page.on("pageerror", lambda error: self.page_errors.append(error))
        self._goto(native=False)

    def tearDown(self):
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _goto(self, *, native):
        capability = self.fixture.app.issue_capability("dashboard", window_id="main", provider="codex")
        fragment = f"capability={capability}&provider=codex"
        if native:
            self.page.route("**/api/window/native", lambda route: route.fulfill(json={
                "supported": True, "marker": "PilferedParrot Native Preview", "ok": True,
            }))
            fragment += "&native-window=1"
        self.page.goto("about:blank")
        self.page.goto(
            f"{self.fixture.base_url}/#{fragment}",
            wait_until="domcontentloaded",
        )
        expect(self.page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        if native:
            expect(self.page.locator("#nativeTitlebar")).to_be_visible()

    def _assert_layout(self, width, height):
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.wait_for_function(
            "size => window.innerWidth === size.width && window.innerHeight === size.height",
            arg={"width": width, "height": height},
        )
        metrics = self.page.evaluate(
            """() => {
                const rect = selector => document.querySelector(selector).getBoundingClientRect();
                const viewport = { width: window.innerWidth, height: window.innerHeight };
                const header = rect('.topbar');
                const composer = rect('.composer');
                const send = rect('#sendButton');
                return {
                    viewport,
                    documentScrollWidth: document.documentElement.scrollWidth,
                    documentScrollHeight: document.documentElement.scrollHeight,
                    bodyScrollWidth: document.body.scrollWidth,
                    bodyScrollHeight: document.body.scrollHeight,
                    header,
                    composer,
                    send,
                };
            }"""
        )
        viewport = metrics["viewport"]
        self.assertLessEqual(metrics["documentScrollWidth"], viewport["width"])
        self.assertLessEqual(metrics["bodyScrollWidth"], viewport["width"])
        self.assertGreater(metrics["header"]["left"], 0)
        self.assertGreater(metrics["header"]["top"], 0)
        for name in ("composer", "send"):
            box = metrics[name]
            self.assertGreater(box["width"], 0, name)
            self.assertGreater(box["height"], 0, name)
            self.assertGreaterEqual(box["left"], -0.5, name)
            self.assertGreaterEqual(box["top"], -0.5, name)
            self.assertLessEqual(box["right"], viewport["width"] + 0.5, name)
            self.assertLessEqual(box["bottom"], viewport["height"] + 0.5, name)

    def test_work_composer_stays_in_view_at_responsive_sizes(self):
        for width, height in ((320, 568), (390, 844), (1024, 600)):
            with self.subTest(width=width, height=height, native=False):
                self._assert_layout(width, height)

        self._goto(native=True)
        self.page.locator("#prompt").fill("Keep the message box visible while I write.")
        expect(self.page.locator("#sendButton")).to_be_enabled()
        for width, height in ((320, 568), (1024, 600)):
            with self.subTest(width=width, height=height, native=True):
                self._assert_layout(width, height)


if __name__ == "__main__":
    unittest.main()
