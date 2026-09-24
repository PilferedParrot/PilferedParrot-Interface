"""Browser checks for explicit ACP setup and on-demand GPU inventory."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class AcpSetupBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(viewport={"width": 1200, "height": 900})
        self.addCleanup(self.context.close)
        self.installed = False
        manager = self.fixture.app.acp_adapters
        self.install = patch.object(manager, "install", side_effect=self._install)
        self.locate = patch.object(manager, "locate", side_effect=self._locate)
        self.gpu = patch.object(self.fixture.app, "gpu_snapshot", return_value={
            "available": True,
            "gpus": [{"uuid": "GPU-test-uuid", "name": "Fixture GPU",
                      "memory_total_mib": 12000, "memory_used_mib": 3000,
                      "memory_free_mib": 9000, "utilization_percent": 42}],
            "error": None,
        })
        self.install.start(); self.addCleanup(self.install.stop)
        self.locate.start(); self.addCleanup(self.locate.stop)
        self.gpu.start(); self.addCleanup(self.gpu.stop)

    def _install(self):
        self.installed = True

    def _locate(self, _provider):
        return Path("/mock/acp-adapter") if self.installed else None

    def _open_dashboard(self):
        page = self.context.new_page()
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        button = page.get_by_role("button", name="Provider dashboard")
        expect(button).to_be_enabled(timeout=5000)
        button.click()
        expect(page.get_by_role("heading", name="Provider transport")).to_be_visible()
        return page

    def test_install_is_explicit_gpu_probe_is_on_demand_and_engine_persists(self):
        page = self._open_dashboard()
        manager = self.fixture.app.acp_adapters
        self.assertEqual(manager.install.call_count, 0)
        self.assertEqual(self.fixture.app.gpu_snapshot.call_count, 0)
        self.assertEqual(manager.locate.call_count, 2)
        expect(page.get_by_role("button", name="Install ACP adapters")).to_have_count(1)

        page.get_by_role("button", name="Install ACP adapters").click()
        expect(page.locator('[data-acp-engine="codex"]')).to_be_enabled()
        self.assertEqual(manager.install.call_count, 1)
        expect(page.locator('[data-acp-engine="codex"]')).to_have_value("legacy")

        page.get_by_role("button", name="Check GPUs").click()
        expect(page.get_by_text("GPU-test-uuid")).to_be_visible()
        expect(page.get_by_text("9,000 MiB free · 3,000 / 12,000 MiB used · 42% utilized")).to_be_visible()
        self.assertEqual(self.fixture.app.gpu_snapshot.call_count, 1)

        page.locator('[data-acp-engine="codex"]').select_option("acp")
        expect(page.locator('[data-acp-engine="codex"]')).to_have_value("acp")
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Provider dashboard").click()
        expect(page.locator('[data-acp-engine="codex"]')).to_have_value("acp")
        self.assertEqual(self.fixture.app.acp_adapters.install.call_count, 1)
        self.assertEqual(self.fixture.app.gpu_snapshot.call_count, 1)

    def test_install_http_success_with_unavailable_adapters_reports_failure(self):
        page = self._open_dashboard()
        self.fixture.app.acp_adapters.install.side_effect = RuntimeError("fixture install failure")
        page.get_by_role("button", name="Install ACP adapters").click()
        expect(page.locator("[data-acp-feedback]")).to_contain_text("Installation failed:")
        expect(page.locator("[data-acp-feedback]")).to_contain_text("ACP adapter installation failed")
        expect(page.locator(".setup-provider-row").first).to_contain_text("ACP adapter installation failed")

    def test_provider_window_shows_only_its_provider_setup(self):
        page = self.context.new_page()
        capability = self.fixture.app.issue_capability("dashboard", window_id="provider-codex", provider="codex")
        page.goto(f"{self.fixture.base_url}/#capability={capability}&provider=codex&window=provider-codex",
                  wait_until="domcontentloaded")
        page.get_by_role("button", name="Provider dashboard").click()
        expect(page.locator('[data-acp-engine="codex"]')).to_be_visible()
        expect(page.locator('[data-acp-engine="claude"]')).to_have_count(0)
        expect(page.get_by_text("Installing adds both the Codex and Claude ACP adapters, including from a provider window.")).to_be_visible()
        page.set_viewport_size({"width": 320, "height": 640})
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 320)


if __name__ == "__main__":
    unittest.main()
