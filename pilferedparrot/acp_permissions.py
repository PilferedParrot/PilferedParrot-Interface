"""Bounded, default-deny decisions for ACP permission callbacks.

``request`` runs on the ACP permission thread. ``on_pending`` publishes a
sanitized snapshot to the browser; a separate HTTP thread calls ``decide``.
Only an explicit, timely choice from the same chat can produce an option ID.
"""

from __future__ import annotations

import copy
import json
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


_KINDS = frozenset({"allow_once", "allow_always", "reject_once", "reject_always"})
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")


def _display_text(value: str) -> str:
    return _EMAIL.sub("[redacted-email]", value)


def _redact_public(value: Any) -> Any:
    if isinstance(value, str):
        return _display_text(value)
    if isinstance(value, list):
        return [_redact_public(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_public(item) for key, item in value.items()}
    return value


def _public_request(request_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Project only protocol fields needed to render a decision.

    This allowlist excludes account, auth, secret and arbitrary ``_meta``
    fields even if a caller bypasses ACPClient's ingress sanitizer.
    """
    tool_call = params["toolCall"]
    public_tool: dict[str, Any] = {"toolCallId": tool_call["toolCallId"]}
    for key in ("title", "name", "kind"):
        if key in tool_call and tool_call[key] is not None:
            if not isinstance(tool_call[key], str):
                raise ValueError(f"invalid tool call {key}")
            public_tool[key] = tool_call[key]
    content = tool_call.get("content")
    if content is not None:
        if not isinstance(content, list):
            raise ValueError("invalid tool call content")
        diffs: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "diff":
                continue
            diff: dict[str, Any] = {"type": "diff"}
            for key in ("path", "oldText", "newText"):
                if key in block:
                    if key == "oldText" and block[key] is None:
                        diff[key] = None
                        continue
                    if not isinstance(block[key], str):
                        raise ValueError(f"invalid diff {key}")
                    diff[key] = block[key]
            if "path" not in diff or "newText" not in diff:
                raise ValueError("incomplete diff")
            diffs.append(diff)
        if diffs:
            public_tool["content"] = diffs
    if tool_call.get("kind") == "edit" and "content" not in public_tool:
        raise ValueError("missing edit diff")
    is_shell = tool_call.get("kind") == "execute" or any(
        word in (tool_call.get("name") or "").lower()
        for word in ("execute", "shell", "bash", "terminal")
    )
    raw_input = tool_call.get("rawInput")
    if raw_input is not None:
        command = None
        if isinstance(raw_input, dict):
            for key in ("command", "cmd", "commandLine"):
                if key in raw_input:
                    command = raw_input[key]
                    break
        elif isinstance(raw_input, str) and is_shell:
            command = raw_input
        if command is not None:
            if not isinstance(command, str):
                raise ValueError("invalid command")
            public_tool["command"] = command
        elif is_shell:
            raise ValueError("missing shell command")
    elif is_shell:
        raise ValueError("missing shell command")
    return {
        "requestId": request_id,
        "toolCall": public_tool,
        "options": [
            {"optionId": option["optionId"], "name": option["name"],
             "kind": option["kind"]}
            for option in params["options"]
        ],
    }


def _valid_request(params: Any) -> bool:
    if not isinstance(params, dict):
        return False
    tool_call = params.get("toolCall")
    options = params.get("options")
    if (not isinstance(params.get("sessionId"), str) or not params["sessionId"]
            or not isinstance(tool_call, dict)
            or not isinstance(tool_call.get("toolCallId"), str)
            or not tool_call["toolCallId"]
            or _EMAIL.search(tool_call["toolCallId"]) is not None
            or not isinstance(options, list) or not options):
        return False
    seen: set[str] = set()
    for option in options:
        if (not isinstance(option, dict)
                or not isinstance(option.get("optionId"), str)
                or not option["optionId"]
                or _EMAIL.search(option["optionId"]) is not None
                or not isinstance(option.get("name"), str)
                or not isinstance(option.get("kind"), str)
                or option["kind"] not in _KINDS
                or option["optionId"] in seen):
            return False
        seen.add(option["optionId"])
    return True


@dataclass
class _Pending:
    chat_id: str
    option_ids: frozenset[str]
    deadline: float
    cancel_event: threading.Event | None
    public: dict[str, Any]
    ready: threading.Event = field(default_factory=threading.Event)
    choice: str | None = None


class PermissionBroker:
    """Thread-safe rendezvous for a bounded number of ACP decisions.

    ``decide`` returns only whether a choice was accepted. Rejected decisions
    reveal no pending-request details. ``shutdown`` releases every waiter and
    permanently closes the broker. A custom clock supports deterministic tests.
    """

    def __init__(
        self, *, timeout_seconds: float = 60.0, max_pending: int = 32,
        max_preview_bytes: int = 64 * 1024,
        poll_interval: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) \
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if not isinstance(max_pending, int) or isinstance(max_pending, bool) or max_pending <= 0:
            raise ValueError("max_pending must be positive")
        if (not isinstance(max_preview_bytes, int) or isinstance(max_preview_bytes, bool)
                or max_preview_bytes <= 0):
            raise ValueError("max_preview_bytes must be positive")
        if not isinstance(poll_interval, (int, float)) or isinstance(poll_interval, bool) \
                or not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        self._timeout = float(timeout_seconds)
        self._max_pending = max_pending
        self._max_preview_bytes = max_preview_bytes
        self._poll_interval = float(poll_interval)
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._closed = False

    def request(
        self, chat_id: str, params: dict[str, Any],
        cancel_event: threading.Event | None,
        on_pending: Callable[[dict[str, Any]], None],
    ) -> str | None:
        """Publish a permission request and wait for an explicit valid choice.

        Invalid input, a full broker, callback failure, timeout, cancellation,
        and shutdown all return ``None``. ACPClient then applies its own
        default-deny response to the agent.
        """
        if (not isinstance(chat_id, str) or not chat_id or not _valid_request(params)
                or not callable(on_pending)
                or cancel_event is not None and cancel_event.is_set()):
            return None
        request_id = secrets.token_urlsafe(24)
        try:
            deadline = self._clock() + self._timeout
            if not math.isfinite(deadline):
                return None
            projected = _public_request(request_id, params)
            if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) > self._max_preview_bytes:
                return None
            public = _redact_public(projected)
            if len(json.dumps(public, ensure_ascii=False).encode("utf-8")) > self._max_preview_bytes:
                return None
        except (TypeError, KeyError, ValueError, OverflowError, UnicodeError):
            return None
        entry = _Pending(chat_id, frozenset(option["optionId"] for option in params["options"]),
                         deadline, cancel_event, public)
        with self._lock:
            if self._closed or len(self._pending) >= self._max_pending:
                return None
            while request_id in self._pending:
                request_id = secrets.token_urlsafe(24)
                entry.public["requestId"] = request_id
            self._pending[request_id] = entry
        try:
            on_pending(copy.deepcopy(public))
        except Exception:
            with self._lock:
                self._pending.pop(request_id, None)
                entry.choice = None
            entry.ready.set()
            return None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            if entry.ready.wait(min(remaining, self._poll_interval)):
                break
        with self._lock:
            self._pending.pop(request_id, None)
            choice = entry.choice
        if cancel_event is not None and cancel_event.is_set():
            return None
        return choice

    def decide(self, chat_id: str, request_id: str, option_id: str) -> bool:
        """Accept exactly one offered choice for the matching chat."""
        if (not isinstance(chat_id, str) or not isinstance(request_id, str)
                or not isinstance(option_id, str)):
            return False
        with self._lock:
            entry = self._pending.get(request_id)
            if (self._closed or entry is None or entry.chat_id != chat_id
                    or option_id not in entry.option_ids
                    or self._clock() >= entry.deadline
                    or entry.cancel_event is not None and entry.cancel_event.is_set()):
                return False
            entry.choice = option_id
            del self._pending[request_id]
            entry.ready.set()
            return True

    def pending_for_chat(self, chat_id: str) -> list[dict[str, Any]]:
        """Return independent public snapshots for this chat only."""
        with self._lock:
            return [copy.deepcopy(entry.public) for entry in self._pending.values()
                    if entry.chat_id == chat_id and self._clock() < entry.deadline
                    and (entry.cancel_event is None or not entry.cancel_event.is_set())]

    def shutdown(self) -> None:
        """Default-deny all pending requests and reject future requests."""
        with self._lock:
            self._closed = True
            entries = tuple(self._pending.values())
            self._pending.clear()
            for entry in entries:
                entry.choice = None
                entry.ready.set()
