"""Runtime-attributed whiteboard posting for native provider processes.

The posting process receives note content on stdin.  Identity is derived from
provider-owned runtime records, never from fields supplied in that content.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator


_NATIVE_PROVIDERS = frozenset({"antigravity", "claude", "codex", "gemini"})
_DESCRIPTOR_FIELDS = frozenset({
    "version", "provider", "whiteboard_directory", "codex_home", "read_only",
})
_PAYLOAD_FIELDS = frozenset({
    "text", "author", "workspace", "kind", "title", "project", "topics",
    "evidence", "applies_to", "status", "reply_to", "expires_at", "basis",
})
_IDENTITY_FIELDS = frozenset({
    "identity", "model", "reasoning_effort", "source", "model_source",
    "reasoning_source",
})
_MAX_DESCRIPTOR_BYTES = 8_192
_MAX_PAYLOAD_BYTES = 32_000
_MAX_SESSION_BYTES = 16_000_000
_MAX_SESSION_LINE_BYTES = 256_000
_MAX_SESSION_FILES = 20_000
_THREAD_ID = re.compile(r"[A-Za-z0-9-]{8,128}")


def _is_private_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        return False
    if os.name == "posix":
        return info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700
    return True


def _context_directory(board: Path) -> Path:
    board.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = board / ".native-contexts"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if not _is_private_directory(directory):
        raise RuntimeError("native whiteboard context directory is not private")
    return directory


def _write_descriptor(config: dict[str, Any], provider: str, *, read_only: bool) -> Path:
    from .config import expanded_path
    from .whiteboard import whiteboard_directory

    board = whiteboard_directory(config)
    directory = _context_directory(board)
    codex_config = config.get("codex") if isinstance(config.get("codex"), dict) else {}
    configured_home = os.environ.get("CODEX_HOME")
    raw_config_path = codex_config.get("config_path") or "~/.codex/config.toml"
    descriptor = {
        "version": 1,
        "provider": provider,
        "whiteboard_directory": str(board),
        "codex_home": str(
            Path(configured_home).expanduser().resolve()
            if configured_home else expanded_path(str(raw_config_path)).parent
        ),
        "read_only": bool(read_only),
    }
    path = directory / f"{uuid.uuid4().hex}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor_fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor_fd, "w", encoding="utf-8") as output:
            json.dump(descriptor, output, ensure_ascii=False, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def _command(descriptor: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--whiteboard-post", str(descriptor)]
    return [
        str(Path(sys.executable).resolve()), str(Path(__file__).resolve()), str(descriptor),
    ]


def _render_command(command: list[str]) -> str:
    if os.name == "nt":
        # Native Windows agents use PowerShell. A quoted executable needs the
        # call operator; CreateProcess quoting alone is not a shell command.
        return "& " + " ".join("'" + item.replace("'", "''") + "'" for item in command)
    return shlex.join(command)


def _read_only(config: dict[str, Any], provider: str) -> bool:
    from .whiteboard import whiteboard_read_only
    settings = config.get(provider) if isinstance(config.get(provider), dict) else {}
    return whiteboard_read_only(settings)


def native_whiteboard_discovery(conversation: Any, config: dict[str, Any]) -> str:
    """Create one invocation descriptor and return its native posting instruction."""
    provider = str(getattr(conversation, "provider", "") or "")
    settings = config.get(provider) if isinstance(config.get(provider), dict) else {}
    if provider not in _NATIVE_PROVIDERS or settings.get("adapter") == "openai_compatible":
        return ""
    read_only = _read_only(config, provider)
    if read_only:
        return ""
    descriptor = _write_descriptor(config, provider, read_only=read_only)
    previous = getattr(conversation, "_native_whiteboard_descriptor", None)
    if isinstance(previous, str):
        try:
            Path(previous).unlink()
        except OSError:
            pass
    conversation._native_whiteboard_descriptor = str(descriptor)
    command = _render_command(_command(descriptor))
    return (
        "\n\n[Native whiteboard posting] To post a shared note, invoke this exact "
        + ("PowerShell command " if os.name == "nt" else "command ") +
        f"and send one JSON object on stdin: {command}. Required field: text. "
        "Optional fields: author, workspace, kind, title, project, topics, evidence, applies_to, "
        "status, reply_to, expires_at, basis. The helper records the invoking agent's "
        "runtime identity. Do not include identity, model, or reasoning fields and do not "
        "create shared .txt notes directly. Delegated workers must invoke the command "
        "themselves.\n"
    )


def cleanup_native_whiteboard_discovery(conversation: Any) -> None:
    """Remove the descriptor created for the just-finished provider invocation."""
    value = getattr(conversation, "_native_whiteboard_descriptor", None)
    if isinstance(value, str):
        try:
            Path(value).unlink()
        except OSError:
            pass
    try:
        delattr(conversation, "_native_whiteboard_descriptor")
    except AttributeError:
        pass


def _read_small_regular_file(path: Path, maximum: int) -> bytes:
    if not path.is_absolute():
        raise ValueError("native whiteboard descriptor path must be absolute")
    try:
        before = path.lstat()
    except OSError as error:
        raise ValueError("native whiteboard descriptor is invalid") from error
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ValueError("native whiteboard descriptor is invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError("native whiteboard descriptor is invalid")
        if os.name == "posix" and info.st_uid != os.geteuid():
            raise ValueError("native whiteboard descriptor owner is invalid")
        data = os.read(descriptor, maximum + 1)
        if len(data) > maximum:
            raise ValueError("native whiteboard descriptor is too large")
        return data
    finally:
        os.close(descriptor)


def _read_descriptor(path: Path) -> dict[str, Any]:
    if path.parent.name != ".native-contexts" or not _is_private_directory(path.parent):
        raise ValueError("native whiteboard descriptor location is invalid")
    try:
        value = json.loads(_read_small_regular_file(path, _MAX_DESCRIPTOR_BYTES))
    except (OSError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("native whiteboard descriptor is invalid") from error
    if not isinstance(value, dict) or set(value) != _DESCRIPTOR_FIELDS or value.get("version") != 1:
        raise ValueError("native whiteboard descriptor is invalid")
    provider = value.get("provider")
    if provider not in _NATIVE_PROVIDERS:
        raise ValueError("native whiteboard provider is invalid")
    for name in ("whiteboard_directory", "codex_home"):
        candidate = value.get(name)
        if not isinstance(candidate, str) or not candidate or not Path(candidate).is_absolute():
            raise ValueError(f"native whiteboard {name} is invalid")
    if not isinstance(value.get("read_only"), bool):
        raise ValueError("native whiteboard read_only is invalid")
    return value


def _validate_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("native whiteboard payload must be one JSON object")
    forbidden = set(value) & _IDENTITY_FIELDS
    if forbidden:
        raise ValueError(f"native whiteboard payload cannot set {sorted(forbidden)[0]}")
    unexpected = set(value) - _PAYLOAD_FIELDS
    if unexpected:
        raise ValueError(f"unknown native whiteboard field: {sorted(unexpected)[0]}")
    return value


def _read_payload(stream: Any) -> dict[str, Any]:
    data = stream.buffer.read(_MAX_PAYLOAD_BYTES + 1) if hasattr(stream, "buffer") \
        else stream.read(_MAX_PAYLOAD_BYTES + 1)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > _MAX_PAYLOAD_BYTES:
        raise ValueError("native whiteboard payload is too large")
    try:
        value = json.loads(data)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("native whiteboard payload must be one JSON object") from error
    return _validate_payload(value)


def _walk_session_files(root: Path, thread_id: str) -> list[Path]:
    sessions = root / "sessions"
    try:
        sessions_info = sessions.lstat()
    except OSError:
        return []
    if not stat.S_ISDIR(sessions_info.st_mode) or sessions.is_symlink():
        return []
    matches: list[Path] = []
    visited = 0
    try:
        walker = os.walk(sessions, topdown=True, followlinks=False)
        for directory, names, files in walker:
            safe_names = []
            for name in names:
                visited += 1
                if visited > _MAX_SESSION_FILES:
                    return []
                candidate = Path(directory) / name
                try:
                    if stat.S_ISDIR(candidate.lstat().st_mode) and not candidate.is_symlink():
                        safe_names.append(name)
                except OSError:
                    pass
            names[:] = safe_names
            for name in files:
                visited += 1
                if visited > _MAX_SESSION_FILES:
                    return []
                if not name.endswith(f"{thread_id}.jsonl"):
                    continue
                candidate = Path(directory) / name
                try:
                    info = candidate.lstat()
                except OSError:
                    continue
                if stat.S_ISREG(info.st_mode) and not candidate.is_symlink() \
                        and info.st_size <= _MAX_SESSION_BYTES:
                    matches.append(candidate)
    except OSError:
        return []
    return matches


def _bounded_lines(handle: Any, total: int) -> Iterator[bytes]:
    consumed = 0
    while consumed < total:
        line = handle.readline(min(_MAX_SESSION_LINE_BYTES + 1, total - consumed + 1))
        if not line:
            return
        consumed += len(line)
        if len(line) <= _MAX_SESSION_LINE_BYTES and line.endswith(b"\n"):
            yield line
            continue
        while line and not line.endswith(b"\n") and consumed < total:
            line = handle.readline(min(_MAX_SESSION_LINE_BYTES + 1, total - consumed + 1))
            consumed += len(line)


def _codex_runtime(descriptor: dict[str, Any], environment: dict[str, str]) -> tuple[str | None, str | None]:
    thread_id = environment.get("CODEX_THREAD_ID", "")
    if not _THREAD_ID.fullmatch(thread_id):
        return None, None
    matches = _walk_session_files(Path(descriptor["codex_home"]), thread_id)
    if len(matches) != 1:
        return None, None
    path = matches[0]
    try:
        if not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
            return None, None
    except OSError:
        return None, None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        session_fd = os.open(path, flags)
    except OSError:
        return None, None
    session_id: str | None = None
    model: str | None = None
    effort: str | None = None
    try:
        info = os.fstat(session_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SESSION_BYTES:
            return None, None
        with os.fdopen(session_fd, "rb") as stream:
            session_fd = -1
            for line in _bounded_lines(stream, info.st_size):
                try:
                    event = json.loads(line)
                except (UnicodeError, RecursionError, json.JSONDecodeError):
                    continue
                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta" and isinstance(payload.get("id"), str):
                    session_id = payload["id"]
                elif event.get("type") == "turn_context":
                    candidate_model = payload.get("model")
                    candidate_effort = payload.get("effort", payload.get("reasoning_effort"))
                    model = candidate_model.strip() \
                        if isinstance(candidate_model, str) and candidate_model.strip() else None
                    effort = candidate_effort.strip() \
                        if isinstance(candidate_effort, str) and candidate_effort.strip() else None
    finally:
        if session_fd >= 0:
            os.close(session_fd)
    return (model, effort) if session_id == thread_id else (None, None)


def _unknown_identity(provider: str) -> dict[str, Any]:
    from pilferedparrot.whiteboard_identity import normalize_identity
    return normalize_identity({"provider": provider, "source": "unknown"})


def _runtime_identity(descriptor: dict[str, Any], environment: dict[str, str]) -> dict[str, Any]:
    provider = descriptor["provider"]
    if provider != "codex":
        return _unknown_identity(provider)
    model, effort = _codex_runtime(descriptor, environment)
    if model is None and effort is None:
        return _unknown_identity(provider)
    from pilferedparrot.whiteboard_identity import runtime_identity
    config = {provider: {}, "_whiteboard_directory": descriptor["whiteboard_directory"]}
    return runtime_identity(
        config, provider, reported_model=model, reported_reasoning_effort=effort,
    )


def post_native(
    descriptor_path: Path, payload: dict[str, Any], *, environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Post one validated note using identity derived from the invoking runtime."""
    descriptor = _read_descriptor(descriptor_path)
    if descriptor["read_only"]:
        raise PermissionError("native whiteboard posting is unavailable in read-only mode")
    payload = _validate_payload(payload)
    identity = _runtime_identity(descriptor, dict(os.environ if environment is None else environment))
    note = dict(payload)
    text = note.pop("text", None)
    author = note.pop("author", None)
    workspace = note.pop("workspace", None)
    from pilferedparrot.whiteboard import Whiteboard
    config = {"_whiteboard_directory": descriptor["whiteboard_directory"]}
    return Whiteboard(config).post(
        text, author, workspace=workspace, identity=identity, **note,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(arguments) != 1:
            raise ValueError("usage: whiteboard_native.py DESCRIPTOR")
        payload = _read_payload(sys.stdin)
        result = post_native(Path(arguments[0]), payload)
        output = {"posted": True, **result}
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, PermissionError, RuntimeError, TypeError, ValueError) as error:
        print(json.dumps({"posted": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Direct script execution is intentional: discovery supplies an absolute
    # interpreter and script path so workspaces containing spaces are harmless.
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
