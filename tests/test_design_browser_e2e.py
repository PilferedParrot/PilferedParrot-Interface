"""Browser checks for the shared dialog and narrow layout contract."""

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
class DesignBrowserEndToEndTests(unittest.TestCase):
    DIALOGS = (
        ("projectDialog", "projectTitle"),
        ("terminalDialog", "terminalTitle"),
        ("providerDialog", "providerDialogTitle"),
        ("providerLogoutDialog", "providerLogoutTitle"),
        ("providerRemoveDialog", "providerRemoveTitle"),
    )

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
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page_errors = []
        self.page.on("pageerror", lambda error: self.page_errors.append(error))
        self.page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(self.page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)

    def tearDown(self):
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    @staticmethod
    def _light_theme():
        return {
            "active": True,
            "id": "design-light",
            "version": "1",
            "colors": {
                "ntp_background": "#f7f3ea",
                "ntp_text": "#171717",
                "ntp_section": "#f7f3ea",
                "frame": "#e4dac8",
                "toolbar": "#eee6d9",
            },
            "background": False,
        }

    def _apply_light_theme(self):
        theme = self._light_theme()
        self.fixture.app.browser_theme = lambda: theme
        self.page.evaluate("theme => applyBrowserTheme(theme)", theme)

    def _assert_button_contrast(self, button):
        ratio = button.evaluate("""node => {
            const parse = value => {
              const channels = value.match(/\\d+(?:\\.\\d+)?/g).map(Number);
              return { rgb: channels.slice(0, 3), alpha: channels[3] ?? 1 };
            };
            const luminance = rgb => rgb.map(value => value / 255).map(value =>
              value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
              .reduce((total, value, index) => total + value * [.2126, .7152, .0722][index], 0);
            const style = getComputedStyle(node);
            let backgroundNode = node;
            let background = parse(getComputedStyle(backgroundNode).backgroundColor);
            while (background.alpha === 0 && backgroundNode.parentElement) {
              backgroundNode = backgroundNode.parentElement;
              background = parse(getComputedStyle(backgroundNode).backgroundColor);
            }
            const foreground = luminance(parse(style.color).rgb);
            const backgroundLuminance = luminance(background.rgb);
            return (Math.max(foreground, backgroundLuminance) + .05) / (Math.min(foreground, backgroundLuminance) + .05);
        }""")
        self.assertGreaterEqual(ratio, 4.5)

    def test_dialog_headings_close_without_submit_or_escape(self):
        for light in (False, True):
            with self.subTest(light=light):
                if light:
                    self._apply_light_theme()
                for width, height in ((1280, 900), (320, 568)):
                    self.page.set_viewport_size({"width": width, "height": height})
                    for dialog_id, heading_id in self.DIALOGS:
                        with self.subTest(viewport=(width, height), dialog=dialog_id):
                            dialog = self.page.locator(f"#{dialog_id}")
                            self.assertEqual(dialog.get_attribute("aria-labelledby"), heading_id)
                            heading = dialog.locator(".dialog-heading")
                            expect(heading.locator("h2")).to_have_count(1)
                            close = heading.locator("button.dialog-close")
                            expect(close).to_have_count(1)
                            self.page.locator("#whiteboardButton").focus()
                            self.page.evaluate("id => document.getElementById(id).showModal()", dialog_id)
                            expect(dialog).to_be_visible()
                            self._assert_dialog_geometry(dialog)
                            if light:
                                self._assert_button_contrast(
                                    dialog.locator("button:not(.dialog-close)").first,
                                )
                            close.click()
                            expect(dialog).to_be_hidden()
                            self.page.locator("#whiteboardButton").focus()
                            self.page.evaluate("id => document.getElementById(id).showModal()", dialog_id)
                            self.page.keyboard.press("Escape")
                            expect(dialog).to_be_hidden()

    def _assert_dialog_geometry(self, dialog):
        geometry = dialog.evaluate("""
            node => { const r = node.getBoundingClientRect();
              const close = node.querySelector('.dialog-close').getBoundingClientRect();
              return {dialog: [r.left, r.top, r.right, r.bottom], close: [close.left, close.top, close.right, close.bottom]}; }
        """)
        viewport = self.page.evaluate("[innerWidth, innerHeight]")
        self.assertGreaterEqual(geometry["dialog"][0], 0)
        self.assertGreaterEqual(geometry["dialog"][1], 0)
        self.assertLessEqual(geometry["dialog"][2], viewport[0])
        self.assertLessEqual(geometry["dialog"][3], viewport[1])
        self.assertGreaterEqual(geometry["close"][0], geometry["dialog"][0])
        self.assertGreaterEqual(geometry["close"][1], geometry["dialog"][1])
        self.assertLessEqual(geometry["close"][2], viewport[0])
        self.assertLessEqual(geometry["close"][3], viewport[1])
        self.assertGreater(geometry["close"][0], geometry["dialog"][2] - 96)
        self.assertLessEqual(geometry["close"][1], geometry["dialog"][1] + 64)
        self.assertEqual(dialog.locator(".dialog-heading").evaluate("node => getComputedStyle(node).position"), "sticky")

    def test_whiteboard_close_is_top_heading_control_and_no_footer_close(self):
        dialog = self.page.locator("#whiteboardDialog")
        self.page.locator("#whiteboardButton").click()
        expect(dialog).to_be_visible()
        expect(dialog.locator(".dialog-heading button.dialog-close")).to_have_count(1)
        self.assertEqual(dialog.locator("#whiteboardClose").evaluate("node => node.parentElement.className"), "dialog-heading")
        self.assertEqual(dialog.locator("button.dialog-close").count(), 1)
        dialog.locator("#whiteboardClose").click()
        expect(dialog).to_be_hidden()

    def test_dialog_close_and_provider_remove_cancel_do_not_post(self):
        requests = []
        self.page.on("request", lambda request: requests.append(request))
        dialog = self.page.locator("#providerRemoveDialog")
        self.page.evaluate("removeProvider('codex')")
        dialog.get_by_role("button", name="Close", exact=True).click()
        expect(dialog).to_be_hidden()
        self.assertEqual([r.url for r in requests if "/api/providers/remove" in r.url], [])
        self.page.evaluate("removeProvider('codex')")
        self.page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        self.assertEqual([r.url for r in requests if "/api/providers/remove" in r.url], [])
        self.page.evaluate("removeProvider('codex')")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        expect(dialog).to_be_hidden()
        self.assertEqual([r.url for r in requests if "/api/providers/remove" in r.url], [])
        self.page.route("**/api/providers/remove", lambda route: route.fulfill(status=200, content_type="application/json", body='{"ok":true}'))
        self.page.evaluate("removeProvider('codex')")
        with self.page.expect_request("**/api/providers/remove") as removal_request:
            dialog.get_by_role("button", name="Remove provider", exact=True).click()
        self.assertEqual(removal_request.value.post_data_json, {"provider": "codex"})
        removals = [r for r in requests if "/api/providers/remove" in r.url]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0].post_data_json, {"provider": "codex"})

    def test_provider_remove_fast_close_and_reopen_keeps_new_target(self):
        self.page.route(
            "**/api/providers/remove",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"ok":true}',
            ),
        )
        dialog = self.page.locator("#providerRemoveDialog")
        self.page.evaluate("""() => {
            removeProvider('codex');
            document.querySelector('#providerRemoveDialog').close();
            removeProvider('claude');
        }""")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("Remove claude?")
        with self.page.expect_request("**/api/providers/remove") as removal_request:
            dialog.get_by_role("button", name="Remove provider", exact=True).click()
        self.assertEqual(removal_request.value.post_data_json, {"provider": "claude"})

    def test_provider_confirmation_enter_submits_code_without_closing_dashboard(self):
        requests = []
        self.page.on("request", lambda request: requests.append(request))
        self.page.route(
            "**/api/providers/claude/code",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"ok":true,"submitted":true}',
            ),
        )
        self.page.get_by_role("button", name="Provider dashboard").click()
        dialog = self.page.locator("#providerDialog")
        expect(dialog).to_be_visible()
        self.page.evaluate("""() => {
            state.authPending.claude = true;
            state.authConfirmation.claude = true;
            state.authCodes.claude = '';
            state.budgets.claude = {
              auth_status: 'signed_out', status: 'signed_out', reachability: 'reachable',
            };
            state.budgetsLoaded = true;
            document.querySelector('#providerConnectionList').innerHTML =
              '<div class="provider-auth-code"><input data-provider-auth-code="claude">' +
              '<button type="button" data-provider-code="claude" disabled>Confirm sign-in</button></div>';
        }""")
        code = dialog.locator('[data-provider-auth-code="claude"]')
        expect(code).to_be_visible()
        code.fill("synthetic-code#synthetic-state")
        with self.page.expect_request("**/api/providers/claude/code") as submission:
            code.press("Enter")
        self.assertEqual(
            submission.value.post_data_json,
            {"code": "synthetic-code#synthetic-state"},
        )
        expect(dialog).to_be_visible()
        self.assertEqual(
            [request.url for request in requests if "/api/providers/" in request.url],
            [f"{self.fixture.base_url}/api/providers/claude/code"],
        )

    def test_work_and_chat_sidebar_disclosures_have_no_vertical_border(self):
        pages = [self.page]
        self.page.get_by_role("button", name="Open Chat window").click()
        chat_page = self.context.new_page()
        chat_page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        chat_page.goto(
            f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(chat_page.get_by_role("textbox", name="Message Chat")).to_be_enabled(timeout=5_000)
        pages.append(chat_page)
        for page in pages:
            for width, height in ((1280, 900), (320, 568)):
                page.set_viewport_size({"width": width, "height": height})
                disclosures = page.locator(".sidebar-disclosure")
                expect(disclosures).to_have_count(2)
                for index in range(disclosures.count()):
                    styles = disclosures.nth(index).evaluate(
                        "node => { const s = getComputedStyle(node); return [s.borderTopWidth, s.borderBottomWidth]; }",
                    )
                    self.assertEqual(styles, ["0px", "0px"])
                self.assertLessEqual(page.locator("body").evaluate("node => node.scrollWidth"), width)

    def test_provider_close_stays_reachable_after_scroll_on_narrow_light_view(self):
        self._apply_light_theme()
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.get_by_role("button", name="Provider dashboard").evaluate("node => node.click()")
        dialog = self.page.locator("#providerDialog")
        expect(dialog).to_be_visible()
        self.page.evaluate("""() => {
            const list = document.querySelector('#providerConnectionList');
            list.insertAdjacentHTML('beforeend', Array.from({length: 24}, (_, index) =>
              `<section class="provider-connection-card"><p>Extra provider ${index}</p></section>`).join(''));
            const dialog = document.querySelector('#providerDialog');
            dialog.scrollTop = dialog.scrollHeight;
        }""")
        self.assertGreater(dialog.evaluate("node => node.scrollTop"), 0)
        self._assert_dialog_geometry(dialog)
        close = dialog.locator(".dialog-close")
        hit = close.evaluate("node => { const r=node.getBoundingClientRect(); const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2); return hit === node || hit?.closest('.dialog-close') === node; }")
        self.assertTrue(hit)


if __name__ == "__main__":
    unittest.main()
