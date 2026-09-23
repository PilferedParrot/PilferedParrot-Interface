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
import tempfile
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
    required = {os.open, os.stat, os.mkdir, os.unlink}
    if not required.issubset(os.supports_dir_fd):
        raise UnsupportedPlatform("fd-relative filesystem operations are unavailable")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _record(path: str, reason: str, disposition: str = "incomplete") -> dict[str, str]:
    return {"path": path, "disposition": disposition, "reason": reason}


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

    entries: dict[str, dict[str, Any]] = {}
    coverage: list[dict[str, str]] = [
        _record(str(store), "checkpoint storage outside workspace", "excluded")]
    counts = {"files": 0, "entries": 0, "bytes": 0}
    checkpoint_id = uuid.uuid4().hex
    folder = Path(tempfile.mkdtemp(prefix=f"checkpoint-{checkpoint_id}-", dir=store))
    os.chmod(folder, 0o700)
    (folder / "blobs").mkdir(mode=0o700)

    def discard_subtree(rel: str) -> None:
        for path in list(entries):
            if not rel or path == rel or path.startswith(rel + "/"):
                entry = entries.pop(path)
                if entry["type"] == "file":
                    (folder / entry["blob"]).unlink(missing_ok=True)
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
            blob = folder / "blobs" / blob_name
            source_fd = None
            try:
                source_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
                opened = os.fstat(source_fd)
                if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode):
                    raise OSError("file changed before open")
                digest = hashlib.sha256()
                size = 0
                with open(blob, "xb") as output:
                    os.chmod(blob, 0o600)
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
                blob.unlink(missing_ok=True)
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
                scan(root_fd, "")
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
        "workspace": str(root), "storage": str(folder), "entries": entries,
        "coverage": coverage,
        "coverage_complete_under_policy": not any(c["disposition"] == "incomplete" for c in coverage),
        "counts": counts,
    }
    with open(folder / "manifest.json", "x", encoding="utf-8") as output:
        json.dump(manifest, output, ensure_ascii=True, sort_keys=True, indent=2)
    os.chmod(folder / "manifest.json", 0o600)
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
