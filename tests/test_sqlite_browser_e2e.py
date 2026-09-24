"""The opt-in SQLite authority still serves a complete, private Work UI."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from pilferedparrot.sqlite_state import SQLiteStateStore
from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "Playwright required")
class SQLiteBrowserPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_work_turn_journals_without_exposing_opaque_fields_or_rewriting_source(self):
        fixture = PilferedParrotBrowserFixture(sqlite_preview=True)
        self.addCleanup(fixture.stop)
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(fixture.browser_url, wait_until="domcontentloaded")
        prompt = page.get_by_role("textbox", name="Message")
        expect(prompt).to_be_enabled(timeout=5_000)
        prompt.fill("sqlite preview")
        page.get_by_role("button", name="Send").click()
        expect(page.get_by_text("Fake provider completed: sqlite preview", exact=True)) \
            .to_be_visible(timeout=5_000)

        self.assertNotIn("synthetic-private-marker", page.locator("body").inner_text())
        self.assertNotIn("synthetic-private-marker", page.evaluate("JSON.stringify(state)"))
        source = Path(fixture.config["web"]["chat_store"])
        self.assertEqual(source.read_bytes(), fixture.sqlite_source_bytes)
        with SQLiteStateStore(fixture.sqlite_database) as inspected:
            snapshot = inspected.import_json(source)
            self.assertEqual(snapshot.document["preferences"]["future_secret"],
                             "synthetic-private-marker")
            self.assertEqual(inspected.source_backup()[0], fixture.sqlite_source_bytes)
            chat = fixture.app.store.list_public()[0]
            kinds = [event.kind for event in inspected.replay(session_key=chat["id"])]
            self.assertIn("progress", kinds)
            self.assertEqual(kinds[-1], "completed")


if __name__ == "__main__":
    unittest.main()
