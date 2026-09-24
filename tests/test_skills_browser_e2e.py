"""Opt-in skill metadata stays local and renders as inert text."""

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
class SkillPreviewBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def test_disabled_then_explicit_preview_renders_metadata_as_text(self) -> None:
        fixture = PilferedParrotBrowserFixture()
        self.addCleanup(fixture.stop)
        skill_root = fixture.root / "my-skills"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(
            "---\nname: Fixture skill\n"
            "description: <img src=x onerror=window.__skillExecuted=1>\n"
            "---\nprivate instructions must not appear\n",
            encoding="utf-8",
        )
        context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5000)
        page.get_by_role("button", name="Open sidebar", exact=True).click()
        page.get_by_role("button", name="Preferences").click()
        preview = page.get_by_role("button", name="Preview local skills")
        expect(preview).to_be_disabled()
        expect(page.locator("#skillsResults li")).to_have_count(0)

        fixture.app.config["skills"] = {"enabled": True, "roots": [str(skill_root)]}
        page.locator("#preferencesDialog").get_by_role("button", name="Close").click()
        page.get_by_role("button", name="Preferences").click()
        expect(preview).to_be_enabled()
        preview.click()
        expect(page.locator("#skillsResults li")).to_have_count(1)
        expect(page.locator("#skillsResults li")).to_contain_text("Fixture skill")
        expect(page.locator("#skillsResults li")).to_contain_text("<img src=x")
        self.assertEqual(page.locator("#skillsResults img").count(), 0)
        self.assertIsNone(page.evaluate("window.__skillExecuted"))
        self.assertNotIn("private instructions", page.locator("#skillsResults").inner_text())
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)


if __name__ == "__main__":
    unittest.main()
