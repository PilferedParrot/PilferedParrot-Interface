"""Pinned ACP adapter installation without provider calls or global npm writes."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import acp_adapters
from pilferedparrot.acp_adapters import AdapterInstallError, AdapterManager


FAKE_NODE = """#!/usr/bin/env python3
import sys
from pathlib import Path
if sys.argv[1:] == ['--version']:
    print('v22.1.0')
elif len(sys.argv) == 3 and sys.argv[1] == '--check' and Path(sys.argv[2]).is_file():
    pass
else:
    sys.exit(2)
"""

FAKE_NPM = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
root = Path.cwd()
(root / 'fake-npm-invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'cwd': str(root)}))
if os.environ.get('PPI_FAKE_NPM_FAIL') == '1':
    sys.exit(7)
for name, version in json.loads((root / 'package.json').read_text())['dependencies'].items():
    package = root / 'node_modules' / name
    package.mkdir(parents=True)
    binary = name.split('/')[-1]
    (package / 'package.json').write_text(json.dumps({
        'name': name, 'version': version, 'bin': {binary: 'dist/index.js'},
    }))
    (package / 'dist').mkdir()
    (package / 'dist/index.js').write_text('console.log("installed");\\n')
"""


def config_for(root: Path) -> dict:
    return {"web": {"chat_store": str(root / "state" / "chats.json")}}


def executable(root: Path, name: str, source: str) -> str:
    path = root / name
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class AdapterManagerTests(unittest.TestCase):
    def test_missing_install_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = AdapterManager(config_for(root))
            self.assertIsNone(manager.locate("codex"))
            self.assertFalse((root / "state").exists())
            with self.assertRaises(ValueError):
                manager.locate("qwen")

    def test_pinned_install_and_locator_use_node_entry_without_global_npm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = executable(root, "fake-node", FAKE_NODE)
            npm = executable(root, "fake-npm", FAKE_NPM)
            manager = AdapterManager(config_for(root), node=node, npm=npm)
            installed = manager.install()
            self.assertTrue(installed["installed"])
            destination = Path(installed["path"])
            self.assertEqual(destination.parent, root / "state" / "acp-adapters" / "installs")
            for provider in ("codex", "claude"):
                command = manager.locate(provider)
                self.assertEqual(command[0], node)
                self.assertEqual(Path(command[1]), destination / "node_modules" /
                                 acp_adapters.ADAPTERS[provider].package / "dist/index.js")
                self.assertNotIn(".cmd", command[1])
            invocation = json.loads((destination / "fake-npm-invocation.json").read_text())
            self.assertEqual(Path(invocation["cwd"]).parent, manager.root)
            self.assertEqual(invocation["argv"][0], "ci")
            self.assertIn("--ignore-scripts", invocation["argv"])
            self.assertIn("--cache", invocation["argv"])
            self.assertNotIn("-g", invocation["argv"])
            self.assertFalse(any(path.name.startswith(".npm-cache-") for path in manager.root.iterdir()))
            self.assertFalse(manager.install()["installed"])
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE((manager.root / "active.json").stat().st_mode), 0o600)

    def test_failure_keeps_active_install_and_old_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = executable(root, "fake-node", FAKE_NODE)
            npm = executable(root, "fake-npm", FAKE_NPM)
            manager = AdapterManager(config_for(root), node=node, npm=npm)
            old = Path(manager.install()["path"])
            pointer = (manager.root / "active.json").read_bytes()
            codex_entry = Path(manager.locate("codex")[1])
            codex_entry.write_text("changed", encoding="utf-8")
            with patch.dict(os.environ, {"PPI_FAKE_NPM_FAIL": "1"}):
                with self.assertRaisesRegex(AdapterInstallError, "npm ci failed"):
                    manager.install()
            self.assertEqual((manager.root / "active.json").read_bytes(), pointer)
            self.assertTrue(old.exists())
            self.assertEqual(codex_entry.read_text(), "changed")
            self.assertFalse(any(path.name.startswith((".stage-", ".npm-cache-"))
                                 for path in manager.root.iterdir()))
            new = Path(manager.install()["path"])
            self.assertNotEqual(new, old)
            self.assertTrue(old.exists())
            self.assertTrue(Path(manager.locate("codex")[1]).is_relative_to(new))

    def test_windows_node_resolution_and_entry_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = executable(root, "fake-node", FAKE_NODE)
            npm = executable(root, "fake-npm", FAKE_NPM)
            manager = AdapterManager(config_for(root), node=node, npm=npm)
            manager.install()
            windows_node = r"C:\Program Files\nodejs\node.exe"
            with patch.object(acp_adapters.sys, "platform", "win32"), \
                 patch.object(acp_adapters.shutil, "which", return_value=windows_node) as which, \
                 patch.object(acp_adapters, "_check_node"):
                windows_manager = AdapterManager(config_for(root))
                command = windows_manager.locate("claude")
            which.assert_called_with("node.exe")
            self.assertEqual(command[0], windows_node)
            self.assertTrue(command[1].endswith("dist/index.js"))
            self.assertFalse(command[1].endswith((".cmd", ".bat")))

    def test_windows_npm_shim_resolves_adjacent_javascript_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "npm.cmd"
            shim.write_text("@echo off\n", encoding="utf-8")
            package = root / "node_modules/npm"
            (package / "bin").mkdir(parents=True)
            (package / "package.json").write_text('{"name":"npm"}', encoding="utf-8")
            (package / "bin/npm-cli.js").write_text("// npm", encoding="utf-8")
            with patch.object(acp_adapters.sys, "platform", "win32"):
                self.assertEqual(acp_adapters._npm_argv(str(shim), "node.exe"),
                                 ["node.exe", str(package / "bin/npm-cli.js")])
                (package / "bin/npm-cli.js").unlink()
                with self.assertRaises(AdapterInstallError):
                    acp_adapters._npm_argv(str(shim), "node.exe")

    def test_pointer_replace_failure_preserves_previous_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = executable(root, "fake-node", FAKE_NODE)
            npm = executable(root, "fake-npm", FAKE_NPM)
            manager = AdapterManager(config_for(root), node=node, npm=npm)
            previous = Path(manager.install()["path"])
            pointer = (manager.root / "active.json").read_bytes()
            Path(manager.locate("codex")[1]).write_text("needs reinstall")
            original_replace = acp_adapters.os.replace
            def fail_pointer(source, target):
                if Path(target).name == "active.json":
                    raise OSError("simulated pointer failure")
                return original_replace(source, target)
            with patch.object(acp_adapters.os, "replace", side_effect=fail_pointer):
                with self.assertRaises(OSError):
                    manager.install()
            self.assertEqual((manager.root / "active.json").read_bytes(), pointer)
            self.assertTrue(previous.exists())

    def test_symlinked_install_root_is_rejected_without_writing_target(self):
        if os.name == "nt":
            self.skipTest("creating directory symlinks may require Windows privileges")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            node = executable(root, "fake-node", FAKE_NODE)
            npm = executable(root, "fake-npm", FAKE_NPM)
            manager = AdapterManager(config_for(root), node=node, npm=npm)
            manager.root.parent.mkdir()
            manager.root.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AdapterInstallError):
                manager.install()
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
