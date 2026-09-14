"""Scoped graphical sudo authentication for non-interactive provider CLIs.

The helper is intentionally supplied only in the environment of provider
processes.  It never receives, records, or relays a password through PPI; sudo
executes it and reads its standard output directly.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import shlex
import stat
import sys
from pathlib import Path
from typing import Mapping


_RUNTIME_DIRECTORY = "pilferedparrot"
_HELPER_TITLE = "PilferedParrot administrator authentication"


def _owned_private_directory(path: Path) -> bool:
    """Return whether *path* is a private, non-symlink directory we own."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _runtime_directory(environment: Mapping[str, str]) -> Path | None:
    value = environment.get("XDG_RUNTIME_DIR")
    if not value:
        return None
    root = Path(value)
    if not _owned_private_directory(root):
        return None
    directory = root / _RUNTIME_DIRECTORY
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        return None
    return directory if _owned_private_directory(directory) else None


def _helper_source(zenity: str) -> str:
    """Generate a standalone askpass executable with no password arguments."""
    return f"""#!/bin/sh
prompt=${{1:-Administrator authentication is required}}
exec {shlex.quote(zenity)} --password {shlex.quote('--title=' + _HELPER_TITLE)} "--text=$prompt"
"""


def _existing_helper_matches(path: Path, source: str) -> bool:
    """Accept only the exact private helper this process would have created."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            return False
        expected = source.encode("utf-8")
        if info.st_size != len(expected):
            return False
        return os.read(descriptor, len(expected) + 1) == expected
    except OSError:
        return False
    finally:
        os.close(descriptor)


def _askpass_path(environment: Mapping[str, str]) -> Path | None:
    if not (environment.get("DISPLAY") or environment.get("WAYLAND_DISPLAY")):
        return None
    zenity = shutil.which("zenity", path=environment.get("PATH"))
    if not zenity:
        return None
    directory = _runtime_directory(environment)
    if directory is None:
        return None
    source = _helper_source(zenity)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    helper = directory / f"sudo-askpass-{digest}.py"
    try:
        descriptor = os.open(
            helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o700,
        )
    except FileExistsError:
        if not _existing_helper_matches(helper, source):
            return None
    except OSError:
        return None
    else:
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(source)
        except OSError:
            try:
                helper.unlink()
            except OSError:
                pass
            return None
    return helper


def provider_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a provider-only environment with a safe graphical sudo askpass.

    A caller's configured helper takes precedence.  Without a graphical
    session, Zenity, or a private XDG runtime directory, the environment is
    unchanged so sudo retains its normal behavior.
    """
    result = dict(os.environ if environment is None else environment)
    if sys.platform != "linux":
        return result
    if result.get("SUDO_ASKPASS", "").strip():
        return result
    helper = _askpass_path(result)
    if helper is not None:
        result["SUDO_ASKPASS"] = str(helper)
    return result
