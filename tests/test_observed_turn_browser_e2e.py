"""Real browser check for the opt-in observed-files Work flow."""

from __future__ import annotations

import os
import unittest

from pilferedparrot import web
from pilferedparrot.dispatch import RunResult
from playwright_fixture import PilferedParrotBrowserFixture

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None


@unittest.skipUnless(os.name == "posix" and sync_playwright, "POSIX and Playwright required")
class ObservedTurnBrowserTests(unittest.TestCase):
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
        self.original_dispatch = web.capture_dispatch
        self.addCleanup(setattr, web, "capture_dispatch", self.original_dispatch)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(error))
        self.page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(self.page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5000)

    def test_one_turn_opt_in_wide_and_narrow(self):
        def dispatch(_provider, prompt, cwd, *_rest):
            if prompt == "off":
                (cwd / "edit.txt").write_text("BEFORE-CONTENT")
            else:
                (cwd / "edit.txt").write_text("PRIVATE-EDITED-CONTENT")
                (cwd / "created.txt").write_text("PRIVATE-CREATED-CONTENT")
                (cwd / "deleted.txt").unlink()
            return RunResult("Fake provider done", 0)

        web.capture_dispatch = dispatch
        option = self.page.get_by_label("Observe files for this turn")
        expect(option).to_be_visible()
        expect(option).not_to_be_checked()
        prompt = self.page.get_by_role("textbox", name="Message")
        prompt.fill("off")
        self.page.get_by_role("button", name="Send", exact=True).click()
        expect(self.page.get_by_text("Fake provider done")).to_be_visible(timeout=5000)
        self.assertFalse(self.fixture.app.turn_observations.folder.exists())
        (self.fixture.project / "deleted.txt").write_text("TO-DELETE")

        option.check()
        prompt.fill("on")
        self.page.get_by_role("button", name="Send", exact=True).click()
        expect(self.page.locator(".observed-files summary")).to_be_visible(timeout=5000)
        expect(option).not_to_be_checked()
        self.page.locator(".observed-files summary").click()
        for path in ("created.txt", "edit.txt", "deleted.txt"):
            expect(self.page.locator(".observed-files code").filter(has_text=path)).to_be_visible()
        expect(self.page.locator(".observed-files")).to_contain_text("authorship unknown")
        expect(self.page.locator(".observed-files")).to_contain_text("Coverage complete under scan policy")
        if screenshot_dir := os.environ.get("PPI_OBSERVED_SCREENSHOTS"):
            self.page.screenshot(path=f"{screenshot_dir}/observed-wide.png")
        self.page.set_viewport_size({"width": 600, "height": 780})
        self.page.locator("#closeSidebar").evaluate("button => button.click()")
        self.page.locator(".observed-files").scroll_into_view_if_needed()
        expect(option).to_be_visible()
        expect(self.page.locator(".observed-files")).to_be_visible()
        if screenshot_dir:
            self.page.wait_for_timeout(350)  # Let the mobile sidebar transition finish.
            self.page.screenshot(path=f"{screenshot_dir}/observed-narrow.png")
        self.assertFalse(self.errors)
        self.assertNotIn("PRIVATE-", self.page.locator("body").inner_text())
        with self.fixture.app.store.lock:
            public = self.fixture.app.store.public(self.fixture.app.store.data["chats"][0])
        self.assertNotIn("PRIVATE-", str(public))
        self.assertNotIn("blobs/", str(public))
