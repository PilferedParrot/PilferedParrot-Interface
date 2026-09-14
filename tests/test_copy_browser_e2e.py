"""Browser coverage for Markdown code copy controls."""

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
class CopyBrowserEndToEndTests(unittest.TestCase):
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
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 760})
        self.addCleanup(self.context.close)
        self.context.add_init_script("""() => {
            Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: () => Promise.resolve()}});
        }""")
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(error))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def _load(self, kind, width=1280):
        self.page.goto("about:blank")
        self.page.set_viewport_size({"width": width, "height": 760})
        provider = "codex"
        if kind == "work":
            capability = self.fixture.app.issue_capability("dashboard", window_id="main", provider=provider)
            path = "/"
        else:
            capability = self.fixture.app.issue_capability("chat", provider=provider)
            path = "/chat"
        self.page.goto(f"{self.fixture.base_url}{path}#capability={capability}&provider={provider}", wait_until="domcontentloaded")
        expect(self.page.get_by_role("textbox", name="Message" if kind == "work" else "Message Chat")).to_be_enabled(timeout=5_000)
        return "#messages" if kind == "work" else "#chatMessages"

    def _add(self, root, markdown, kind, runnable=False):
        classes = ("message", "message-content") if kind == "work" else ("chat-message", "chat-message-body")
        self.page.locator(root).evaluate("""(root, args) => {
            const message = document.createElement('article');
            message.className = args.classes[0];
            const options = args.runnable
              ? {commandTarget: {messageId: 'copy-test'}, shellLanguages: new Set(['bash'])}
              : undefined;
            message.innerHTML = '<div class="' + args.classes[1] + '">' +
              PilferedParrotMarkdown.render(args.markdown, options) + '</div>';
            root.append(message);
        }""", {"classes": classes, "markdown": markdown, "runnable": runnable})
        return self.page.locator("[data-copy-code]").last

    def _clipboard_recorder(self):
        self.page.evaluate("""() => {
            window.copyWrites = [];
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                writeText(value) { window.copyWrites.push(value); return Promise.resolve(); }
            }});
        }""")

    def test_native_copy_preserves_exact_unicode_whitespace_in_work_and_chat(self):
        expected = "  α🙂  \n\n\t終  "
        source = "```text\n" + expected + "\n```"
        for kind in ("work", "chat"):
            with self.subTest(kind=kind):
                root = self._load(kind)
                self._clipboard_recorder()
                button = self._add(root, source, kind)
                button.click()
                expect(button).to_have_text("Copied")
                self.assertEqual(self.page.evaluate("copyWrites"), [expected])
                self.page.wait_for_timeout(1_500)
                expect(button).to_have_text("Copy")

    def test_repeated_clicks_restore_copy_and_older_completion_cannot_replace_new_feedback(self):
        root = self._load("work")
        button = self._add(root, "```text\nvalue\n```", "work")
        self.page.evaluate("""() => {
            window.copyDeferred = [];
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                writeText() { return new Promise((resolve, reject) => copyDeferred.push({resolve, reject})); }
            }});
        }""")
        button.click()
        button.click()
        self.page.wait_for_function("() => copyDeferred.length === 2")
        self.page.evaluate("() => copyDeferred[1].resolve()")
        expect(button).to_have_text("Copied")
        self.page.evaluate("() => copyDeferred[0].reject(new Error('late failure'))")
        self.page.wait_for_timeout(100)
        expect(button).to_have_text("Copied")
        self.page.wait_for_timeout(1_500)
        expect(button).to_have_text("Copy")

    def test_native_and_fallback_failures_show_error_then_restore_copy(self):
        root = self._load("work")
        button = self._add(root, "```text\nvalue\n```", "work")
        self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                writeText() { return Promise.reject(new Error('native denied')); }
            }});
            document.execCommand = () => false;
        }""")
        button.click()
        expect(button).to_have_text("Copy failed")
        expect(button).to_have_attribute("data-copy-state", "error")
        self.page.wait_for_timeout(1_500)
        expect(button).to_have_text("Copy")

        self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
            document.execCommand = () => { throw new Error('exec denied'); };
        }""")
        button.click()
        expect(button).to_have_text("Copy failed")
        self.page.wait_for_timeout(1_500)
        expect(button).to_have_text("Copy")

    def test_fallback_inside_expanded_dialog_selects_text_and_restores_focus_on_throw(self):
        root = self._load("chat", 390)
        expected = "  modal α🙂  \n\n" + "x" * 1200 + "  \nlast  "
        self._add(root, "```text\n" + expected + "\n```", "chat")
        expand = self.page.locator(".markdown-expand-button")
        expect(expand).to_be_visible()
        self.page.evaluate("""() => Object.defineProperty(document.documentElement, 'requestFullscreen', {
            configurable: true, value: () => Promise.reject(new Error('fullscreen blocked'))
        })""")
        expand.click()
        dialog = self.page.locator("#markdownExpansionDialog")
        expect(dialog).to_be_visible()
        button = dialog.locator("[data-copy-code]")
        self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
            window.fallbackCheck = null;
            document.execCommand = command => {
                const active = document.activeElement;
                window.fallbackCheck = {
                    command, value: active && active.value, start: active && active.selectionStart,
                    end: active && active.selectionEnd,
                    inDialog: Boolean(active && active.closest('dialog[open]')),
                };
                throw new Error('copy refused');
            };
        }""")
        button.click()
        expect(button).to_have_text("Copy failed")
        self.assertEqual(self.page.evaluate("fallbackCheck"), {
            "command": "copy", "value": expected, "start": 0,
            "end": len(expected.encode("utf-16-le")) // 2, "inDialog": True,
        })
        expect(button).to_be_focused()

    def test_pending_native_copy_does_not_steal_user_focus(self):
        root = self._load("work")
        button = self._add(root, "```text\nvalue\n```", "work")
        self.page.evaluate("""() => {
            window.pendingCopy = null;
            Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
                writeText() { return new Promise(resolve => { pendingCopy = resolve; }); }
            }});
            const input = document.createElement('input'); input.id = 'focus-target'; document.body.append(input);
        }""")
        button.click()
        self.page.wait_for_function("() => Boolean(pendingCopy)")
        self.page.locator("#focus-target").focus()
        self.page.evaluate("() => pendingCopy()")
        expect(button).to_have_text("Copied")
        expect(self.page.locator("#focus-target")).to_be_focused()

    def test_copy_and_play_hit_rectangles_are_separate_on_desktop_and_mobile(self):
        for width in (1280, 390):
            with self.subTest(width=width):
                root = self._load("work", width)
                self._add(root, "```bash\necho copied\n```", "work", runnable=True)
                copy = self.page.locator("[data-copy-code]").last
                play = self.page.locator("[data-run-command]").last
                expect(copy).to_be_visible()
                expect(play).to_be_visible()
                boxes = self.page.evaluate("""() => {
                    const copy = document.querySelector('[data-copy-code]');
                    const play = document.querySelector('[data-run-command]');
                    const box = node => { const r = node.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
                    const firstLine = () => {
                        const text = copy.closest('.code-block').querySelector('code').firstChild;
                        const range = document.createRange(); range.selectNodeContents(text);
                        return range.getBoundingClientRect().top;
                    };
                    return ['Copy', 'Copied', 'Copy failed'].map(label => {
                        copy.textContent = label;
                        return {label, copy: box(copy), play: box(play), firstLine: firstLine()};
                    });
                }""")
                for state in boxes:
                    copy_box, play_box = state["copy"], state["play"]
                    overlap = (copy_box[0] < play_box[2] and copy_box[2] > play_box[0]
                               and copy_box[1] < play_box[3] and copy_box[3] > play_box[1])
                    self.assertFalse(overlap, state)
                    self.assertEqual(copy_box[2] - copy_box[0], 96, state)
                    self.assertGreaterEqual(state["firstLine"], max(copy_box[3], play_box[3]), state)
                    if width == 390:
                        self.assertEqual(copy_box[3] - copy_box[1], 44, state)
                        self.assertEqual(play_box[2] - play_box[0], 44, state)

    def test_expanded_copy_does_not_inherit_transient_source_feedback(self):
        root = self._load("work", 390)
        source = "```text\n" + "x" * 1200 + "\n```"
        button = self._add(root, source, "work")
        button.click()
        expect(button).to_have_text("Copied")
        expand = self.page.locator(".markdown-expand-button")
        expect(expand).to_be_visible()
        self.page.evaluate("""() => Object.defineProperty(document.documentElement, 'requestFullscreen', {
            configurable: true, value: () => Promise.reject(new Error('fullscreen blocked'))
        })""")
        expand.click()
        dialog = self.page.locator("#markdownExpansionDialog")
        expect(dialog).to_be_visible()
        copied = dialog.locator("[data-copy-code]")
        expect(copied).to_have_text("Copy")
        self.assertIsNone(copied.get_attribute("data-copy-state"))
        copied.click()
        expect(copied).to_have_text("Copied")
        self.page.wait_for_timeout(1_500)
        expect(copied).to_have_text("Copy")


if __name__ == "__main__":
    unittest.main()
