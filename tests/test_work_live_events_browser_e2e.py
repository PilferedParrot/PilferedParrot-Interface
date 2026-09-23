"""Browser coverage for live work events over the capability-protected stream."""

from __future__ import annotations

import os
import re
import threading
import time
import unittest
from urllib.parse import urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

import playwright_fixture as fixture_module
from playwright_fixture import FakeProvider, PilferedParrotBrowserFixture


PROMPT = "Held work event stream response"
LIVE_PROGRESS = "Live tool event arrived before the provider completed"


class LiveEventProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self._progress_callbacks: dict[str, object] = {}
        self._ready: dict[str, threading.Event] = {}

    def hold(self, prompt: str) -> None:
        super().hold(prompt)
        with self._lock:
            self._ready[prompt] = threading.Event()

    def dispatch(self, provider, prompt, cwd, conversation, config, cancel_event):
        callback = getattr(cancel_event, "_pilferedparrot_progress", None)
        with self._lock:
            self._progress_callbacks[prompt] = callback
            ready = self._ready.setdefault(prompt, threading.Event())
            ready.set()
        return super().dispatch(provider, prompt, cwd, conversation, config, cancel_event)

    def wait_ready(self, prompt: str) -> None:
        with self._lock:
            ready = self._ready[prompt]
        if not ready.wait(timeout=5):
            raise TimeoutError("fake provider did not begin the held request")

    def emit_progress(self, prompt: str, text: str) -> None:
        with self._lock:
            callback = self._progress_callbacks[prompt]
        if callback is None:
            raise RuntimeError("progress callback was unavailable")
        callback("tool", text)


