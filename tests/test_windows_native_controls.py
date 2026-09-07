"""Exercise the real Chromium app frame and window controls on Windows."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes
from pathlib import Path

try:
    from playwright.sync_api import expect, sync_playwright
except ModuleNotFoundError:
    if os.environ.get("PILFEREDPARROT_REQUIRE_PLAYWRIGHT") == "1":
        raise
    expect = sync_playwright = None

from playwright_fixture import PilferedParrotBrowserFixture


@unittest.skipUnless(sys.platform == "win32" and sync_playwright, "Windows and Playwright required")
class WindowsNativeControlsTests(unittest.TestCase):
    def _wait(self, predicate, message):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        self.fail(message)

    def test_app_caption_is_replaced_and_controls_operate_on_real_window(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        for name in ("IsWindow", "IsZoomed", "IsIconic", "SetForegroundWindow"):
            function = getattr(user32, name)
            function.argtypes = [wintypes.HWND]
            function.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL

        fixture = PilferedParrotBrowserFixture()
        self.addCleanup(fixture.stop)
        temporary = tempfile.TemporaryDirectory(prefix="ppi-native-window-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        profile = Path(temporary.name)
        with sync_playwright() as playwright:
            process = subprocess.Popen([
                playwright.chromium.executable_path,
                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-background-mode", "--remote-debugging-port=0",
                "--window-size=900,700", f"--app={fixture.browser_url}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            browser = None
            try:
                port_file = profile / "DevToolsActivePort"
                self._wait(port_file.exists, "Chromium debugging endpoint did not start")
                port = port_file.read_text().splitlines()[0]
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                page = next(page for context in browser.contexts for page in context.pages
                            if page.url.startswith(fixture.base_url))
                expect(page.locator("#prompt")).to_be_enabled()
                expect(page.locator("#nativeTitlebar")).to_be_hidden()

                windows = []

                @callback_type
                def collect(hwnd, _parameter):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    title = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, title, len(title))
                    if pid.value == process.pid and title.value == "PilferedParrot":
                        windows.append(hwnd)
                    return True

                user32.EnumWindows(collect, 0)
                self.assertEqual(len(windows), 1, "Expected only this test's app window")
                hwnd = windows[0]
                before_gap = page.evaluate("window.outerHeight - window.innerHeight")
                self.assertTrue(user32.GetWindowLongPtrW(hwnd, -16) & 0x00C00000)
                user32.SetForegroundWindow(hwnd)
                page.goto(fixture.browser_url + "&native-window=1")
                expect(page.locator("#nativeTitlebar")).to_be_visible(timeout=15000)
                after_gap = page.evaluate("window.outerHeight - window.innerHeight")
                self.assertFalse(user32.GetWindowLongPtrW(hwnd, -16) & 0x00C00000)
                self.assertGreater(before_gap - after_gap, 8,
                                   f"Browser caption remained visible: {before_gap=} {after_gap=}")

                maximize = page.get_by_role("button", name="Maximize or restore window", exact=True)
                maximize.click()
                self._wait(lambda: user32.IsZoomed(hwnd), "Maximize button did not maximize")
                maximize.click()
                self._wait(lambda: not user32.IsZoomed(hwnd), "Restore button did not restore")
                page.get_by_role("button", name="Minimize window", exact=True).click()
                self._wait(lambda: user32.IsIconic(hwnd), "Minimize button did not minimize")
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
                self._wait(lambda: not user32.IsIconic(hwnd), "Window did not restore from taskbar")
                page.reload()
                expect(page.locator("#nativeTitlebar")).to_be_visible(timeout=15000)
                page.get_by_role("button", name="Close window", exact=True).click(no_wait_after=True)
                self._wait(lambda: not user32.IsWindow(hwnd), "Close button did not close")
            finally:
                if browser is not None:
                    browser.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    process.wait(timeout=10)

