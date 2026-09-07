"""Focused tests for native integration without opening real GUI resources."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from pilferedparrot.web_native import (
    CHROME_THEME_GALLERY_URL, NativeIntegration, browser_url, chromium_browser,
    chrome_theme_version, notify_window_closed, open_browser, persistent_browser_profile,
    native_app_url, select_project_directory, selected_chrome_theme,
)


class BrowserIntegrationTests(unittest.TestCase):
    def test_native_marker_is_added_only_to_app_window_url(self):
        with patch("pilferedparrot.web_native.WINDOWS", False):
            self.assertEqual(
                native_app_url("http://127.0.0.1/#capability=secret"),
                "http://127.0.0.1/#capability=secret&native-window=1",
            )
        with patch("pilferedparrot.web_native.WINDOWS", True):
            self.assertEqual(
                native_app_url("http://127.0.0.1/#capability=secret"),
                "http://127.0.0.1/#capability=secret",
            )

    @patch("pilferedparrot.web_native.shutil.which")
    def test_chromium_discovery_preserves_launcher_preference_order(self, which):
        which.side_effect = lambda candidate: (
            "/usr/bin/chromium" if candidate == "chromium" else None
        )

        self.assertEqual(chromium_browser(), "/usr/bin/chromium")
        self.assertEqual(which.call_args_list, [
            call("google-chrome-stable"), call("google-chrome"), call("chromium"),
        ])

    @patch("pilferedparrot.web_native.webbrowser.open", return_value=True)
    def test_default_browser_open_is_a_mockable_boundary(self, browser_open):
        self.assertTrue(open_browser("http://127.0.0.1:8765/"))
        browser_open.assert_called_once_with("http://127.0.0.1:8765/")

    def test_browser_url_keeps_capability_in_fragment(self):
        self.assertEqual(
            browser_url(
                "http://127.0.0.1:8765", "secret", api_generation=20,
                asset_version="assets", runtime_version="runtime",
            ),
            "http://127.0.0.1:8765/?generation=20&assets=assets"
            "&runtime=runtime#capability=secret",
        )

    def test_close_notification_uses_injected_http_dependency(self):
        response = MagicMock(status=202)
        response.__enter__.return_value = response
        opener = MagicMock(return_value=response)

        self.assertTrue(notify_window_closed(
            "http://127.0.0.1:8765/#capability=secret", opener=opener,
        ))

        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8765/api/window/close")
        self.assertEqual(request.data, b'{"window_id":"main"}')
        self.assertEqual(request.get_header("X-pilferedparrot-capability"), "secret")

    def test_close_notification_refuses_non_loopback_destination(self):
        opener = MagicMock()
        self.assertFalse(notify_window_closed(
            "https://example.com/#capability=secret", opener=opener,
        ))
        opener.assert_not_called()


class NativeChooserTests(unittest.TestCase):
    @patch("pilferedparrot.web_native._active_x11_window", return_value="42")
    @patch("pilferedparrot.web_native.subprocess.Popen")
    @patch("pilferedparrot.web_native.shutil.which")
    def test_kdialog_selection_is_attached_and_normalized(self, which, popen, _active):
        which.side_effect = lambda candidate: (
            "/usr/bin/kdialog" if candidate == "kdialog" else None
        )
        process = MagicMock(pid=7, returncode=0)
        process.communicate.return_value = ("/chosen/project\n", "")
        popen.return_value = process
        normalize = MagicMock(return_value=Path("/chosen/project"))

        selected = select_project_directory("/missing", normalize=normalize)

        self.assertEqual(selected, Path("/chosen/project"))
        self.assertEqual(popen.call_args.args[0][:3], [
            "/usr/bin/kdialog", "--attach", "42",
        ])
        normalize.assert_called_once_with("/chosen/project")

    @patch("pilferedparrot.web_native._active_x11_window", return_value=None)
    @patch("pilferedparrot.web_native.shutil.which", return_value=None)
    def test_missing_chooser_keeps_manual_entry_fallback(self, _which, _active):
        with self.assertRaisesRegex(RuntimeError, "enter the project folder path manually"):
            select_project_directory(None, normalize=Path)


class NativeWindowManagerTests(unittest.TestCase):
    @patch("pilferedparrot.web_native.secrets.token_hex", return_value="a" * 32)
    def test_native_binding_uses_server_marker_and_capability_window(self, _token):
        manager = NativeIntegration(MagicMock())
        adapter = MagicMock()
        window = MagicMock()
        adapter.bind_active_window.return_value = window
        manager.native_window_adapter = adapter

        prepared = manager.native_window_action("provider-codex", {"action": "prepare"})
        self.assertEqual(prepared, {
            "ok": True, "marker": "PilferedParrot Native " + "a" * 32,
        })
        self.assertEqual(
            manager.native_window_action("provider-codex", {"action": "bind"}),
            {"ok": True, "supported": True},
        )
        adapter.bind_active_window.assert_called_once_with(prepared["marker"])
        self.assertIs(manager.native_windows["provider-codex"], window)

        self.assertEqual(
            manager.native_window_action("provider-codex", {"action": "maximize"}),
            {"ok": True, "supported": True},
        )
        window.maximize_or_restore.assert_called_once_with()
        self.assertEqual(
            manager.native_window_action(
                "provider-codex", {"action": "begin-resize", "direction": "east"},
            ),
            {"ok": True, "supported": True},
        )
        window.begin_resize.assert_called_once_with("east")
        manager.native_window_action("provider-codex", {"action": "update-geometry"})
        manager.native_window_action("provider-codex", {"action": "end-geometry"})
        window.update_geometry.assert_called_once_with()
        window.end_geometry.assert_called_once_with()

    def test_native_actions_require_prepare_binding_and_allowlisted_data(self):
        manager = NativeIntegration(MagicMock())
        with self.assertRaisesRegex(ValueError, "action"):
            manager.native_window_action("main", {"action": "launch-command"})
        with self.assertRaisesRegex(ValueError, "fields"):
            manager.native_window_action(
                "main", {"action": "prepare", "handle": "another-window"},
            )
        with self.assertRaisesRegex(ValueError, "prepared"):
            manager.native_window_action("main", {"action": "bind"})
        self.assertEqual(
            manager.native_window_action("main", {"action": "minimize"}),
            {"ok": False, "supported": False},
        )
        manager.native_windows["main"] = MagicMock()
        with self.assertRaisesRegex(ValueError, "resize direction"):
            manager.native_window_action(
                "main", {"action": "begin-resize", "direction": ["north"]},
            )

    def test_failed_native_close_keeps_the_verified_binding(self):
        manager = NativeIntegration(MagicMock())
        window = MagicMock()
        window.close.return_value = False
        manager.native_windows["main"] = window

        self.assertEqual(
            manager.native_window_action("main", {"action": "close"}),
            {"ok": False, "supported": True},
        )
        self.assertIs(manager.native_windows["main"], window)

    @patch("pilferedparrot.web_native.threading.Thread")
    @patch("pilferedparrot.web_native.subprocess.Popen")
    @patch("pilferedparrot.web_native.tempfile.mkdtemp", return_value="/tmp/provider-profile")
    def test_provider_window_launch_isolated_behind_facade(self, _mkdtemp, popen, thread):
        manager = NativeIntegration(MagicMock())
        issue = MagicMock(return_value="provider-secret")

        result = manager.open_provider_window(
            "http://127.0.0.1:8765/", provider="claude", model="opus", cwd=None,
            payload={"width": 1000, "height": 700, "left": 10, "top": 20},
            issue_capability=issue, browser="/usr/bin/chromium",
        )

        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/chromium")
        self.assertIn("--user-data-dir=/tmp/provider-profile", command)
        self.assertIn("--start-maximized", command)
        self.assertIn("&pick=1", next(item for item in command if item.startswith("--app=")))
        issue.assert_called_once()
        self.assertIn(result["launch_id"], manager.provider_windows)
        thread.return_value.start.assert_called_once_with()

    @patch("pilferedparrot.web_native.shutil.rmtree")
    @patch("pilferedparrot.web_native.subprocess.Popen", side_effect=OSError("no display"))
    @patch("pilferedparrot.web_native.tempfile.mkdtemp", return_value="/tmp/provider-profile")
    def test_provider_launch_failure_revokes_and_cleans_up(self, _mkdtemp, _popen, rmtree):
        revoke = MagicMock()
        manager = NativeIntegration(revoke)
        issue = MagicMock(return_value="provider-secret")

        with self.assertRaisesRegex(OSError, "no display"):
            manager.open_provider_window(
                "http://127.0.0.1:8765/", provider="claude", model=None,
                cwd=Path("/project"),
                payload={"width": 1000, "height": 700, "left": 10, "top": 20},
                issue_capability=issue, browser="/usr/bin/chromium",
            )

        revoke.assert_called_once_with("provider-secret")
        rmtree.assert_called_once_with(Path("/tmp/provider-profile"), ignore_errors=True)

    @patch("pilferedparrot.web_native.subprocess.run")
    @patch("pilferedparrot.web_native.shutil.which", return_value="/usr/bin/wmctrl")
    def test_existing_chat_window_is_focused_without_new_process(self, _which, run):
        manager = NativeIntegration(MagicMock())
        process = MagicMock()
        process.poll.return_value = None
        manager.chat_window_process = process
        manager.chat_window_provider = "codex"
        issue = MagicMock()

        result = manager.open_chat_window(
            "http://127.0.0.1:8765/chat", provider="codex", model="gpt-test",
            payload={"width": 900, "height": 600, "left": 0, "top": 0},
            issue_capability=issue, browser="/usr/bin/chromium",
        )

        self.assertEqual(result, {"ok": True, "existing": True})
        issue.assert_not_called()
        run.assert_called_once_with(
            ["/usr/bin/wmctrl", "-x", "-a", "pilferedparrot-chat"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


class BrowserThemeTests(unittest.TestCase):
    def test_profile_path_preserves_xdg_state_home_contract(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/state/root"}):
            self.assertEqual(
                persistent_browser_profile(), Path("/state/root/pilferedparrot/chrome-profile"),
            )

    def test_malformed_preferences_degrades_to_inactive_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            preferences = Path(directory) / "pilferedparrot/chrome-profile/Default/Preferences"
            preferences.parent.mkdir(parents=True)
            preferences.write_text("{broken", encoding="utf-8")
            with patch.dict(os.environ, {"XDG_STATE_HOME": directory}):
                self.assertEqual(selected_chrome_theme(), ({"active": False}, None))

    def test_theme_artwork_is_reported_and_kept_inside_extension_pack(self):
        theme_id = "abcdefghijklmnopabcdefghijklmnop"
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "pilferedparrot/chrome-profile"
            pack = profile / "Default/Extensions" / theme_id / "1.0.0"
            pack.mkdir(parents=True)
            for name in ("frame.png", "toolbar.png", "background.png", "attribution.png"):
                (pack / name).write_bytes(b"image")
            (pack / "manifest.json").write_text(json.dumps({
                "name": "Artwork theme", "version": "1.0.0",
                "theme": {
                    "colors": {"frame": [1, 2, 3], "toolbar": [4, 5, 6]},
                    "images": {
                        "theme_frame": {"100": "frame.png"}, "theme_toolbar": "toolbar.png",
                        "theme_ntp_background": "background.png",
                        "theme_ntp_attribution": "attribution.png",
                    },
                    "properties": {"ntp_background_alignment": "right bottom", "ntp_background_repeat": "repeat-x"},
                },
            }), encoding="utf-8")
            preferences = profile / "Default/Preferences"
            preferences.write_text(json.dumps({"extensions": {"theme": {
                "id": theme_id, "pack": str(pack),
            }}}), encoding="utf-8")
            with patch.dict(os.environ, {"XDG_STATE_HOME": directory}):
                theme, background = selected_chrome_theme()
                self.assertEqual(theme["frame_url"].split("?")[0], "/api/browser/theme/image/theme_frame")
                self.assertEqual(theme["toolbar_url"].split("?")[0], "/api/browser/theme/image/theme_toolbar")
                self.assertEqual(theme["attribution_url"].split("?")[0], "/api/browser/theme/image/theme_ntp_attribution")
                self.assertEqual(theme["background_alignment"], "right bottom")
                self.assertEqual(theme["background_repeat"], "repeat-x")
                self.assertEqual(background, pack / "background.png")
                version = chrome_theme_version(theme)
                self.assertEqual(
                    NativeIntegration.chrome_theme_image(
                        "theme_frame", theme_version=version,
                    ),
                    (b"image", "image/png"),
                )
                self.assertIsNone(NativeIntegration.chrome_theme_image(
                    "theme_frame", theme_version=f"{theme['id']}-stale",
                ))

    def test_theme_image_rejects_unallowlisted_key(self):
        self.assertIsNone(NativeIntegration.chrome_theme_image("../../Preferences"))

    @patch("pilferedparrot.web_native.subprocess.Popen")
    def test_theme_gallery_uses_persistent_profile_without_real_browser(self, popen):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"XDG_STATE_HOME": directory}):
            manager = NativeIntegration(MagicMock())
            self.assertEqual(
                manager.open_theme_gallery(browser="/usr/bin/chromium"), {"ok": True},
            )
            profile = Path(directory) / "pilferedparrot/chrome-profile"

        command = popen.call_args.args[0]
        self.assertIn(f"--user-data-dir={profile}", command)
        self.assertEqual(command[-1], CHROME_THEME_GALLERY_URL)


if __name__ == "__main__":
    unittest.main()