class LiveEventFixture(PilferedParrotBrowserFixture):
    def __init__(self) -> None:
        super().__init__()
        self.provider = LiveEventProvider()
        fixture_module.web.capture_dispatch = self.provider.dispatch


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class WorkLiveEventsBrowserTests(unittest.TestCase):
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
        self.fixture = LiveEventFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.external_requests = []
        self.page_errors = []
        self.event_requests = []
        self.context.on("request", self._record_external_request)
        self.allow_chat_snapshots = False
        self.chat_snapshot_requests = []

    def tearDown(self):
        self.assertEqual(self.external_requests, [], "browser attempted external network")
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _record_external_request(self, request):
        if "/events?" in request.url:
            self.event_requests.append(request)
        if urlparse(request.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.external_requests.append(request.url)

    def _open_dashboard(self):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        capability = self.fixture.app.dashboard_capability
        page.goto(
            f"{self.fixture.base_url}/#capability={capability}",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        return page, capability

    def test_live_progress_uses_header_and_completion_refreshes_chat(self):
        self.fixture.provider.hold(PROMPT)
        page, capability = self._open_dashboard()

        def block_chat_snapshots(route):
            request = route.request
            if request.method == "GET" and re.fullmatch(
                r"/api/chats/[^/]+", urlparse(request.url).path,
            ):
                if not self.allow_chat_snapshots:
                    self.chat_snapshot_requests.append(request.url)
                    route.abort()
                    return
            route.continue_()

        page.route(re.compile(r".*/api/chats/[^/]+$"), block_chat_snapshots)
        page.get_by_role("textbox", name="Message").fill(PROMPT)
        with page.expect_request(lambda request: "/events?" in request.url, timeout=5_000) as stream:
            page.get_by_role("button", name="Send").click()
        request = stream.value
        self.assertEqual(
            request.headers.get("x-pilferedparrot-capability"), capability,
        )
        self.assertNotIn(capability, request.url)
        self.fixture.provider.wait_ready(PROMPT)

        # This unique event is emitted after the stream connects. Snapshot GETs
        # stay blocked until completion, so only the live event can reveal it.
        self.assertFalse(self.chat_snapshot_requests)
        self.assertFalse(page.get_by_text(LIVE_PROGRESS, exact=True).count())
        self.fixture.provider.emit_progress(PROMPT, LIVE_PROGRESS)
        expect(page.get_by_text(LIVE_PROGRESS, exact=True)).to_be_visible(timeout=5_000)
        expect(page.get_by_label("OpenAI Codex is working")).to_be_visible()

        self.allow_chat_snapshots = True
        self.fixture.provider.complete(PROMPT)
        expect(page.get_by_text(f"Fake provider completed: {PROMPT}", exact=True)) \
            .to_be_visible(timeout=5_000)
        self.assertEqual(len(self.event_requests), 1)

    def test_missing_event_route_stops_retries_and_polling_remains_live(self):
        self.fixture.provider.hold(PROMPT)
        page, capability = self._open_dashboard()
        chat_gets = []
        page.on("request", lambda request: chat_gets.append(request.url)
                if request.method == "GET"
                and re.fullmatch(r"/api/chats/[^/]+", urlparse(request.url).path) else None)

        def reject_event_route(route):
            route.fulfill(
                status=404, content_type="application/json",
                body='{"error":"live events unavailable"}',
            )

        page.route(re.compile(r".*/api/chats/[^/]+/events(?:\?.*)?$"), reject_event_route)
        page.get_by_role("textbox", name="Message").fill(PROMPT)
        with page.expect_request(lambda request: "/events?" in request.url, timeout=5_000) as stream:
            page.get_by_role("button", name="Send").click()
        request = stream.value
        self.assertEqual(request.headers.get("x-pilferedparrot-capability"), capability)
        self.assertNotIn(capability, request.url)
        self.fixture.provider.wait_ready(PROMPT)
        chat_gets.clear()

        expect(page.get_by_text("Preparing deterministic response", exact=True)) \
            .to_be_visible(timeout=5_000)
        self.assertTrue(chat_gets, "the 404 fallback should use full-chat polling")
        page.wait_for_timeout(1_100)
        statuses = [request.response().status for request in self.event_requests]
        self.assertEqual(
            len(self.event_requests), 1,
            f"a definitive 404 should stop SSE retries; response statuses: {statuses}",
        )

        self.fixture.provider.complete(PROMPT)
        expect(page.get_by_text(f"Fake provider completed: {PROMPT}", exact=True)) \
            .to_be_visible(timeout=5_000)
        self.assertGreaterEqual(len(chat_gets), 2, "polling should fetch the completion snapshot")

    def test_revoked_event_capability_stops_stream_and_polling(self):
        self.fixture.provider.hold(PROMPT)
        page, capability = self._open_dashboard()
        chat_gets = []
        page.on("request", lambda request: chat_gets.append(request.url)
                if request.method == "GET"
                and re.fullmatch(r"/api/chats/[^/]+", urlparse(request.url).path) else None)

        def revoke_event_access(route):
            route.fulfill(
                status=403, content_type="application/json",
                body='{"error":"dashboard authorization failed"}',
            )

        page.route(re.compile(r".*/api/chats/[^/]+/events(?:\?.*)?$"), revoke_event_access)
        page.get_by_role("textbox", name="Message").fill(PROMPT)
        with page.expect_request(lambda request: "/events?" in request.url, timeout=5_000) as stream:
            page.get_by_role("button", name="Send").click()
        request = stream.value
        self.assertEqual(request.headers.get("x-pilferedparrot-capability"), capability)
        self.assertNotIn(capability, request.url)
        self.fixture.provider.wait_ready(PROMPT)

        self.assertEqual(request.response().status, 403)
        expect(page.get_by_text(
            "This window's access expired. Reopen PilferedParrot to continue.", exact=True,
        )).to_be_visible(timeout=5_000)
        chat_gets.clear()
        page.wait_for_timeout(1_200)

        self.assertEqual(len(self.event_requests), 1, "revoked access must stop SSE retries")
        self.assertEqual(chat_gets, [], "revoked access must not fall back to full-chat polling")
        with self.fixture.app.runs_lock:
            self.assertTrue(self.fixture.app.runs, "the fake provider should still be held pending")

    def test_background_pending_summary_completing_before_hydration_notifies(self):
        self.fixture.app.store.set_notification_permission("granted")
        pending = self.fixture.app.create_chat(
            {"cwd": str(self.fixture.project), "provider": "codex", "model": "fake-small"},
            window_id="main", window_provider="codex",
        )
        self.fixture.provider.hold(PROMPT)
        self.fixture.app.send_message(
            pending["id"],
            {"content": PROMPT, "provider": "codex", "model": "fake-small",
             "request_id": "preseed-live-event-run"},
            window_id="main", window_provider="codex",
        )
        self.fixture.provider.wait_ready(PROMPT)
        self.fixture.app.create_chat(
            {"cwd": str(self.fixture.project), "provider": "codex", "model": "fake-small"},
            window_id="main", window_provider="codex",
        )

        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.add_init_script("""
          window.__ppNotifications = [];
          window.Notification = class {
            static permission = "granted";
            constructor(title, options = {}) {
              window.__ppNotifications.push({title, body: options.body, tag: options.tag});
            }
          };
        """)

        def finish_before_snapshot(route):
            if route.request.method == "GET" and urlparse(route.request.url).path == \
                    f"/api/chats/{pending['id']}":
                self.fixture.provider.complete(PROMPT)
                deadline = time.monotonic() + 5
                while any(
                    message.get("pending")
                    for message in self.fixture.app.chat_state(
                        pending["id"], window_id="main",
                    ).get("messages", [])
                ):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("fake provider did not complete before chat hydration")
                    time.sleep(0.01)
            route.continue_()

        page.route(re.compile(r".*/api/chats/[^/]+$"), finish_before_snapshot)
        page.goto(
            f"{self.fixture.base_url}/#capability={self.fixture.app.dashboard_capability}",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name="Message")).to_be_enabled(timeout=5_000)
        page.wait_for_function(
            "window.__ppNotifications.some(item => item.body.includes('Held work event stream response'))",
            timeout=5_000,
        )
        notifications = page.evaluate("window.__ppNotifications")
        self.assertEqual(len(notifications), 1, notifications)
        self.assertIn("Held work event stream response", notifications[0]["body"])


if __name__ == "__main__":
    unittest.main()
