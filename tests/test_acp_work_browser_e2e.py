"""Browser coverage for the opt-in ACP Work UI using the deterministic stdio agent."""

from __future__ import annotations

import os
import json
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


FAKE_AGENT = Path(__file__).parent / "fixtures" / "acp" / "fake_agent.py"
PREVIEW_COMMAND = "printf 'approved\\n' > allowed.txt"
SPLIT_SECRET = "FAKE-ACP-SPLIT-SECRET-739a"


class ACPBrowserFixture(PilferedParrotBrowserFixture):
    def _config(self):
        config = super()._config()
        config["codex"]["engine"] = "acp"
        config["codex"]["api_key_env"] = "FAKE_ACP_SECRET"
        return config

    def __init__(self):
        self._agent_mode = patch.dict(os.environ, {
            "FAKE_ACP_BROWSER_E2E": "1", "FAKE_ACP_SECRET": SPLIT_SECRET,
        })
        self._agent_mode.start()
        super().__init__()
        self.app.acp_adapters.locate = lambda provider: [sys.executable, str(FAKE_AGENT)]

    def stop(self):
        try:
            super().stop()
        finally:
            self._agent_mode.stop()


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class ACPWorkBrowserEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.fixture = ACPBrowserFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page_errors = []
        self.external_requests = []
        self.page.on("pageerror", lambda error: self.page_errors.append(error))
        self.context.on("request", self._record_request)
        self.page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(self.page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)

    def tearDown(self):
        self.assertEqual(self.external_requests, [], "browser attempted external network")
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _record_request(self, request):
        if urlparse(request.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.external_requests.append(request.url)

    def _send_and_wait_for_permission(self, prompt):
        message = self.page.get_by_role("textbox", name="Message")
        message.fill(prompt)
        self.page.get_by_role("button", name="Send").click()
        card = self.page.get_by_role("group", name="Permission requested")
        expect(card).to_be_visible(timeout=8_000)
        expect(self.page.locator(".acp-streamed-text")).to_contain_text(
            "working on ", timeout=5_000,
        )
        return card

    def test_streamed_acp_answer_diff_preview_and_scoped_permission_choices(self):
        card = self._send_and_wait_for_permission("browser-e2e-deny")
        expect(self.page.locator(".acp-streamed-text")).to_contain_text(
            "working on ",
        )
        expect(self.page.locator(".acp-tool-card")).to_contain_text("Prepare browser preview")
        expect(self.page.locator(".acp-tool-card")).to_contain_text("preview.txt")
        expect(self.page.locator(".acp-tool-card")).to_contain_text("ACP browser preview")
        expect(card).to_contain_text("Write allowed.txt")
        expect(card.locator(".acp-command pre")).to_have_text(PREVIEW_COMMAND)
        expect(card.locator(".acp-diff")).to_contain_text("allowed.txt")
        expect(card.locator(".acp-diff")).to_contain_text("approved")

        # A pending permission stays unanswered until the user chooses an option.
        allowed_path = self.fixture.project / "allowed.txt"
        self.assertFalse(allowed_path.exists())
        with self.fixture.app.store.lock:
            current_chat = next(
                chat for chat in self.fixture.app.store.data["chats"]
                if chat.get("window_id") == "main"
                and any(message.get("pending") for message in chat.get("messages", []))
            )
        other_chat = self.fixture.app.create_chat(
            {"cwd": str(self.fixture.project), "provider": "codex", "model": "fake-small"},
            window_id="main", window_provider="codex",
        )
        other_capability = self.fixture.app.issue_capability(
            "dashboard", window_id="other", provider="codex",
        )

        # The wrong window cannot inspect or decide this chat's pending request;
        # a separate chat has no permission to leak into its permission list.
        denied = self.page.evaluate(
            """async ({url, capability, payload}) => {
              const response = await fetch(url, {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'X-PilferedParrot-Capability': capability,
                },
                body: JSON.stringify(payload),
              });
              return {status: response.status, body: await response.text()};
            }""",
            {
                "url": f"/api/chats/{current_chat['id']}/permissions",
                "capability": other_capability,
                "payload": {"request_id": card.locator("button").first.get_attribute("data-acp-permission"),
                            "option_id": "yes"},
            },
        )
        self.assertEqual(denied["status"], 404)
        other_snapshot = self.page.evaluate(
            """async ({url, capability}) => {
              const response = await fetch(url, {
                headers: {'X-PilferedParrot-Capability': capability},
              });
              return {status: response.status, body: await response.json()};
            }""",
            {
                "url": f"/api/chats/{other_chat['id']}/permissions",
                "capability": self.fixture.app.dashboard_capability,
            },
        )
        self.assertEqual(other_snapshot, {"status": 200, "body": {"permissions": []}})
        expect(card).to_be_visible()
        self.assertFalse(allowed_path.exists())

        card.get_by_role("button", name="Reject once").click()
        expect(card).to_have_count(0, timeout=5_000)
        expect(self.page.locator("article.message.assistant").last).to_contain_text(
            "working on browser-e2e-deny",
        )
        self.assertFalse(allowed_path.exists())

        allow_card = self._send_and_wait_for_permission("browser-e2e-allow")
        expect(allow_card.locator(".acp-command pre")).to_have_text(PREVIEW_COMMAND)
        self.assertFalse(allowed_path.exists(), "permission must remain denied until explicit approval")
        allow_card.get_by_role("button", name="Allow once").click()
        expect(allow_card).to_have_count(0, timeout=5_000)
        expect(self.page.locator("article.message.assistant").last).to_contain_text(
            "working on browser-e2e-allow",
        )
        self.assertEqual(allowed_path.read_text(encoding="utf-8"), "changed")

    def test_split_secret_never_reaches_browser_events_or_persisted_chat(self):
        prompt = "browser-e2e-split"
        self.page.get_by_role("textbox", name="Message").fill(prompt)
        self.page.get_by_role("button", name="Send").click()
        card = self.page.get_by_role("group", name="Permission requested")
        expect(card).to_be_visible(timeout=8_000)
        with self.fixture.app.store.lock:
            chat = next(
                chat for chat in self.fixture.app.store.data["chats"]
                if chat.get("window_id") == "main"
                and any(message.get("pending") for message in chat.get("messages", []))
            )

        def assert_secret_absent():
            self.assertNotIn(SPLIT_SECRET, self.page.locator("body").inner_text())
            work_events = self.fixture.app.events.read_after(chat["id"], 0)
            chunks = [
                event for event in work_events if event.get("kind") == "acp_update"
                and event.get("payload", {}).get("entry", {}).get("update", {}).get(
                    "sessionUpdate",
                ) == "agent_message_chunk"
            ]
            self.assertEqual(len(chunks), 2, "fake agent must emit exactly two answer chunks")
            events = json.dumps(
                work_events, ensure_ascii=False,
            )
            self.assertNotIn(SPLIT_SECRET, events)
            self.fixture.app.store.save()
            snapshot = Path(self.fixture.config["web"]["chat_store"]).read_text(
                encoding="utf-8",
            )
            self.assertNotIn(SPLIT_SECRET, snapshot)

        assert_secret_absent()
        card.get_by_role("button", name="Reject once").click()
        expect(card).to_have_count(0, timeout=5_000)
        expect(self.page.locator("article.message.assistant").last).to_be_visible()
        expect(self.page.locator("article.message.assistant").last).to_contain_text(
            "[redacted]",
        )
        assert_secret_absent()

    def test_mobile_permission_choice_scrolls_above_fixed_composer(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        card = self._send_and_wait_for_permission("browser-e2e-deny")
        conversation = self.page.locator("#conversation")
        scroll_state = conversation.evaluate(
            "node => ({height: node.clientHeight, content: node.scrollHeight})",
        )
        self.assertGreater(scroll_state["content"], scroll_state["height"])
        conversation.evaluate("node => { node.scrollTop = node.scrollHeight; }")
        reject = card.get_by_role("button", name="Reject once")
        reject.scroll_into_view_if_needed()
        geometry = reject.evaluate("""node => {
          const button = node.getBoundingClientRect();
          const composer = document.querySelector('.composer-wrap').getBoundingClientRect();
          return {buttonBottom: button.bottom, composerTop: composer.top};
        }""")
        self.assertLessEqual(geometry["buttonBottom"], geometry["composerTop"])
        with self.page.expect_response(
            re.compile(r"/api/chats/[^/]+/permissions$")
        ) as decision:
            reject.click()
        self.assertEqual(decision.value.status, 200)
        expect(card).to_have_count(0, timeout=5_000)


if __name__ == "__main__":
    unittest.main()
