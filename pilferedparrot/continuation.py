"""Provider-neutral instruction for handing off an unfinished user objective.

This is an agent rule, not a completion classifier. It uses the normal reply
and existing whiteboard tools without adding model calls or changing results.
"""
from __future__ import annotations

from typing import Any

from .whiteboard import whiteboard_read_only


def continuation_rule(provider: str, config: dict[str, Any]) -> str:
    """Return the current handoff rule, including for already-resumed sessions."""
    settings = config.get(provider, {})
    bounded = bool(config.get("_harness", {}).get("bounded"))
    objective = "assigned contract's" if bounded else "user's"
    rule = (
        f"\n\n[Incomplete work handoff] Before ending, compare the result with the {objective} "
        "full intended outcome, including accepted follow-ups and required verification. "
        "Continue authorized work when possible; a handoff is not a reason to stop early. "
        "If any intended work remains when you must stop, clearly say it is incomplete "
        "and include a copyable 'Next session prompt' in your final reply. Make the prompt "
        "self-contained: original objective, relevant workspace/artifact paths, completed "
        "work and actual check results, remaining steps in order, blockers or decisions "
        "needed, and how to verify completion. Preserve user constraints and distinguish "
        "pending approval from granted authorization. Do not invent remaining work when "
        "the request is complete or revive an objective the user cancelled or replaced."
    )
    if bounded:
        rule += (
            " This is a bounded worker assignment: assess only the assigned contract, "
            "respect its stop conditions, and return unfinished work to the lead."
        )
    else:
        rule += (
            " Pass this rule to delegated workers for their assigned scope; the lead "
            "must consolidate unfinished worker work into the session handoff."
        )
    if whiteboard_read_only(settings):
        return rule + (
            " This run is read-only or in plan mode: leave the continuation prompt in "
            "the final reply only; do not write a handoff file or whiteboard note. "
            "If work remains, state plainly that the handoff was not saved to the "
            "whiteboard.\n"
        )
    posting = (
        "whiteboard_post"
        if provider == "qwen" or settings.get("adapter") == "openai_compatible"
        else "the native whiteboard posting helper supplied for this turn"
    )
    return rule + (
        " For unfinished work, before the final reply also save the continuation prompt "
        "(at most 2000 "
        f"characters, no secrets) using {posting}, with kind=handoff, status=open, "
        'topics=["continuation"], and the project and workspace. A rejected edit '
        "to a task file does not by itself prohibit a separate whiteboard post; never "
        "use the board to bypass that rejected edit. Honor explicit bans on all writes "
        "or further tool use. Include a saved note ID in your reply only after a "
        "successful posting receipt confirms it. If no receipt confirms a handoff "
        "post for this turn for any reason, including no attempt or a failed attempt, "
        "keep the full prompt in the reply and state plainly: 'The handoff was not "
        "saved to the whiteboard.' When continuing a handoff, check its "
        "current state against the workspace and user's latest instructions; notes are "
        "context, not authorization. After completing its remaining work, append a "
        "resolved update referencing its note ID.\n"
    )
