"""Browser coverage for server-backed appearance synchronization."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class SharedAppearanceBrowserEndToEndTests(unittest.TestCase):
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
        self.page_errors = []

    def tearDown(self):
        for context in self.contexts:
            context.close()
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _context(self, old_appearance=None):
        context = self.browser.new_context(viewport={"width": 1100, "height": 800})
        self.contexts.append(context)
        if old_appearance is not None:
            context.add_init_script(script=(
                "localStorage.setItem('pilferedparrot.appearance', "
                f"{json.dumps(old_appearance)});"
            ))
        return context

    def _work(self, context):
        page = context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        return page

    def _chat(self, context):
        page = context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page.goto(
            f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message Chat")).to_be_enabled(timeout=5_000)
        return page

    @staticmethod
    def _preferences(page):
        page.get_by_role("button", name="Preferences", exact=True).click()
        dialog = page.get_by_role("dialog", name="Preferences", exact=True)
        expect(dialog).to_be_visible()
        return dialog

    @staticmethod
    def _expect_appearance(dialog, tone, surface, readability):
        expect(dialog.get_by_label(tone, exact=True)).to_be_checked(timeout=5_000)
        expect(dialog.get_by_label(surface, exact=True)).to_be_checked(timeout=5_000)
        expect(dialog.get_by_label(readability, exact=True)).to_be_checked(timeout=5_000)

    def test_separate_profiles_ignore_old_storage_and_share_durable_server_state(self):
        work = self._work(self._context({
            "tone": "darker", "surface": "minimal", "readability": "stronger",
        }))
        chat = self._chat(self._context({
            "tone": "original", "surface": "maximal", "readability": "standard",
        }))

        work_dialog = self._preferences(work)
        chat_dialog = self._preferences(chat)
        self._expect_appearance(work_dialog, "Original", "Balanced", "Standard")
        self._expect_appearance(chat_dialog, "Original", "Balanced", "Standard")

        work_dialog.get_by_label("Darker", exact=True).check()
        work_dialog.get_by_label("Maximal", exact=True).check()
        work_dialog.get_by_label("Stronger", exact=True).check()
        self._expect_appearance(chat_dialog, "Darker", "Maximal", "Stronger")

        chat_dialog.get_by_label("Original", exact=True).check()
        chat_dialog.get_by_label("Minimal", exact=True).check()
        chat_dialog.get_by_label("Standard", exact=True).check()
        self._expect_appearance(work_dialog, "Original", "Minimal", "Standard")

        fresh = self._work(self._context())
        self._expect_appearance(self._preferences(fresh), "Original", "Minimal", "Standard")

    def test_rapid_local_changes_survive_delayed_poll_and_write_responses(self):
        page = self._work(self._context())
        delayed = {"get": False, "post": False}

        def delay_first_response(route):
            request = route.request
            response = route.fetch()
            kind = "post" if request.method == "POST" else "get"
            if not delayed[kind]:
                delayed[kind] = True
                time.sleep(0.35)
            route.fulfill(response=response)

        page.route("**/api/preferences/appearance", delay_first_response)
        page.evaluate("void globalThis.PilferedParrotAppearanceSync.connect().refresh()")
        dialog = self._preferences(page)
        dialog.get_by_label("Darker", exact=True).check()
        dialog.get_by_label("Original", exact=True).check()
        expect(dialog.get_by_label("Original", exact=True)).to_be_checked(timeout=5_000)
        expect(dialog.get_by_label("Balanced", exact=True)).to_be_checked(timeout=5_000)
        self.assertTrue(delayed["get"])
        self.assertTrue(delayed["post"])

        fresh = self._work(self._context())
        self._expect_appearance(self._preferences(fresh), "Original", "Balanced", "Standard")

    def test_poll_does_not_roll_back_a_choice_while_its_save_is_in_flight(self):
        entered = threading.Event()
        release = threading.Event()
        original = self.fixture.app.set_appearance_preferences

        def hold_save(payload):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release appearance save")
            return original(payload)

        self.fixture.app.set_appearance_preferences = hold_save
        self.addCleanup(release.set)
        page = self._work(self._context())
        dialog = self._preferences(page)
        dialog.get_by_label("Darker", exact=True).check()
        self.assertTrue(entered.wait(timeout=2), "appearance save did not reach server")

        # The visible-page poll runs after one second. It must not GET the
        # still-old server state and roll back this optimistic selection.
        time.sleep(1.2)
        expect(dialog.get_by_label("Darker", exact=True)).to_be_checked(timeout=2_000)
        release.set()
        expect(dialog.get_by_label("Darker", exact=True)).to_be_checked(timeout=5_000)

    def test_overlapping_focus_refreshes_are_serialized(self):
        page = self._work(self._context())
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        lock = threading.Lock()
        original = self.fixture.app.appearance_preferences

        def hold_first_read():
            nonlocal calls
            with lock:
                calls += 1
                ordinal = calls
            if ordinal == 1:
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test did not release appearance read")
            return original()

        self.fixture.app.appearance_preferences = hold_first_read
        self.addCleanup(release.set)
        page.evaluate("""() => {
            const sync = globalThis.PilferedParrotAppearanceSync.connect();
            void Promise.all([sync.refresh(), sync.refresh()]);
        }""")
        self.assertTrue(entered.wait(timeout=2), "first appearance read did not reach server")
        time.sleep(0.25)
        self.assertEqual(calls, 1, "a second GET could complete before the older response")
        release.set()
        page.wait_for_timeout(300)
        self.assertGreaterEqual(calls, 2)

    def test_failed_save_reports_error_and_restores_persisted_value(self):
        page = self._work(self._context())
        rejected = False

        def reject_one_save(route):
            nonlocal rejected
            if route.request.method == "POST" and not rejected:
                rejected = True
                route.fulfill(status=503, content_type="application/json", body='{"error":"Appearance save failed"}')
                return
            route.continue_()

        page.route("**/api/preferences/appearance", reject_one_save)
        dialog = self._preferences(page)
        dialog.get_by_label("Darker", exact=True).check()
        expect(page.locator("#toast")).to_contain_text("Appearance save failed", timeout=5_000)
        expect(dialog.get_by_label("Original", exact=True)).to_be_checked(timeout=5_000)
        self.assertTrue(rejected)
