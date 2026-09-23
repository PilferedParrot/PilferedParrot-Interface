"""Identity recorded by the posting runtime, never inferred from an author label."""
from __future__ import annotations

from typing import Any

from .config import redact_configured_secrets

IDENTITY_SOURCES = {"runtime", "self-reported", "unknown", "user"}
VALUE_SOURCES = {"configured", "reported", "unknown", "not-applicable"}


def _text(value: Any, limit: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return value if len(value) <= limit else ""


def normalize_identity(value: Any = None, *, native: bool = False) -> dict[str, str]:
    value = value if isinstance(value, dict) else {}
    source = value.get("source")
    source = source if isinstance(source, str) and source in IDENTITY_SOURCES else "unknown"
    result = {"provider": _text(value.get("provider")), "model": _text(value.get("model")),
              "reasoning_effort": _text(value.get("reasoning_effort"), 80), "source": source}
    for field, key in (("model", "model_source"), ("reasoning_effort", "reasoning_source")):
        origin = value.get(key)
        result[key] = origin if isinstance(origin, str) and origin in VALUE_SOURCES else "unknown"
        if not result[field] and result[key] != "not-applicable":
            result[key] = "unknown"
    if native:
        # A directly written header cannot promote itself to runtime evidence.
        result["source"] = "self-reported" if result["model"] or result["reasoning_effort"] else "unknown"
        result["model_source"] = result["reasoning_source"] = "unknown"
    if result["source"] == "user":
        result.update(provider="", model="", reasoning_effort="",
                      model_source="not-applicable", reasoning_source="not-applicable")
    return result


def runtime_identity(config: dict[str, Any], provider: str, *, reported_model: Any = None,
                     reported_reasoning_effort: Any = None) -> dict[str, str]:
    settings = config.get(provider, {})
    settings = settings if isinstance(settings, dict) else {}

    def safe(value: Any, limit: int = 256) -> str:
        return _text(redact_configured_secrets(config, value), limit) if isinstance(value, str) else ""

    configured_model = safe(settings.get("model"))
    # Only native Codex/Claude adapters currently send a reasoning setting.
    # Compatible API config may contain an unused setting; do not claim it ran.
    configured_effort = safe(settings.get("reasoning_effort"), 80) if (
        provider in {"codex", "claude"} and settings.get("adapter") != "openai_compatible"
    ) else ""
    model, effort = safe(reported_model), safe(reported_reasoning_effort, 80)
    return normalize_identity({
        "provider": safe(provider), "source": "runtime",
        "model": model or configured_model,
        "model_source": "reported" if model else "configured" if configured_model else "unknown",
        "reasoning_effort": effort or configured_effort,
        "reasoning_source": "reported" if effort else "configured" if configured_effort else "unknown",
    })
