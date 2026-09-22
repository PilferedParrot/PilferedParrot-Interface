"""Security regressions for the compatible agent's Git review tool."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot.qwen_tools import QwenToolbox


class DiffFallbackContainmentTests(unittest.TestCase):
    def test_baseline_replaced_by_outside_symlink_is_not_read(self) -> None:
        for parent_symlink in (False, True):
            with self.subTest(parent_symlink=parent_symlink), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                workspace = base / "workspace"
                outside = base / "outside"
                workspace.mkdir()
                outside.mkdir()
                (outside / "sample.txt").write_text("OUTSIDE_MARKER\n")
                path = "nested/sample.txt" if parent_symlink else "sample.txt"
                toolbox = QwenToolbox(workspace, {})
                toolbox.execute("write_file", {"path": path, "content": "inside\n"})
                (workspace / path).unlink()
                if parent_symlink:
                    (workspace / "nested").rmdir()
                    (workspace / "nested").symlink_to(outside, target_is_directory=True)
                else:
                    (workspace / path).symlink_to(outside / "sample.txt")
                with patch("pilferedparrot.qwen_tools.shutil.which", return_value=None):
                    with self.assertRaisesRegex(PermissionError, "path escapes workspace"):
                        toolbox.execute("diff", {})


class GitDiffSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("bwrap") is None:
            raise unittest.SkipTest("Bubblewrap is unavailable")
        probe = subprocess.run(
            ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-net",
             "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
             "git", "--version"],
            capture_output=True,
        )
        if probe.returncode:
            raise unittest.SkipTest("Bubblewrap cannot create a sandbox")

    def test_repo_commands_cannot_write_outside_or_read_external_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "workspace"
            root.mkdir()
            marker = parent / "ran-outside"
            workspace_marker = root / "ran-inside"
            outside_document = parent / "external-document.txt"
            outside_document.write_text("external-only-content\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            hook = root / "hook.sh"
            hook.write_text(
                "#!/bin/sh\n"
                f"printf executed > '{marker}'\n"
                f"printf executed > '{workspace_marker}'\n"
                f"cat '{outside_document}' 2>/dev/null\n"
                "cat\n"
            )
            hook.chmod(0o755)
            textconv = root / "textconv.sh"
            textconv.write_text("#!/bin/sh\necho TEXTCONV_EXECUTED\n")
            textconv.chmod(0o755)
            external_config = parent / "external-git-config"
            external_config.write_text(f"[core]\n\tfsmonitor = {hook}\n")
            (root / ".gitattributes").write_text("sample.txt filter=evil diff=evil\n")
            sample = root / "sample.txt"
            sample.write_text("original\n")
            subprocess.run(["git", "-C", str(root), "add", "sample.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "filter.evil.clean", str(hook)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "diff.evil.textconv", str(textconv)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "include.path", str(external_config)], check=True)
            marker.unlink(missing_ok=True)
            sample.write_text("changed\n")

            with patch.dict(os.environ, {
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_VALUE_0": str(hook),
            }):
                result = QwenToolbox(root, {"read_only": True}).execute("diff", {})

            self.assertIn("sample.txt", result)
            self.assertFalse(marker.exists())
            self.assertFalse(workspace_marker.exists())
            self.assertNotIn("external-only-content", result)
            self.assertNotIn("TEXTCONV_EXECUTED", result)

    def test_external_gitdir_is_unavailable_and_missing_bwrap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "workspace"
            gitdir = parent / "gitdir"
            subprocess.run(["git", "init", "-q", "--separate-git-dir", str(gitdir), str(root)], check=True)
            toolbox = QwenToolbox(root, {"read_only": True})
            self.assertIn("metadata is unavailable", toolbox.execute("diff", {}))
            with patch("pilferedparrot.qwen_tools.shutil.which", return_value=None):
                self.assertIn("requires Linux and Bubblewrap", toolbox.execute("diff", {}))


class ShellSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise unittest.SkipTest("Bubblewrap is unavailable")
        try:
            probe = subprocess.run(
                [bwrap, "--die-with-parent", "--new-session", "--unshare-pid",
                 "--unshare-net", "--ro-bind", "/", "/", "--dev", "/dev",
                 "--proc", "/proc", shutil.which("true") or "/bin/true"],
                capture_output=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("Bubblewrap cannot create a sandbox") from None
        if probe.returncode:
            raise unittest.SkipTest("Bubblewrap cannot create a sandbox")

    def test_runtime_tools_and_selected_roots_work_while_sibling_is_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "workspace"
            extra = parent / "extra"
            root.mkdir()
            extra.mkdir()
            hidden = parent / "unselected-document.txt"
            hidden.write_text("must remain hidden")
            toolbox = QwenToolbox(root, {"shell_network": False}, [extra])
            command = (
                "python3 -c 'from pathlib import Path; Path(\"output.txt\").write_text(\"python works\")'"
                f" && printf extra > '{extra / 'result.txt'}'"
                f" && test ! -e '{hidden}'"
                " && test -r /etc/hosts"
                " && git --version"
            )
            result = toolbox.execute("shell", {"command": command})
            self.assertIn("exit_code: 0", result)
            self.assertIn("git version", result)
            self.assertEqual((root / "output.txt").read_text(), "python works")
            self.assertEqual((extra / "result.txt").read_text(), "extra")
            self.assertEqual(hidden.read_text(), "must remain hidden")

    def test_network_is_unshared_and_missing_bubblewrap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            toolbox = QwenToolbox(Path(directory), {"shell_network": False})
            with patch("pilferedparrot.qwen_tools.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, "ok")
                toolbox.execute("shell", {"command": "true"})
                self.assertIn("--unshare-net", run.call_args.args[0])
            with patch("pilferedparrot.qwen_tools.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "bubblewrap is required"):
                    toolbox.execute("shell", {"command": "true"})


if __name__ == "__main__":
    unittest.main()
