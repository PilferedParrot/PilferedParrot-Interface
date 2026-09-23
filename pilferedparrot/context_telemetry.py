"""Incremental, bounded reads of provider-owned local usage telemetry."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


def codex_live_usage(event: dict[str, Any]) -> tuple[int, int, int | None] | None:
    """Read context occupancy from the latest request, never cumulative usage."""
    payload = event.get("payload") if event.get("type") == "event_msg" else event
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
        return None
    if input_tokens == 0 and output_tokens == 0:
        # After compaction Codex can report retained context only in the last
        # request's total_tokens, with both component counters set to zero.
        total = usage.get("total_tokens")
        if type(total) is not int or total <= 0:
            # An empty counter is not evidence that an existing conversation
            # disappeared. Keep the previous sample and its observation time.
            return None
        input_tokens = total
    window = info.get("model_context_window")
    return input_tokens, output_tokens, window if type(window) is int and window > 0 else None


class CodexUsageReader:
    """Follow only token-count records, without reading a growing log repeatedly."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.session_id: str | None = None
        self.path: Path | None = None
        self.offset = 0
        self.signature: tuple[int, int, int] | None = None
        self.next_discovery = 0.0

    def read(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id or not re.fullmatch(r"[A-Za-z0-9-]{8,128}", session_id):
            return None
        if session_id != self.session_id:
            self.session_id, self.path = session_id, None
            self.offset, self.signature, self.next_discovery = 0, None, 0.0
        try:
            now = time.monotonic()
            if self.path is None or now >= self.next_discovery:
                if now < self.next_discovery:
                    return None
                candidates = (self.root / "sessions").rglob(f"*{session_id}.jsonl")
                newest = max(candidates, key=lambda p: p.stat().st_mtime_ns, default=None)
                if newest != self.path:
                    self.path, self.offset, self.signature = newest, 0, None
                self.next_discovery = now + (30 if self.path else 1)
                if self.path is None:
                    return None
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature == self.signature:
                return None
            if self.signature and (stat.st_ino != self.signature[0]
                                   or stat.st_size < self.offset
                                   or stat.st_size == self.signature[1]):
                self.offset = 0
            self.signature = signature
            # Keep large tool results from turning a status refresh into a full
            # transcript read. Skip an incomplete first record when seeking.
            start = max(self.offset, stat.st_size - 1024 * 1024)
            with self.path.open("rb") as handle:
                handle.seek(start)
                if start > self.offset:
                    handle.readline()
                start = handle.tell()
                data = handle.read(1024 * 1024)
            end = data.rfind(b"\n") + 1
            self.offset = start + end
            latest = None
            for line in data[:end].splitlines():
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                if codex_live_usage(event) is not None:
                    latest = event
            return latest
        except (OSError, ValueError):
            # Telemetry must never prevent a provider request from completing.
            self.path, self.signature, self.offset = None, None, 0
            self.next_discovery = 0
            return None
