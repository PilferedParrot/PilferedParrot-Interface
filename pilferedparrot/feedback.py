"""Explicit opt-in, local aggregate feedback. This module has no network transport.

Only fixed vocabulary enters counters. Consent and counters share transactions so
revocation also fences queued events and other running app/CLI processes.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import sqlite3
import stat
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__

POLICY_VERSION = 1
RETENTION_DAYS = 30
CATEGORIES = ("usage", "problems", "preferences", "local_changes")
EVENTS = {
    "usage": frozenset({"message_sent", "new_session", "model_changed", "reasoning_changed",
                         "context_changed", "whiteboard_opened", "command_run"}),
    "problems": frozenset({"provider_failed", "request_failed", "cancelled"}),
    "preferences": frozenset({"tone_original", "tone_darker", "surface_minimal",
        "surface_balanced", "surface_maximal", "readability_standard", "readability_stronger",
        "notifications_granted", "notifications_denied", "notifications_dismissed",
        "notifications_unavailable", "notifications_unasked"}),
}
SURFACES = frozenset({"work", "chat", "cli"})
MAX_COUNT = 10_000
PACKAGE_ROOT = Path(__file__).resolve().parent


def disabled() -> bool:
    return any(os.environ.get(key, "").lower() in {"1", "true", "yes"}
               for key in ("DO_NOT_TRACK", "PILFEREDPARROT_TELEMETRY_DISABLED"))


def _day() -> int:
    return int(time.time() // 86400)


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._queue: queue.Queue = queue.Queue(maxsize=128)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FeedbackStore:
        from .web_persistence import chat_store_path
        path = chat_store_path(config)
        return cls(path.with_name(path.name + ".feedback.sqlite3"))

    def _validate_files(self) -> bool:
        total = 0
        if not self.path.exists() and not self.path.is_symlink():
            return False
        for path in (self.path, *(Path(str(self.path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            if not path.exists() and not path.is_symlink():
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise OSError("feedback storage must use regular files")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise OSError("feedback storage must belong to the current user")
            total += info.st_size
            if total > 4_000_000:
                raise OSError("feedback storage exceeds size limit")
            if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600:
                os.chmod(path, 0o600, follow_symlinks=False)
        return True

    @contextmanager
    def _db(self, *, create: bool = False):
        # No creation on startup, report preview, or any ordinary interaction.
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
        if not self._validate_files():
            yield None
            return
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.2)
        try:
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise sqlite3.DatabaseError("feedback requires bounded rollback-journal storage")
            db.execute("PRAGMA secure_delete=ON")
            if create:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS consent (
                        category TEXT PRIMARY KEY, enabled INTEGER, policy INTEGER, revision TEXT);
                    CREATE TABLE IF NOT EXISTS counts (
                        day INTEGER, category TEXT, event TEXT, surface TEXT, count INTEGER,
                        PRIMARY KEY (day, category, event, surface));
                """)
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM counts WHERE day < ? OR day > ?",
                           (_day() - RETENTION_DAYS + 1, _day()))
                yield db
        finally:
            db.close()

    @staticmethod
    def _revisions(db) -> dict[str, str]:
        if db is None:
            return {}
        return {category: revision for category, enabled, policy, revision in
                db.execute("SELECT category, enabled, policy, revision FROM consent")
                if category in CATEGORIES and enabled == 1 and policy == POLICY_VERSION
                and isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{32}", revision)}

    @classmethod
    def _choices(cls, db) -> dict[str, bool]:
        revisions = cls._revisions(db)
        return {category: category in revisions for category in CATEGORIES}

    @staticmethod
    def _revision(db) -> str:
        rows = list(db.execute("SELECT category, revision FROM consent ORDER BY category")) if db else []
        return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()

    def snapshot(self) -> dict[str, str]:
        """Capture consent before an action; no lock waiting and no DB creation.

        Opaque revisions fence queued events even across clock adjustments and
        processes. They are internal consent generations, never exported IDs.
        """
        if disabled():
            return {}
        try:
            if not self._validate_files():
                return {}
            db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)
            try:
                if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    return {}
                return self._revisions(db)
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            return {}

    def status(self) -> dict[str, Any]:
        available = True
        try:
            with self._db() as db:
                choices = self._choices(db)
                revision = self._revision(db)
        except (OSError, sqlite3.Error):
            choices = dict.fromkeys(CATEGORIES, False)
            revision = "unavailable"
            available = False
        return {"policy_version": POLICY_VERSION, "consent": choices,
                "disabled": disabled(), "available": available, "retention_days": RETENTION_DAYS,
                "revision": revision}

    def set_consent(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"policy_version", "category", "enabled"}:
            raise ValueError("choose one feedback category and explicitly accept policy version 1")
        category, enabled = payload["category"], payload["enabled"]
        if not isinstance(category, str) or category not in CATEGORIES or type(enabled) is not bool \
                or type(payload["policy_version"]) is not int or payload["policy_version"] != POLICY_VERSION:
            raise ValueError("invalid feedback choice or policy version")
        if disabled() and enabled:
            raise ValueError("feedback is disabled by the environment")
        with self._db(create=True) as db:
            # Every explicit decision fences older queued observations. Turning off
            # a category also deletes all its retained counts.
            previous = db.execute("SELECT enabled, policy FROM consent WHERE category = ?",
                                  (category,)).fetchone()
            db.execute("INSERT OR REPLACE INTO consent VALUES (?, ?, ?, ?)",
                       (category, int(enabled), POLICY_VERSION, secrets.token_hex(16)))
            if not enabled or previous != (1, POLICY_VERSION):
                db.execute("DELETE FROM counts WHERE category = ?", (category,))
        return self.status()

    def clear(self, reset: bool = False) -> dict[str, Any]:
        with self._db() as db:
            if db is not None:
                db.execute("DELETE FROM counts")
                db.execute("UPDATE consent SET revision = ?", (secrets.token_hex(16),))
                if reset:
                    db.execute("UPDATE consent SET enabled = 0")
        return self.status()

    def record(self, category: str, event: str, surface: str,
               consent: dict[str, str] | None = None) -> None:
        """Bounded and lossy; no lock waiting for consent reads in the caller."""
        try:
            if disabled() or event not in EVENTS.get(category, ()) or surface not in SURFACES:
                return
            revision = (self.snapshot() if consent is None else consent).get(category)
            if not revision:
                return
            with self._lock:
                if self._closed:
                    return
                self._queue.put_nowait((revision, category, event, surface))
                if self._thread is None:
                    self._thread = threading.Thread(target=self._consume, name="local-feedback", daemon=True)
                    self._thread.start()
        except Exception:
            # Telemetry cannot become a new source of interface errors.
            return

    def _consume(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            try:
                if item is None:
                    return
                if disabled():
                    continue
                revision, category, event, surface = item
                with self._db() as db:
                    if db is None:
                        continue
                    consent = db.execute("SELECT enabled, policy, revision FROM consent WHERE category = ?",
                                         (category,)).fetchone()
                    if consent != (1, POLICY_VERSION, revision):
                        continue
                    db.execute("""INSERT INTO counts VALUES (?, ?, ?, ?, 1)
                        ON CONFLICT(day, category, event, surface)
                        DO UPDATE SET count = MIN(count + 1, ?)""",
                        (_day(), category, event, surface, MAX_COUNT))
            except Exception:
                pass
            finally:
                self._queue.task_done()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            if thread is not None:
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    # Bounded shutdown may discard unsaved counts.
                    while True:
                        try:
                            self._queue.get_nowait()
                            self._queue.task_done()
                        except queue.Empty:
                            break
                    self._queue.put_nowait(None)
        if thread is not None:
            thread.join(timeout=0.3)

    def report(self, feedback: dict[str, Any] | None = None, *, _preview: bool = False) -> dict[str, Any]:
        manual = validate_feedback(feedback) if feedback is not None else None
        report: dict[str, Any] = {
            "schema_version": 1, "policy_version": POLICY_VERSION,
            "app_version": __version__,
            "platform": {"linux": "linux", "win32": "windows", "darwin": "macos"}.get(sys.platform, "other"),
            "window_days": RETENTION_DAYS, "delivery": "manual_export",
            "counts": [],
        }
        # Check consent within the same transaction used to read aggregates and
        # inspect shipped sources. Revocation cannot race the inspection.
        with self._db() as db:
            revision = self._revision(db)
            choices = self._choices(db)
            if disabled():
                choices = dict.fromkeys(CATEGORIES, False)
            report["consent"] = choices
            if db is not None:
                rows = db.execute("SELECT category, event, surface, SUM(MIN(count, ?)) FROM counts "
                                  "WHERE typeof(count) = 'integer' AND count > 0 "
                                  "GROUP BY category, event, surface ORDER BY category, event, surface",
                                  (MAX_COUNT,))
                for category, event, surface, count in rows:
                    if choices.get(category) and event in EVENTS.get(category, ()) and surface in SURFACES \
                            and type(count) is int and count > 0:
                        report["counts"].append({"category": category, "event": event, "surface": surface,
                                                 "count": min(count, MAX_COUNT * RETENTION_DAYS)})
            if choices["local_changes"]:
                report["local_changes"] = local_change_summary()
        if manual is not None:
            report["feedback"] = manual
        if _preview:
            # This token stays in the local API envelope, never in the exported report.
            return {"report": report, "revision": revision}
        return report


def validate_feedback(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"kind", "area", "scope", "description", "change"}:
        raise ValueError("written feedback needs kind, area, scope, description and change")
    for key, allowed in {
        "kind": {"problem", "preference", "local_fix"},
        "area": {"work", "chat", "appearance", "providers", "accessibility", "other"},
        "scope": {"personal", "general", "unsure"},
    }.items():
        if not isinstance(value[key], str) or value[key] not in allowed:
            raise ValueError("invalid written feedback category")
    for key in ("description", "change"):
        if not isinstance(value[key], str) or len(value[key]) > 2000:
            raise ValueError("written feedback fields must be text of at most 2000 characters")
    if not value["description"].strip():
        raise ValueError("describe the feedback before including it")
    return dict(value)


def _read_source(path: Path, limit: int) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise OSError("source unavailable")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit:
            raise OSError("source unavailable")
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise OSError("source too large")
    return content


def local_change_summary(root: Path = PACKAGE_ROOT) -> dict[str, Any]:
    """Inspect only bounded, named shipped sources, never Git or user projects.

    This is an advisory comparison against the distributed baseline; absence of
    detected edits cannot establish that an installation is unmodified.
    """
    unknown = {"status": "unavailable", "coverage": "listed_shipped_sources_only"}
    manifest_path = root / "feedback-baseline.json"
    try:
        manifest = json.loads(_read_source(manifest_path, 100_000).decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("version") != __version__:
            return unknown
        files = manifest.get("files")
        if not isinstance(files, dict) or not 1 <= len(files) <= 256:
            return unknown
        groups = {name: {"matching": 0, "changed": 0, "unavailable": 0}
                  for name in ("interface", "core", "desktop")}
        for relative, expected in files.items():
            if not isinstance(relative, str) or not re.fullmatch(
                r"(?:[a-zA-Z_][a-zA-Z0-9_]*\.py|web_assets/[a-zA-Z0-9_-]+\.(?:js|css|html))", relative,
            ) or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                return unknown
            group = "interface" if relative.startswith("web_assets/") else "desktop" \
                if relative in {"windows.py", "native_window.py", "web_native.py", "desktop_auth.py"} else "core"
            path = root / relative
            try:
                if path.parent.is_symlink() or path.is_symlink() \
                        or not path.resolve().is_relative_to(root.resolve()):
                    raise OSError("symbolic link")
                content = _read_source(path, 1_000_000)
                actual = hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
                groups[group]["matching" if actual == expected else "changed"] += 1
            except OSError:
                groups[group]["unavailable"] += 1
        return {"status": "compared", "coverage": "listed_shipped_sources_only", "components": groups}
    except (OSError, ValueError, TypeError):
        return unknown
