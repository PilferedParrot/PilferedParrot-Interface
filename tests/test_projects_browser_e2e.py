"""A project switch preserves each session's work across browser navigation."""

import os
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


if __name__ == "__main__":
    unittest.main()
