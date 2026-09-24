"""Fail-closed, committed-tree-only private workspace preparation on Linux.

This module prepares data. It does not sandbox a provider session or publish a
workspace. No caller may describe the result as an isolated running session.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


MIB = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW
_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
_ATTRS = ("filter", "working-tree-encoding", "text", "eol", "ident", "crlf")
_GIT_OPTIONS = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null",
    "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false",
    "-c", "core.eol=lf", "-c", "core.quotePath=false",
)


class PreparationError(RuntimeError):
    """A workspace could not be prepared without crossing its safety boundary."""


@dataclass(frozen=True)
class Limits:
    max_entries: int = 20_000
    max_blob_bytes: int = 64 * MIB
    max_materialized_bytes: int = 512 * MIB
    max_tree_bytes: int = 16 * MIB
    max_pack_bytes: int = 600 * MIB

    def __post_init__(self) -> None:
        ceilings = (20_000, 64 * MIB, 512 * MIB, 16 * MIB, 600 * MIB)
        if any(not isinstance(n, int) or n < 1 or n > cap
               for n, cap in zip(asdict(self).values(), ceilings)):
            raise ValueError("private workspace limits exceed policy")


@dataclass(frozen=True)
class PreparedWorkspace:
    workspace: Path
    repository: Path
    journal: Path
    source_commit: str
    source_tree: str
    baseline_commit: str
    materialized_bytes: int


@dataclass(frozen=True)
class _Entry:
    path: bytes
    mode: bytes
    kind: bytes
    oid: bytes


def _environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat",
        "GIT_AUTHOR_NAME": "Private workspace baseline",
        "GIT_AUTHOR_EMAIL": "baseline@pilferedparrot.invalid",
        "GIT_COMMITTER_NAME": "Private workspace baseline",
        "GIT_COMMITTER_EMAIL": "baseline@pilferedparrot.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00 +0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00 +0000",
    })
    return env


def _git(repo: Path, *args: str, data: bytes | None = None,
         limit: int = 4 * MIB, timeout: int = 120) -> bytes:
    command = ["git", *_GIT_OPTIONS, "-C", str(repo), *args]
    input_fd = None
    if data is not None:
        input_fd = os.memfd_create("private-git-input", 0)
        remaining = memoryview(data)
        while remaining:
            remaining = remaining[os.write(input_fd, remaining):]
        os.lseek(input_fd, 0, os.SEEK_SET)
    proc = None
    try:
        proc = subprocess.Popen(command, stdin=input_fd if input_fd is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=_environment())
        assert proc.stdout is not None
        output = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            wait = deadline - time.monotonic()
            if wait <= 0:
                raise PreparationError("Git command timed out")
            if not select.select([proc.stdout], [], [], min(wait, 10))[0]:
                continue
            chunk = os.read(proc.stdout.fileno(), min(MIB, limit + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > limit:
                raise PreparationError("Git output exceeded limit")
        proc.wait(timeout=max(1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        raise PreparationError("Git command timed out") from exc
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()
        if input_fd is not None:
            os.close(input_fd)
    if proc.returncode:
        raise PreparationError("Git command failed or exceeded output limit")
    return bytes(output)


def _oid(value: bytes, width: int) -> bytes:
    value = value.strip()
    if len(value) != width or any(byte not in b"0123456789abcdef" for byte in value):
        raise PreparationError("invalid Git object ID")
    return value


def _safe_path(path: bytes) -> None:
    if not path or len(path) > 512 or path.startswith(b"/") or b"\\" in path:
        raise PreparationError("unsafe committed path")
    for part in path.split(b"/"):
        if not part or part in (b".", b"..") or part.lower() == b".git" \
                or any(byte < 32 or byte == 127 for byte in part):
            raise PreparationError("unsafe committed path")


def _inventory(source: Path, limits: Limits):
    if not stat.S_ISDIR((source / ".git").lstat().st_mode):
        raise PreparationError("source needs its own real Git directory")
    if Path(os.fsdecode(_git(source, "rev-parse", "--show-toplevel").strip())).resolve() != source:
        raise PreparationError("source must be a repository root")
    if Path(os.fsdecode(_git(source, "rev-parse", "--path-format=absolute",
                              "--git-common-dir").strip())).resolve() != (source / ".git").resolve():
        raise PreparationError("linked or separate Git storage is unsupported")
    fmt = _git(source, "rev-parse", "--show-object-format=storage").strip()
    if fmt not in (b"sha1", b"sha256"):
        raise PreparationError("unsupported object format")
    width = 40 if fmt == b"sha1" else 64
    git_dir = source / ".git"
    objects = git_dir / "objects"
    if (git_dir / "config").stat().st_size > MIB or any(
            path.is_symlink() for path in (objects, objects / "info", objects / "pack")):
        raise PreparationError("unsupported Git storage")
    if (objects / "info" / "alternates").exists() or list((objects / "pack").glob("*.promisor")):
        raise PreparationError("alternate or promisor Git storage is unsupported")
    configs = subprocess.run(["git", *_GIT_OPTIONS, "-C", str(source), "config",
                              "--no-includes", "--get-regexp",
                              r"^(include\..*|includeif\..*|extensions\.partialclone|remote\..*\.promisor|remote\..*\.partialclonefilter)$"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=_environment(), timeout=30)
    if configs.returncode not in (0, 1) or configs.stdout:
        raise PreparationError("included or partial Git configuration is unsupported")
    commit = _oid(_git(source, "rev-parse", "--verify", "HEAD^{commit}"), width)
    tree = _oid(_git(source, "rev-parse", "--verify", f"{commit.decode()}^{{tree}}"), width)
    listing = _git(source, "ls-tree", "-r", "-t", "-z", "--full-tree", tree.decode(), limit=24 * MIB)
    entries = []
    objects_to_copy = {tree}
    seen = set()
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        if len(entries) >= limits.max_entries:
            raise PreparationError("tree entry limit exceeded")
        try:
            meta, path = raw.split(b"\t", 1)
            mode, kind, raw_oid = meta.split(b" ")
        except ValueError as exc:
            raise PreparationError("malformed tree listing") from exc
        _safe_path(path)
        if path in seen:
            raise PreparationError("duplicate committed path")
        seen.add(path)
        if (mode, kind) not in ((b"040000", b"tree"), (b"100644", b"blob"),
                                (b"100755", b"blob")):
            raise PreparationError("symlinks, gitlinks, and unsupported modes are refused")
        oid = _oid(raw_oid, width)
        entries.append(_Entry(path, mode, kind, oid))
        objects_to_copy.add(oid)
    query = b"".join(oid + b"\n" for oid in sorted(objects_to_copy))
    output = _git(source, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                  data=query, limit=4 * MIB)
    props = {}
    for row in output.splitlines():
        parts = row.split(b" ")
        if len(parts) != 3 or parts[0] not in objects_to_copy or not parts[2].isdigit():
            raise PreparationError("invalid object inventory")
        props[parts[0]] = (parts[1], int(parts[2]))
    if len(props) != len(objects_to_copy) or props[tree][0] != b"tree":
        raise PreparationError("incomplete object inventory")
    materialized = tree_bytes = 0
    for entry in entries:
        kind, size = props[entry.oid]
        if kind != entry.kind:
            raise PreparationError("object type mismatch")
        if kind == b"blob":
            if size > limits.max_blob_bytes:
                raise PreparationError("blob size limit exceeded")
            materialized += size
    for kind, size in props.values():
        if kind == b"tree":
            tree_bytes += size
    if materialized > limits.max_materialized_bytes or tree_bytes > limits.max_tree_bytes:
        raise PreparationError("materialized or tree byte limit exceeded")
    return fmt.decode(), commit, tree, entries, props, materialized


def _verify_link(parent_fd: int, name: str, stage_fd: int) -> None:
    linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    opened = os.fstat(stage_fd)
    if not stat.S_ISDIR(linked.st_mode) or (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
        raise PreparationError("staging directory changed")


def _verify_repository(stage_fd: int, expected: tuple[int, int] | None = None) -> tuple[int, int]:
    """Reject a swapped private Git directory before every Git write."""
    workspace_fd = os.open("worktree", _DIR, dir_fd=stage_fd)
    try:
        repo_fd = os.open(".git", _DIR, dir_fd=workspace_fd)
        try:
            info = os.fstat(repo_fd)
            identity = (info.st_dev, info.st_ino)
            if expected is not None and identity != expected:
                raise PreparationError("private Git directory changed")
            config_fd = os.open("config", os.O_RDONLY | _NOFOLLOW, dir_fd=repo_fd)
            os.close(config_fd)
            return identity
        finally:
            os.close(repo_fd)
    finally:
        os.close(workspace_fd)


def _append_journal(journal_fd: int, data: dict) -> None:
    """Append through a held inode; never remove or replace a mutable name."""
    line = json.dumps(data, sort_keys=True).encode() + b"\n"
    remaining = memoryview(line)
    while remaining:
        written = os.write(journal_fd, remaining)
        if written <= 0:
            raise PreparationError("journal append stopped")
        remaining = remaining[written:]
    os.fsync(journal_fd)


def _verify_journal(stage_fd: int, journal_fd: int) -> None:
    linked = os.stat("journal.jsonl", dir_fd=stage_fd, follow_symlinks=False)
    held = os.fstat(journal_fd)
    if not stat.S_ISREG(linked.st_mode) or (linked.st_dev, linked.st_ino) != (held.st_dev, held.st_ino):
        raise PreparationError("private journal path changed")


def _transfer(source: Path, repository: Path, stage_fd: int, props: dict,
              limits: Limits) -> None:
    """Move only listed raw tree/blob objects through a bounded pack stream."""
    object_input = b"".join(oid + b"\n" for oid in sorted(props))
    input_fd = os.memfd_create("private-tree-objects", 0)
    os.write(input_fd, object_input)
    os.lseek(input_fd, 0, os.SEEK_SET)
    # An unnamed file cannot be swapped for an unrelated user's file.
    pack_fd = os.open(".", os.O_RDWR | os.O_TMPFILE, 0o600, dir_fd=stage_fd)
    producer = None
    try:
        producer = subprocess.Popen(["git", *_GIT_OPTIONS, "-C", str(source),
                                     "pack-objects", "--stdout", "--no-reuse-object",
                                     "--window=0", "--depth=0", "--compression=0", "--threads=1"],
                                    stdin=input_fd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=_environment())
        assert producer.stdout is not None
        deadline = time.monotonic() + 600
        copied = 0
        while True:
            if not select.select([producer.stdout], [], [], max(0, min(10, deadline - time.monotonic())))[0]:
                if time.monotonic() >= deadline:
                    raise PreparationError("pack transfer timed out")
                continue
            chunk = os.read(producer.stdout.fileno(), MIB)
            if not chunk:
                break
            copied += len(chunk)
            if copied > limits.max_pack_bytes:
                raise PreparationError("pack byte limit exceeded")
            written = 0
            while written < len(chunk):
                written += os.write(pack_fd, chunk[written:])
        if producer.wait(timeout=30) or not copied:
            raise PreparationError("pack transfer failed")
        os.lseek(pack_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(pack_fd), "rb") as pack:
            process = subprocess.run(["git", *_GIT_OPTIONS, "-C", str(repository),
                                      "index-pack", "--stdin", "--strict",
                                      f"--max-input-size={limits.max_pack_bytes}"],
                                     stdin=pack, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=_environment(), timeout=120)
        if process.returncode:
            raise PreparationError("strict pack import failed")
    finally:
        if producer is not None:
            if producer.poll() is None:
                producer.kill()
                producer.wait()
            producer.stdout.close()
        os.close(input_fd)
        os.close(pack_fd)
    found = _git(repository, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                 data=object_input, limit=4 * MIB)
    expected = b"".join(oid + b" " + props[oid][0] + b" " + str(props[oid][1]).encode() + b"\n"
                        for oid in sorted(props))
    if found != expected:
        raise PreparationError("imported objects differ")
    packs = list((repository / ".git" / "objects" / "pack").glob("*.pack"))
    if len(packs) != 1:
        raise PreparationError("unexpected destination packs")
    listed = _git(repository, "verify-pack", "-v", str(packs[0]), limit=4 * MIB)
    present = {row.split()[0] for row in listed.splitlines()
               if len(row.split()) >= 2 and row.split()[1] in (b"tree", b"blob", b"commit", b"tag")}
    if present != set(props):
        raise PreparationError("pack contains unrequested objects")


def _check_private_attributes(workspace: Path, paths: list[bytes]) -> None:
    if not paths:
        return
    query = b"\0".join(paths) + b"\0"
    output = _git(workspace, "check-attr", "--cached", "--stdin", "-z", *_ATTRS,
                  data=query, limit=24 * MIB)
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) != 3 * len(paths) * len(_ATTRS):
        raise PreparationError("private attribute inventory is incomplete")
    for i, path in enumerate(paths):
        for j, attr in enumerate(_ATTRS):
            k = 3 * (i * len(_ATTRS) + j)
            if fields[k:k + 3] != [path, attr.encode(), b"unspecified"]:
                raise PreparationError("active checkout conversion attributes are unsupported")


def _materialize(repo: Path, workspace: Path, entries: list[_Entry], props: dict, fmt: str) -> None:
    root_fd = os.open(workspace, _DIR)
    try:
        for entry in entries:
            if entry.kind != b"blob":
                continue
            size = props[entry.oid][1]
            data = _git(repo, "cat-file", "blob", entry.oid.decode(), limit=size)
            if len(data) != size:
                raise PreparationError("blob byte count changed")
            digest = hashlib.new(fmt, b"blob " + str(size).encode() + b"\0" + data).hexdigest().encode()
            if digest != entry.oid:
                raise PreparationError("blob contents changed")
            parent_fd = os.dup(root_fd)
            try:
                parts = entry.path.split(b"/")
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o755, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    following = os.open(part, _DIR, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = following
                fd = os.open(parts[-1], _NEW, 0o600, dir_fd=parent_fd)
                try:
                    with os.fdopen(os.dup(fd), "wb") as out:
                        out.write(data)
                        out.flush()
                    os.fchmod(fd, 0o755 if entry.mode == b"100755" else 0o644)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                check_fd = os.open(parts[-1], os.O_RDONLY | _NOFOLLOW, dir_fd=parent_fd)
                try:
                    info = os.fstat(check_fd)
                    verify = hashlib.new(fmt, b"blob " + str(size).encode() + b"\0")
                    while chunk := os.read(check_fd, MIB):
                        verify.update(chunk)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != size \
                            or stat.S_IMODE(info.st_mode) != (0o755 if entry.mode == b"100755" else 0o644) \
                            or verify.hexdigest().encode() != entry.oid:
                        raise PreparationError("materialized file verification failed")
                finally:
                    os.close(check_fd)
            finally:
                os.close(parent_fd)
    finally:
        os.close(root_fd)


def _prepare_worker(source: Path, private_parent: Path, limits: Limits) -> dict:
    fmt, commit, tree, entries, props, materialized = _inventory(source, limits)
    stage_name = "private-workspace-" + uuid.uuid4().hex
    parent_fd = os.open(private_parent, _DIR)
    stage_fd = journal_fd = None
    journal = None
    try:
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_fd = os.open(stage_name, _DIR, dir_fd=parent_fd)
        journal_fd = os.open("journal.jsonl", _NEW | os.O_APPEND, 0o600, dir_fd=stage_fd)
        os.fsync(stage_fd)
        stage = private_parent / stage_name
        journal = {"schema": 1, "state": "preparing", "identity": stage_name,
                   "source_commit": commit.decode(), "source_tree": tree.decode(),
                   "object_format": fmt, "materialized_bytes": materialized}
        _append_journal(journal_fd, journal)
        _verify_link(parent_fd, stage_name, stage_fd)
        _verify_journal(stage_fd, journal_fd)
        workspace = stage / "worktree"
        _git(stage, "init", "--template=/dev/null", f"--object-format={fmt}", str(workspace))
        _verify_link(parent_fd, stage_name, stage_fd)
        git_identity = _verify_repository(stage_fd)
        _transfer(source, workspace, stage_fd, props, limits)
        _verify_link(parent_fd, stage_name, stage_fd)
        _verify_repository(stage_fd, git_identity)
        baseline = _oid(_git(workspace, "commit-tree", tree.decode(), data=b"Private workspace baseline\n"), len(commit))
        _git(workspace, "update-ref", "refs/heads/private-baseline", baseline.decode())
        _verify_repository(stage_fd, git_identity)
        _git(workspace, "symbolic-ref", "HEAD", "refs/heads/private-baseline")
        _git(workspace, "read-tree", tree.decode())
        _check_private_attributes(workspace, [e.path for e in entries if e.kind == b"blob"])
        _materialize(workspace, workspace, entries, props, fmt)
        _verify_link(parent_fd, stage_name, stage_fd)
        _verify_repository(stage_fd, git_identity)
        if _oid(_git(workspace, "write-tree"), len(tree)) != tree:
            raise PreparationError("private index tree differs from source")
        journal.update(state="ready", baseline_commit=baseline.decode())
        _verify_journal(stage_fd, journal_fd)
        _append_journal(journal_fd, journal)
        _verify_link(parent_fd, stage_name, stage_fd)
        _verify_journal(stage_fd, journal_fd)
        stage_info = os.fstat(stage_fd)
        return {"stage": stage_name, "source_commit": commit.decode(),
                "source_tree": tree.decode(), "baseline_commit": baseline.decode(),
                "materialized_bytes": materialized,
                "stage_device": stage_info.st_dev, "stage_inode": stage_info.st_ino}
    except BaseException:
        # Failed stages may contain unique data. Never recursively delete a path
        # whose name can be replaced by another same-UID process.
        try:
            if stage_fd is not None and journal_fd is not None and journal is not None:
                _verify_link(parent_fd, stage_name, stage_fd)
                journal["state"] = "failed"
                _append_journal(journal_fd, journal)
        except (OSError, PreparationError):
            pass
        raise
    finally:
        if journal_fd is not None:
            os.close(journal_fd)
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(parent_fd)


def prepare_private_workspace(source: os.PathLike | str, private_parent: os.PathLike | str,
                              limits: Limits = Limits()) -> PreparedWorkspace:
    """Prepare a private copy, refusing same-filesystem and unsandboxed writes.

    The destination must be a private directory on a *different filesystem*.
    This prevents a same-UID host process from renaming an open writable stage
    into the source while the worker writes. Failed stages remain for review.
    """
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise PreparationError("Linux bubblewrap is required")
    if not all(hasattr(os, name) for name in
               ("O_DIRECTORY", "O_NOFOLLOW", "O_TMPFILE", "memfd_create")):
        raise PreparationError("Linux descriptor operations are unavailable")
    source = Path(os.path.abspath(source))
    private_parent = Path(os.path.abspath(private_parent))
    source_real = source.resolve()
    if source_real == Path("/mnt") or Path("/mnt") in source_real.parents \
            or source_real == Path("/dev") or Path("/dev") in source_real.parents:
        raise PreparationError("source under reserved sandbox mountpoint")
    source_info = source.lstat()
    parent_info = private_parent.lstat()
    if not stat.S_ISDIR(source_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode) \
            or stat.S_IMODE(parent_info.st_mode) & 0o077:
        raise PreparationError("source must be real and destination parent private (0700)")
    if source_info.st_dev == parent_info.st_dev:
        raise PreparationError("destination needs a different filesystem from source")
    # A bind mount of the destination filesystem below source would reintroduce
    # the directory relocation race even though source itself has another st_dev.
    for row in Path("/proc/self/mountinfo").read_text().splitlines():
        mount = row.split()[4]
        mount = bytes(mount, "utf-8").decode("unicode_escape")
        mount_path = Path(mount)
        if mount_path == source_real or source_real in mount_path.parents:
            try:
                if mount_path.stat().st_dev == parent_info.st_dev:
                    raise PreparationError("destination filesystem is mounted inside source")
            except OSError as exc:
                raise PreparationError("cannot inspect source mounts") from exc
    if os.path.commonpath((str(source_real), str(private_parent.resolve()))) in \
            (str(source_real), str(private_parent.resolve())):
        raise PreparationError("destination must be outside source")
    # The anonymous input pack and index-pack's destination pack coexist.
    peak = 2 * limits.max_pack_bytes + limits.max_materialized_bytes + 32 * MIB
    if shutil.disk_usage(private_parent).free < peak:
        raise PreparationError("insufficient free space for bounded preparation peak")
    parent_fd = os.open(private_parent, _DIR)
    try:
        if (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino) != \
                (parent_info.st_dev, parent_info.st_ino):
            raise PreparationError("destination parent changed")
        command = ["bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid",
                   "--ro-bind", "/", "/", "--dev", "/dev",
                   "--bind", f"/proc/self/fd/{parent_fd}", "/mnt",
                   "--", sys.executable, str(Path(__file__).resolve()), "--worker",
                   str(source), json.dumps(asdict(limits))]
        proc = subprocess.run(command, pass_fds=(parent_fd,), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=900)
        if proc.returncode:
            raise PreparationError("sandboxed preparation failed: " +
                                   proc.stderr.decode(errors="replace")[-1000:])
        result = json.loads(proc.stdout)
        linked = private_parent.lstat()
        source_linked = source.lstat()
        if (source_linked.st_dev, source_linked.st_ino) != (source_info.st_dev, source_info.st_ino):
            raise PreparationError("source directory changed")
        if (linked.st_dev, linked.st_ino) != (parent_info.st_dev, parent_info.st_ino) \
                or stat.S_IMODE(linked.st_mode) & 0o077:
            raise PreparationError("destination parent changed")
        stage = private_parent / result["stage"]
        stage_info = stage.lstat()
        if not stat.S_ISDIR(stage_info.st_mode) or \
                (stage_info.st_dev, stage_info.st_ino) != (result["stage_device"], result["stage_inode"]):
            raise PreparationError("prepared staging directory changed")
        return PreparedWorkspace(stage / "worktree", stage / "worktree" / ".git", stage / "journal.jsonl",
                                 result["source_commit"], result["source_tree"],
                                 result["baseline_commit"], result["materialized_bytes"])
    finally:
        os.close(parent_fd)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    try:
        print(json.dumps(_prepare_worker(Path(sys.argv[2]), Path("/mnt"),
                                         Limits(**json.loads(sys.argv[3])))))
    except (OSError, ValueError, PreparationError) as error:
        print(f"private workspace preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
