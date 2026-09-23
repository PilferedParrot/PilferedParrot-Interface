"""ACP picker behavior with a fake live-options endpoint; no ACP process or prompt."""
from __future__ import annotations

import json
import os
import unittest
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "Playwright required")
class ACPOptionsBrowserTests(unittest.TestCase):
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
        self.model_probes = []
        self.model_probe_queries = []
        self.engine_for_state = "acp"

        def state_with_acp(route):
            response = route.fetch()
            payload = response.json()
            payload["provider_engines"] = {"codex": self.engine_for_state}
            route.fulfill(response=response, json=payload)

        def fake_acp_probe(route):
            query = parse_qs(urlparse(route.request.url).query)
            self.model_probe_queries.append(query.get("model", [""])[0])
            model = query.get("model", ["sonnet"])[0]
            self.model_probes.append(model)
            models = [
                {"value": "sonnet", "label": "Sonnet live"},
                {"value": "haiku", "label": "Haiku live"},
            ]
            efforts = ([
                {"value": "low", "label": "Low"},
                {"value": "high", "label": "High"},
            ] if model == "sonnet" else [])
            options = [dict(item) for item in models]
            if efforts:
                next(item for item in options if item["value"] == model)["reasoning_efforts"] = [
                    item["value"] for item in efforts
                ]
            route.fulfill(json={
                "provider": "codex", "default": "sonnet", "source": "acp",
                "options": options,
                "acp_options": {
                    "models": models, "efforts": efforts, "modes": [],
                    "current_model": model,
                    "current_effort": "low" if efforts else None,
                    "current_mode": None,
                },
            })

        self.page.route("**/api/state?compact=1", state_with_acp)
        self.page.route("**/api/providers/codex/models**", fake_acp_probe)
        self.page.goto(self.fixture.browser_url)
        expect(self.page.locator("#prompt")).to_be_enabled()

    def test_stale_choice_recovers_and_effort_tracks_each_live_model(self):
        model = self.page.locator("#modelSelect")
        model.dispatch_event("pointerdown")
        expect(model.locator('option[value="sonnet"]')).to_have_count(1)
        expect(model.locator('option[value="haiku"]')).to_have_count(1)
        expect(model.locator('option[value="gpt-5.6-sol"]')).to_have_count(0)
        expect(model.locator('option[value="fake-small"]')).to_have_attribute("disabled", "")
        self.assertEqual(self.model_probe_queries[0], "")

        model.select_option("sonnet")
        expect(self.page.locator("#reasoningControl")).to_be_visible()
        expect(self.page.locator('#reasoningSelect option[value="low"]')).to_have_count(1)
        expect(self.page.locator('#reasoningSelect option[value="high"]')).to_have_count(1)

        model.select_option("haiku")
        expect(self.page.locator("#reasoningControl")).to_be_hidden()
        self.assertIn("sonnet", self.model_probes)
        self.assertIn("haiku", self.model_probes)
        self.assertEqual(self.model_probes[0], "sonnet")

    def test_engine_switch_updates_picker_without_reload(self):
        self.engine_for_state = "legacy"
        self.page.reload()
        expect(self.page.locator("#prompt")).to_be_enabled()
        self.page.route("**/api/acp/setup", lambda route: route.fulfill(json={
            "providers": {"codex": {"installed": True, "engine": "legacy"}},
        }))
        self.page.route("**/api/acp/providers/codex/engine", lambda route: route.fulfill(json={
            "providers": {"codex": {"installed": True, "engine": "acp"}},
        }))
        self.page.locator("#providerWindows").click()
        transport = self.page.locator('[data-acp-engine="codex"]')
        expect(transport).to_be_enabled()
        transport.select_option("acp")
        expect(self.page.locator('#modelSelect option[value="sonnet"]')).to_have_count(1)
        self.assertIn("", self.model_probe_queries)

    def test_saved_model_choice_does_not_override_another_session(self):
        model = self.page.locator("#modelSelect")
        model.dispatch_event("pointerdown")
        expect(model.locator('option[value="sonnet"]')).to_have_count(1)
        first_session = self.page.locator("#chatList [data-chat]").first.get_attribute("data-chat")
        model.select_option("sonnet")
        expect(model).to_be_enabled()
        expect(model).to_have_value("sonnet")
        self.page.locator("#newWorkSession").click()
        expect(self.page.locator("#chatList [data-chat]")).to_have_count(2)
        model.select_option("haiku")
        expect(model).to_be_enabled()
        expect(model).to_have_value("haiku")
        self.page.locator(f'#chatList [data-chat="{first_session}"]').click()
        expect(model).to_have_value("sonnet")



if __name__ == "__main__":
    unittest.main()
