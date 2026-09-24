"""Explicit ACP option discovery without a model prompt or adapter install.

The caller supplies an installed agent command and selected workspace. A
temporary session provides the options the agent actually advertises; it is
closed when supported. Returned fields are allowlisted for browser display.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .acp_client import ACPClient, ACPError


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")


def _display(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _EMAIL.sub("[redacted-email]", value).strip()
    return cleaned[:limit] if not any(ord(char) < 32 for char in cleaned) else ""


def _choices(option: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(option, dict) or not isinstance(option.get("options"), list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in option["options"][:200]:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("value"), str) or len(item["value"].strip()) > 128:
            continue
        value = _display(item.get("value"), limit=128)
        if not value or value in seen or "[redacted-email]" in value:
            continue
        seen.add(value)
        result.append({
            "value": value,
            "label": _display(item.get("name"), limit=160) or value,
            "description": _display(item.get("description"), limit=600),
        })
    return result


def _find(options: Any, *, category: str, ids: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(options, list):
        return None
    matches = [item for item in options if isinstance(item, dict)
               and (item.get("category") == category or item.get("id") in ids)]
    preferred = next((item for item in matches if item.get("id") in ids), None)
    if preferred is not None:
        return preferred
    if len(matches) > 1:
        raise ACPError(f"agent advertised ambiguous {category} options")
    return matches[0] if matches else None


def discover_acp_options(
    argv: Sequence[str], *, cwd: Path, env: dict[str, str],
    model: str | None = None, timeout: float = 30,
    client_factory: Callable[..., ACPClient] = ACPClient,
) -> dict[str, Any]:
    """Probe one ACP agent session; never call ``session/prompt``.

    A selected model is validated against the live option list. The agent's
    returned option list after changing models determines available effort
    choices. An agent that refuses the requested model fails the probe.
    """
    workspace = Path(cwd)
    if not workspace.is_absolute() or not workspace.is_dir():
        raise ValueError("ACP option probe needs an existing absolute workspace")
    if not isinstance(env, dict):
        raise ValueError("ACP option probe needs an explicit environment")
    if timeout <= 0:
        raise ValueError("ACP option probe timeout must be positive")
    client = client_factory(argv, cwd=workspace.resolve(), env=env)
    session_id: str | None = None
    try:
        initialized = client.initialize(timeout=timeout)
        session = client.new_session(workspace.resolve(), timeout=timeout)
        session_id = session["sessionId"]
        options = session.get("configOptions")
        mode_source = session.get("modes") if isinstance(session.get("modes"), dict) else {}
        model_option = _find(options, category="model", ids=("model",))
        models = _choices(model_option)
        if model is not None:
            if model not in {item["value"] for item in models}:
                raise ACPError("requested ACP model is not advertised")
            if model_option is None:
                raise ACPError("ACP agent has no model option")
            if model_option.get("currentValue") != model:
                result = client.set_config_option(
                    session_id, str(model_option["id"]), model, timeout=timeout,
                )
                options = result.get("configOptions")
                if isinstance(result.get("modes"), dict):
                    mode_source = result["modes"]
                model_option = _find(options, category="model", ids=("model",))
            if not isinstance(model_option, dict) or model_option.get("currentValue") != model:
                raise ACPError("ACP agent did not apply the requested model")
            models = _choices(model_option)
        effort_option = _find(
            options, category="thought_level", ids=("reasoning_effort", "effort"),
        )
        modes = []
        for item in mode_source.get("availableModes", [])[:50] \
                if isinstance(mode_source.get("availableModes"), list) else []:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("id"), str) or len(item["id"].strip()) > 128:
                continue
            value = _display(item.get("id"), limit=128)
            if value and "[redacted-email]" not in value:
                modes.append({"value": value,
                              "label": _display(item.get("name"), limit=160) or value,
                              "description": _display(item.get("description"), limit=600)})
        current_model = _display(model_option.get("currentValue"), limit=128) \
            if isinstance(model_option, dict) else ""
        current_effort = _display(effort_option.get("currentValue"), limit=128) \
            if isinstance(effort_option, dict) else ""
        current_mode = _display(mode_source.get("currentModeId"), limit=128)
        return {
            "models": models,
            "efforts": _choices(effort_option),
            "modes": modes,
            "current_model": current_model,
            "current_effort": current_effort,
            "current_mode": current_mode,
            "effort_option_id": _display(effort_option.get("id"), limit=128)
            if isinstance(effort_option, dict) else "",
            "model_option_id": _display(model_option.get("id"), limit=128)
            if isinstance(model_option, dict) else "",
            "agent_protocol": initialized.get("protocolVersion"),
        }
    finally:
        if session_id is not None:
            try:
                sessions = client.agent_capabilities.get("sessionCapabilities") or {}
                if isinstance(sessions, dict) and "close" in sessions:
                    client.close_session(session_id, timeout=min(timeout, 5))
            except Exception:
                pass
        client.close()
