"""Private, bounded one-turn filesystem observations.

Only the returned summary belongs in a session or an API response. Manifests and
blobs stay in the private checkpoint directory and are never served.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Any

from . import workspace_checkpoints


MAX_STORE_BYTES = 1024 * 1024 * 1024
RESERVED_TURN_BYTES = 210 * 1024 * 1024
MAX_CHECKPOINTS = 512
MAX_VISIBLE_CHANGES = 100
MAX_VISIBLE_COVERAGE = 30


class ObservationUnavailable(RuntimeError):
    pass


def _storage_bytes(folder: Path) -> int:
    """Count existing storage without following links; fail closed on surprises."""
    total = 0
    stack = [folder]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as listing:
            for item in listing:
                info = item.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    stack.append(Path(item.path))
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    total += info.st_size
                else:
                    raise ObservationUnavailable("checkpoint storage contains an unsupported entry")
    return total


def _safe_entry(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    if entry.get("type") == "file":
        return {"type": "file", "size": entry["size"], "sha256": entry["sha256"]}
    return {"type": "directory"}


def _safe_coverage(records: list[dict[str, str]]) -> dict[str, Any]:
    incomplete = [item for item in records if item.get("disposition") == "incomplete"]
    return {
        "incomplete_count": len(incomplete),
        "incomplete_paths": [item["path"] for item in incomplete[:MAX_VISIBLE_COVERAGE]],
        "truncated": len(incomplete) > MAX_VISIBLE_COVERAGE,
    }


def _summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    report = workspace_checkpoints.observed_changes(before, after)
    changes = report["changes"]
    return {
        "label": "Changes observed during this turn; authorship unknown",
        "status": "complete" if report["coverage_complete_under_policy"] else "incomplete",
        "before_checkpoint_id": before["checkpoint_id"],
        "after_checkpoint_id": after["checkpoint_id"],
        "coverage": {
            "before": _safe_coverage(before["coverage"]),
            "after": _safe_coverage(after["coverage"]),
        },
        "change_count": len(changes),
        "changes_truncated": len(changes) > MAX_VISIBLE_CHANGES,
        "changes": [{
            "path": item["path"], "kind": item["kind"],
            "before": _safe_entry(item["before"]),
            "after": _safe_entry(item["after"]),
        } for item in changes[:MAX_VISIBLE_CHANGES]],
        "unverified_count": len(report["unverified_paths"]),
    }


class TurnObservations:
    """Reserve room for two captures; never evict prior evidence implicitly."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()
        self.reserved = 0

    def begin(self, workspace: Path) -> "TurnCapture":
        with self.lock:
            workspace_real = os.path.realpath(workspace)
            parent_real = os.path.realpath(self.folder.parent)
            if os.path.commonpath((workspace_real, parent_real)) == workspace_real:
                raise ObservationUnavailable("checkpoint storage must be outside the workspace")
            if not self.folder.exists():
                self.folder.mkdir(mode=0o700)
            info = self.folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                raise ObservationUnavailable("private checkpoint storage is unavailable")
            if _storage_bytes(self.folder) + self.reserved + RESERVED_TURN_BYTES > MAX_STORE_BYTES:
                raise ObservationUnavailable("checkpoint storage limit reached")
            checkpoint_count = sum(
                item.name.startswith("checkpoint-") for item in self.folder.iterdir()
            )
            if checkpoint_count + 2 * (self.reserved // RESERVED_TURN_BYTES + 1) > MAX_CHECKPOINTS:
                raise ObservationUnavailable("checkpoint count limit reached")
            self.reserved += RESERVED_TURN_BYTES
        try:
            before = workspace_checkpoints.capture(workspace, self.folder)
        except BaseException:
            self.release()
            raise
        return TurnCapture(self, workspace, before)

    def release(self) -> None:
        with self.lock:
            self.reserved -= RESERVED_TURN_BYTES


class TurnCapture:
    def __init__(self, owner: TurnObservations, workspace: Path, before: dict[str, Any]):
        self.owner = owner
        self.workspace = workspace
        self.before = before

    def finish(self) -> dict[str, Any]:
        try:
            after = workspace_checkpoints.capture(self.workspace, self.owner.folder)
            return _summary(self.before, after)
        except Exception:
            return {
                "label": "Changes observed during this turn; authorship unknown",
                "status": "incomplete",
                "before_checkpoint_id": self.before["checkpoint_id"],
                "coverage": {"before": _safe_coverage(self.before["coverage"]),
                             "after": {"incomplete_count": 1, "incomplete_paths": ["."], "truncated": False}},
                "change_count": 0, "changes_truncated": False,
                "changes": [], "unverified_count": 0,
            }
        finally:
            self.owner.release()
