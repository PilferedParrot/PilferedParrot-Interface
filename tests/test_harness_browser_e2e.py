"""Browser coverage for the retired Harness UI and retained session records."""

from __future__ import annotations

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


CONTRACT = {
    "task": "Implement the bounded fixture change",
    "category": "implementation",
    "inputs": ["input.txt"],
    "write_scope": ["output.txt"],
    "acceptance_check": "A lead verifies the expected line",
    "artifact": "output.txt",
    "stop_conditions": "Stop if the file is outside the allowed scope",
}


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class HarnessBrowserEndToEndTests(unittest.TestCase):
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
        self.page_errors = []
        self.page.on("pageerror", lambda error: self.page_errors.append(error))
        self.harness_requests = []
        self.page.on("request", self._record_harness_request)
        self.page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(self.page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)

    def tearDown(self):
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _record_harness_request(self, request):
        if "/harness" in request.url:
            self.harness_requests.append(request)

    def test_work_sends_directly_without_harness_api_or_planner(self):
        self.page.get_by_role("textbox", name="Message").fill("Read the fixture metadata")
        self.page.get_by_role("button", name="Send").click()
        expect(self.page.get_by_text("Fake provider completed", exact=False)).to_be_visible(
            timeout=5_000,
        )
        self.assertEqual(self.harness_requests, [])
        self.assertEqual(self.page.get_by_role("button", name="Harness").count(), 0)

    def test_saved_harness_child_is_read_only_and_returns_to_parent(self):
        parent_id = self.fixture.app.store.data["chats"][-1]["id"]
        planned = self.fixture.app.harness_action(
            parent_id,
            {"action": "plan", "preset": "sol-luna", "contract": CONTRACT,
             "estimates": {"unit": "effort_points", "direct": 20, "briefing": 1,
                            "execution": 4, "verification": 2, "rework": 1}},
        )["harness_tasks"][-1]
        child = self.fixture.app.harness_action(parent_id, {"action": "run", "task_id": planned["id"]})
        child_id = child["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            child_state = self.fixture.app.chat_state(child_id)
            if child_state["messages"] and not any(
                message.get("pending") for message in child_state["messages"]
            ):
                break
            time.sleep(0.02)
        self.assertFalse(any(message.get("pending") for message in child_state["messages"]))

        self.page.reload(wait_until="domcontentloaded")
        expect(self.page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        child_button = self.page.locator(f'[data-chat="{child_id}"]')
        expect(child_button).to_be_visible()
        child_button.click()
        self.assertEqual(self.page.locator("#harnessDialog").count(), 0)
        back = self.page.locator("#harnessReturn")
        expect(back).to_be_visible()
        expect(back).to_have_attribute("aria-label", "Back to session")
        expect(back).to_contain_text("Back to session")
        expect(self.page.get_by_role("textbox", name="Message")).to_be_disabled()
        back.click()
        expect(self.page.locator("#harnessReturn")).to_be_hidden()
        self.assertEqual(self.page.evaluate("activeChat()?.id"), parent_id)


if __name__ == "__main__":
    unittest.main()
