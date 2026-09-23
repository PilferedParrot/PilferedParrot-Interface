"""Value-free summary of compatibility normalization for SQLite imports.

This module deliberately exposes only fixed schema paths and allow-listed field
names. Unknown object keys are grouped under an opaque marker and are never
included in the report.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


_KNOWN_FIELDS = frozenset({
    "version", "chats", "chat", "chat_history", "preferences",
    "id", "window_id", "title", "cwd", "created_at", "updated_at",
    "requested_provider", "requested_model", "provider", "model",
    "provider_session_id", "acp_mode", "messages", "draft", "archived",
    "archived_at", "last_used_order", "provider_messages", "qwen_messages",
    "context_used_tokens", "reasoning_effort", "harness_parent",
    "harness_tasks", "context_chars", "context_warning", "pending",
    "session_engine", "run_id", "role", "content", "active_chat_id",
    "control_action", "work_models", "work_context_window_percent",
    "chat_model", "chat_context_window_percent", "notification_permission",
    "appearance", "tone", "surface", "readability", "last_turn_usage",
    "warning_announced", "engine", "acp_stop_reason", "acp_updates",
    "acp_updates_truncated", "streamed_text", "streamed_text_truncated",
    "activity", "error", "interrupted", "cancel_requested", "cancelled",
    "exit_code", "response_identity", "whiteboard_discovered",
    "work_cleanup_last_run", "project_workrooms", "recent", "pinned",
    "selected", "endpoint_kind", "endpoint_origin", "reported_models",
})
_SAFE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATIONS = frozenset({"added", "changed", "retired", "opaque-retained"})
_MAX_NODES = 100_000
_MAX_DEPTH = 24
_MAX_COUNT_ROWS = 256
_MAX_SAMPLES = 16


def build_transform_report(
    raw_document: dict[str, Any], normalized_document: dict[str, Any], *,
    source_sha256: str | None = None, revision: int | None = None,
) -> dict[str, Any]:
    """Return bounded counts and field-name samples without copying values.

    The documents are inspected in memory only. `source_sha256` and `revision`
    are included only when they pass conservative type/format checks.
    """
    counts: Counter[tuple[str, str, str]] = Counter()
    sample_fields: set[str] = set()
    visited = 0
    overflowed = False

    def mark_overflow() -> None:
        nonlocal overflowed
        if not overflowed:
            overflowed = True
            bump("$.<bounded>", "opaque", "opaque-retained")

    def bump(path: str, category: str, operation: str, field: str | None = None) -> None:
        if operation not in _OPERATIONS:
            return
        counts[(path, category, operation)] += 1
        if field is None:
            candidate = path.rsplit(".", 1)[-1].removesuffix("[*]")
            field = candidate
        if field in _KNOWN_FIELDS and len(sample_fields) < _MAX_SAMPLES:
            sample_fields.add(field)

    def walk(before: Any, after: Any, path: str, depth: int) -> None:
        nonlocal visited
        visited += 1
        if visited > _MAX_NODES or depth > _MAX_DEPTH:
            mark_overflow()
            return
        if type(before) is not type(after):
            bump(path, "known", "changed")
            return
        if isinstance(before, dict):
            before_keys = set(before)
            after_keys = set(after)
            for key in before_keys | after_keys:
                visited += 1
                if visited > _MAX_NODES:
                    mark_overflow()
                    return
                if not isinstance(key, str) or key not in _KNOWN_FIELDS:
                    if key in before_keys:
                        bump(path + ".<opaque>", "opaque", "opaque-retained")
                    continue
                child_path = path + "." + key
                if key not in before_keys:
                    bump(child_path, "known", "added", key)
                elif key not in after_keys:
                    bump(child_path, "known", "retired", key)
                else:
                    walk(before[key], after[key], child_path, depth + 1)
            return
        if isinstance(before, list):
            if len(before) != len(after):
                bump(path, "known", "changed")
            for index, (old_item, new_item) in enumerate(zip(before, after)):
                if visited >= _MAX_NODES:
                    mark_overflow()
                    break
                # Array positions, including IDs, never enter a report path.
                walk(old_item, new_item, path + "[*]", depth + 1)
            return
        if before != after:
            bump(path, "known", "changed")

    walk(raw_document, normalized_document, "$", 0)
    rows = [
        {"path": path, "category": category, "operation": operation, "count": count}
        for (path, category, operation), count in sorted(counts.items())
    ]
    truncated = len(rows) > _MAX_COUNT_ROWS
    rows = rows[:_MAX_COUNT_ROWS]
    report: dict[str, Any] = {
        "counts": rows,
        "sample_fields": sorted(sample_fields),
        "truncated": truncated or overflowed,
    }
    metadata: dict[str, Any] = {}
    if isinstance(source_sha256, str) and _SAFE_SHA256.fullmatch(source_sha256):
        metadata["source_sha256"] = source_sha256
    if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
        metadata["revision"] = revision
    if metadata:
        report["metadata"] = metadata
    return report
