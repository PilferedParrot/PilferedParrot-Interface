"""Opt-in, local-only preview of skill metadata.

Discovery reads SKILL.md frontmatter from explicitly configured directories. It
never returns instruction bodies and has no connection to provider prompts.
"""

from __future__ import annotations

import os
import re
import stat
from itertools import islice
from pathlib import Path
from typing import Any


MAX_ROOTS = 20
MAX_SKILLS = 100
MAX_CANDIDATES = 500
MAX_CHILDREN_PER_ROOT = 250
MAX_SKILL_FILE_BYTES = 32 * 1024
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$")


def discovery_status(config: dict[str, Any]) -> dict[str, Any]:
    """Return configuration status without touching any configured path."""
    settings = config.get("skills")
    if not isinstance(settings, dict):
        settings = {}
    roots = settings.get("roots", [])
    if not isinstance(roots, list):
        roots = []
    valid_roots = [root for root in roots if isinstance(root, str) and root.strip()]
    return {"enabled": settings.get("enabled") is True,
            "roots": len(valid_roots)}


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = value.replace('\\"', '"').replace('\\\\', '\\')
        elif quote == "'":
            value = value.replace("''", "'")
    else:
        # YAML comments are only recognized after whitespace. This small
        # metadata parser intentionally ignores tags and nested values.
        value = re.split(r"\s+#", value, maxsplit=1)[0]
    return value.strip()


def _metadata(data: bytes) -> tuple[str, str] | None:
    if len(data) > MAX_SKILL_FILE_BYTES:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() in {"---", "..."}:
            closed = True
            break
        match = _FIELD.match(line)
        if match:
            key, value = match.groups()
            if value not in {"|", ">", "|-", ">-"}:
                fields[key.lower()] = _scalar(value)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if (not closed or not name or not description or len(name) > MAX_NAME_CHARS
            or len(description) > MAX_DESCRIPTION_CHARS):
        return None
    return name, description


def _read_skill_file(directory_fd: int) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        file_fd = os.open("SKILL.md", flags, dir_fd=directory_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            return None
        with os.fdopen(file_fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_SKILL_FILE_BYTES + 1)
        return data if len(data) <= MAX_SKILL_FILE_BYTES else None
    finally:
        os.close(file_fd)


def discover(config: dict[str, Any]) -> list[dict[str, str]]:
    """Scan explicitly configured roots and return bounded metadata only."""
    status = discovery_status(config)
    if not status["enabled"]:
        return []
    settings = config.get("skills", {})
    roots = settings.get("roots", []) if isinstance(settings, dict) else []
    roots = [root for root in roots if isinstance(root, str) and root.strip()][:MAX_ROOTS]
    if os.name == "nt":
        return _discover_windows(roots)
    found: list[dict[str, str]] = []
    checked = 0
    for root_index, configured_root in enumerate(roots, start=1):
        root = Path(configured_root).expanduser()
        try:
            root = root.resolve(strict=True)
            if not root.is_dir():
                continue
            root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                              | getattr(os, "O_CLOEXEC", 0))
        except OSError:
            continue
        try:
            direct_data = _read_skill_file(root_fd)
            if direct_data is not None:
                metadata = _metadata(direct_data)
                if metadata:
                    found.append({"name": metadata[0], "description": metadata[1],
                                  "source": f"Root {root_index}"})
                    if len(found) >= MAX_SKILLS:
                        return found
            try:
                with os.scandir(root_fd) as iterator:
                    entries = sorted(
                        (entry.name for entry in islice(iterator, MAX_CHILDREN_PER_ROOT)),
                        key=str.casefold,
                    )
            except OSError:
                entries = []
            for entry in entries:
                checked += 1
                if checked > MAX_CANDIDATES:
                    return found
                if entry.startswith(".") or entry in {"node_modules", "__pycache__"}:
                    continue
                try:
                    child_fd = os.open(entry, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                                       | getattr(os, "O_NOFOLLOW", 0)
                                       | getattr(os, "O_CLOEXEC", 0), dir_fd=root_fd)
                except OSError:
                    continue
                try:
                    data = _read_skill_file(child_fd)
                    metadata = _metadata(data) if data is not None else None
                    if metadata:
                        found.append({"name": metadata[0], "description": metadata[1],
                                      "source": f"Root {root_index} / {entry}"})
                finally:
                    os.close(child_fd)
                if len(found) >= MAX_SKILLS:
                    return found
        finally:
            os.close(root_fd)
    return found


def _discover_windows(roots: list[str]) -> list[dict[str, str]]:
    """Path-based fallback for Windows, where dir_fd/openat are unavailable."""
    found: list[dict[str, str]] = []
    checked = 0
    for root_index, configured_root in enumerate(roots, start=1):
        try:
            root = Path(configured_root).expanduser().resolve(strict=True)
            if not root.is_dir():
                continue
            candidates = [(root, f"Root {root_index}")]
            children = sorted(
                (entry for entry in islice(root.iterdir(), MAX_CHILDREN_PER_ROOT)
                 if not entry.name.startswith(".") and entry.name not in {"node_modules", "__pycache__"}
                 and not entry.is_symlink() and entry.is_dir()),
                key=lambda entry: entry.name.casefold(),
            )
            candidates.extend((entry, f"Root {root_index} / {entry.name}") for entry in children)
        except OSError:
            continue
        for directory, source in candidates:
            checked += 1
            if checked > MAX_CANDIDATES:
                return found
            skill_file = directory / "SKILL.md"
            try:
                if skill_file.is_symlink() or not stat.S_ISREG(skill_file.stat(follow_symlinks=False).st_mode):
                    continue
                with skill_file.open("rb") as stream:
                    data = stream.read(MAX_SKILL_FILE_BYTES + 1)
            except OSError:
                continue
            metadata = _metadata(data)
            if metadata:
                found.append({"name": metadata[0], "description": metadata[1], "source": source})
                if len(found) >= MAX_SKILLS:
                    return found
    return found
