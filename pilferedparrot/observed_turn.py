"""Private, bounded one-turn filesystem observations.

Only the returned summary belongs in a session or an API response. Manifests and
blobs stay in the private checkpoint directory and are never served.
"""

from __future__ import annotations

import os
import ntpath
import re
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
OBSERVATION_LABEL = "Changes observed during this turn; authorship unknown"
_OMITTED_PATH = "[path omitted]"
_CHECKPOINT_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ObservationUnavailable(RuntimeError):
    pass


def _public_count(value: Any, maximum: int = 1_000_000) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _public_path(value: Any, *, allow_root: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    if value == ".":
        return value if allow_root else None
    try:
        size = len(value.encode("utf-8", "surrogateescape"))
    except UnicodeError:
        return None
    if not 0 < size <= 512 or os.path.isabs(value) or ntpath.isabs(value) \
            or any(part in {"", ".", ".."} for part in value.split("/")) \
            or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    return value


def _public_entry(value: Any) -> dict[str, Any] | None | bool:
    if value is None:
        return None
    if not isinstance(value, dict):
        return False
    if value.get("type") == "directory":
        return {"type": "directory"}
    size = _public_count(value.get("size"), 100 * 1024 * 1024)
    digest = value.get("sha256")
    if value.get("type") != "file" or size is None \
            or not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        return False
    return {"type": "file", "size": size, "sha256": digest}


def _public_coverage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("incomplete_paths"), list) \
            or type(value.get("truncated")) is not bool:
        return None
    count = _public_count(value.get("incomplete_count"))
    if count is None or count < len(value["incomplete_paths"]):
        return None
    original_paths = value["incomplete_paths"][:MAX_VISIBLE_COVERAGE]
    if any(not isinstance(path, str) for path in original_paths):
        return None
    paths = [_public_path(path, allow_root=True) or _OMITTED_PATH
             for path in original_paths]
    return {"incomplete_count": count, "incomplete_paths": paths,
            "truncated": value["truncated"] or len(value["incomplete_paths"]) > MAX_VISIBLE_COVERAGE}


def public_observation_summary(value: Any) -> dict[str, Any] | None:
    """Validate and bound a persisted summary before any browser response.

    This is also used for imported JSON/SQLite trees: persisted dictionaries
    are untrusted, even when PPI normally generated them.
    """
    if not isinstance(value, dict) or value.get("label") != OBSERVATION_LABEL \
            or not isinstance(value.get("status"), str) \
            or value["status"] not in {"complete", "incomplete"} \
            or not isinstance(value.get("coverage"), dict) \
            or not isinstance(value.get("changes"), list) \
            or type(value.get("changes_truncated")) is not bool:
        return None
    before_id = value.get("before_checkpoint_id")
    after_id = value.get("after_checkpoint_id")
    if not isinstance(before_id, str) or _CHECKPOINT_ID.fullmatch(before_id) is None \
            or after_id is not None and (
                not isinstance(after_id, str) or _CHECKPOINT_ID.fullmatch(after_id) is None
            ):
        return None
    count = _public_count(value.get("change_count"))
    unverified = _public_count(value.get("unverified_count"))
    if count is None or unverified is None or count < len(value["changes"]):
        return None
    before = _public_coverage(value["coverage"].get("before"))
    after = _public_coverage(value["coverage"].get("after"))
    if before is None or after is None:
        return None
    changes = []
    for item in value["changes"][:MAX_VISIBLE_CHANGES]:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) \
                or item["kind"] not in {"created", "deleted", "modified"} \
                or not isinstance(item.get("path"), str):
            return None
        path = _public_path(item["path"]) or _OMITTED_PATH
        prior = _public_entry(item.get("before"))
        later = _public_entry(item.get("after"))
        if prior is False or later is False:
            return None
        changes.append({"path": path, "kind": item["kind"],
                        "before": prior, "after": later})
    public = {
        "label": OBSERVATION_LABEL,
        "status": value["status"],
        "before_checkpoint_id": before_id,
        "coverage": {"before": before, "after": after},
        "change_count": count,
        "changes_truncated": value["changes_truncated"] or len(value["changes"]) > MAX_VISIBLE_CHANGES,
        "changes": changes,
        "unverified_count": unverified,
    }
    if after_id is not None:
        public["after_checkpoint_id"] = after_id
    return public


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
