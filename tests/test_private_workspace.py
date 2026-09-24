"""Focused synthetic tests for the private preparation boundary."""

import errno
import json
import os
import runpy
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilferedparrot import private_workspace as manager


@unittest.skipUnless(os.name == "posix" and shutil.which("bwrap") and Path("/dev/shm").exists(),
                     "Linux bubblewrap and a separate temporary filesystem required")
class PrivateWorkspaceTests(unittest.TestCase):
    @staticmethod
    def journal_records(path):
        return [json.loads(line) for line in path.read_text().splitlines()]

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
        # Keep the fixture's Git writes synchronous: detached auto maintenance
        # can outlive commit() and change .git during the snapshot comparison.
        return subprocess.run(["git", "-c", "maintenance.auto=false", "-C",
                               str(cwd or self.source), *args],
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
        self.assertEqual([entry["state"] for entry in self.journal_records(result.journal)],
                         ["preparing", "ready"])
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

    def test_sandbox_result_cannot_select_host_path(self):
        self.seed()
        output = {"stage": "../../source", "source_commit": "a" * 40,
                  "source_tree": "b" * 40, "baseline_commit": "c" * 40,
                  "stage_device": self.source.stat().st_dev,
                  "stage_inode": self.source.stat().st_ino,
                  "materialized_bytes": 0}
        fake = subprocess.CompletedProcess([], 0, json.dumps(output).encode(), b"")
        with patch.object(manager, "_run_worker_sandbox", return_value=fake):
            with self.assertRaisesRegex(manager.PreparationError, "stage identity"):
                manager.prepare_private_workspace(self.source, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])
        output["stage"] = "private-workspace-" + "a" * 32
        output["stage_inode"] = "1"
        with self.assertRaisesRegex(manager.PreparationError, "counters"):
            manager._validated_worker_result(json.dumps(output).encode(), manager.Limits())

    def test_source_path_cannot_supply_bubblewrap_executable(self):
        self.seed()
        marker = self.source / "fake-bwrap-marker"
        fake = self.source / "bwrap"
        fake.write_text(f"#!/bin/sh\nprintf fake >> {shlex.quote(str(marker))}\nexit 99\n")
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": f"{self.source}:/usr/bin:/bin"}):
            result = manager.prepare_private_workspace(self.source, self.parent)
        self.assertTrue(result.workspace.is_dir())
        self.assertFalse(marker.exists())

    def test_ambient_preload_cannot_run_before_sandbox(self):
        compiler = shutil.which("gcc")
        if compiler is None:
            self.skipTest("gcc is unavailable")
        self.seed()
        marker = self.source / "preload-marker"
        marker.write_bytes(b"unchanged\n")
        preload = self.source / "preload.so"
        code = ("#include <fcntl.h>\n#include <unistd.h>\n"
                "__attribute__((constructor)) static void mark(void) {\n"
                "  int fd = open(" + json.dumps(str(marker)) + ", O_WRONLY|O_APPEND);\n"
                "  if (fd >= 0) { write(fd, \"x\", 1); close(fd); }\n}\n")
        subprocess.run([compiler, "-shared", "-fPIC", "-x", "c", "-o", str(preload), "-"],
                       input=code.encode(), check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
        with patch.dict(os.environ, {"LD_PRELOAD": str(preload)}):
            result = manager.prepare_private_workspace(self.source, self.parent)
        self.assertTrue(result.workspace.is_dir())
        self.assertEqual(marker.read_bytes(), b"unchanged\n")

    def test_worker_mount_does_not_inherit_writable_host_directory_fd(self):
        # A ro-bind of / does not protect host paths reached by walking upward
        # through an inherited directory fd under /proc/self/fd.
        marker = self.source / "fd-escape-marker"
        marker.write_bytes(b"unchanged\n")
        mounted_marker = self.parent / "mounted-marker"
        parent_fd = os.open(self.parent, manager._DIR)
        try:
            escape = f"/proc/self/fd/{parent_fd}/{os.path.relpath(marker, self.parent)}"
            probe = """
import json
import os
import sys

results = {}
for label, path in (("direct", sys.argv[1]), ("fd_escape", sys.argv[2]),
                    ("mounted", sys.argv[3])):
    try:
        with open(path, "ab") as output:
            output.write(label.encode() + b"\\n")
        results[label] = "writable"
    except OSError as error:
        results[label] = error.errno
print(json.dumps(results))
"""
            result = manager._run_worker_sandbox(
                parent_fd, [sys.executable, "-c", probe, str(marker), escape,
                            "/mnt/mounted-marker"], timeout=30,
            )
        finally:
            os.close(parent_fd)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        outcomes = json.loads(result.stdout)
        self.assertNotEqual(outcomes["direct"], "writable")
        self.assertNotEqual(outcomes["fd_escape"], "writable")
        self.assertEqual(outcomes["mounted"], "writable")
        self.assertEqual(marker.read_bytes(), b"unchanged\n")
        self.assertEqual(mounted_marker.read_bytes(), b"mounted\n")

    def test_worker_does_not_inherit_writable_caller_stdin(self):
        marker = self.source / "stdin-escape-marker"
        marker.write_bytes(b"unchanged\n")
        mounted_marker = self.parent / "stdin-mounted-marker"
        probe = """
import json
import os
import sys

results = {"fd0_target": os.readlink("/proc/self/fd/0")}
try:
    os.write(0, b"fd0 escape\\n")
    results["fd0_write"] = "writable"
except OSError as error:
    results["fd0_write"] = error.errno
try:
    with open("/proc/self/fd/0", "ab") as output:
        output.write(b"proc fd0 escape\\n")
    results["proc_fd0_write"] = "writable"
except OSError as error:
    results["proc_fd0_write"] = error.errno
for label, path in (("direct", sys.argv[1]), ("mounted", sys.argv[2])):
    try:
        with open(path, "ab") as output:
            output.write(label.encode() + b"\\n")
        results[label] = "writable"
    except OSError as error:
        results[label] = error.errno
print(json.dumps(results))
"""
        launcher = """
import os
import sys
from pilferedparrot import private_workspace as manager

parent_fd = os.open(sys.argv[1], manager._DIR)
try:
    result = manager._run_worker_sandbox(
        parent_fd, [sys.executable, "-c", sys.argv[2], sys.argv[3],
                    "/mnt/stdin-mounted-marker"], timeout=30,
    )
finally:
    os.close(parent_fd)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
"""
        with marker.open("r+b") as writable_stdin:
            result = subprocess.run(
                [sys.executable, "-c", launcher, str(self.parent), probe, str(marker)],
                stdin=writable_stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        outcomes = json.loads(result.stdout)
        self.assertEqual(outcomes["fd0_target"], "/dev/null")
        # subprocess.DEVNULL may open /dev/null read-write; writing fd 0 can
        # succeed there, but it must no longer point at the caller's marker.
        self.assertNotEqual(outcomes["direct"], "writable")
        self.assertEqual(outcomes["mounted"], "writable")
        self.assertEqual(marker.read_bytes(), b"unchanged\n")
        self.assertEqual(mounted_marker.read_bytes(), b"mounted\n")

    def test_worker_cannot_reach_host_sockets_or_network(self):
        self.seed()
        hidden = self.source / "untracked-host.sock"
        exposed = self.source / ".git" / "git-host.sock"
        fifo = self.source / ".git" / "git-host-fifo"
        os.mkfifo(fifo)
        fifo_reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        listeners = []
        try:
            for path in (hidden, exposed):
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(str(path))
                listener.listen(1)
                listeners.append(listener)
            host_namespaces = {name: os.readlink(f"/proc/self/ns/{name}")
                               for name in ("net", "ipc", "uts")}
            probe = """
import errno
import json
import os
import socket
import stat
import sys

paths = sys.argv[1:5]
results = {"visible": [os.path.exists(path) for path in paths],
           "namespaces": {name: os.readlink("/proc/self/ns/" + name)
                          for name in ("net", "ipc", "uts")}}
fds = {}
for number in os.listdir("/proc/self/fd"):
    try:
        target = os.readlink("/proc/self/fd/" + number)
        with open("/proc/self/fdinfo/" + number) as fdinfo:
            flags = next(line.split()[1] for line in fdinfo if line.startswith("flags:"))
        fds[number] = {"target": target, "flags": int(flags, 8),
                       "regular": stat.S_ISREG(os.fstat(int(number)).st_mode)}
    except FileNotFoundError:
        pass
results["fds"] = fds
for family in (socket.AF_UNIX, socket.AF_INET):
    try:
        socket.socket(family, socket.SOCK_STREAM)
        results[str(family)] = "created"
    except OSError as error:
        results[str(family)] = error.errno
try:
    fd = os.open(sys.argv[5], os.O_WRONLY | os.O_NONBLOCK)
    os.write(fd, b"host write")
    os.close(fd)
    results["fifo"] = "writable"
except OSError as error:
    results["fifo"] = error.errno
print(json.dumps(results))
"""
            parent_fd = os.open(self.parent, manager._DIR)
            try:
                result = manager._run_worker_sandbox(
                    parent_fd, [sys.executable, "-c", probe, str(hidden), str(exposed),
                                "/var/run/docker.sock", f"/run/user/{os.getuid()}/bus",
                                str(fifo)],
                    timeout=30, source=self.source,
                )
            finally:
                os.close(parent_fd)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            outcomes = json.loads(result.stdout)
            self.assertEqual(outcomes["visible"], [False, True, False, False])
            for name, host_namespace in host_namespaces.items():
                self.assertNotEqual(outcomes["namespaces"][name], host_namespace)
            self.assertTrue({"0", "1", "2"} <= set(outcomes["fds"]))
            self.assertEqual(outcomes["fds"]["0"]["target"], "/dev/null")
            for number, info in outcomes["fds"].items():
                if int(number) > 2:
                    self.assertTrue(info["regular"], info)
                    self.assertEqual(info["flags"] & os.O_ACCMODE, os.O_RDONLY, info)
                    self.assertNotIn(str(self.source), info["target"])
            self.assertEqual(outcomes[str(socket.AF_UNIX)], errno.EPERM)
            self.assertEqual(outcomes[str(socket.AF_INET)], errno.EPERM)
            self.assertEqual(outcomes["fifo"], errno.EACCES)
            self.assertEqual(os.read(fifo_reader, 100), b"")
        finally:
            for listener in listeners:
                listener.close()
            os.close(fifo_reader)

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
        self.assertTrue((moved / "journal.jsonl").exists())

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
        self.assertEqual(self.journal_records(stages[0] / "journal.jsonl")[-1]["state"], "failed")

    def test_unique_file_preserved_when_journal_name_swapped_before_or_after_append(self):
        self.seed()
        for timing in ("before", "after"):
            with self.subTest(timing=timing):
                unique = self.parent / f"unique-{timing}"
                unique.write_bytes(b"unique data; never delete or overwrite")
                real_append = manager._append_journal

                def swap():
                    stage = next(p for p in self.parent.iterdir()
                                 if p.name.startswith("private-workspace-"))
                    (stage / "journal.jsonl").rename(stage / "held-journal.jsonl")
                    unique.rename(stage / "journal.jsonl")

                def swapped_append(fd, data):
                    if data["state"] == "ready" and timing == "before":
                        swap()
                    real_append(fd, data)
                    if data["state"] == "ready" and timing == "after":
                        swap()

                with patch.object(manager, "_append_journal", side_effect=swapped_append):
                    with self.assertRaisesRegex(manager.PreparationError, "journal path changed"):
                        manager._prepare_worker(self.source, self.parent, manager.Limits())
                stage = next(p for p in self.parent.iterdir()
                             if p.name.startswith("private-workspace-"))
                self.assertEqual((stage / "journal.jsonl").read_bytes(),
                                 b"unique data; never delete or overwrite")
                self.assertEqual(self.journal_records(stage / "held-journal.jsonl")[-1]["state"],
                                 "failed")
                shutil.rmtree(stage)

    def test_preexisting_journal_name_is_never_overwritten(self):
        self.seed()
        real_open = os.open
        marker = b"preexisting unique data"

        def occupy_before_create(path, flags, *args, **kwargs):
            if path == "journal.jsonl":
                fd = real_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                               0o600, dir_fd=kwargs["dir_fd"])
                try:
                    os.write(fd, marker)
                finally:
                    os.close(fd)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(manager.os, "open", side_effect=occupy_before_create):
            with self.assertRaises(FileExistsError):
                manager._prepare_worker(self.source, self.parent, manager.Limits())
        stage = next(self.parent.glob("private-workspace-*"))
        self.assertEqual((stage / "journal.jsonl").read_bytes(), marker)

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

    def test_import_without_linux_open_flags_and_fail_closed_on_windows(self):
        flags = {name: getattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")}
        try:
            for name in flags:
                delattr(os, name)
            namespace = runpy.run_path(str(Path(manager.__file__)),
                                       run_name="private_workspace_windows_probe")
            with patch.object(sys, "platform", "win32"):
                with self.assertRaisesRegex(namespace["PreparationError"], "Linux bubblewrap"):
                    namespace["prepare_private_workspace"]("unused", "unused")
        finally:
            for name, value in flags.items():
                setattr(os, name, value)


if __name__ == "__main__":
    unittest.main()
