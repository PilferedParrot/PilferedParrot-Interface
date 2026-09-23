"""Browser regression coverage for context telemetry across follow-up turns."""

from __future__ import annotations

import os
import threading
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
from pilferedparrot.dispatch import RunResult


CONTEXT_LIMIT = 654_000
FIRST_INPUT = 99_498
FIRST_OUTPUT = 814
UPDATED_INPUT = 200_000


class FollowupContextProvider(FakeProvider):
    """Provider whose follow-up telemetry can be advanced independently."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[str, object] = {}
        self._started: dict[str, threading.Event] = {}
        self._usage: dict[str, tuple[int, int]] = {}

    def hold(self, prompt: str) -> None:
        super().hold(prompt)
        with self._lock:
            self._started[prompt] = threading.Event()

    def emit(self, prompt: str, input_tokens: int, output_tokens: int = 0) -> None:
        with self._lock:
            started = self._started[prompt]
        if not started.wait(timeout=5):
            raise TimeoutError("browser request did not reach the fake provider")
        with self._lock:
            callback = self._callbacks[prompt]
            self._usage[prompt] = (input_tokens, output_tokens)
        callback({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "context_window_tokens": CONTEXT_LIMIT,
            "observed_at": 1_800_000_000,
        })

    def dispatch(self, provider, prompt, cwd, conversation, _config, cancel_event):
        assert provider == "codex"
        callback = getattr(cancel_event, "_pilferedparrot_usage")
        with self._lock:
            self.requests.append((provider, prompt, cwd))
            gate = self._gates.get(prompt)
            started = self._started.setdefault(prompt, threading.Event())
            self._callbacks[prompt] = callback
            started.set()

        if gate is None:
            self._usage[prompt] = (FIRST_INPUT, FIRST_OUTPUT)
            callback({
                "input_tokens": FIRST_INPUT,
                "output_tokens": FIRST_OUTPUT,
                "context_window_tokens": CONTEXT_LIMIT,
                "observed_at": 1_799_999_900,
            })
        elif not gate.wait(timeout=10):
            raise TimeoutError("browser test did not release the fake provider")

        with self._lock:
            input_tokens, output_tokens = self._usage.get(
                prompt, (FIRST_INPUT, FIRST_OUTPUT),
            )
        return RunResult(
            f"Fake provider completed: {prompt}", 0,
            session_id="fake-context-followup-session",
            input_tokens=input_tokens, output_tokens=output_tokens,
            live_input_tokens=input_tokens, live_output_tokens=output_tokens,
            live_context_window_tokens=CONTEXT_LIMIT,
        )


class FollowupContextFixture(PilferedParrotBrowserFixture):
    def __init__(self) -> None:
        super().__init__()
        self.provider = FollowupContextProvider()
        fixture_module.web.capture_dispatch = self.provider.dispatch

    def _config(self):
        config = super()._config()
        config["codex"]["context_window_tokens"] = CONTEXT_LIMIT
        config["codex"]["context_window_percent"] = 100
        return config


@unittest.skipUnless(sync_playwright, "install requirements-browser.txt to run Playwright")
class ContextFollowupBrowserEndToEndTests(unittest.TestCase):
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
        self.fixture = FollowupContextFixture()
        self.addCleanup(self.fixture.stop)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.external_requests = []
        self.page_errors = []
        self.context.on("request", self._record_external_request)

    def tearDown(self):
        self.assertEqual(self.external_requests, [], "browser fixture attempted external network")
        self.assertEqual(self.page_errors, [], "browser emitted an unhandled JavaScript error")

    def _record_external_request(self, request):
        if urlparse(request.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.external_requests.append(request.url)

    def _page(self, path: str, capability: str, textbox: str):
        page = self.context.new_page()
        page.on("pageerror", lambda error: self.page_errors.append(error))
        page.goto(
            f"{self.fixture.base_url}{path}#capability={capability}&provider=codex",
            wait_until="domcontentloaded",
        )
        expect(page.get_by_role("textbox", name=textbox)).to_be_enabled(timeout=5_000)
        return page

    def _watch_meter(self, page, container: str) -> None:
        page.evaluate(
            """container => {
                const root = document.querySelector(container);
                const meterSelector = ".context-pie-center strong";
                const values = [];
                const append = node => {
                    if (!(node instanceof Element)) return;
                    const meters = node.matches(meterSelector)
                      ? [node] : [...node.querySelectorAll(meterSelector)];
                    meters.forEach(meter => values.push(meter.textContent.trim()));
                };
                const capture = records => {
                    records.forEach(record => {
                        if (record.type === "characterData") {
                            values.push(record.target.textContent.trim());
                        }
                        record.addedNodes.forEach(append);
                    });
                    const current = root.querySelector(meterSelector);
                    if (current) values.push(current.textContent.trim());
                };
                append(root);
                const observer = new MutationObserver(capture);
                observer.observe(root, {childList: true, subtree: true, characterData: true});
                globalThis.__contextFollowupMeterWatch = {capture, observer, values};
            }""",
            container,
        )

    def _stop_meter_watch(self, page) -> list[str]:
        values = page.evaluate(
            """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
                const watch = globalThis.__contextFollowupMeterWatch;
                watch.capture(watch.observer.takeRecords());
                watch.observer.disconnect();
                delete globalThis.__contextFollowupMeterWatch;
                resolve(watch.values);
            })))""",
        )
        percentages = [value for value in values if value.endswith("%")]
        self.assertGreater(len(percentages), 1, percentages)
        self.assertEqual(set(percentages), {"15%"}, percentages)
        return percentages

    def test_work_followup_retains_context_until_new_telemetry_arrives(self):
        page = self._page("/", self.fixture.app.dashboard_capability, "Message")
        meter = page.locator("#technicalContext .context-pie-center strong")

        first = "First work turn with nonzero context"
        page.get_by_role("textbox", name="Message").fill(first)
        page.get_by_role("button", name="Send").click()
        expect(page.get_by_text(f"Fake provider completed: {first}", exact=True)).to_be_visible(
            timeout=5_000,
        )
        expect(meter).to_have_text("15%")

        followup = "Held work follow-up"
        self.fixture.provider.hold(followup)
        page.get_by_role("textbox", name="Message").fill(followup)
        self._watch_meter(page, "#technicalContext")
        with page.expect_response("**/api/chats/*/messages") as response:
            page.get_by_role("button", name="Send").click()
        self.assertTrue(response.value.ok)
        expect(page.get_by_label("OpenAI Codex is working")).to_be_visible()
        expect(meter).to_have_text("15%")
        self._stop_meter_watch(page)

        self.fixture.provider.emit(followup, UPDATED_INPUT)
        expect(meter).to_have_text("31%", timeout=5_000)
        self.fixture.provider.complete(followup)
        expect(page.get_by_text(f"Fake provider completed: {followup}", exact=True)).to_be_visible(
            timeout=5_000,
        )
        expect(meter).to_have_text("31%")

    def test_chat_followup_retains_context_until_new_telemetry_arrives(self):
        self.fixture.app.reset_chat({"model": "fake-small", "provider": "codex"})
        capability = self.fixture.app.issue_capability("chat", provider="codex")
        page = self._page("/chat", capability, "Message Chat")
        meter = page.locator("#chatContext .context-pie-center strong")

        first = "First chat turn with nonzero context"
        page.get_by_role("textbox", name="Message Chat").fill(first)
        page.get_by_role("button", name="Send to Chat").click()
        expect(page.locator("article.chat-message.assistant").last).to_contain_text(
            first, timeout=5_000,
        )
        expect(meter).to_have_text("15%")

        followup = "Held chat follow-up"
        self.fixture.provider.hold(followup)
        page.get_by_role("textbox", name="Message Chat").fill(followup)
        self._watch_meter(page, "#chatContext")
        with page.expect_response("**/api/chat/messages") as response:
            page.get_by_role("button", name="Send to Chat").click()
        self.assertTrue(response.value.ok)
        expect(page.get_by_label("Chat is working")).to_be_visible()
        expect(meter).to_have_text("15%")
        self._stop_meter_watch(page)

        self.fixture.provider.emit(followup, UPDATED_INPUT)
        expect(meter).to_have_text("31%", timeout=5_000)
        self.fixture.provider.complete(followup)
        expect(page.locator("article.chat-message.assistant").last).to_contain_text(
            followup, timeout=5_000,
        )
        expect(meter).to_have_text("31%")


if __name__ == "__main__":
    unittest.main()
