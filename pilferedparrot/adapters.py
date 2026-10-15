"""Provider-neutral execution contracts and built-in provider adapters."""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ProgressEvent:
    """Normalized provider progress, independent of a CLI's wire format."""
    kind: str
    text: str = ""
    provider: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    run: bool = True
    chat: bool = True
    resume: bool = False
    cancel: bool = True
    streaming: bool = True
    tools: bool = False
    # ``usage`` is retained for compatibility and means execution-derived,
    # per-turn token telemetry. Allowance reporting is a separate capability.
    usage: bool = False
    allowance_reporting: bool = False
    organization_usage_reporting: bool = False
    models: bool = False


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_window_tokens: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "context_window_tokens": self.context_window_tokens,
        }


@dataclass(frozen=True)
class ProviderAvailability:
    available: bool
    authenticated: bool | None
    reason: str | None = None


class ProviderAdapter(ABC):
    provider: str
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def __init__(self, provider: str, config: dict[str, Any]) -> None:
        self.provider, self.config = provider, config
        self._cancellations: dict[int, threading.Event] = {}

    @abstractmethod
    def run(self, prompt: str, cwd: Path, conversation: Any,
            cancel_event: threading.Event | None = None,
            on_progress: Callable[[ProgressEvent], None] | None = None) -> Any:
        raise NotImplementedError

    def resume(self, prompt: str, cwd: Path, conversation: Any,
               cancel_event: threading.Event | None = None,
               on_progress: Callable[[ProgressEvent], None] | None = None) -> Any:
        return self.run(prompt, cwd, conversation, cancel_event, on_progress)

    def cancel(self, conversation: Any) -> None:
        event = self._cancellations.get(id(conversation))
        if event is not None:
            event.set()

    def token_usage(self, conversation: Any) -> dict[str, int]:
        return dict(getattr(conversation, "token_usage", {}) or {})

    def context_usage(self, conversation: Any) -> dict[str, int | None]:
        usage = self.token_usage(conversation)
        return {"input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "context_window_tokens": usage.get("context_window_tokens")}

    def authentication(self) -> bool | None:
        return None

    def available(self) -> bool:
        return True

    def availability(self) -> ProviderAvailability:
        available = self.available()
        return ProviderAvailability(
            available, self.authentication() if available else None,
            None if available else "provider command or endpoint is unavailable",
        )

    def models(self) -> list[dict[str, Any]]:
        from .config import model_catalog
        if not self.available():
            raise RuntimeError(f"{self.provider} is unavailable")
        return list(model_catalog(self.config).get(self.provider, {}).get("options", []))

    # Explicit names make the contract convenient for status endpoints and
    # avoid forcing callers to know whether a provider uses a CLI or HTTP.
    def is_authenticated(self) -> bool:
        return self.authentication() is True

    def is_available(self) -> bool:
        return self.available()

    def usage(self, conversation: Any) -> TokenUsage:
        values = self.context_usage(conversation)
        return TokenUsage(**values)

    def _run_capture(self, func: Callable[..., Any], *args: Any, conversation: Any,
                     cancel_event: threading.Event | None,
                     on_progress: Callable[[ProgressEvent], None] | None) -> Any:
        event = cancel_event or threading.Event()
        self._cancellations[id(conversation)] = event
        callback = None
        if on_progress:
            callback = lambda kind, text: on_progress(
                ProgressEvent(kind, str(text), self.provider))
        try:
            result = func(*args, cancel_event=event, on_progress=callback)
            usage = getattr(conversation, "token_usage", None)
            if isinstance(usage, dict):
                for field_name, result_name in (
                    ("input_tokens", "input_tokens"),
                    ("output_tokens", "output_tokens"),
                    ("context_window_tokens", "live_context_window_tokens"),
                ):
                    value = getattr(result, result_name, None)
                    if value is not None:
                        usage[field_name] = int(value)
            return result
        finally:
            self._cancellations.pop(id(conversation), None)


class CodexAdapter(ProviderAdapter):
    capabilities = ProviderCapabilities(
        resume=True, tools=True, usage=True, allowance_reporting=True, models=True,
    )

    def run(self, prompt, cwd, conversation, cancel_event=None, on_progress=None):
        from .dispatch import capture_codex
        return self._run_capture(capture_codex, prompt, cwd, conversation, self.config,
                                 conversation=conversation, cancel_event=cancel_event,
                                 on_progress=on_progress)

    def authentication(self) -> bool | None:
        from .budgets import read_codex_budget
        from .model import AUTH_SIGNED_IN, AUTH_SIGNED_OUT
        status = read_codex_budget(self.config).auth_status
        return True if status == AUTH_SIGNED_IN else False if status == AUTH_SIGNED_OUT else None

    def available(self) -> bool:
        from .config import resolve_command
        return resolve_command(self.config, "codex") is not None


class ClaudeAdapter(ProviderAdapter):
    capabilities = ProviderCapabilities(resume=True, tools=True, usage=True, models=True)

    def run(self, prompt, cwd, conversation, cancel_event=None, on_progress=None):
        from .dispatch import capture_claude
        return self._run_capture(capture_claude, prompt, cwd, conversation, self.config,
                                 conversation=conversation, cancel_event=cancel_event,
                                 on_progress=on_progress)

    def authentication(self) -> bool | None:
        from .budgets import read_claude_status
        from .model import AUTH_SIGNED_IN, AUTH_SIGNED_OUT
        status = read_claude_status(self.config).auth_status
        return True if status == AUTH_SIGNED_IN else False if status == AUTH_SIGNED_OUT else None

    def available(self) -> bool:
        from .config import resolve_command
        return resolve_command(self.config, "claude") is not None


class OpenAICompatibleAdapter(ProviderAdapter):
    capabilities = ProviderCapabilities(
        resume=True, cancel=True, tools=True, usage=True, models=True,
    )

    def run(self, prompt, cwd, conversation, cancel_event=None, on_progress=None):
        from . import dispatch as dispatch_module
        run_compatible_agent = dispatch_module.run_compatible_agent
        run_qwen_agent = dispatch_module.run_qwen_agent
        event = cancel_event or threading.Event()
        self._cancellations[id(conversation)] = event
        callback = (lambda kind, text: on_progress(ProgressEvent(kind, str(text), self.provider))) \
            if on_progress else None
        try:
            runner = run_qwen_agent if self.provider == "qwen" else run_compatible_agent
            args = (prompt, conversation.messages, self.config, cwd)
            if runner is run_compatible_agent:
                args = (self.provider, *args)
            text = runner(*args, cancel_event=event, on_progress=callback,
                          token_usage=conversation.token_usage,
                          response_identity=getattr(conversation, "response_identity", None))
            from .dispatch import RunResult
            usage = conversation.token_usage
            return RunResult(
                text, 0, input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                live_input_tokens=usage.get("input_tokens"),
                live_output_tokens=usage.get("output_tokens"),
            )
        finally:
            self._cancellations.pop(id(conversation), None)

    def available(self) -> bool:
        return bool(self.config.get(self.provider, {}).get("base_url"))

    def authentication(self) -> bool | None:
        import os
        variable = str(self.config.get(self.provider, {}).get("api_key_env") or "").strip()
        return bool(os.environ.get(variable)) if variable else True

    def models(self) -> list[dict[str, Any]]:
        """Poll the standard OpenAI model-list endpoint for this account."""
        from .config import (
            compatible_api_headers, model_catalog, open_compatible_url,
            redact_configured_secrets,
        )
        from urllib.request import Request
        base_url = str(self.config.get(self.provider, {}).get("base_url") or "").rstrip("/")
        request = Request(
            base_url + "/models", headers=compatible_api_headers(self.config, self.provider),
        )
        try:
            with open_compatible_url(self.config, self.provider, request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Discovery only needs the status. Upstream bodies can contain
            # credentials or unrelated diagnostics; do not reflect them.
            exc.close()
            raise RuntimeError(
                f"{self.provider} model discovery failed (HTTP {exc.code})."
            ) from None
        except urllib.error.URLError as exc:
            detail = redact_configured_secrets(self.config, exc)
            raise RuntimeError(f"{self.provider} model discovery failed: {detail}") from None
        raw_models = payload.get("data") if isinstance(payload, dict) else None
        candidates = raw_models if isinstance(raw_models, list) else []
        ids = list(dict.fromkeys(
            value.strip()
            for item in candidates
            if isinstance(item, dict)
            if isinstance((value := item.get("id")), str)
            and value.strip() and len(value.strip()) <= 128
        ))[:200]
        if not ids:
            raise ValueError("provider returned no models")
        known = {
            str(option.get("value")): option
            for option in model_catalog(self.config).get(self.provider, {}).get("options", [])
            if isinstance(option, dict)
        }
        return [dict(known.get(model_id) or {"value": model_id, "label": model_id})
                for model_id in ids]


# Google ended consumer Google sign-in for Gemini CLI on 2026-06-18, and its
# terms bar third-party software from using Gemini CLI's OAuth. Google names a
# Gemini API or Vertex AI key as the supported way for other tools to use
# Gemini, so PPI launches the CLI only with one of these authentication types.
GEMINI_CLI_AUTH_TYPES = ("gemini-api-key", "vertex-ai")
GEMINI_AUTH_HELP = (
    "PPI runs Gemini CLI only with a Gemini API key or Vertex AI credentials; "
    "Google account sign-in (OAuth) is not supported."
)


# Environment variables that name a Gemini CLI auth type outright. 0.64 copies
# GEMINI_CLI_AUTH_OVERRIDE (its relaunch hand-off) into the user settings layer,
# above any environment selector; GEMINI_DEFAULT_AUTH_TYPE seeds interactive
# sign-in. PPI refuses an inherited value naming another type and never passes
# either on. Boolean selectors are pinned in gemini_cli_environment instead.
GEMINI_AUTH_TYPE_VARIABLES = ("GEMINI_CLI_AUTH_OVERRIDE", "GEMINI_DEFAULT_AUTH_TYPE")
# Path variables Gemini CLI resolves against its own working directory.
GEMINI_PATH_VARIABLES = (
    "GEMINI_CLI_HOME", "GEMINI_CLI_SYSTEM_SETTINGS_PATH", "GEMINI_CLI_SYSTEM_DEFAULTS_PATH",
)


class GeminiAuthError(ValueError):
    """The configured Gemini CLI authentication is not one PPI may launch."""


def _gemini_resolved_paths(environment: Any, cwd: Path | None) -> dict[str, str]:
    """Resolve Gemini CLI's path variables as the child at ``cwd`` will.

    Relative values are joined to the launch directory, the same directory the
    CLI resolves them against, and forwarded absolute so that the files PPI
    checks are the files the CLI reads. Without a launch directory a relative
    value cannot be resolved and is left out.
    """
    result: dict[str, str] = {}
    for name in GEMINI_PATH_VARIABLES:
        value = environment.get(name)
        if not value:
            continue
        if os.path.isabs(value):
            result[name] = value
        elif cwd is not None:
            result[name] = os.path.join(os.path.abspath(cwd), value)
    return result


def _strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments outside strings, as Gemini CLI accepts them."""
    out: list[str] = []
    index, quoted = 0, False
    while index < len(text):
        char = text[index]
        if quoted:
            out.append(char)
            if char == "\\":
                out.append(text[index + 1:index + 2])
                index += 1
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            out.append(char)
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close < 0 else close + 2
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _gemini_settings_layers(environment: Any, cwd: Path | None) -> list[Path]:
    """Gemini CLI settings files from lowest to highest precedence."""
    environment = {**environment, **_gemini_resolved_paths(environment, cwd)}
    if environment.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH"):
        system = Path(environment["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
    elif sys.platform == "darwin":
        system = Path("/Library/Application Support/GeminiCli/settings.json")
    elif sys.platform == "win32":
        system = Path("C:\\ProgramData\\gemini-cli\\settings.json")
    else:
        system = Path("/etc/gemini-cli/settings.json")
    defaults = Path(environment.get("GEMINI_CLI_SYSTEM_DEFAULTS_PATH")
                    or system.parent / "system-defaults.json")
    home = Path(environment.get("GEMINI_CLI_HOME") or Path.home())
    layers = [defaults, home / ".gemini" / "settings.json"]
    if cwd is not None:
        layers.append(Path(cwd) / ".gemini" / "settings.json")
    return layers + [system]


def gemini_cli_auth_problem(
    config: dict[str, Any], environment: Any = None, cwd: Path | None = None,
) -> str | None:
    """Explain why Gemini CLI cannot launch with key authentication, or return None.

    In headless mode Gemini CLI uses the auth type saved in its settings before
    it consults the environment, so a key in the environment cannot displace a
    saved Google sign-in. PPI reads every settings layer the CLI merges and
    refuses unless the saved choice, if any, is the configured key type. Each
    layer is checked on its own, so an untrusted workspace's choice also
    refuses; a missing workspace (``cwd=None``) is skipped.
    """
    environment = os.environ if environment is None else environment
    cfg = config.get("gemini", {})
    auth_type = str(cfg.get("auth_type") or "gemini-api-key")
    if auth_type not in GEMINI_CLI_AUTH_TYPES:
        return (f"{GEMINI_AUTH_HELP} Set gemini.auth_type to gemini-api-key or vertex-ai "
                f"(found {auth_type!r}).")
    if auth_type == "gemini-api-key":
        variable = str(cfg.get("api_key_env") or "GEMINI_API_KEY").strip()
        if not environment.get(variable, "").strip():
            return (f"{GEMINI_AUTH_HELP} Set {variable} in the environment that launches "
                    "PPI, then restart PPI.")
    else:
        project = environment.get("GOOGLE_CLOUD_PROJECT", "").strip() \
            and environment.get("GOOGLE_CLOUD_LOCATION", "").strip()
        if not (environment.get("GOOGLE_API_KEY", "").strip() or project):
            return (f"{GEMINI_AUTH_HELP} For Vertex AI, set GOOGLE_API_KEY, or "
                    "GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION, in the environment "
                    "that launches PPI, then restart PPI.")
    for name in GEMINI_AUTH_TYPE_VARIABLES:
        value = str(environment.get(name) or "").strip()
        if value and value != auth_type:
            return (f"The environment that launches PPI sets {name}={value!r}. "
                    f"{GEMINI_AUTH_HELP} Unset {name}, then restart PPI.")
    for path in _gemini_settings_layers(environment, cwd):
        try:
            if not path.is_file():
                continue
            settings = json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return (f"PPI could not read Gemini CLI settings at {path} ({error}), so it "
                    "cannot confirm key authentication.")
        security = settings.get("security") if isinstance(settings, dict) else None
        auth = security.get("auth") if isinstance(security, dict) else None
        saved = [auth.get(key) for key in ("selectedType", "enforcedType")] \
            if isinstance(auth, dict) else []
        if isinstance(auth, dict) and auth.get("useExternal"):
            saved.append("external authentication (useExternal)")
        if isinstance(settings, dict):
            saved.append(settings.get("selectedAuthType"))  # pre-0.3 flat layout
        for value in saved:
            if value and value != auth_type:
                return (f"Gemini CLI settings at {path} choose {value!r} authentication. "
                        f"{GEMINI_AUTH_HELP} Run gemini in a terminal and use /auth to "
                        f"select {'Use Gemini API key' if auth_type == 'gemini-api-key' else 'Vertex AI'}, "
                        "or change security.auth.selectedType in that file.")
    return None


def gemini_cli_environment(
    config: dict[str, Any], environment: Any, cwd: Path | None,
) -> dict[str, str]:
    """Return a launch environment in which Gemini CLI can only use the key.

    With no saved auth type, Gemini CLI picks one from the environment, and it
    loads ``.env`` files only for variables that are not already set. Setting
    every competing selector explicitly therefore keeps a ``.env`` file from
    switching the run to Google sign-in, Cloud Shell or Compute credentials.
    """
    problem = gemini_cli_auth_problem(config, environment, cwd)
    if problem:
        raise GeminiAuthError(problem)
    cfg = config.get("gemini", {})
    auth_type = str(cfg.get("auth_type") or "gemini-api-key")
    result = dict(environment)
    for name in GEMINI_AUTH_TYPE_VARIABLES:
        result.pop(name, None)
    result.update(_gemini_resolved_paths(environment, cwd))
    result.update({
        "GOOGLE_GENAI_USE_GCA": "false",
        "GEMINI_CLI_USE_COMPUTE_ADC": "false",
        "CLOUD_SHELL": "false",
        "GOOGLE_GEMINI_BASE_URL": "",
    })
    if auth_type == "gemini-api-key":
        variable = str(cfg.get("api_key_env") or "GEMINI_API_KEY").strip()
        result["GEMINI_API_KEY"] = environment[variable].strip()
        result["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
    else:
        result["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    return result


class GeminiAdapter(ProviderAdapter):
    capabilities = ProviderCapabilities(resume=True, tools=True, usage=True, models=True)

    def _command(self, conversation: Any) -> list[str]:
        cfg = self.config.get("gemini", {})
        from .dispatch import provider_command
        from .whiteboard import whiteboard_directory
        # A piped stdin puts Gemini in headless mode without exposing the user
        # prompt in the process list. Gemini's JSONL formatter then supplies a
        # stable provider-neutral stream to normalize below.
        command = [provider_command(self.config, "gemini"), "--output-format", "stream-json"]
        approval_mode = str(cfg.get("approval_mode") or "auto_edit")
        if approval_mode not in {"default", "auto_edit", "yolo", "plan"}:
            raise ValueError(f"unsupported Gemini approval mode: {approval_mode}")
        command += ["--approval-mode", approval_mode]
        if approval_mode != "plan":
            board = whiteboard_directory(self.config)
            board.mkdir(parents=True, exist_ok=True, mode=0o700)
            command += ["--include-directories", str(board)]
        if cfg.get("model"):
            command += ["--model", str(cfg["model"])]
        if conversation.provider_session_id:
            command += ["--resume", str(conversation.provider_session_id)]
        return command

    def run(self, prompt, cwd, conversation, cancel_event=None, on_progress=None):
        event = cancel_event or threading.Event()
        self._cancellations[id(conversation)] = event
        text_parts: list[str] = []
        error_message: str | None = None
        from .dispatch import RunResult, _provider_process_environment
        try:
            from .dispatch import _stream_process
            try:
                environment = gemini_cli_environment(
                    self.config, _provider_process_environment(), Path(cwd),
                )
            except GeminiAuthError as error:
                return RunResult("", 1, conversation.provider_session_id, str(error),
                                 unavailable=True)

            def receive(line: str) -> None:
                nonlocal error_message
                try: item = json.loads(line)
                except json.JSONDecodeError:
                    if on_progress and line.strip():
                        on_progress(ProgressEvent("output", line.rstrip(), self.provider))
                    return
                event_type = item.get("type")
                if event_type == "init" and item.get("session_id"):
                    conversation.provider_session_id = str(item["session_id"])
                    if on_progress:
                        on_progress(ProgressEvent("status", "Started working", self.provider, item))
                text = item.get("content")
                if event_type == "message" and item.get("role") == "assistant" \
                        and isinstance(text, str):
                    text_parts.append(text)
                    if on_progress:
                        on_progress(ProgressEvent("output", text, self.provider, item))
                elif event_type == "tool_use" and on_progress:
                    on_progress(ProgressEvent(
                        "tool", f"Using {item.get('tool_name') or 'tool'}", self.provider, item,
                    ))
                elif event_type == "tool_result" and on_progress:
                    on_progress(ProgressEvent(
                        "tool_result", f"Tool finished ({item.get('status') or 'unknown'})",
                        self.provider, item,
                    ))
                elif event_type == "error" and on_progress:
                    on_progress(ProgressEvent(
                        "commentary", str(item.get("message") or "Gemini warning"),
                        self.provider, item,
                    ))
                elif event_type == "result" and item.get("status") == "error":
                    detail = item.get("error")
                    error_message = str(detail.get("message")) \
                        if isinstance(detail, dict) and detail.get("message") else "Gemini failed"
                usage = item.get("stats") if event_type == "result" else None
                if isinstance(usage, dict):
                    for dest in ("input_tokens", "output_tokens"):
                        if usage.get(dest) is not None:
                            try: conversation.token_usage[dest] = int(usage[dest])
                            except (TypeError, ValueError): pass
            completed = _stream_process(
                self._command(conversation), prompt, cwd,
                cancel_event=event,
                stdout_line=receive,
                env=environment,
            )
        finally:
            self._cancellations.pop(id(conversation), None)
        error = error_message or completed.stderr.strip() or None
        if error and (
            "IneligibleTierError" in error
            or "This client is no longer supported for Gemini Code Assist for individuals" in error
        ):
            error = (
                "Gemini CLI rejected this account tier. " + GEMINI_AUTH_HELP
            )
        exit_code = completed.returncode or (1 if error_message else 0)
        return RunResult(
            "".join(text_parts), exit_code, conversation.provider_session_id, error,
            unavailable=exit_code != 0 and not text_parts,
            input_tokens=conversation.token_usage.get("input_tokens"),
            output_tokens=conversation.token_usage.get("output_tokens"),
        )

    def available(self) -> bool:
        from .config import resolve_command
        return resolve_command(self.config, "gemini") is not None

    def authentication(self) -> bool | None:
        # Credential presence does not establish that the account is eligible
        # for the installed Gemini CLI or that a request can execute.
        return None


def adapter_for(provider: str, config: dict[str, Any]) -> ProviderAdapter:
    adapter_type = PROVIDER_ADAPTERS.get(provider)
    if adapter_type is not None:
        return adapter_type(provider, config)
    from .model import retired_provider_notice
    notice = retired_provider_notice(config, provider)
    if notice:
        raise ValueError(notice)
    if config.get(provider, {}).get("adapter") == "openai_compatible":
        return OpenAICompatibleAdapter(provider, config)
    raise ValueError(f"unknown provider: {provider}")


# Public registry hook for applications that want to inspect or extend the
# built-ins without adding another dispatch branch.
PROVIDER_ADAPTERS = {
    "codex": CodexAdapter,
    "claude": ClaudeAdapter,
    "gemini": GeminiAdapter,
    "qwen": OpenAICompatibleAdapter,
}

get_provider_adapter = adapter_for
