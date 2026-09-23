"""Browser coverage for responsive sidebar accessibility state."""

from __future__ import annotations

import os
import re
import unittest

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class SidebarBrowserEndToEndTests(unittest.TestCase):
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
        self.fixture = PilferedParrotBrowserFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.addCleanup(self.context.close)
        self.page_errors = []

    def tearDown(self):
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _load_work(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(self.fixture.browser_url, wait_until="domcontentloaded")
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        return page

    def _load_chat(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page.goto(
            f"{self.fixture.base_url}/chat#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message Chat")).to_be_enabled(timeout=5_000)
        return page

    def _assert_resize_state(self, page, *, sidebar, toggle, close, conversation, input_selector):
        page.locator(toggle).click()
        expect(page.locator(sidebar)).to_have_class(re.compile(r"\bopen\b"))
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "true")
        self.assertTrue(page.locator(conversation).evaluate("node => node.inert"))

        # The open mobile drawer remains visible across the breakpoint, but the
        # conversation must become usable as soon as the mobile modal behavior ends.
        page.set_viewport_size({"width": 900, "height": 844})
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "false")
        self.assertFalse(page.locator(conversation).evaluate("node => node.inert"))
        page.locator(input_selector).focus()
        self.assertEqual(page.evaluate("document.activeElement.id"), input_selector.removeprefix("#"))

        # Returning to narrow layout restores the modal semantics. Setting a
        # focused conversation inert may naturally blur it; the resize handler
        # itself must not move focus to a sidebar control.
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "true")
        self.assertTrue(page.locator(conversation).evaluate("node => node.inert"))
        page.locator(close).click()
        expect(page.locator(toggle)).to_have_attribute("aria-expanded", "false")
        self.assertEqual(page.evaluate("document.activeElement.id"), toggle.removeprefix("#"))
        self.assertFalse(page.locator(conversation).evaluate("node => node.inert"))

    def test_work_sidebar_inert_tracks_breakpoint_and_close_focus(self):
        page = self._load_work()
        self._assert_resize_state(
            page,
            sidebar="#sidebar",
            toggle="#openSidebar",
            close="#closeSidebar",
            conversation=".main",
            input_selector="#prompt",
        )

    def test_chat_sidebar_inert_tracks_breakpoint_and_close_focus(self):
        page = self._load_chat()
        self._assert_resize_state(
            page,
            sidebar=".chat-window-sidebar",
            toggle="#toggleChatSidebar",
            close="#closeChatSidebar",
            conversation=".chat-window-conversation",
            input_selector="#chatPrompt",
        )

    def test_project_required_launch_cannot_submit_until_a_folder_is_chosen(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        create_requests = []
        page.on(
            "request",
            lambda request: create_requests.append(request.url)
            if request.method == "POST" and (
                request.url.endswith("/api/chats")
                or ("/api/chats/" in request.url and request.url.endswith("/messages"))
            )
            else None,
        )
        capability = self.fixture.app.dashboard_capability
        page.goto(
            f"{self.fixture.base_url}/#capability={capability}&provider=codex&pick=1",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        dialog = page.locator("#projectDialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()
        page.get_by_role("textbox", name="Message").fill("must choose a folder")
        page.get_by_role("button", name="Open sidebar").click()
        page.get_by_role("button", name="Start a new work session").click()
        expect(dialog).to_be_visible()
        self.assertEqual(create_requests, [])
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()
        page.get_by_role("button", name="Close sidebar").click()

        page.get_by_role("button", name="Send").click()
        expect(dialog).to_be_visible()
        self.assertEqual(create_requests, [])

        page.locator("#projectInput").fill(str(self.fixture.project))
        dialog.get_by_role("button", name="Use folder").click()
        expect(dialog).to_be_hidden()
        expect(page.locator("#chatTitle")).to_be_visible()
        expect(page.get_by_role("textbox", name="Message")).to_have_value("must choose a folder")
        page.get_by_role("button", name="Send").click()
        expect(page.get_by_text("Fake provider completed: must choose a folder", exact=True)).to_be_visible(timeout=5_000)

    def test_project_session_search_filters_summaries_with_keyboard_and_provider_scope(self):
        fixture = self.fixture
        indexed_project = fixture.root / "session-indexed-project"
        indexed_project.mkdir()
        app = fixture.app
        seeded = []
        for provider, title in (
            ("codex", "Needle Alpha"),
            ("codex", "Beta"),
            ("claude", "Needle Claude Private"),
        ):
            chat = app.create_chat(
                {"cwd": str(indexed_project)},
                window_id="provider-codex", window_provider=provider,
            )
            with app.store.lock:
                stored = next(item for item in app.store.data["chats"] if item["id"] == chat["id"])
                stored["title"] = title
                app.store.save()
            seeded.append(chat["id"])

        capability = app.issue_capability(
            "dashboard", window_id="provider-codex", provider="codex",
        )
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(
            f"{fixture.base_url}/#capability={capability}&provider=codex&window=provider-codex",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        search = page.get_by_role("searchbox", name="Search sessions in this project")
        expect(page.locator("#sessionSearchScope")).to_have_text("Scope: session-indexed-project")
        expect(page.locator("#sessionSearchScope")).to_have_attribute("title", str(indexed_project))
        expect(page.locator("#chatList .chat-item")).to_have_count(2)
        expect(page.get_by_text("Needle Claude Private", exact=True)).to_have_count(0)

        initial_ids = set(page.locator("#chatList .chat-item").evaluate_all(
            "nodes => nodes.map(node => node.dataset.chat)"
        ))
        self.assertEqual(initial_ids, set(seeded[:2]))
        active_before = page.evaluate("sessionStorage.getItem('pilferedparrot-active-chat')")
        title_before = page.locator("#chatTitle").inner_text()
        project_before = page.locator("#projectSelect").input_value()
        prompt = page.get_by_role("textbox", name="Message")
        prompt.fill("draft stays while filtering")
        page.get_by_role("button", name="Open sidebar").click()
        expect(page.locator("#sidebar")).to_have_class(re.compile(r"\bopen\b"))
        expect(search).to_be_visible()
        search.fill("codex")
        expect(page.locator("#chatList .chat-item")).to_have_count(2)
        expect(page.locator("#sessionSearchStatus")).to_have_text("2 of 2 sessions in this project match.")
        expect(prompt).to_have_value("draft stays while filtering")
        expect(page.locator("#chatTitle")).to_have_text(title_before)
        self.assertEqual(page.locator("#projectSelect").input_value(), project_before)

        search.fill("needle")
        expect(page.locator("#chatList .chat-item")).to_have_count(1)
        expect(page.get_by_text("Needle Alpha", exact=True)).to_be_visible()
        expect(page.locator("#sessionSearchStatus")).to_have_text("1 of 2 sessions in this project match.")
        expect(prompt).to_have_value("draft stays while filtering")
        expect(page.locator("#chatTitle")).to_have_text(title_before)
        self.assertEqual(page.evaluate("sessionStorage.getItem('pilferedparrot-active-chat')"), active_before)

        search.press("Escape")
        expect(search).to_have_value("")
        expect(page.locator("#chatList .chat-item")).to_have_count(2)
        expect(search).to_be_focused()
        search.fill(fixture.root.name)
        expect(page.locator("#chatList .chat-item")).to_have_count(2)
        expect(page.locator("#sessionSearchStatus")).to_have_text("2 of 2 sessions in this project match.")
        search.fill("no-such-session")
        expect(page.locator("#chatList .chat-item")).to_have_count(0)
        expect(page.locator("#sessionSearchStatus")).to_have_text("0 of 2 sessions in this project match.")
        expect(page.get_by_text("No sessions match this search.")).to_be_visible()
        expect(page.get_by_role("button", name="Clear session search")).to_be_visible()
        search.press("Escape")
        expect(search).to_have_value("")
        expect(page.locator("#chatList .chat-item")).to_have_count(2)

        search.fill(fixture.root.name)
        search.press("Tab")
        clear = page.get_by_role("button", name="Clear session search")
        expect(clear).to_be_focused()
        page.keyboard.press("Enter")
        expect(search).to_have_value("")
        expect(search).to_be_focused()
        expect(page.locator("#chatList .chat-item")).to_have_count(2)
        search.press("Escape")
        expect(page.locator("#sidebar")).not_to_have_class(re.compile(r"\bopen\b"))
        expect(page.get_by_role("button", name="Open sidebar")).to_be_focused()


    def test_workspace_and_connection_controls_stay_in_their_groups_across_themes(self):
        page = self._load_work()
        for colors in (None, ("#f7f3ea", "#171717", "#e4dac8", "#eee6d9"),
                       ("#281443", "#ffffff", "#6a225e", "#c352b0")):
            theme = {"active": False} if colors is None else {
                "active": True, "id": "grouping-check", "version": colors[0],
                "background": False, "colors": {
                    "ntp_background": colors[0], "ntp_section": colors[0],
                    "ntp_text": colors[1], "frame": colors[2], "toolbar": colors[3],
                },
            }
            page.evaluate("theme => applyBrowserTheme(theme)", theme)
            page.set_viewport_size({"width": 1280, "height": 900})
            groups = page.evaluate("""() => {
                const group = id => document.getElementById(id).closest('.sidebar-group');
                return {
                    workspace: ['newWorkSession', 'providerWindows', 'openChat', 'whiteboardButton']
                        .every(id => group(id) && group(id) === group('newWorkSession')),
                    connection: ['refreshBudgets', 'providerList', 'contextDetails', 'providerUpdate']
                        .every(id => group(id) && group(id) === group('refreshBudgets')),
                    separate: group('newWorkSession') !== group('refreshBudgets'),
                };
            }""")
            self.assertEqual(groups, {"workspace": True, "connection": True, "separate": True})
            for hovered in (False, True):
                colors_by_button = []
                for selector in ('#newWorkSession', '#providerWindows', '#openChat', '#whiteboardButton'):
                    if hovered:
                        page.locator(selector).hover()
                    else:
                        page.locator('#prompt').hover()
                    colors_by_button.append(page.locator(selector).evaluate(
                        "node => { const s = getComputedStyle(node); return [s.backgroundColor, s.color, s.borderColor]; }",
                    ))
                self.assertTrue(all(value == colors_by_button[0] for value in colors_by_button))

        # Whiteboard moved from the header into the drawer; keyboard opening and
        # native dialog focus restoration must still work on a small screen.
        page.set_viewport_size({"width": 320, "height": 568})
        page.locator('#openSidebar').click()
        page.locator('#whiteboardButton').focus()
        page.keyboard.press('Enter')
        expect(page.locator('#whiteboardDialog')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('#whiteboardButton')).to_be_focused()

    def test_history_heading_and_entries_share_one_box_with_local_scrolling(self):
        for loader, sidebar, toggle, list_id in (
            (self._load_work, '#sidebar', '#openSidebar', '#chatList'),
            (self._load_chat, '#chatWindowSidebar', '#toggleChatSidebar', '#chatHistoryList'),
        ):
            page = loader()
            # Add enough representative rows to exercise the scroll geometry.
            page.locator(list_id).evaluate("""node => {
                node.replaceChildren(...Array.from({length: 30}, (_, i) => {
                    const row = document.createElement('button');
                    row.className = 'chat-item';
                    row.textContent = `Session ${i + 1} — a long project name to wrap`;
                    return row;
                }));
            }""")
            for width, height in ((1280, 900), (320, 568)):
                page.set_viewport_size({"width": width, "height": height})
                if width == 320:
                    page.locator(toggle).click()
                for expanded in (False, True):
                    page.locator('#contextDetails').evaluate('(node, value) => node.open = value', expanded)
                    page.locator(list_id).scroll_into_view_if_needed()
                    metrics = page.locator(list_id).evaluate("""node => {
                        const group = node.closest('.history-group');
                        const heading = group.querySelector('.history-heading');
                        const top = heading.getBoundingClientRect().top;
                        node.scrollTop = node.scrollHeight;
                        const box = group.getBoundingClientRect();
                        const list = node.getBoundingClientRect();
                        return {
                            border: parseFloat(getComputedStyle(group).borderTopWidth),
                            listBorder: parseFloat(getComputedStyle(node).borderTopWidth),
                            scroll: node.scrollTop,
                            stableHeading: heading.getBoundingClientRect().top === top,
                            contained: list.top >= box.top && list.bottom <= box.bottom,
                            height: node.clientHeight,
                        };
                    }""")
                    self.assertGreater(metrics['border'], 0)
                    self.assertEqual(metrics['listBorder'], 0)
                    self.assertGreater(metrics['scroll'], 0)
                    self.assertTrue(metrics['stableHeading'])
                    self.assertTrue(metrics['contained'])
                    self.assertGreaterEqual(metrics['height'], 48)
                    self.assertLessEqual(page.locator(sidebar).evaluate('node => node.scrollWidth'),
                                         page.locator(sidebar).evaluate('node => node.clientWidth'))
                    self.assertLessEqual(page.locator('body').evaluate('node => node.scrollWidth'), width)
            page.close()

    def test_preferences_is_separate_reachable_and_restores_keyboard_focus(self):
        for loader, sidebar, toggle in (
            (self._load_work, '#sidebar', '#openSidebar'),
            (self._load_chat, '#chatWindowSidebar', '#toggleChatSidebar'),
        ):
            page = loader()
            button = page.get_by_role('button', name='Preferences', exact=True)
            dialog = page.get_by_role('dialog', name='Preferences', exact=True)
            for width, height in ((1280, 900), (320, 568)):
                page.set_viewport_size({'width': width, 'height': height})
                if width == 320:
                    page.locator(toggle).click()
                # Measure settled layout, not an in-flight mobile drawer transform.
                page.locator(sidebar).evaluate(
                    'async node => { await Promise.all(node.getAnimations().map(animation => animation.finished)); }',
                )
                # Even expanded telemetry and overflowing history cannot push
                # the application settings control out of the viewport.
                page.locator('#contextDetails').evaluate('node => node.open = true')
                content = page.locator('.sidebar-content')
                for scroll in ('0', 'node.scrollHeight'):
                    content.evaluate(f'node => node.scrollTop = {scroll}')
                    geometry = button.evaluate("""node => {
                        const rect = node.getBoundingClientRect();
                        const content = document.querySelector('.sidebar-content').getBoundingClientRect();
                        const style = getComputedStyle(node);
                        return {separate: !node.closest('.sidebar-group'),
                            visible: rect.top >= 0 && rect.bottom <= innerHeight,
                            below: rect.top >= content.bottom,
                            border: parseFloat(style.borderTopWidth),
                            radius: style.borderRadius};
                    }""")
                    self.assertTrue(geometry['separate'])
                    self.assertTrue(geometry['visible'])
                    self.assertTrue(geometry['below'])
                    self.assertEqual(geometry['border'], 1)
                    self.assertEqual(geometry['radius'], '10px')
                history_before = page.locator('.history-group').bounding_box()
                button.focus()
                page.keyboard.press('Enter')
                expect(dialog).to_be_visible()
                self.assertEqual(page.locator('.history-group').bounding_box(), history_before)
                self.assertTrue(dialog.evaluate("node => node.matches(':modal')"))
                expect(dialog.get_by_role('button', name='Close', exact=True)).to_be_focused()
                for _ in range(5):
                    page.keyboard.press('Tab')
                    self.assertTrue(dialog.evaluate(
                        'node => node.contains(document.activeElement) || document.activeElement === document.body',
                    ))
                dialog.get_by_role('button', name='Close', exact=True).focus()
                button.evaluate('node => node.focus()')
                expect(dialog.get_by_role('button', name='Close', exact=True)).to_be_focused()
                self.assertLessEqual(dialog.evaluate('node => node.scrollWidth'),
                                     dialog.evaluate('node => node.clientWidth'))
                page.keyboard.press('Escape')
                expect(dialog).to_be_hidden()
                expect(button).to_be_focused()
                if width == 320:
                    expect(page.locator(sidebar)).to_have_class(re.compile(r'\bopen\b'))
                button.click()
                dialog.get_by_role('button', name='Close', exact=True).click()
                expect(dialog).to_be_hidden()
                expect(button).to_be_focused()
                if width == 320:
                    page.keyboard.press('Escape')
                    expect(page.locator(toggle)).to_be_focused()
            page.close()
