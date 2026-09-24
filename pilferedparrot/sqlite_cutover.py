"""Offline, explicit preparation and rollback export for SQLite chat authority.

This tool cannot stop a JSON writer. The operator must stop every process using
the source before invoking it and keep the source unchanged while SQLite is in
use. Its checks catch changes; they do not substitute for quiescence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from .sqlite_state import SQLiteStateStore, StateStoreError


def validate_paths(source: Path, database: Path) -> tuple[Path, Path]:
    """Require a private POSIX DB directory and an unchanged regular source."""
    if os.name != "posix":
        raise StateStoreError("SQLite cutover requires POSIX rollback export")
    source = Path(os.path.abspath(os.fspath(source.expanduser())))
    database = Path(os.path.abspath(os.fspath(database.expanduser())))
    if source == database:
        raise StateStoreError("SQLite database and JSON source must differ")
    try:
        source_info = source.lstat()
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_uid != os.getuid() \
                or stat.S_IMODE(source_info.st_mode) & 0o077:
            raise StateStoreError("JSON source must be an owner-only regular file")
        parent = database.parent
        # Resolve no component implicitly: an alias changed after the check
        # must never be accepted as the intended private database directory.
        for directory in (parent, *parent.parents):
            if directory.is_symlink():
                raise StateStoreError("SQLite database path must not contain symlinks")
        parent_info = parent.stat()
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() \
                or stat.S_IMODE(parent_info.st_mode) & 0o077:
            raise StateStoreError("SQLite database directory must be owner-only")
        if database.is_symlink() or (database.exists() and not database.is_file()):
            raise StateStoreError("SQLite database path must be a regular file")
        if database.exists() and os.path.samefile(source, database):
            raise StateStoreError("SQLite database and JSON source must differ")
    except OSError as error:
        raise StateStoreError("SQLite cutover paths are unavailable") from error
    return source, database


def inspect(source: Path, database: Path, *, create: bool = False,
            export: Path | None = None, expected_source_sha256: str | None = None,
            ) -> dict[str, str | int]:
    """Import or verify a quiesced source; optionally export a new rollback file."""
    source, database = validate_paths(source, database)
    if not create and not database.exists():
        raise StateStoreError("SQLite database does not exist")
    if expected_source_sha256 is not None and (
        len(expected_source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_source_sha256)
    ):
        raise StateStoreError("expected source SHA-256 must be lowercase hex")
    with SQLiteStateStore(database) as store:
        snapshot = store.import_json(source)
        backup, digest, _ = store.source_backup()
        if expected_source_sha256 is not None and digest != expected_source_sha256:
            raise StateStoreError("JSON source does not match the expected SHA-256")
        if hashlib.sha256(backup).hexdigest() != digest or source.read_bytes() != backup:
            raise StateStoreError("JSON source changed during cutover verification")
        result: dict[str, str | int] = {
            "source_sha256": digest,
            "revision": snapshot.revision,
            "tree_sha256": snapshot.tree_hash,
            "source_bytes": len(backup),
        }
        if export is not None:
            exported = store.export_json(export)
            if exported.revision != snapshot.revision \
                    or exported.tree_hash != snapshot.tree_hash:
                raise StateStoreError("export does not match SQLite authority")
            result["export_sha256"] = exported.sha256
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline SQLite cutover and rollback checks")
    parser.add_argument("command", choices=("prepare", "verify", "export"))
    parser.add_argument("--source", type=Path, required=True,
                        help="unchanged, stopped JSON source")
    parser.add_argument("--database", type=Path, required=True,
                        help="SQLite path within an existing owner-only directory")
    parser.add_argument("--destination", type=Path,
                        help="new JSON path for export; never overwrites another file")
    parser.add_argument("--expect-source-sha256")
    args = parser.parse_args(argv)
    if (args.command == "export") != (args.destination is not None):
        parser.error("--destination is required only for export")
    try:
        result = inspect(
            args.source, args.database, create=args.command == "prepare",
            export=args.destination,
            expected_source_sha256=args.expect_source_sha256,
        )
    except (StateStoreError, OSError) as error:
        parser.exit(1, f"SQLite cutover refused: {error}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
