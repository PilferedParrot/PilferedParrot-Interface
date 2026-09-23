"""A project switch preserves each session's work across browser navigation."""

import json
import os
import time
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "Playwright required")
class ProjectWorkroomBrowserTests(unittest.TestCase):
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
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.goto(self.fixture.browser_url)
        expect(self.page.locator("#projectSelect")).to_be_enabled()

    def test_switch_project_keeps_drafts_and_selection_after_reload(self):
        first = str(self.fixture.project)
        second = self.fixture.root / "second-project"
        second.mkdir()
        initial_id = self.page.evaluate("state.activeId")
        self.page.locator("#prompt").fill("Draft for first project")

        self.page.locator("#addProject").click()
        self.page.locator("#projectInput").fill(str(second))
        self.page.locator("#saveProject").click()
        expect(self.page.locator("#projectSelect")).to_have_value(str(second))
        self.page.wait_for_function("(old) => state.activeId !== old", arg=initial_id)
        second_id = self.page.evaluate("state.activeId")
        self.assertEqual(self.page.locator(f'[data-chat="{initial_id}"]').count(), 0)
        self.page.locator("#prompt").fill("Draft for second project")

        self.page.locator("#projectSelect").select_option(first)
        expect(self.page.locator("#prompt")).to_have_value("Draft for first project")
        self.assertEqual(self.page.evaluate("state.activeId"), initial_id)
        self.page.locator("#pinProject").click()
        expect(self.page.locator("#pinProject")).to_have_attribute("aria-pressed", "true")

        self.page.locator("#projectSelect").select_option(str(second))
        expect(self.page.locator("#prompt")).to_have_value("Draft for second project")
        self.assertEqual(self.page.evaluate("state.activeId"), second_id)
        self.page.reload()
        expect(self.page.locator("#projectSelect")).to_have_value(str(second))
        expect(self.page.locator("#prompt")).to_have_value("Draft for second project")
        self.assertEqual(self.page.evaluate("state.activeId"), second_id)
        self.assertEqual(self.page.evaluate("windowChats().length"), 2)
        pinned_label = self.page.evaluate("""path => [...document.querySelectorAll('#projectSelect option')]
          .find(option => option.value === path)?.textContent""", first)
        self.assertIn("★", pinned_label)

        # A fresh app open carries a new capability fragment. It should resume
        # the selected project's work instead of adding another empty session.
        reopened = self.context.new_page()
        reopened.goto(self.fixture.browser_url)
        expect(reopened.locator("#projectSelect")).to_have_value(str(second))
        expect(reopened.locator("#prompt")).to_have_value("Draft for second project")
        self.assertEqual(reopened.evaluate("state.activeId"), second_id)
        self.assertEqual(reopened.evaluate("windowChats().length"), 2)

    def test_delayed_draft_save_keeps_submitted_project_cwd(self):
        first = str(self.fixture.project)
        first_id = self.page.evaluate("state.activeId")
        second = self.fixture.root / "second-project"
        second.mkdir()
        self.page.locator("#addProject").click()
        self.page.locator("#projectInput").fill(str(second))
        self.page.locator("#saveProject").click()
        expect(self.page.locator("#projectSelect")).to_have_value(str(second))
        self.page.wait_for_function("(old) => state.activeId !== old", arg=first_id)
        second_id = self.page.evaluate("state.activeId")
        self.page.locator("#projectSelect").select_option(first)
        expect(self.page.locator("#projectSelect")).to_have_value(first)
        self.assertEqual(self.page.evaluate("state.activeId"), first_id)

        # Hold the exact save that sendMessage awaits. The message request must
        # retain the first session's cwd even if mutable project state changes
        # before that save completes.
        self.page.evaluate("""chatId => {
          const original = api;
          window.__draftBlocked = false;
          window.__releaseDraft = null;
          let held = false;
          api = async (path, options) => {
            if (!held && path === `/api/chats/${encodeURIComponent(chatId)}/draft`) {
              held = true;
              window.__draftBlocked = true;
              await new Promise(resolve => { window.__releaseDraft = resolve; });
            }
            return original(path, options);
          };
        }""", first_id)
        prompt = "Keep this request in the first project"
        self.page.locator("#prompt").fill(prompt)
        self.page.locator("#sendButton").click()
        self.page.wait_for_function("window.__draftBlocked && messageSubmissionPending")
        expect(self.page.locator("#projectSelect")).to_be_disabled()
        self.assertEqual(self.page.evaluate("state.activeId"), first_id)

        # Simulate a project switch that was already in flight before the UI
        # lock. The pending request must use the submitted session's folder.
        self.page.evaluate("path => { state.draftCwd = path; }", str(second))
        with self.page.expect_request(
            lambda request: request.url.endswith(f"/api/chats/{first_id}/messages"),
            timeout=5_000,
        ) as submitted:
            self.page.evaluate("window.__releaseDraft()")
        payload = json.loads(submitted.value.post_data)
        self.assertEqual(payload["cwd"], first)
        self.assertEqual(payload["content"], prompt)
        expect(self.page.locator("#projectSelect")).to_be_enabled()
        self.page.evaluate("path => { state.draftCwd = path; }", first)
        deadline = time.monotonic() + 5
        while not self.fixture.provider.requests and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.fixture.provider.requests[0][2], self.fixture.project)
        self.assertEqual(self.fixture.app.store.get(first_id)["cwd"], first)
        self.assertEqual(self.fixture.app.store.get(second_id)["messages"], [])


if __name__ == "__main__":
    unittest.main()
