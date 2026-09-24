"""Focused synthetic tests for the private preparation boundary."""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import private_workspace as manager


@unittest.skipUnless(os.name == "posix" and shutil.which("bwrap") and Path("/dev/shm").exists(),
                     "Linux bubblewrap and a separate temporary filesystem required")
class PrivateWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.source_temp = tempfile.TemporaryDirectory()
        self.parent_temp = tempfile.TemporaryDirectory(dir="/dev/shm")
        self.addCleanup(self.source_temp.cleanup)
        self.addCleanup(self.parent_temp.cleanup)
        self.source = Path(self.source_temp.name)
        self.parent = Path(self.parent_temp.name)
        self.parent.chmod(0o700)
        self.git("init", "-q")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.source), *args],
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                 "commit", "-qm", "fixture")

    def seed(self):
        (self.source / ".gitattributes").write_text("exported export-ignore\n")
        (self.source / ".gitignore").write_text("ignored\n")
        (self.source / "exported").write_bytes(b"retained\0")
        (self.source / "tracked").write_bytes(b"committed\r\n")
        (self.source / "executable").write_text("#!/bin/sh\n")
        (self.source / "executable").chmod(0o755)
        self.commit()

    def test_exact_tree_and_independent_host_repository(self):
        self.seed()
        (self.source / "tracked").write_text("dirty")
        (self.source / "ignored").write_text("ignored")
        (self.source / "untracked").write_text("untracked")
        before = {p.relative_to(self.source / ".git"): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in (self.source / ".git").rglob("*") if p.is_file()}
        result = manager.prepare_private_workspace(self.source, self.parent)
        self.assertEqual((result.workspace / "tracked").read_bytes(), b"committed\r\n")
        self.assertEqual((result.workspace / "exported").read_bytes(), b"retained\0")
        self.assertFalse((result.workspace / "ignored").exists())
        self.assertFalse((result.workspace / "untracked").exists())
        self.assertEqual(stat.S_IMODE((result.workspace / "executable").stat().st_mode), 0o755)
        self.assertEqual(self.git("status", "--porcelain", cwd=result.workspace), b"")
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}", cwd=result.workspace).strip().decode(),
                         result.source_tree)
        source_commit_lookup = subprocess.run(
            ["git", "-C", str(result.workspace), "cat-file", "-e", result.source_commit],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(source_commit_lookup.returncode, 0)
        self.assertFalse((result.repository / "objects" / "info" / "alternates").exists())
        self.assertEqual(json.loads(result.journal.read_text())["state"], "ready")
        after = {p.relative_to(self.source / ".git"): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in (self.source / ".git").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        moved = self.source.with_name(self.source.name + "-moved")
        self.source.rename(moved)
        try:
            self.assertEqual(self.git("status", "--porcelain", cwd=result.workspace), b"")
        finally:
            moved.rename(self.source)

    def test_same_filesystem_refused_before_writes(self):
        self.seed()
        local = self.source.parent / "other-private"
        local.mkdir(mode=0o700)
        self.addCleanup(lambda: local.rmdir() if local.exists() else None)
        with self.assertRaisesRegex(manager.PreparationError, "different filesystem"):
            manager.prepare_private_workspace(self.source, local)
        self.assertEqual(list(local.iterdir()), [])

    def test_symlink_git_substitution_cannot_write_source(self):
        self.seed()
        stage = self.parent / "attack"
        workspace = stage / "worktree"
        workspace.mkdir(parents=True)
        (workspace / ".git").symlink_to(self.source / ".git", target_is_directory=True)
        stage_fd = os.open(stage, manager._DIR)
        try:
            with self.assertRaises(OSError):
                manager._verify_repository(stage_fd)
        finally:
            os.close(stage_fd)
        command = ["bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid",
                   "--ro-bind", "/", "/", "--dev", "/dev", "--bind", str(self.parent),
                   "/mnt", "--", "git", "-C", "/mnt/attack/worktree", "update-ref",
                   "refs/heads/evil", self.git("rev-parse", "HEAD").strip().decode()]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Read-only file system", result.stderr)
        self.assertFalse((self.source / ".git" / "refs" / "heads" / "evil").exists())

    def test_moved_directory_cannot_enter_source(self):
        self.seed()
        stage = self.parent / "stage"
        stage.mkdir()
        with self.assertRaises(OSError):
            stage.rename(self.source / "stage")
        self.assertTrue(stage.exists())
        self.assertFalse((self.source / "stage").exists())

    def test_unrelated_swapped_stage_is_preserved_on_failure(self):
        self.seed()
        unrelated = self.parent / "unrelated"
        unrelated.mkdir()
        (unrelated / "unique").write_text("retain")
        moved = self.parent / "moved-original"

        def swap_then_fail(_source, _repo, _stage_fd, _props, _limits):
            stage = next(p for p in self.parent.iterdir() if p.name.startswith("private-workspace-"))
            stage.rename(moved)
            unrelated.rename(stage)
            raise manager.PreparationError("injected failure")

        with patch.object(manager, "_transfer", side_effect=swap_then_fail):
            with self.assertRaises(manager.PreparationError):
                manager._prepare_worker(self.source, self.parent, manager.Limits())
        self.assertEqual((next(p for p in self.parent.iterdir()
                               if p.name.startswith("private-workspace-")) / "unique").read_text(), "retain")
        self.assertTrue((moved / "journal.json").exists())

    def test_private_index_attributes_resist_source_local_masking(self):
        (self.source / ".gitattributes").write_text("tracked text\n")
        (self.source / "tracked").write_text("bytes\n")
        self.commit()
        info = self.source / ".git" / "info" / "attributes"
        info.write_text("tracked !text\n")
        self.assertIn(b"unspecified", self.git("check-attr", "text", "tracked"))
        with self.assertRaisesRegex(manager.PreparationError, "attributes"):
            manager.prepare_private_workspace(self.source, self.parent)
        stages = list(self.parent.glob("private-workspace-*"))
        self.assertEqual(len(stages), 1)
        self.assertEqual(json.loads((stages[0] / "journal.json").read_text())["state"], "failed")

    def test_symlink_committed_path_refused(self):
        (self.source / "tracked").write_text("x")
        (self.source / "link").symlink_to("tracked")
        self.commit()
        with self.assertRaisesRegex(manager.PreparationError, "symlinks"):
            manager.prepare_private_workspace(self.source, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_sha256_repository_keeps_tree_and_object_format(self):
        shutil.rmtree(self.source / ".git")
        self.git("init", "-q", "--object-format=sha256")
        self.seed()
        result = manager.prepare_private_workspace(self.source, self.parent)
        self.assertEqual(len(result.source_tree), 64)
        self.assertEqual(self.git("rev-parse", "--show-object-format=storage",
                                  cwd=result.workspace).strip(), b"sha256")
        self.assertEqual(self.git("status", "--porcelain", cwd=result.workspace), b"")


if __name__ == "__main__":
    unittest.main()
