"""Synthetic safety and coverage checks for filesystem observations."""

from __future__ import annotations

import json
import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import workspace_checkpoints as checkpoints


class WorkspaceCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "work"
        self.workspace.mkdir()
        self.storage = self.base / "private"
        self.storage.mkdir(mode=0o700)

    def capture(self, limits: checkpoints.Limits = checkpoints.Limits()) -> dict:
        return checkpoints.capture(self.workspace, self.storage, limits)

    def test_create_edit_delete_binary_non_utf8_and_honest_label(self):
        (self.workspace / ".gitignore").write_text("ignored.bin\n")
        (self.workspace / "ignored.bin").write_bytes(b"\x00\xff\xfe")
        (self.workspace / "edit.txt").write_text("before")
        (self.workspace / "delete.txt").write_text("gone")
        odd = os.fsdecode(b"odd-\xff")
        (self.workspace / odd).write_bytes(b"raw")
        first = self.capture()
        (self.workspace / "ignored.bin").write_bytes(b"\x00\xff\xfd")
        (self.workspace / "edit.txt").write_text("after")
        (self.workspace / "delete.txt").unlink()
        (self.workspace / "create.txt").write_text("new")
        second = self.capture()
        changes = checkpoints.observed_changes(first, second)
        self.assertEqual(changes["label"], "observed filesystem changes")
        self.assertEqual(changes["attribution"], "unknown")
        self.assertEqual({x["path"]: x["kind"] for x in changes["changes"]}, {
            "ignored.bin": "modified", "edit.txt": "modified",
            "delete.txt": "deleted", "create.txt": "created"})
        self.assertIn(odd, first["entries"])
        entry = first["entries"]["ignored.bin"]
        self.assertEqual((Path(first["storage"]) / entry["blob"]).read_bytes(), b"\x00\xff\xfe")
        self.assertEqual(stat.S_IMODE((Path(first["storage"]) / entry["blob"]).stat().st_mode), 0o600)
        persisted = json.loads((Path(first["storage"]) / "manifest.json").read_text())
        self.assertEqual(persisted["entries"][odd]["sha256"], first["entries"][odd]["sha256"])
        self.assertTrue(changes["coverage_complete_under_policy"])

    def test_symlink_escape_hardlink_special_and_administrative_coverage(self):
        outside = self.base / "secret"
        outside.write_text("outside")
        (self.workspace / "escape").symlink_to(outside)
        (self.workspace / "linked").write_text("linked")
        os.link(self.workspace / "linked", self.workspace / "linked2")
        os.mkfifo(self.workspace / "pipe")
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "config").write_text("secret")
        (self.workspace / ".ppi-checkpoints").mkdir()
        result = self.capture()
        self.assertFalse(result["coverage_complete_under_policy"])
        self.assertNotIn("escape", result["entries"])
        self.assertNotIn("linked", result["entries"])
        self.assertNotIn("pipe", result["entries"])
        cover = {x["path"]: x for x in result["coverage"]}
        self.assertIn("symlink", cover["escape"]["reason"])
        self.assertIn("hard-linked", cover["linked"]["reason"])
        self.assertIn("special", cover["pipe"]["reason"])
        self.assertEqual(cover[".git"]["disposition"], "excluded")
        self.assertEqual(cover[".ppi-checkpoints"]["disposition"], "excluded")
        self.assertEqual(cover[str(self.storage)]["disposition"], "excluded")
        self.assertNotIn(str(outside), json.dumps(result))

    def test_limits_expose_incomplete_coverage_and_never_exceed_byte_cap(self):
        (self.workspace / "large").write_bytes(b"a" * 5)
        (self.workspace / "small").write_bytes(b"b" * 3)
        result = self.capture(checkpoints.Limits(max_total_bytes=4, max_file_bytes=4))
        self.assertFalse(result["coverage_complete_under_policy"])
        self.assertLessEqual(result["counts"]["bytes"], 4)
        self.assertTrue(any("file size" in c["reason"] for c in result["coverage"]))
        capped = self.capture(checkpoints.Limits(max_total_bytes=8, max_file_bytes=8, max_files=1))
        self.assertFalse(capped["coverage_complete_under_policy"])
        self.assertTrue(any("file count" in c["reason"] for c in capped["coverage"]))
        total = self.capture(checkpoints.Limits(max_total_bytes=6, max_file_bytes=6))
        self.assertFalse(total["coverage_complete_under_policy"])
        self.assertLessEqual(total["counts"]["bytes"], 6)
        self.assertTrue(any("total byte" in c["reason"] for c in total["coverage"]))
        with self.assertRaises(ValueError):
            checkpoints.Limits(max_total_bytes=101 * 1024 * 1024)

    def test_concurrent_file_mutation_is_incomplete_and_discards_blob(self):
        target = self.workspace / "racing"
        target.write_bytes(b"a" * 100)
        actual_read = os.read
        mutated = False

        def read_and_mutate(fd: int, count: int) -> bytes:
            nonlocal mutated
            value = actual_read(fd, count)
            if not mutated:
                mutated = True
                target.write_bytes(b"changed")
            return value

        with patch.object(checkpoints.os, "read", side_effect=read_and_mutate):
            result = self.capture()
        self.assertNotIn("racing", result["entries"])
        self.assertFalse(result["coverage_complete_under_policy"])
        self.assertTrue(any(c["path"] == "racing" and "changed" in c["reason"] for c in result["coverage"]))
        self.assertEqual(list((Path(result["storage"]) / "blobs").iterdir()), [])

    def test_symlink_swap_between_stat_and_open_is_not_followed(self):
        outside = self.base / "outside"
        outside.write_text("secret")
        victim = self.workspace / "victim"
        victim.write_text("ordinary")
        actual_open = os.open
        swapped = False

        def swap_then_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "victim" and not swapped:
                swapped = True
                victim.unlink()
                victim.symlink_to(outside)
            return actual_open(path, flags, *args, **kwargs)

        with patch.object(checkpoints, "_require_posix"), patch.object(checkpoints.os, "open", side_effect=swap_then_open):
            result = self.capture()
        self.assertNotIn("victim", result["entries"])
        self.assertFalse(result["coverage_complete_under_policy"])
        self.assertEqual(list((Path(result["storage"]) / "blobs").iterdir()), [])
        self.assertTrue(any("file not captured" in c["reason"] for c in result["coverage"]))

    def test_missing_entry_in_incomplete_scan_is_not_called_deleted(self):
        (self.workspace / "sensitive").write_text("known")
        first = self.capture()
        (self.workspace / "sensitive").chmod(0o600)
        second = self.capture(checkpoints.Limits(max_total_bytes=3, max_file_bytes=3))
        report = checkpoints.observed_changes(first, second)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["unverified_paths"], ["sensitive"])
        self.assertFalse(report["coverage_complete_under_policy"])

    def test_directory_move_during_scan_discards_captured_subtree(self):
        child = self.workspace / "child"
        child.mkdir()
        (child / "data").write_bytes(b"safe")
        moved = self.base / "moved"
        actual_read = os.read
        moved_once = False

        def read_and_move(fd: int, count: int) -> bytes:
            nonlocal moved_once
            value = actual_read(fd, count)
            if not moved_once:
                moved_once = True
                child.rename(moved)
            return value

        with patch.object(checkpoints.os, "read", side_effect=read_and_move):
            result = self.capture()
        self.assertEqual(result["entries"], {})
        self.assertFalse(result["coverage_complete_under_policy"])
        self.assertEqual(list((Path(result["storage"]) / "blobs").iterdir()), [])

    def test_storage_must_be_private_and_outside_workspace(self):
        (self.workspace / "snapshots").mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            checkpoints.capture(self.workspace, self.workspace / "snapshots")
        self.storage.chmod(0o755)
        with self.assertRaises(ValueError):
            self.capture()

    def test_nested_quota_stop_does_not_call_unvisited_root_sibling_deleted(self):
        child = self.workspace / "a"
        child.mkdir()
        (child / "one").write_text("1")
        (child / "two").write_text("2")
        (self.workspace / "z").write_text("still here")
        first = self.capture()
        limited = self.capture(checkpoints.Limits(max_files=1))
        report = checkpoints.observed_changes(first, limited)
        self.assertFalse(limited["coverage_complete_under_policy"])
        self.assertTrue(any(c["path"] == "." and "scan stopped" in c["reason"]
                            for c in limited["coverage"]))
        self.assertNotIn("z", {c["path"] for c in report["changes"]})
        self.assertIn("z", report["unverified_paths"])

    def test_storage_parent_swap_cannot_redirect_bytes_into_workspace(self):
        (self.workspace / "payload").write_bytes(b"private bytes")
        redirect = self.workspace / "redirect"
        redirect.mkdir(mode=0o700)
        moved = self.base / "private-moved"
        actual_mkdir = os.mkdir
        swapped = False

        def swap_then_mkdir(path, mode=0o777, *, dir_fd=None):
            nonlocal swapped
            if str(path).startswith("checkpoint-") and not swapped:
                swapped = True
                self.storage.rename(moved)
                self.storage.symlink_to(redirect, target_is_directory=True)
            return actual_mkdir(path, mode, dir_fd=dir_fd)

        with patch.object(checkpoints, "_require_posix"), patch.object(
                checkpoints.os, "mkdir", side_effect=swap_then_mkdir):
            with self.assertRaises(RuntimeError):
                self.capture()
        self.assertTrue(swapped)
        self.assertEqual(list(redirect.iterdir()), [])
        self.assertEqual(list(moved.iterdir()), [])

    def test_open_checkpoint_folder_moved_into_workspace_is_rejected_and_scrubbed(self):
        (self.workspace / "payload").write_bytes(b"private bytes")
        admin = self.workspace / ".ppi-checkpoints"
        admin.mkdir()
        actual_read = os.read
        moved: Path | None = None

        def read_and_move(fd: int, count: int) -> bytes:
            nonlocal moved
            value = actual_read(fd, count)
            if moved is None:
                folder, = self.storage.glob("checkpoint-*")
                moved = admin / folder.name
                folder.rename(moved)
            return value

        with patch.object(checkpoints.os, "read", side_effect=read_and_move):
            with self.assertRaisesRegex(RuntimeError, "checkpoint storage changed"):
                self.capture()
        self.assertIsNotNone(moved)
        self.assertEqual(list(self.storage.iterdir()), [])
        self.assertEqual(list(moved.rglob("*")), [])

    def test_manifest_enospc_removes_only_this_capture(self):
        (self.workspace / "payload").write_bytes(b"private bytes")
        existing = self.storage / "keep"
        existing.mkdir()
        (existing / "sentinel").write_text("preserve")

        def fail_manifest(_manifest, output, **_kwargs):
            output.write("partial")
            raise OSError(errno.ENOSPC, "no space left")

        with patch.object(checkpoints.json, "dump", side_effect=fail_manifest):
            with self.assertRaises(OSError) as caught:
                self.capture()
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertEqual([p.name for p in self.storage.iterdir()], ["keep"])
        self.assertEqual((existing / "sentinel").read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
