from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.desktop_auth import provider_environment
from pilferedparrot.dispatch import RunCancelled, _stream_process


class DesktopAuthTests(unittest.TestCase):
    def _environment(self, root: Path, **extra: str) -> dict[str, str]:
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        return {
            "XDG_RUNTIME_DIR": str(runtime), "DISPLAY": ":0", "PATH": "/usr/bin:/bin", **extra,
        }

    def test_creates_private_helper_that_only_passes_zenity_output_to_sudo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool_directory = root / "tool path"
            tool_directory.mkdir()
            fake_zenity = tool_directory / "zenity"
            fake_zenity.write_text("#!/bin/sh\nprintf 'helper-output\\n'\n", encoding="utf-8")
            fake_zenity.chmod(0o755)
            environment = self._environment(root)
            with patch("pilferedparrot.desktop_auth.shutil.which", return_value=str(fake_zenity)):
                configured = provider_environment(environment)
                reused = provider_environment(environment)
            helper = Path(configured["SUDO_ASKPASS"])
            self.assertEqual(reused["SUDO_ASKPASS"], str(helper))
            self.assertEqual(helper.parent.name, "pilferedparrot")
            self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
            result = subprocess.run([str(helper), "sudo prompt"], text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout, "helper-output\n")
            self.assertEqual(result.stderr, "")

    def test_refuses_to_overwrite_an_existing_different_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(Path(directory))
            with patch("pilferedparrot.desktop_auth.shutil.which", return_value="/usr/bin/zenity"):
                first = Path(provider_environment(environment)["SUDO_ASKPASS"])
                first.write_text("sentinel", encoding="utf-8")
                second = provider_environment(environment)
            self.assertNotIn("SUDO_ASKPASS", second)
            self.assertEqual(first.read_text(encoding="utf-8"), "sentinel")

    def test_preserves_existing_configured_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = provider_environment(self._environment(Path(directory), SUDO_ASKPASS="/custom/askpass"))
        self.assertEqual(configured["SUDO_ASKPASS"], "/custom/askpass")

    def test_without_a_graphical_display_leaves_environment_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(Path(directory))
            environment.pop("DISPLAY")
            self.assertNotIn("SUDO_ASKPASS", provider_environment(environment))

    def test_non_linux_leaves_environment_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = self._environment(Path(directory))
            with patch("pilferedparrot.desktop_auth.sys.platform", "win32"):
                self.assertNotIn("SUDO_ASKPASS", provider_environment(environment))

    def test_rejects_an_existing_askpass_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = self._environment(root)
            with patch("pilferedparrot.desktop_auth.shutil.which", return_value="/usr/bin/zenity"), \
                 patch("pilferedparrot.desktop_auth.hashlib.sha256") as sha:
                sha.return_value.hexdigest.return_value = "a" * 64
                helper = root / "runtime" / "pilferedparrot" / "sudo-askpass-aaaaaaaaaaaaaaaa.py"
                helper.parent.mkdir(mode=0o700)
                helper.symlink_to("/tmp/not-an-askpass")
                self.assertNotIn("SUDO_ASKPASS", provider_environment(environment))

    def test_streamed_provider_receives_scoped_environment(self):
        environment = {"SUDO_ASKPASS": "/runtime/helper"}
        observed: list[str] = []
        command = [os.sys.executable, "-c", "import os; print(os.environ['SUDO_ASKPASS'])"]
        with patch("pilferedparrot.dispatch.provider_environment", return_value=environment):
            completed = _stream_process(command, "", Path.cwd(), cancel_event=None, stdout_line=observed.append)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(observed, ["/runtime/helper\n"])

    @unittest.skipUnless(sys.platform == "linux", "Linux process groups")
    def test_cancelling_provider_stops_its_generated_askpass_dialog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "askpass.pid"
            fake_zenity = root / "zenity"
            fake_zenity.write_text(
                "#!/bin/sh\necho $$ > \"$PPI_HELPER_PID_FILE\"\nwhile :; do sleep 1; done\n",
                encoding="utf-8",
            )
            fake_zenity.chmod(0o755)
            environment = self._environment(root, PPI_HELPER_PID_FILE=str(marker))
            with patch("pilferedparrot.desktop_auth.shutil.which", return_value=str(fake_zenity)):
                environment = provider_environment(environment)
            cancelled = threading.Event()
            command = [sys.executable, "-c", (
                "import os, subprocess; child = subprocess.Popen([os.environ['SUDO_ASKPASS'], 'prompt']); "
                "print('ready', flush=True); child.wait()"
            )]
            def cancel_after_dialog_starts(_line: str) -> None:
                deadline = time.monotonic() + 1
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                cancelled.set()
            with patch("pilferedparrot.dispatch.provider_environment", return_value=environment):
                with self.assertRaises(RunCancelled):
                    _stream_process(
                        command, "", root, cancel_event=cancelled,
                        stdout_line=cancel_after_dialog_starts,
                    )
            deadline = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            helper_pid = int(marker.read_text(encoding="utf-8").strip())
            while time.monotonic() < deadline:
                try:
                    os.kill(helper_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.01)
            with self.assertRaises(ProcessLookupError):
                os.kill(helper_pid, 0)


if __name__ == "__main__":
    unittest.main()
