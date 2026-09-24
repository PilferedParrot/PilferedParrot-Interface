"""Deterministic browser coverage for provider allowance UI in Work and Chat."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

import playwright_fixture as fixture_module
from playwright_fixture import BrowserTestApp, FakeBudget, PilferedParrotBrowserFixture


class UsageApp(BrowserTestApp):
    def budgets(self):
        revision = getattr(self, "usage_revision", 0)
        if revision == "error":
            raise RuntimeError("deterministic budget refresh failure")
        if revision == "missing":
            return {"codex": FakeBudget(
                "codex", available=True, status="ok", auth_status="signed_in",
                reachability="reachable", observed_at=int(time.time()),
                usage_status="unavailable", usage_note="Allowance unavailable.", windows=[],
            )}
        if revision == "stale":
            observed = int(time.time()) - 900
            windows = [{"label": "Codex · 5-hour", "used_percent": 34,
                        "remaining_percent": 66, "resets_at": int(time.time()) + 3600}]
        elif revision == "missing_reset":
            observed = int(time.time())
            windows = [{"label": "Codex · Missing reset", "used_percent": 22,
                        "remaining_percent": 78}]
        elif revision == "expired":
            observed = int(time.time())
            windows = [{"label": "Codex · Expired", "used_percent": 22,
                        "remaining_percent": 78, "resets_at": int(time.time()) - 60}]
        else:
            observed = int(time.time())
            used = 34 if revision == 0 else 48
            windows = [
                {"label": "Codex · 5-hour", "used_percent": used,
                 "remaining_percent": 100 - used, "resets_at": int(time.time()) + 3600},
                {"label": "Codex · Weekly", "used_percent": 12,
                 "remaining_percent": 88, "resets_at": int(time.time()) + 86400},
                {"label": "gpt-reserve · Weekly included usage", "used_percent": 0,
                 "remaining_percent": 100, "resets_at": int(time.time()) + 86400},
                {"label": "GPT-5.3-Codex-Spark · 5-hour included usage", "used_percent": 5,
                 "remaining_percent": 95, "resets_at": int(time.time()) + 1800},
                {"label": "GPT-5.3-Codex-Spark · Weekly included usage", "used_percent": 0,
                 "remaining_percent": 100, "resets_at": int(time.time()) + 86400},
            ]
        return {"codex": FakeBudget(
            "codex", available=True, status="ok", auth_status="signed_in",
            reachability="reachable", observed_at=observed, usage_status="available",
            windows=windows,
        )}


class UsageFixture(PilferedParrotBrowserFixture):
    def __init__(self):
        with patch.object(fixture_module, "BrowserTestApp", UsageApp):
            super().__init__()
        self.app.usage_revision = 0


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class UsageBrowserEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page_errors = []

    def tearDown(self):
        self.assertEqual(self.page_errors, [])

    def _open(self, surface):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.issue_capability(surface, window_id="main", provider="codex") \
            if surface == "dashboard" else self.fixture.app.issue_capability("chat", provider="codex")
        path = "/" if surface == "dashboard" else "/chat"
        page.goto(f"{self.fixture.base_url}{path}#capability={capability}&provider=codex", wait_until="domcontentloaded")
        textbox = "Message" if surface == "dashboard" else "Message Chat"
        expect(page.get_by_role("textbox", name=textbox)).to_be_enabled(timeout=5_000)
        expect(page.locator("script[src='/usage.js']")).to_have_count(1)
        return page

    def _assert_two_windows(self, page, selector):
        rows = page.locator(selector)
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text("5-hour")
        expect(rows.nth(0)).to_contain_text("34% used")
        expect(rows.nth(1)).to_contain_text("Weekly")
        expect(rows.nth(1)).to_contain_text("12% used")

    def test_work_and_chat_render_distinct_codex_windows(self):
        work = self._open("dashboard")
        self._assert_two_windows(work, "#providerList .allowance-row")
        work.set_viewport_size({"width": 320, "height": 568})
        expect(work.locator("body")).to_have_js_property("scrollWidth", 320)
        self.assertLessEqual(work.evaluate("document.documentElement.scrollWidth"), 320)
        chat = self._open("chat")
        self._assert_two_windows(chat, "#chatProviderUsage .allowance-row")

    def test_focus_refresh_updates_work_and_missing_data_is_truthful(self):
        page = self._open("dashboard")
        self._assert_two_windows(page, "#providerList .allowance-row")

        self.fixture.app.usage_revision = 1
        self.fixture.app._invalidate_budgets()
        with page.expect_response("**/api/budgets"):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator("#providerList .allowance-row").first).to_contain_text("48% used")

        self.fixture.app.usage_revision = "missing"
        self.fixture.app._invalidate_budgets()
        with page.expect_response("**/api/budgets"):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator("#providerList .provider-usage.unavailable")).to_contain_text("Allowance unavailable")
        expect(page.locator("#providerList .allowance-row")).to_have_count(0)

    def test_chat_marks_stale_snapshot_as_stale(self):
        page = self._open("chat")
        self.fixture.app.usage_revision = "stale"
        self.fixture.app._invalidate_budgets()
        with page.expect_response("**/api/budgets"):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        usage = page.locator("#chatProviderUsage .provider-usage")
        expect(usage).to_contain_text("stale")
        # A stale snapshot may remain visible for diagnosis, but it must carry
        # an explicit last-reported marker and never look current.
        expect(usage).to_contain_text("Last reported")

    def test_work_marks_missing_and_expired_resets_without_fabricating_times(self):
        page = self._open("dashboard")
        self.fixture.app.usage_revision = "missing_reset"
        self.fixture.app._invalidate_budgets()
        with page.expect_response("**/api/budgets"):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator("#providerList .allowance-reset")).to_contain_text("Reset unavailable")

        self.fixture.app.usage_revision = "expired"
        self.fixture.app._invalidate_budgets()
        with page.expect_response("**/api/budgets"):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(page.locator("#providerList .allowance-reset")).to_contain_text("Reset due")
        expect(page.locator("#providerList .allowance-head strong")).to_contain_text("Last reported")

    def test_work_retains_last_reported_allowance_when_refresh_fails(self):
        page = self._open("dashboard")
        self.fixture.app.usage_revision = "error"
        self.fixture.app._invalidate_budgets()
        with page.expect_response("**/api/budgets"):
            page.evaluate("window.dispatchEvent(new Event('focus'))")
        usage = page.locator("#providerList .provider-usage")
        expect(usage).to_contain_text("Refresh failed")
        expect(usage).to_contain_text("Last reported")

    def test_work_refreshes_allowance_after_completion(self):
        page = self._open("dashboard")
        prompt = "Deterministic allowance refresh completion"
        self.fixture.provider.hold(prompt)
        page.locator("#prompt").fill(prompt)
        page.locator("#sendButton").click()
        expect(page.locator("#cancelButton")).to_be_visible(timeout=5_000)
        self.fixture.app.usage_revision = 1
        self.fixture.app._invalidate_budgets()
        self.fixture.provider.complete(prompt)
        expect(page.locator("#providerList .allowance-row").first).to_contain_text("48% used", timeout=10_000)


if __name__ == "__main__":
    unittest.main()
