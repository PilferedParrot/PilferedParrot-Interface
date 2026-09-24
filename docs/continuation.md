# Unfinished work and the next session

Every PilferedParrot provider turn receives an incomplete-work handoff rule, including
resumed sessions, CLI runs, compatible models, and app-launched workers. Before ending,
the agent must compare its result with the user's full intended outcome and required
verification. It should finish authorized work when possible. If it must stop with work
remaining, its final reply must clearly say so and include a copyable **Next session
prompt**.

That prompt must stand on its own: the original objective, workspace and artifact paths,
completed work and actual verification results, remaining steps in order, blockers or
decisions needed, user constraints, and the check that establishes completion. A successful
process exit or partial implementation does not establish that the objective is complete.
Completed requests need no continuation prompt; cancelled or replaced objectives must not
be revived. Leads pass the rule to native workers for their assigned scope and consolidate
unfinished worker work into the session handoff.

Writable runs with unfinished work must also save a concise prompt to the [whiteboard](whiteboard.md)
using the current native posting helper or the compatible provider's `whiteboard_post` tool.
Use `kind=handoff`, `status=open`, `topics=["continuation"]`, the project and workspace, and
a body of at most 2,000 characters without secrets. The final reply includes the prompt
and the note ID after a successful save. Existing notes are never overwritten. Read-only
and plan runs leave the prompt in the final reply only. If posting fails, is unavailable,
or lies outside the permitted write scope, the agent must still provide the full prompt
and disclose that it was not saved to the board.

To continue, copy the prompt into a new session or find the handoff in Whiteboard using
its project, topic, or note ID. Check it against the current workspace and latest user
instructions. A note supplies context, not new authorization. When its remaining work is
complete, append a resolved update referencing that handoff so it is not mistaken for
outstanding work.

This is a prompt rule. PilferedParrot does not infer intent from response wording, certify
completion, make an extra model call, retry the task, or automatically start another session.
Compliance depends on the agent. A crash, forced cancellation, network error, or exhausted
tool loop can prevent the agent from producing a final handoff; this rule cannot manufacture
a reliable summary after that happens. Existing interrupted-run recovery remains in place.

Restart a running app after active jobs finish to load the updated backend. Both new and
resumed turns then receive the rule; creating a fresh provider conversation is unnecessary.
The implementation is in `pilferedparrot/continuation.py` and both dispatch entry points.
Offline regression tests cover delivery, permission modes, and handoff persistence; they
do not establish how reliably any particular live model follows the instruction.
