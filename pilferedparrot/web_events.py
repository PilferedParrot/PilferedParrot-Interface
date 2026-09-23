"""Bounded, sequenced live events for one local web-server lifetime.

The chat store remains the durable snapshot. A client that misses this buffer
reloads its authorized chat and then resumes from the new cursor.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from typing import Any


def _contains_auth_status(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("sessionUpdate") == "_auth/status_update" \
                or value.get("method") == "_auth/status_update":
            return True
        return any(_contains_auth_status(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_auth_status(item) for item in value)
    return False


class EventHub:
    def __init__(self, *, per_chat_limit: int = 256, chat_limit: int = 128) -> None:
        if per_chat_limit < 1 or chat_limit < 1:
            raise ValueError("event limits must be positive")
        self.epoch = uuid.uuid4().hex
        self._per_chat_limit = per_chat_limit
        self._chat_limit = chat_limit
        self._condition = threading.Condition(threading.RLock())
        self._buffers: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self._truncated: set[str] = set()
        self._next_seq = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def publish(self, chat_id: str, kind: str, payload: dict[str, Any]) -> int:
        if not isinstance(chat_id, str) or not chat_id \
                or not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", kind) \
                or not isinstance(payload, dict):
            raise ValueError("invalid live event")
        if _contains_auth_status(payload):
            raise ValueError("auth status cannot be a live event")
        frozen_payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        with self._condition:
            if self._closed:
                raise RuntimeError("live event hub is closed")
            events = self._buffers.get(chat_id)
            if events is None:
                events = deque(maxlen=self._per_chat_limit)
                self._buffers[chat_id] = events
            self._buffers.move_to_end(chat_id)
            if len(events) == self._per_chat_limit:
                self._truncated.add(chat_id)
            self._next_seq += 1
            seq = self._next_seq
            events.append({"seq": seq, "kind": kind, "payload": frozen_payload})
            while len(self._buffers) > self._chat_limit:
                evicted, _ = self._buffers.popitem(last=False)
                self._truncated.discard(evicted)
            self._condition.notify_all()
            return seq

    def read_after(self, chat_id: str, after: int, *, limit: int = 128) -> list[dict[str, Any]]:
        if after < 0 or limit < 1:
            raise ValueError("invalid live event cursor")
        with self._condition:
            events = self._buffers.get(chat_id)
            if not events:
                return [{"seq": 0, "kind": "reset", "payload": {}}] if after else []
            latest = events[-1]["seq"]
            if (after > latest
                    or (after == 0 and chat_id in self._truncated)
                    or (after > 0 and after < events[0]["seq"] - 1)):
                return [{"seq": latest, "kind": "reset", "payload": {}}]
            return [event for event in events if event["seq"] > after][:limit]

    def latest(self, chat_id: str) -> int:
        with self._condition:
            events = self._buffers.get(chat_id)
            return events[-1]["seq"] if events else 0

    def wait_after(
        self, chat_id: str, after: int, *, timeout: float = 10,
        limit: int = 128,
    ) -> list[dict[str, Any]]:
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._closed:
                ready = self.read_after(chat_id, after, limit=limit)
                if ready:
                    return ready
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return []

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
