"""One-turn, provider-neutral ACP v1 work facade.

The caller owns the executable, selected workspace, configured-secret redactor,
permission policy, and event persistence. This module owns the ACP session
lifecycle for one turn and returns its structured result. Pass
``sanitize_update`` to remove configured secrets before updates are collected
or forwarded; :class:`ACPClient` already filters account/auth metadata, but it
cannot know provider secrets from the embedding application's configuration.

When ``session_id`` is supplied, the facade loads or resumes that exact session
according to the agent's advertised capabilities. A failed load/resume is an
error; it never silently starts a fresh session. The ACP process is closed on
all exit paths, while the agent session remains available for later resume.
"""
from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acp_client import ACPClient, ACPError


UpdateCallback = Callable[[str, dict[str, Any]], None]
PermissionCallback = Callable[[dict[str, Any]], str | None]
UpdateSanitizer = Callable[[dict[str, Any]], dict[str, Any]]
ClientFactory = Callable[..., ACPClient]


class ACPCancelled(ACPError):
    """The caller cancelled before an ACP prompt could be completed."""


@dataclass(frozen=True)
class ACPWorkResult:
    """Safe, structured output from one ACP work-session turn.

    ``succeeded`` is deliberately true only for ACP ``end_turn``. Other stop
    reasons are returned for the caller to display and persist, but are not
    presented as successful completion.
    """

    text: str
    text_truncated: bool
    session_id: str
    stop_reason: str
    usage: dict[str, int | float] = field(default_factory=dict)
    config_options: tuple[dict[str, Any], ...] = ()
    updates: tuple[dict[str, Any], ...] = ()
    updates_truncated: bool = False
    model: str | None = None
    effort: str | None = None
    mode: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.stop_reason == "end_turn"


def _json_copy(value: Any) -> Any:
    """Copy protocol data through JSON so callbacks cannot mutate our snapshot."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _config_options(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_json_copy(item) for item in value if isinstance(item, dict))


def _sanitize_options(
    options: tuple[dict[str, Any], ...], sanitizer: UpdateSanitizer | None,
) -> tuple[dict[str, Any], ...]:
    update: dict[str, Any] = {
        "sessionUpdate": "config_option_update",
        "configOptions": list(options),
    }
    if sanitizer is not None:
        update = sanitizer(_json_copy(update))
        if not isinstance(update, dict):
            raise ACPError("ACP update sanitizer must return an object")
    return _config_options(update.get("configOptions"))


def _option_values(option: dict[str, Any]) -> tuple[Any, ...]:
    values = option.get("options")
    if not isinstance(values, list):
        return ()
    return tuple(
        item["value"] for item in values
        if isinstance(item, dict) and "value" in item
    )


def _find_option(
    options: tuple[dict[str, Any], ...], kind: str,
) -> dict[str, Any] | None:
    if kind == "model":
        matches = [item for item in options
                   if item.get("category") == "model" or item.get("id") == "model"]
        preferred_id = "model"
    else:
        matches = [item for item in options
                   if item.get("category") in {"thought_level", "reasoning_effort", "effort"}
                   or item.get("id") in {"reasoning_effort", "effort"}]
        preferred_id = "reasoning_effort" if any(
            item.get("id") == "reasoning_effort" for item in matches
        ) else "effort"
    if not matches:
        return None
    preferred = [item for item in matches if item.get("id") == preferred_id]
    if preferred:
        return preferred[0]
    if len(matches) != 1:
        raise ACPError(f"ACP agent advertised ambiguous {kind} configuration options")
    return matches[0]


def _mode_info(session: dict[str, Any]) -> tuple[str | None, tuple[dict[str, Any], ...]]:
    modes = session.get("modes")
    if not isinstance(modes, dict):
        return None, ()
    available = modes.get("availableModes")
    options = tuple(_json_copy(item) for item in available if isinstance(item, dict)) \
        if isinstance(available, list) else ()
    current = modes.get("currentModeId")
    return (current if isinstance(current, str) else None), options


def _text_from_content(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return value["text"]
        return ""
    if isinstance(value, list):
        return "".join(_text_from_content(item) for item in value)
    return ""


def _number_fields(value: Any, names: Sequence[str]) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int | float] = {}
    for name in names:
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        if isinstance(item, float) and not math.isfinite(item):
            continue
        result[name] = item
    return result


def _token_count(value: Any) -> dict[str, int | float]:
    """Normalize ACP's common camelCase and snake_case token counters."""
    if not isinstance(value, dict):
        return {}
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_tokens": ("reasoning_tokens", "reasoningTokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens"),
    }
    normalized: dict[str, Any] = {}
    for target, keys in aliases.items():
        for key in keys:
            if key in value:
                normalized[target] = value[key]
                break
    return _number_fields(normalized, tuple(aliases))


