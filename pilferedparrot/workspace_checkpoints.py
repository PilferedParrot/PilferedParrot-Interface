"""Bounded, local filesystem observations for a future change inspector.

This module does not restore files or attribute changes to an agent. A checkpoint
contains private copies of observed regular-file bytes and an explicit coverage
report. Concurrent writers can still defeat a userspace scan (for example by
changing bytes and restoring all metadata); consumers must treat it as evidence
of an observation, not an atomic filesystem snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_MAX_BYTES = 100 * 1024 * 1024
_DEFAULT_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_EXCLUDED_NAMES = frozenset({".git", ".agents", ".codex", ".ppi-checkpoints"})


class UnsupportedPlatform(RuntimeError):
    """The platform cannot provide the required no-follow, fd-relative scan."""


@dataclass(frozen=True)
class Limits:
    max_total_bytes: int = _DEFAULT_BYTES
    max_file_bytes: int = 16 * 1024 * 1024
    max_files: int = 10_000
    max_entries: int = 5_000

    def __post_init__(self) -> None:
        if not (0 < self.max_total_bytes <= _MAX_BYTES):
            raise ValueError("max_total_bytes must be between 1 and 100 MiB")
        if not (0 < self.max_file_bytes <= self.max_total_bytes):
            raise ValueError("max_file_bytes must fit max_total_bytes")
        if self.max_files < 1 or self.max_entries < 1:
            raise ValueError("file and entry limits must be positive")


def _require_posix() -> None:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise UnsupportedPlatform("checkpoints require POSIX fd-relative no-follow operations")
    required = {os.open, os.stat, os.mkdir, os.unlink, os.rmdir}
    if not required.issubset(os.supports_dir_fd):
        raise UnsupportedPlatform("fd-relative filesystem operations are unavailable")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _record(path: str, reason: str, disposition: str = "incomplete") -> dict[str, str]:
    return {"path": path, "disposition": disposition, "reason": reason}


class _CheckpointStorage:
    """Own one private capture directory through directory descriptors."""

    def __init__(self, path: Path, initial: os.stat_result, workspace_real: str):
        self.path = path
        self.initial = initial
        self.workspace_real = workspace_real
        self.name = f"checkpoint-{uuid.uuid4().hex}"
        self.folder_path = path / self.name
        self.store_fd: int | None = None
        self.folder_fd: int | None = None
        self.blobs_fd: int | None = None
        self.created = False
        self.folder_info: os.stat_result | None = None
        self.blobs_info: os.stat_result | None = None

    def __enter__(self) -> _CheckpointStorage:
        try:
            self.store_fd = os.open(self.path, _DIR_FLAGS)
            if _identity(os.fstat(self.store_fd)) != _identity(self.initial):
                raise RuntimeError("checkpoint storage changed before open")
            os.mkdir(self.name, 0o700, dir_fd=self.store_fd)
            self.created = True
            self.folder_fd = os.open(self.name, _DIR_FLAGS, dir_fd=self.store_fd)
            self.folder_info = os.fstat(self.folder_fd)
            os.mkdir("blobs", 0o700, dir_fd=self.folder_fd)
            self.blobs_fd = os.open("blobs", _DIR_FLAGS, dir_fd=self.folder_fd)
            self.blobs_info = os.fstat(self.blobs_fd)
            self._verify_path()
            return self
        except BaseException:
            try:
                if self.created:
                    self._cleanup()
            finally:
                self._close()
            raise

    def __exit__(self, exc_type, _exc, _tb) -> None:
        changed = False
        if exc_type is None:
            try:
                self._verify_path()
            except (OSError, RuntimeError):
                changed = True
        try:
            if exc_type is not None or changed:
                self._cleanup()
        finally:
            self._close()
        if changed:
            raise RuntimeError("checkpoint storage changed during capture; capture removed")

    def _verify_path(self) -> None:
        assert self.store_fd is not None
        linked = os.stat(self.path, follow_symlinks=False)
        opened = os.fstat(self.store_fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode)
        if identity(linked) != identity(opened) or identity(opened) != identity(self.initial):
            raise RuntimeError("checkpoint storage path changed")
        if os.path.commonpath((self.workspace_real, os.path.realpath(self.path))) == self.workspace_real:
            raise RuntimeError("checkpoint storage moved into workspace")

    def _cleanup(self) -> None:
        assert self.store_fd is not None
        if self.folder_fd is None:
            # No content has been written; without an opened descriptor there is
            # no verified directory to remove by name.
            return
        if self.blobs_fd is not None:
            with os.scandir(self.blobs_fd) as listing:
                for item in listing:
                    os.unlink(item.name, dir_fd=self.blobs_fd)
            os.close(self.blobs_fd)
            self.blobs_fd = None
            if self._entry_is("blobs", self.folder_fd, self.blobs_info):
                os.rmdir("blobs", dir_fd=self.folder_fd)
        try:
            os.unlink("manifest.json", dir_fd=self.folder_fd)
        except FileNotFoundError:
            pass
        if self._entry_is(self.name, self.store_fd, self.folder_info):
            os.rmdir(self.name, dir_fd=self.store_fd)

    @staticmethod
    def _entry_is(name: str, parent_fd: int, expected: os.stat_result | None) -> bool:
        if expected is None:
            return False
        try:
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return (linked.st_dev, linked.st_ino) == (expected.st_dev, expected.st_ino)

    def _close(self) -> None:
        for name in ("blobs_fd", "folder_fd", "store_fd"):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def open_blob(self, name: str):
        assert self.blobs_fd is not None
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.blobs_fd)
        return os.fdopen(fd, "wb")

    def unlink_blob(self, name: str) -> None:
        assert self.blobs_fd is not None
        try:
            os.unlink(name, dir_fd=self.blobs_fd)
        except FileNotFoundError:
            pass

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        assert self.folder_fd is not None
        fd = os.open("manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.folder_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=True, sort_keys=True, indent=2)


def capture(workspace: os.PathLike[str] | str, storage_parent: os.PathLike[str] | str,
            limits: Limits = Limits()) -> dict[str, Any]:
    """Observe a workspace and save private raw bytes outside it.

    The storage parent must already exist, be a private real directory, and lie
    outside the workspace. Ordinary tracked, untracked, and ignored files are
    treated alike. Only named Git/checkpoint administrative directories are
    excluded. Any read failure or race is reported as incomplete coverage.
    """
    _require_posix()
    root = Path(os.path.abspath(workspace))
    store = Path(os.path.abspath(storage_parent))
    root_info = os.stat(root, follow_symlinks=False)
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("workspace must be a real directory")
    store_info = os.stat(store, follow_symlinks=False)
    if not stat.S_ISDIR(store_info.st_mode) or store_info.st_mode & 0o077:
        raise ValueError("storage parent must be a private real directory (mode 0700)")
    real_root, real_store = os.path.realpath(root), os.path.realpath(store)
    if os.path.commonpath((real_root, real_store)) == real_root:
        raise ValueError("checkpoint storage must be outside the workspace")
    with _CheckpointStorage(store, store_info, real_root) as private:
        return _capture_contents(root, root_info, store, limits, private)


def _capture_contents(root: Path, root_info: os.stat_result, store: Path,
                      limits: Limits, private: _CheckpointStorage) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    coverage: list[dict[str, str]] = [
        _record(str(store), "checkpoint storage outside workspace", "excluded")]
    counts = {"files": 0, "entries": 0, "bytes": 0}
    checkpoint_id = private.name.removeprefix("checkpoint-")

    def discard_subtree(rel: str) -> None:
        for path in list(entries):
            if not rel or path == rel or path.startswith(rel + "/"):
                entry = entries.pop(path)
                if entry["type"] == "file":
                    private.unlink_blob(entry["blob"].removeprefix("blobs/"))
                    counts["files"] -= 1
                    counts["bytes"] -= entry["size"]

    def scan(directory_fd: int, rel: str) -> bool:
        try:
            names = []
            with os.scandir(directory_fd) as listing:
                for item in listing:
                    names.append(item.name)
                    if len(names) > limits.max_entries - counts["entries"]:
                        coverage.append(_record(rel or ".", "directory listing exceeds entry limit"))
                        return True
            names.sort()
        except OSError as exc:
            coverage.append(_record(rel or ".", f"directory listing failed: {exc.strerror}"))
            return True
        for name in names:
            path = f"{rel}/{name}" if rel else name
            if name in _EXCLUDED_NAMES:
                coverage.append(_record(path, "administrative path excluded", "excluded"))
                continue
            if len(os.fsencode(path)) > 512:
                coverage.append(_record(path, "path length limit reached"))
                continue
            if counts["entries"] >= limits.max_entries:
                coverage.append(_record(rel or ".", "entry count limit reached"))
                return True
            counts["entries"] += 1
            try:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                coverage.append(_record(path, f"stat failed: {exc.strerror}"))
                continue
            if stat.S_ISLNK(before.st_mode):
                coverage.append(_record(path, "symlink not traversed"))
                continue
            if stat.S_ISDIR(before.st_mode):
                try:
                    child_fd = os.open(name, _DIR_FLAGS, dir_fd=directory_fd)
                    opened = os.fstat(child_fd)
                    if _identity(before) != _identity(opened):
                        coverage.append(_record(path, "directory changed before open"))
                        os.close(child_fd)
                        continue
                except OSError as exc:
                    coverage.append(_record(path, f"directory open failed: {exc.strerror}"))
                    continue
                entries[path] = {"type": "directory", "mode": stat.S_IMODE(before.st_mode)}
                try:
                    stop = scan(child_fd, path)
                    after = os.fstat(child_fd)
                    linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if _identity(before) != _identity(after) or _identity(before) != _identity(linked):
                        coverage.append(_record(path, "directory changed during scan"))
                        discard_subtree(path)
                except OSError as exc:
                    coverage.append(_record(path, f"directory verification failed: {exc.strerror}"))
                    discard_subtree(path)
                    stop = False
                finally:
                    os.close(child_fd)
                if stop:
                    return True
                continue
            if not stat.S_ISREG(before.st_mode):
                coverage.append(_record(path, "special file not read"))
                continue
            if before.st_nlink != 1:
                coverage.append(_record(path, "hard-linked file not read"))
                continue
            if counts["files"] >= limits.max_files:
                coverage.append(_record(rel or ".", "file count limit reached"))
                return True
            if before.st_size > limits.max_file_bytes:
                coverage.append(_record(path, "file size limit exceeded"))
                continue
            if counts["bytes"] + before.st_size > limits.max_total_bytes:
                coverage.append(_record(rel or ".", "total byte limit reached"))
                return True
            blob_name = uuid.uuid4().hex
            source_fd = None
            try:
                source_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
                opened = os.fstat(source_fd)
                if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode):
                    raise OSError("file changed before open")
                digest = hashlib.sha256()
                size = 0
                with private.open_blob(blob_name) as output:
                    while True:
                        chunk = os.read(source_fd, min(_CHUNK, limits.max_file_bytes + 1 - size))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limits.max_file_bytes or counts["bytes"] + size > limits.max_total_bytes:
                            raise OSError("file grew beyond byte limit")
                        output.write(chunk)
                        digest.update(chunk)
                after = os.fstat(source_fd)
                linked = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if size != before.st_size or _identity(before) != _identity(after) or _identity(before) != _identity(linked):
                    raise OSError("file changed during read")
                entries[path] = {"type": "file", "mode": stat.S_IMODE(before.st_mode),
                                 "size": size, "sha256": digest.hexdigest(),
                                 "blob": f"blobs/{blob_name}"}
                counts["files"] += 1
                counts["bytes"] += size
            except OSError as exc:
                private.unlink_blob(blob_name)
                coverage.append(_record(path, f"file not captured: {exc.strerror or str(exc)}"))
            finally:
                if source_fd is not None:
                    os.close(source_fd)
        return False

    try:
        root_fd = os.open(root, _DIR_FLAGS)
    except OSError as exc:
        coverage.append(_record(".", f"workspace open failed: {exc.strerror}"))
    else:
        try:
            if _identity(root_info) != _identity(os.fstat(root_fd)):
                coverage.append(_record(".", "workspace changed before open"))
            else:
                if scan(root_fd, ""):
                    coverage.append(_record(".", "scan stopped before all workspace paths were visited"))
            try:
                if _identity(root_info) != _identity(os.fstat(root_fd)) or _identity(root_info) != _identity(os.stat(root, follow_symlinks=False)):
                    coverage.append(_record(".", "workspace changed during scan"))
                    discard_subtree("")
            except OSError as exc:
                coverage.append(_record(".", f"workspace verification failed: {exc.strerror}"))
                discard_subtree("")
        finally:
            os.close(root_fd)
    manifest: dict[str, Any] = {
        "schema": 1, "label": "filesystem observation", "checkpoint_id": checkpoint_id,
        "workspace": str(root), "storage": str(private.folder_path), "entries": entries,
        "coverage": coverage,
        "coverage_complete_under_policy": not any(c["disposition"] == "incomplete" for c in coverage),
        "counts": counts,
    }
    private.write_manifest(manifest)
    return manifest


def observed_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two observations without attributing any change to an actor."""
    if before.get("schema") != 1 or after.get("schema") != 1:
        raise ValueError("unsupported checkpoint schema")
    if before.get("workspace") != after.get("workspace"):
        raise ValueError("checkpoints describe different workspaces")
    changes = []
    unverified = []
    old, new = before["entries"], after["entries"]
    for path in sorted(old.keys() | new.keys()):
        if any(c["disposition"] == "incomplete" and
               (c["path"] == "." or path == c["path"] or path.startswith(c["path"] + "/"))
               for c in before["coverage"] + after["coverage"]):
            unverified.append(path)
            continue
        left, right = old.get(path), new.get(path)
        # Blob names identify private copies, not file contents.
        if ({k: v for k, v in left.items() if k != "blob"} if left else None) == (
                {k: v for k, v in right.items() if k != "blob"} if right else None):
            continue
        kind = "created" if left is None else "deleted" if right is None else "modified"
        changes.append({"path": path, "kind": kind, "before": left, "after": right})
    return {
        "label": "observed filesystem changes",
        "attribution": "unknown",
        "coverage_complete_under_policy": bool(before["coverage_complete_under_policy"] and after["coverage_complete_under_policy"]),
        "coverage": {"before": before["coverage"], "after": after["coverage"]},
        "changes": changes,
        "unverified_paths": unverified,
    }
