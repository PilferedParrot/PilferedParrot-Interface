"""Transactional SQLite document journal for a future ChatStore cutover.

This module does not alter or replace ``chats.json``. Call ``import_json`` on
each startup before reading or writing state. The first import retains the
source file's exact bytes; later calls verify that the source is unchanged.
The document remains opaque so historical and unknown fields survive intact.
Cutover must quiesce the JSON writer or import an immutable copy. If the source
changes after a committed import, this store fails closed; recovery uses the
retained raw backup rather than silently importing the changed file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
APPLICATION_ID = 0x50504931  # "PPI1"
SOURCE_VERSION_MAX = 8
BUSY_TIMEOUT_MS = 5_000
EVENT_PAYLOAD_MAX_BYTES = 1_000_000
_EVENT_KIND = re.compile(r"[a-z][a-z0-9_/-]{0,63}\Z")
_EVENT_ID = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PRIVATE_KEYS = frozenset({
    "account", "accountid", "accountemail", "email", "apikey", "accesstoken",
    "refreshtoken", "authorization", "credential", "credentials", "secret", "token",
})


class StateStoreError(RuntimeError):
    """A state file, schema, source, or transaction failed validation."""


class RevisionConflict(StateStoreError):
    """The document changed after the caller read its revision."""


class SourceChanged(StateStoreError):
    """The original JSON source differs from the retained import."""


@dataclass(frozen=True)
class StateSnapshot:
    revision: int
    document: dict[str, Any]
    tree_hash: str


@dataclass(frozen=True)
class SaveResult:
    revision: int
    tree_hash: str
    event_seq: int | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class JournalEvent:
    seq: int
    event_id: str
    session_key: str
    run_id: str
    kind: str
    payload: dict[str, Any]
    state_revision: int


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _decode_document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (UnicodeError, ValueError, TypeError) as error:
        raise StateStoreError("invalid JSON state document") from error
    if not isinstance(value, dict) or not isinstance(value.get("chats"), list):
        raise StateStoreError("state document has no work-session list")
    version = value.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) \
            or not 1 <= version <= SOURCE_VERSION_MAX:
        raise StateStoreError("unsupported JSON state version")
    return value


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    try:
        return json.dumps(document, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise StateStoreError("state document is not finite JSON") from error


def _tree_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _sanitize_event(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("sessionUpdate") == "_auth/status_update" \
                or value.get("method") == "_auth/status_update":
            raise StateStoreError("auth status must not enter the event journal")
        return {
            key: _sanitize_event(item) for key, item in value.items()
            if isinstance(key, str)
            and re.sub(r"[^a-z0-9]", "", key.lower()) not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_event(item) for item in value]
    if isinstance(value, str):
        return _EMAIL.sub("[redacted-email]", value)
    return value


def _event_scope(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() \
            or len(value) > 256 \
            or any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid {field}")
    return value


def _prepared_event(
    kind: str, payload: dict[str, Any], event_id: str | None,
) -> tuple[str, bytes]:
    if not isinstance(kind, str) or not _EVENT_KIND.fullmatch(kind) \
            or kind.startswith("_auth/") or not isinstance(payload, dict):
        raise ValueError("invalid event kind or payload")
    safe_payload = _sanitize_event(payload)
    payload_json = _canonical_bytes(safe_payload)
    if len(payload_json) > EVENT_PAYLOAD_MAX_BYTES:
        raise ValueError("event payload exceeds limit")
    if event_id is None:
        event_id = uuid.uuid4().hex
    if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
        raise ValueError("invalid event_id")
    return event_id, payload_json


def _read_stable_source(source: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    if source.is_symlink() or not source.is_file():
        raise StateStoreError("JSON source must be a regular file")
    try:
        with source.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read()
            after = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or len(raw) != after.st_size:
            raise SourceChanged("JSON source changed during import")
        # Catch replacement of the path while the open descriptor was read.
        current = source.stat()
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise SourceChanged("JSON source changed during import")
        signature = after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        return raw, signature
    except OSError as error:
        raise StateStoreError("could not read JSON source") from error


def _source_signature(source: Path) -> tuple[int, int, int, int]:
    try:
        info = source.stat()
    except OSError as error:
        raise SourceChanged("JSON source is unavailable") from error
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class SQLiteStateStore:
    """One synchronous writer, with CAS revisions and an append-only event log.

    All methods serialize access to the connection. SQLite transactions cover
    the document revision and optional event together. Reopen with the same
    source file and call ``import_json`` before ``load``/``save``/``replay``.
    Event ingestion removes account fields and email addresses; callers must
    still redact configured API-key values before passing freeform event text.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._source_verified = False
        self._source_path: Path | None = None
        self._source_alias: Path | None = None
        self._source_stat: tuple[int, int, int, int] | None = None
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or any(
            Path(str(self.path) + suffix).is_symlink() for suffix in ("-wal", "-shm")
        ):
            raise StateStoreError("SQLite state paths must not be symlinks")
        if not self.path.exists():
            self._publish_initialized_database()
        self._check_owner_files()
        self._validate_existing_read_only()
        try:
            self._db = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=rw", uri=True,
                timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
                check_same_thread=False,
            )
            self._db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self._db.execute("PRAGMA foreign_keys=ON")
            mode = self._db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise StateStoreError("SQLite WAL mode is unavailable")
            self._db.execute("PRAGMA synchronous=FULL")
            self._check_owner_files()
        except (sqlite3.Error, OSError, StateStoreError) as error:
            if hasattr(self, "_db"):
                self._db.close()
            raise StateStoreError("could not open a safe SQLite state store") from error

    def _publish_initialized_database(self) -> None:
        """Publish only a complete private schema, never a partial final DB."""
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.init-", suffix=".sqlite3",
            dir=self.path.parent,
        )
        stage = Path(name)
        os.close(descriptor)
        try:
            if os.name == "posix":
                stage.chmod(0o600)
            stage_db = sqlite3.connect(
                stage.resolve().as_uri() + "?mode=rw", uri=True,
                timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
            )
            self._db = stage_db
            try:
                stage_db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                stage_db.execute("PRAGMA journal_mode=DELETE")
                stage_db.execute("PRAGMA synchronous=FULL")
                self._create_schema()
            finally:
                stage_db.close()
                del self._db
            with stage.open("rb") as handle:
                os.fsync(handle.fileno())
            try:
                # A hard link provides no-replace publication on Linux and
                # Windows. A concurrent initializer may win; validate its DB.
                os.link(stage, self.path)
            except FileExistsError:
                pass
            if os.name == "posix":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except (sqlite3.Error, OSError) as error:
            raise StateStoreError("could not initialize SQLite state safely") from error
        finally:
            stage.unlink(missing_ok=True)

    def _check_owner_files(self) -> None:
        if os.name != "posix":
            return
        for candidate in (self.path, Path(str(self.path) + "-wal"),
                          Path(str(self.path) + "-shm")):
            if not candidate.exists():
                continue
            info = candidate.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                    or stat.S_IMODE(info.st_mode) & 0o077:
                raise StateStoreError("SQLite state files must be owner-only regular files")

    def _validate_existing_read_only(self) -> None:
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=BUSY_TIMEOUT_MS / 1000)) as db:
                if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID \
                        or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise StateStoreError("unsupported SQLite state schema")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise StateStoreError("corrupt SQLite state")
                version = db.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                if version != (str(SCHEMA_VERSION),):
                    raise StateStoreError("unsupported SQLite state metadata")
                backup = db.execute(
                    "SELECT raw_json,sha256 FROM source_backup WHERE id=1"
                ).fetchone()
                recorded_hash = db.execute(
                    "SELECT value FROM meta WHERE key='source_sha256'"
                ).fetchone()
                if backup is not None and (
                    hashlib.sha256(backup[0]).hexdigest() != backup[1]
                    or recorded_hash != (backup[1],)
                ):
                    raise StateStoreError("SQLite source backup or metadata is corrupt")
                for sql in (
                    "SELECT raw_json, sha256, source_path FROM source_backup LIMIT 0",
                    "SELECT revision, full_json, tree_hash FROM state_document LIMIT 0",
                    "SELECT seq, event_id, session_key, run_id, payload_json "
                    "FROM events LIMIT 0",
                ):
                    db.execute(sql)
        except sqlite3.Error as error:
            raise StateStoreError("corrupt or unsupported SQLite state") from error

    def _create_schema(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            for statement in (
                "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                "CREATE TABLE source_backup("
                "id INTEGER PRIMARY KEY CHECK(id=1), source_path TEXT NOT NULL, "
                "raw_json BLOB NOT NULL, sha256 TEXT NOT NULL)",
                "CREATE TABLE state_document("
                "id INTEGER PRIMARY KEY CHECK(id=1), "
                "revision INTEGER NOT NULL CHECK(revision>=0), "
                "full_json BLOB NOT NULL, tree_hash TEXT NOT NULL)",
                "CREATE TABLE events("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "event_id TEXT NOT NULL UNIQUE, "
                "session_key TEXT NOT NULL, run_id TEXT NOT NULL, "
                "kind TEXT NOT NULL, "
                "payload_json BLOB NOT NULL, state_revision INTEGER NOT NULL)",
                "CREATE INDEX events_session_seq ON events(session_key,seq)",
            ):
                self._db.execute(statement)
            self._db.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)",
                             (str(SCHEMA_VERSION),))
            self._db.execute("COMMIT")
        except Exception:
            self._rollback()
            raise

    def _rollback(self) -> None:
        try:
            self._db.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _require_ready(self) -> None:
        if self._closed:
            raise StateStoreError("SQLite state store is closed")
        if not self._source_verified:
            raise StateStoreError("verify the unchanged JSON source before reading state")
        try:
            alias_changed = self._source_alias is None \
                or self._source_alias.resolve() != self._source_path
        except (OSError, RuntimeError):
            alias_changed = True
        if alias_changed:
            self._source_verified = False
            raise SourceChanged("JSON source path changed after verification")
        if self._source_path is None or self._source_stat != _source_signature(self._source_path):
            self._source_verified = False
            raise SourceChanged("JSON source changed after verification")

    def import_json(self, source: Path) -> StateSnapshot:
        """Import once, then verify the exact original bytes on every startup."""
        source_alias = Path(source).expanduser().absolute()
        source = source_alias.resolve()
        self._source_verified = False
        raw, signature = _read_stable_source(source)
        document = _decode_document(raw)
        digest = hashlib.sha256(raw).hexdigest()
        tree_hash = _tree_hash(document)
        with self._lock:
            if self._closed:
                raise StateStoreError("SQLite state store is closed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                previous = self._db.execute(
                    "SELECT source_path, raw_json, sha256 FROM source_backup WHERE id=1"
                ).fetchone()
                if previous is None:
                    if self._db.execute("SELECT 1 FROM state_document LIMIT 1").fetchone():
                        raise StateStoreError("state document exists without source backup")
                    self._db.execute(
                        "INSERT INTO source_backup(id,source_path,raw_json,sha256) VALUES(1,?,?,?)",
                        (str(source), raw, digest),
                    )
                    self._db.execute(
                        "INSERT INTO state_document(id,revision,full_json,tree_hash) "
                        "VALUES(1,0,?,?)", (raw, tree_hash),
                    )
                    self._db.execute("INSERT INTO meta(key,value) VALUES('source_sha256',?)",
                                     (digest,))
                elif previous != (str(source), raw, digest):
                    raise SourceChanged("JSON source differs from its retained backup")
                snapshot = self._load_locked()
                self._db.execute("COMMIT")
            except sqlite3.Error as error:
                self._rollback()
                raise StateStoreError("SQLite source import failed") from error
            except Exception:
                self._rollback()
                raise
            if signature != _source_signature(source):
                raise SourceChanged("JSON source changed during import")
            if source_alias.resolve() != source:
                raise SourceChanged("JSON source path changed during import")
            self._source_path = source
            self._source_alias = source_alias
            self._source_stat = signature
            self._source_verified = True
            return snapshot

    def source_backup(self) -> tuple[bytes, str, str]:
        """Return the exact imported bytes, SHA-256, and canonical source path."""
        with self._lock:
            self._require_ready()
            row = self._db.execute(
                "SELECT raw_json, sha256, source_path FROM source_backup WHERE id=1"
            ).fetchone()
            if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
                raise StateStoreError("source backup is missing or corrupt")
            return row[0], row[1], row[2]

    def _load_locked(self) -> StateSnapshot:
        row = self._db.execute(
            "SELECT revision, full_json, tree_hash FROM state_document WHERE id=1"
        ).fetchone()
        if row is None:
            raise StateStoreError("state document is missing")
        document = _decode_document(row[1])
        if _tree_hash(document) != row[2]:
            raise StateStoreError("state document tree hash does not match")
        return StateSnapshot(int(row[0]), document, row[2])

    def load(self) -> StateSnapshot:
        with self._lock:
            self._require_ready()
            return self._load_locked()

    def _insert_event(self, event_id: str, session_key: str, run_id: str,
                      kind: str, payload: bytes, revision: int) -> int:
        cursor = self._db.execute(
            "INSERT INTO events(event_id,session_key,run_id,kind,payload_json,state_revision) "
            "VALUES(?,?,?,?,?,?)",
            (event_id, session_key, run_id, kind, payload, revision),
        )
        return int(cursor.lastrowid)

    def append_event(
        self, session_key: str, run_id: str, kind: str,
        payload: dict[str, Any], *, event_id: str | None = None,
        expected_revision: int | None = None,
    ) -> JournalEvent:
        """Durably append one sanitized progress event without saving the document.

        Return only after the WAL transaction commits; callers may publish the
        returned event to a browser stream then. A caller-supplied event ID can
        be retried safely if its scope, kind and sanitized payload are equal.
        A writer can require its document revision before a new append so a
        stale process cannot journal progress for another writer's state.
        """
        if expected_revision is not None and (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a nonnegative integer")
        session_key = _event_scope(session_key, "session_key")
        run_id = _event_scope(run_id, "run_id")
        event_id, payload_json = _prepared_event(kind, payload, event_id)
        with self._lock:
            self._require_ready()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                prior = self._db.execute(
                    "SELECT seq,session_key,run_id,kind,payload_json,state_revision "
                    "FROM events WHERE event_id=?", (event_id,),
                ).fetchone()
                if prior is not None:
                    if prior[1:5] != (session_key, run_id, kind, payload_json):
                        raise StateStoreError("event ID already belongs to different content")
                    seq, _, _, _, _, revision = prior
                else:
                    current = self._db.execute(
                        "SELECT revision FROM state_document WHERE id=1"
                    ).fetchone()
                    if current is None:
                        raise StateStoreError("state document is missing")
                    revision = int(current[0])
                    if expected_revision is not None and revision != expected_revision:
                        raise RevisionConflict("state revision changed")
                    seq = self._insert_event(
                        event_id, session_key, run_id, kind, payload_json, revision,
                    )
                self._db.execute("COMMIT")
            except sqlite3.Error as error:
                self._rollback()
                raise StateStoreError("SQLite event append failed") from error
            except Exception:
                self._rollback()
                raise
        return JournalEvent(seq, event_id, session_key, run_id, kind,
                            json.loads(payload_json), revision)

    def save_document(
        self, document: dict[str, Any], *, expected_revision: int,
        event_kind: str | None = None, event_payload: dict[str, Any] | None = None,
        event_id: str | None = None, session_key: str | None = None,
        run_id: str | None = None,
    ) -> SaveResult:
        """CAS-save a stable full-document snapshot and optional event atomically.

        The caller must stop concurrent mutation of ``document`` while this
        method serializes it (for example by holding ``ChatStore.lock``).
        """
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        full_json = _canonical_bytes(document)
        _decode_document(full_json)
        tree_hash = hashlib.sha256(full_json).hexdigest()
        if event_kind is None:
            if event_payload is not None or event_id is not None \
                    or session_key is not None or run_id is not None:
                raise ValueError("event fields require event_kind")
            payload_json = None
        else:
            session_key = _event_scope(session_key, "session_key")
            run_id = _event_scope(run_id, "run_id")
            event_id, payload_json = _prepared_event(event_kind, event_payload, event_id)
        with self._lock:
            self._require_ready()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                current = self._db.execute(
                    "SELECT revision FROM state_document WHERE id=1"
                ).fetchone()
                if current is None:
                    raise StateStoreError("state document is missing")
                if current[0] != expected_revision:
                    raise RevisionConflict("state revision changed")
                revision = expected_revision + 1
                self._db.execute(
                    "UPDATE state_document SET revision=?, full_json=?, tree_hash=? WHERE id=1",
                    (revision, full_json, tree_hash),
                )
                event_seq = self._insert_event(
                    event_id, session_key, run_id, event_kind, payload_json, revision,
                ) \
                    if payload_json is not None else None
                self._db.execute("COMMIT")
            except sqlite3.Error as error:
                self._rollback()
                raise StateStoreError("SQLite state save failed") from error
            except Exception:
                self._rollback()
                raise
            return SaveResult(revision, tree_hash, event_seq, event_id)

    def replay(self, *, after_seq: int = 0, limit: int = 128,
               session_key: str | None = None) -> list[JournalEvent]:
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0 \
                or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("invalid event replay cursor or limit")
        if session_key is not None:
            session_key = _event_scope(session_key, "session_key")
        with self._lock:
            self._require_ready()
            self._db.execute("BEGIN")
            try:
                current_revision = self._current_revision()
                select = ("SELECT seq,event_id,session_key,run_id,kind,payload_json,state_revision "
                          "FROM events WHERE seq>?")
                if session_key is None:
                    rows = self._db.execute(select + " ORDER BY seq LIMIT ?",
                                            (after_seq, limit)).fetchall()
                else:
                    rows = self._db.execute(select + " AND session_key=? ORDER BY seq LIMIT ?",
                                            (after_seq, session_key, limit)).fetchall()
                self._db.execute("COMMIT")
            except sqlite3.Error as error:
                self._rollback()
                raise StateStoreError("SQLite event replay failed") from error
            except Exception:
                self._rollback()
                raise
        try:
            events = []
            for seq, event_id, scope, run_id, kind, payload, revision in rows:
                decoded = json.loads(payload, object_pairs_hook=_unique_object,
                                     parse_constant=_reject_constant)
                if not isinstance(seq, int) or seq < 1 \
                        or not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id) \
                        or not isinstance(scope, str) or not scope \
                        or not isinstance(run_id, str) or not run_id \
                        or not isinstance(kind, str) or not _EVENT_KIND.fullmatch(kind) \
                        or not isinstance(decoded, dict) or _sanitize_event(decoded) != decoded \
                        or not isinstance(revision, int) or not 0 <= revision <= current_revision:
                    raise StateStoreError("event journal contains invalid data")
                events.append(JournalEvent(seq, event_id, scope, run_id, kind,
                                           decoded, revision))
            return events
        except (ValueError, TypeError, UnicodeError) as error:
            raise StateStoreError("event journal contains invalid JSON") from error

    def _current_revision(self) -> int:
        row = self._db.execute(
            "SELECT revision FROM state_document WHERE id=1"
        ).fetchone()
        if row is None:
            raise StateStoreError("state document is missing")
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()

    def __enter__(self) -> SQLiteStateStore:
        return self

    def __exit__(self, *_error: Any) -> None:
        self.close()