def run_acp_turn(
    argv: Sequence[str],
    *,
    cwd: Path,
    prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    mode: str | None = None,
    env: dict[str, str] | None = None,
    on_update: UpdateCallback | None = None,
    sanitize_update: UpdateSanitizer | None = None,
    on_permission: PermissionCallback | None = None,
    cancel_event: threading.Event | None = None,
    cancel_grace: float = 2.0,
    max_text_chars: int = 1_000_000,
    startup_timeout: float = 120,
    option_timeout: float = 30,
    prompt_timeout: float = 600,
    max_collected_updates: int = 2048,
    client_factory: ClientFactory = ACPClient,
) -> ACPWorkResult:
    """Initialize/load one ACP session, set supported options, and run a prompt.

    ``cwd`` must be explicit, absolute, and an existing directory. ``model``
    and ``effort`` are checked against each session's live ``configOptions``;
    selecting a model may remove effort support, in which case the stale effort
    is omitted and the result reports ``effort=None``. Unsupported explicit
    model choices fail before prompting.

    Updates are consumed in the order provided by ``ACPClient``. The optional
    sanitizer runs before both collection and ``on_update`` so integrations can
    redact configured API keys or other local secrets before persistence/UI use.
    ``env`` is passed as the exact child process environment; callers should
    supply their already-sanitized provider environment when one is required.
    Session replay/configuration updates are consumed internally before the
    prompt starts; only updates after the prompt boundary are returned or sent
    through ``on_update``.
    Returned update retention is bounded to ``max_collected_updates`` recent
    updates; ``updates_truncated`` reports when older entries were dropped.
    The streamed callback still receives every sanitized turn update. Response
    text is capped at ``max_text_chars`` including a truncation marker when
    needed; ``text_truncated`` reports when assistant output exceeded the cap.
    With no sanitizer, only ACPClient's built-in account/auth filtering applies.
    Permissions are default-deny when ``on_permission`` is absent.
    """
    workdir = Path(cwd).expanduser()
    if not workdir.is_absolute() or not workdir.is_dir():
        raise ValueError("ACP work cwd must be an existing absolute directory")
    workdir = workdir.resolve()
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("ACP prompt must be non-empty text")
    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        raise ValueError("ACP session ID must be non-empty text")
    if isinstance(max_collected_updates, bool) or not isinstance(max_collected_updates, int) \
            or max_collected_updates < 1:
        raise ValueError("max_collected_updates must be a positive integer")
    if not isinstance(cancel_grace, (int, float)) or isinstance(cancel_grace, bool) \
            or not 0 < cancel_grace <= 30:
        raise ValueError("cancel_grace must be positive and at most 30 seconds")
    text_truncation_marker = "\n[output truncated]"
    if isinstance(max_text_chars, bool) or not isinstance(max_text_chars, int) \
            or max_text_chars < len(text_truncation_marker):
        raise ValueError("max_text_chars must fit the output truncation marker")
    if cancel_event is not None and cancel_event.is_set():
        raise ACPCancelled("ACP turn cancelled before start")

    lock = threading.RLock()
    collected: deque[dict[str, Any]] = deque(maxlen=max_collected_updates)
    updates_truncated = False
    text_parts: list[str] = []
    text_chars = 0
    text_truncated = False
    usage: dict[str, int | float] = {}
    latest_options: tuple[dict[str, Any], ...] = ()
    latest_mode_id: str | None = None
    client_session_id = ""
    updates_ready = False
    turn_started = False
    pending_session_id: str | None = None
    pending_session_mismatch = False
    pending_config_update: tuple[str, dict[str, Any]] | None = None

    def clean_update(update: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(update, dict):
            raise ACPError("ACP session update must be an object")
        clean = _json_copy(update)
        if sanitize_update is not None:
            clean = sanitize_update(clean)
        if not isinstance(clean, dict):
            raise ACPError("ACP update sanitizer must return an object")
        return _json_copy(clean)

    def accept_update(update_session_id: str, update: dict[str, Any]) -> None:
        nonlocal pending_session_id, pending_session_mismatch, pending_config_update
        if not isinstance(update, dict):
            raise ACPError("ACP session update must be an object")
        with lock:
            if updates_ready:
                if update_session_id != client_session_id:
                    raise ACPError("ACP update belongs to an unexpected session")
                kind = update.get("sessionUpdate")
                # Stateful outer sanitizers may build this turn's transcript.
                # Drop replay/status updates before invoking them; configuration
                # snapshots are still sanitized and consumed internally.
                if not turn_started and kind != "config_option_update":
                    return
                _record_update(update_session_id, clean_update(update))
                return

            if client_session_id and update_session_id != client_session_id:
                raise ACPError("ACP update belongs to an unexpected session")
            if not updates_ready:
                if pending_session_id is None:
                    pending_session_id = update_session_id
                elif update_session_id != pending_session_id:
                    pending_session_mismatch = True
                if update.get("sessionUpdate") == "config_option_update" \
                        and "configOptions" in update:
                    pending_config_update = (update_session_id, clean_update(update))
                return

    def _record_update(update_session_id: str, clean: dict[str, Any]) -> None:
        nonlocal latest_options, latest_mode_id, updates_truncated
        nonlocal text_chars, text_truncated
        if update_session_id != client_session_id:
            raise ACPError("ACP update belongs to an unexpected session")
        kind = clean.get("sessionUpdate")
        if not turn_started:
            if kind == "config_option_update" and "configOptions" in clean:
                latest_options = _config_options(clean.get("configOptions"))
            elif kind == "current_mode_update" and isinstance(clean.get("currentModeId"), str):
                latest_mode_id = clean["currentModeId"]
            return
        if len(collected) == max_collected_updates:
            updates_truncated = True
        collected.append(clean)
        if kind == "config_option_update" and "configOptions" in clean:
            latest_options = _config_options(clean.get("configOptions"))
        elif kind == "current_mode_update" and isinstance(clean.get("currentModeId"), str):
            latest_mode_id = clean["currentModeId"]
        elif kind == "usage_update":
            usage.update(_number_fields(clean, ("used", "size", "cost")))
        elif kind == "agent_message_chunk":
            message_text = _text_from_content(clean.get("content"))
            if message_text and not text_truncated:
                payload_limit = max_text_chars - len(text_truncation_marker)
                remaining = payload_limit - text_chars
                if len(message_text) <= remaining:
                    text_parts.append(message_text)
                    text_chars += len(message_text)
                else:
                    if remaining > 0:
                        text_parts.append(message_text[:remaining])
                        text_chars += remaining
                    text_parts.append(text_truncation_marker)
                    text_chars += len(text_truncation_marker)
                    text_truncated = True
        if on_update is not None:
            on_update(update_session_id, _json_copy(clean))

    def activate_session_updates() -> None:
        nonlocal updates_ready, pending_config_update
        with lock:
            if pending_session_mismatch or (pending_session_id is not None
                                              and pending_session_id != client_session_id):
                raise ACPError("ACP update belongs to an unexpected session")
            if pending_config_update is not None:
                _record_update(*pending_config_update)
            pending_config_update = None
            updates_ready = True

    def permission_guard(params: dict[str, Any]) -> str | None:
        with lock:
            valid_session = params.get("sessionId") == client_session_id and bool(client_session_id)
            allowed_phase = turn_started and not (
                cancel_event is not None and cancel_event.is_set()
            )
        if not valid_session or not allowed_phase or on_permission is None:
            return None
        return on_permission(params)

    client: ACPClient | None = None
    cancel_stop = threading.Event()
    turn_finished = threading.Event()
    client_initialized = threading.Event()
    cancel_watcher: threading.Thread | None = None
    try:
        client = client_factory(
            argv, cwd=workdir, on_update=accept_update,
            on_permission=permission_guard, env=env,
        )
        if cancel_event is not None:
            def watch_cancel() -> None:
                while not cancel_stop.is_set() and not cancel_event.wait(0.025):
                    pass
                if cancel_stop.is_set() or not cancel_event.is_set():
                    return
                deadline = time.monotonic() + cancel_grace
                cancel_sent = False
                while not cancel_stop.is_set() and not turn_finished.is_set():
                    with lock:
                        active_session_id = client_session_id
                    if client_initialized.is_set() and active_session_id and not cancel_sent:
                        try:
                            client.cancel(active_session_id)
                        except Exception:
                            pass
                        cancel_sent = True
                        deadline = time.monotonic() + cancel_grace
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        try:
                            client.close()
                        except Exception:
                            pass
                        return
                    cancel_stop.wait(min(0.025, remaining))

            cancel_watcher = threading.Thread(
                target=watch_cancel, name="ppi-acp-cancel-watch", daemon=True,
            )
            cancel_watcher.start()

        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise ACPCancelled("ACP turn cancelled")

        initialized = client.initialize(timeout=startup_timeout)
        client_initialized.set()
        if session_id is not None:
            with lock:
                client_session_id = session_id
        check_cancelled()
        capabilities = initialized.get("agentCapabilities")
        capabilities = capabilities if isinstance(capabilities, dict) else {}

        if session_id is None:
            session = client.new_session(workdir, timeout=startup_timeout)
            with lock:
                client_session_id = session.get("sessionId", "")
        elif capabilities.get("loadSession") is True:
            session = client.load_session(session_id, workdir, timeout=startup_timeout)
            client_session_id = session_id
        else:
            session_capabilities = capabilities.get("sessionCapabilities") or {}
            if not isinstance(session_capabilities, dict) or "resume" not in session_capabilities:
                raise ACPError("ACP agent cannot load or resume the requested session")
            session = client.resume_session(session_id, workdir, timeout=startup_timeout)
            client_session_id = session_id
        if not isinstance(client_session_id, str) or not client_session_id:
            raise ACPError("ACP session response omitted sessionId")
        check_cancelled()
        reported_session = session.get("sessionId")
        if isinstance(reported_session, str) and reported_session != client_session_id:
            raise ACPError("ACP agent returned a different session ID")
        activate_session_updates()

        latest_options = _sanitize_options(
            _config_options(session.get("configOptions")), sanitize_update,
        )
        current_mode, available_modes = _mode_info(session)
        latest_mode_id = current_mode
        if mode is not None:
            if not any(item.get("id") == mode for item in available_modes):
                raise ACPError("requested ACP mode is not advertised by the session")
            if current_mode != mode:
                mode_result = client.set_mode(client_session_id, mode, timeout=option_timeout)
                check_cancelled()
                mode_acknowledged = False
                if "configOptions" in mode_result:
                    latest_options = _sanitize_options(
                        _config_options(mode_result.get("configOptions")), sanitize_update,
                    )
                    config_mode = next((item for item in latest_options
                                        if item.get("id") == "mode"
                                        or item.get("category") == "mode"), None)
                    if config_mode is not None:
                        if config_mode.get("currentValue") != mode:
                            raise ACPError("ACP agent did not apply the requested mode")
                        mode_acknowledged = True
                reported_mode = mode_result.get("currentModeId")
                if isinstance(reported_mode, str):
                    if reported_mode != mode:
                        raise ACPError("ACP agent did not apply the requested mode")
                    latest_mode_id = reported_mode
                    mode_acknowledged = True
                else:
                    flush_updates = getattr(client, "flush_updates", None)
                    if callable(flush_updates):
                        flush_updates(timeout=min(option_timeout, 5))
                    check_cancelled()
                    with lock:
                        reported_mode = latest_mode_id
                    if reported_mode == mode:
                        mode_acknowledged = True
                    if not mode_acknowledged:
                        raise ACPError("ACP agent did not acknowledge the requested mode")
                current_mode = mode
                latest_mode_id = mode
                check_cancelled()

        model_option = _find_option(latest_options, "model")
        applied_model = model_option.get("currentValue") if model_option else None
        if model is not None:
            if model_option is None or model not in _option_values(model_option):
                raise ACPError("requested ACP model is not advertised by the session")
            if model_option.get("currentValue") != model:
                model_result = client.set_config_option(
                    client_session_id, str(model_option["id"]), model,
                    timeout=option_timeout,
                )
                if "configOptions" not in model_result:
                    raise ACPError("ACP model update omitted the latest config options")
                latest_options = _sanitize_options(
                    _config_options(model_result.get("configOptions")), sanitize_update,
                )
                check_cancelled()
            model_option = _find_option(latest_options, "model")
            if model_option is None or model_option.get("currentValue") != model:
                raise ACPError("ACP agent did not apply the requested model")
            applied_model = model_option.get("currentValue")

        effort_option = _find_option(latest_options, "effort")
        applied_effort = effort_option.get("currentValue") if effort_option else None
        if effort is not None and effort_option is not None:
            if effort not in _option_values(effort_option):
                raise ACPError("requested ACP effort is not advertised by the session")
            if effort_option.get("currentValue") != effort:
                effort_result = client.set_config_option(
                    client_session_id, str(effort_option["id"]), effort,
                    timeout=option_timeout,
                )
                if "configOptions" not in effort_result:
                    raise ACPError("ACP effort update omitted the latest config options")
                latest_options = _sanitize_options(
                    _config_options(effort_result.get("configOptions")), sanitize_update,
                )
                effort_option = _find_option(latest_options, "effort")
                check_cancelled()
            if effort_option is None or effort_option.get("currentValue") != effort:
                raise ACPError("ACP agent did not apply the requested effort")
            applied_effort = effort_option.get("currentValue")

        check_cancelled()

        # ACPClient resolves non-prompt responses on the reader thread while
        # delivering session/update callbacks on its dispatcher thread. Flush
        # updates queued before session/load's response so replay chunks cannot
        # cross the current-turn boundary. Injected clients may omit this hook.
        flush_updates = getattr(client, "flush_updates", None)
        if callable(flush_updates):
            flush_updates(timeout=min(option_timeout, 5))
        check_cancelled()
        with lock:
            turn_started = True

        check_cancelled()
        response = client.prompt(
            client_session_id, prompt, timeout=prompt_timeout,
        )
        check_cancelled()
        stop_reason = response.get("stopReason")
        if not isinstance(stop_reason, str) or not stop_reason:
            raise ACPError("ACP session/prompt omitted stopReason")
        meta = response.get("_meta")
        quota = meta.get("quota") if isinstance(meta, dict) else None
        token_count = quota.get("token_count") if isinstance(quota, dict) else None
        usage.update(_token_count(token_count))
        with lock:
            return ACPWorkResult(
                text="".join(text_parts),
                text_truncated=text_truncated,
                session_id=client_session_id,
                stop_reason=stop_reason,
                usage=dict(usage),
                config_options=tuple(_json_copy(item) for item in latest_options),
                updates=tuple(_json_copy(item) for item in collected),
                updates_truncated=updates_truncated,
                model=applied_model if isinstance(applied_model, str) else None,
                effort=applied_effort if isinstance(applied_effort, str) else None,
                mode=current_mode,
            )
    except ACPCancelled:
        raise
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
            raise ACPCancelled("ACP turn cancelled") from error
        raise
    finally:
        turn_finished.set()
        cancel_stop.set()
        if cancel_watcher is not None:
            cancel_watcher.join(timeout=cancel_grace + 1)
        if client is not None:
            client.close()
