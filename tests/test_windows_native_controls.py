"""Check Chromium's themed native frame in a real Windows app window."""

from __future__ import annotations

import ctypes
import json
import os
import secrets
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
from pilferedparrot.web_native import chromium_browser


@unittest.skipUnless(sys.platform == "win32" and sync_playwright, "Windows and Playwright required")
class WindowsNativeControlsTests(unittest.TestCase):
    def _wait(self, predicate, message):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        self.fail(message)

    def test_chrome_frame_uses_selected_theme_without_duplicate_html_caption(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.GetPixel.restype = wintypes.DWORD
        for name in ("IsWindow", "IsZoomed", "IsIconic", "SetForegroundWindow"):
            function = getattr(user32, name)
            function.argtypes = [wintypes.HWND]
            function.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL

        fixture = PilferedParrotBrowserFixture()
        self.addCleanup(fixture.stop)
        color = [197, 43, 127]
        fixture.app.browser_theme = lambda: {
            "active": True, "id": "a" * 32, "version": "1.0", "background": False,
            "colors": {"frame": "#c52b7f", "toolbar": "#c52b7f", "ntp_background": "#ffffff"},
        }
        temporary = tempfile.TemporaryDirectory(prefix="ppi-native-window-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        theme = root / "theme"
        theme.mkdir()
        (theme / "manifest.json").write_text(json.dumps({
            "manifest_version": 2, "name": "PilferedParrot native frame test", "version": "1.0",
            "theme": {"colors": {"frame": color, "frame_inactive": color, "toolbar": color,
                                   "ntp_background": [255, 255, 255], "ntp_text": [0, 0, 0]}},
        }), encoding="utf-8")
        with sync_playwright() as playwright:
            # Chromium can install the deterministic fixture unpacked. Branded
            # Chrome no longer accepts --load-extension, but can read the theme
            # already installed in this disposable profile.
            setup = playwright.chromium.launch_persistent_context(
                str(root / "profile"), headless=False, timeout=15000,
                args=[f"--disable-extensions-except={theme}", f"--load-extension={theme}"],
            )
            try:
                self._wait(lambda: (theme / "Cached Theme.pak").is_file(),
                           "Chromium did not install the fixture theme")
            finally:
                setup.close()
            installed_browser = chromium_browser()
            if installed_browser and "edge" in Path(installed_browser).name.lower():
                installed_browser = None
            context = playwright.chromium.launch_persistent_context(
                str(root / "profile"), headless=False, timeout=15000,
                executable_path=installed_browser or playwright.chromium.executable_path,
                args=["--window-size=900,700", "--window-position=40,40",
                      f"--app={fixture.browser_url}"],
            )
            try:
                page = next(page for page in context.pages if page.url.startswith(fixture.base_url))
                print("Native frame browser:", context.new_cdp_session(page).send("Browser.getVersion")["product"])
                expect(page.locator("#prompt")).to_be_enabled()
                expect(page.locator("#nativeTitlebar")).to_be_hidden()
                page.wait_for_function("document.body.dataset.chromeTheme")
                windows = []
                test_title = "PilferedParrot Test " + secrets.token_hex(16)
                page.evaluate("title => { document.title = title; }", test_title)

                @callback_type
                def collect(hwnd, _parameter):
                    title = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, title, len(title))
                    if title.value == test_title:
                        windows.append(hwnd)
                    return True

                def find_window():
                    windows.clear()
                    user32.EnumWindows(collect, 0)
                    return bool(windows)

                self._wait(find_window, "Chromium did not expose this test's native window")
                self.assertEqual(len(windows), 1, "Expected only this test's app window")
                hwnd = windows[0]
                user32.SetForegroundWindow(hwnd)
                self.assertTrue(user32.GetWindowLongPtrW(hwnd, -16) & 0x00C00000)
                expected_pixel = color[0] | color[1] << 8 | color[2] << 16
                observed = []

                def frame_is_themed():
                    rect = wintypes.RECT()
                    self.assertTrue(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
                    dc = user32.GetDC(None)
                    try:
                        observed[:] = [gdi32.GetPixel(dc, rect.left + (rect.right - rect.left) // 2,
                                                     rect.top + offset) for offset in (10, 15, 20, 25)]
                    finally:
                        user32.ReleaseDC(None, dc)
                    return expected_pixel in observed

                try:
                    self._wait(frame_is_themed, "Chromium's native caption did not use the installed theme")
                finally:
                    print("Native frame pixels:", observed, "expected:", expected_pixel)
                self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('.topbar')).backgroundColor"),
                                 "rgb(197, 43, 127)")
                user32.ShowWindow(hwnd, 3)
                self._wait(lambda: user32.IsZoomed(hwnd), "Native maximize did not work")
                user32.ShowWindow(hwnd, 9)
                self._wait(lambda: not user32.IsZoomed(hwnd), "Native restore did not work")
                user32.ShowWindow(hwnd, 6)
                self._wait(lambda: user32.IsIconic(hwnd), "Native minimize did not work")
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
                page.reload()
                expect(page.locator("#nativeTitlebar")).to_be_hidden()
                self.assertTrue(user32.GetWindowLongPtrW(hwnd, -16) & 0x00C00000)
                self._wait(frame_is_themed, "Reload lost the native frame theme")
            finally:
                context.close()
