"""Browser checks for the deliberately opt-in feedback surface."""
from __future__ import annotations

import json
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
class FeedbackBrowserEndToEndTests(unittest.TestCase):
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

    def _open(self, page):
        page.get_by_role("button", name="Preferences", exact=True).click()
        dialog = page.get_by_role("dialog", name="Preferences", exact=True)
        details = dialog.locator("#feedbackDetails")
        details.locator("summary").click()
        expect(details.locator("#feedbackStatus")).to_contain_text("Optional local counts")
        return dialog, details

    def _work(self):
        page = self.context.new_page()
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        return page

    def _chat(self):
        page = self.context.new_page()
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page.goto(f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
                  wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message Chat")).to_be_enabled(timeout=5_000)
        return page

    def test_work_and_chat_start_closed_and_all_choices_off(self):
        work = self._work()
        dialog, details = self._open(work)
        expect(details).to_be_visible()
        for checkbox in details.locator("[data-feedback-consent]").all():
            expect(checkbox).not_to_be_checked()
        chat = self._chat()
        chat_dialog, chat_details = self._open(chat)
        expect(chat_details).to_be_visible()
        expect(chat_details.locator("[data-feedback-consent]")).to_have_count(4)

    def test_preferences_make_no_feedback_request_or_store_until_section_opens(self):
        requests = []
        page = self.context.new_page()
        page.on("request", lambda request: requests.append(request.url)
                if "/api/feedback" in request.url else None)
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        self.assertEqual(requests, [])
        self.assertFalse(self.fixture.app.feedback.path.exists())

        page.get_by_role("button", name="Preferences", exact=True).click()
        self.assertEqual(requests, [])
        details = page.get_by_role("dialog", name="Preferences", exact=True).locator("#feedbackDetails")
        details.locator("summary").click()
        expect(details.locator("#feedbackStatus")).to_contain_text("Optional local counts")
        self.assertEqual(len(requests), 1)
        self.assertFalse(self.fixture.app.feedback.path.exists())

    def test_usage_choice_persists_to_chat_and_chat_can_revoke_it(self):
        work = self._work()
        _, work_details = self._open(work)
        usage = work_details.locator('[data-feedback-consent="usage"]')
        usage.click()
        expect(usage).to_be_checked()
        expect(usage).to_be_enabled()
        self.assertTrue(self.fixture.app.feedback.status()["consent"]["usage"])

        chat = self._chat()
        _, chat_details = self._open(chat)
        chat_usage = chat_details.locator('[data-feedback-consent="usage"]')
        expect(chat_usage).to_be_checked()
        chat_usage.click()
        expect(chat_usage).not_to_be_checked()
        expect(chat_usage).to_be_enabled()
        self.assertFalse(self.fixture.app.feedback.status()["consent"]["usage"])

        work.get_by_role("button", name="Close", exact=True).click()
        _, work_details = self._open(work)
        expect(work_details.locator('[data-feedback-consent="usage"]')).not_to_be_checked()

    def test_unchecked_written_fields_are_absent_from_preview_and_download(self):
        page = self._work()
        dialog, details = self._open(page)
        expect(details.locator("#feedbackDownload")).to_be_disabled()
        details.locator("#feedbackDescription").fill("private written detail")
        details.locator("#feedbackChange").fill("private requested change")
        details.locator("#feedbackReviewButton").click()
        expect(details.locator("#feedbackPreview")).to_be_visible()
        preview = json.loads(details.locator("#feedbackPreview").text_content())
        self.assertNotIn("feedback", preview)
        self.assertNotIn("private written detail", json.dumps(preview))
        self.assertNotIn("private requested change", json.dumps(preview))
        expect(details.locator("#feedbackDownload")).to_be_disabled()
        details.locator("#feedbackReview").check()
        expect(details.locator("#feedbackDownload")).to_be_enabled()
        with page.expect_download() as download_info:
            details.locator("#feedbackDownload").click()
        downloaded = json.loads(download_info.value.path().read_text(encoding="utf-8"))
        self.assertEqual(downloaded, preview)
        self.assertNotIn("revision", downloaded)

    def test_editing_written_feedback_invalidates_review(self):
        page = self._work()
        _, details = self._open(page)
        details.locator("#feedbackReviewButton").click()
        expect(details.locator("#feedbackPreview")).to_be_visible()
        details.locator("#feedbackReview").check()
        expect(details.locator("#feedbackDownload")).to_be_enabled()
        details.locator("#feedbackDescription").fill("An edit after review")
        expect(details.locator("#feedbackPreview")).to_be_hidden()
        expect(details.locator("#feedbackDownload")).to_be_disabled()

    def test_cross_window_clear_invalidates_stale_download_with_same_choices(self):
        work = self._work()
        _, work_details = self._open(work)
        work_details.locator('[data-feedback-consent="usage"]').click()
        expect(work_details.locator('[data-feedback-consent="usage"]')).to_be_checked()
        expect(work_details.locator('[data-feedback-consent="usage"]')).to_be_enabled()
        self.fixture.app.feedback.record("usage", "message_sent", "work")
        self.fixture.app.feedback._queue.join()
        work_details.locator("#feedbackReviewButton").click()
        expect(work_details.locator("#feedbackPreview")).to_be_visible()
        self.assertIn('"count": 1', work_details.locator("#feedbackPreview").text_content())
        work_details.locator("#feedbackReview").check()
        expect(work_details.locator("#feedbackDownload")).to_be_enabled()

        chat = self._chat()
        _, chat_details = self._open(chat)
        chat_details.locator("#feedbackClear").click()
        expect(chat_details.locator("#feedbackStatus")).to_contain_text("Optional local counts")

        work_details.locator("#feedbackDownload").click()
        expect(work_details.locator("#feedbackPreview")).to_be_hidden()
        expect(work_details.locator("#feedbackStatus")).to_contain_text("review a fresh report")

    def test_failed_consent_save_reverts_checkbox_without_hiding_section(self):
        page = self._work()
        _, details = self._open(page)
        page.route("**/api/feedback/consent", lambda route: route.fulfill(
            status=500, content_type="application/json", body='{"error":"Deliberate consent failure"}',
        ))
        usage = details.locator('[data-feedback-consent="usage"]')
        usage.click()
        expect(usage).not_to_be_checked()
        expect(details).to_be_visible()
        expect(details.locator("#feedbackStatus")).to_contain_text("Deliberate consent failure")

    def test_feedback_controls_fit_narrow_preferences_and_work_with_keyboard(self):
        page = self.context.new_page()
        page.set_viewport_size({"width": 320, "height": 720})
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        page.get_by_role("button", name="Open sidebar", exact=True).click()
        page.get_by_role("button", name="Preferences", exact=True).click()
        details = page.locator("#feedbackDetails")
        details.locator("summary").focus()
        page.keyboard.press("Enter")
        expect(details).to_have_attribute("open", "")
        expect(details.locator("#feedbackReviewButton")).to_be_enabled()
        sizes = page.locator("#preferencesDialog").evaluate("node => [node.scrollWidth, node.clientWidth]")
        self.assertLessEqual(sizes[0], sizes[1] + 1)
        details.locator("#feedbackReviewButton").click()
        expect(details.locator("#feedbackPreview")).to_be_visible()
        details.locator("#feedbackReview").focus()
        page.keyboard.press("Space")
        expect(details.locator("#feedbackDownload")).to_be_enabled()
