# ACP Work preview (unreleased source branch)

Codex and Claude Work sessions can opt into ACP v1 with `"engine": "acp"` in that
provider's private configuration. The existing provider path remains the default.
ACP adapters are pinned and installed into PilferedParrot's state directory only
after an explicit `AdapterManager.install()` call; opening the app does not install
packages. Node.js 22 or newer is required. This branch has no adapter-install
button yet.

The preview opens an ACP process for each turn and loads the saved provider
session on follow-up. Model, effort and mode choices are checked against that
session's advertised options before a prompt starts. A requested setting that
the agent does not confirm fails the turn. Structured assistant chunks, tool
updates, diffs and usage stream to the Work window. A permission card shows the
agent's exact command or diff and only the choices it offered. No choice, a
timeout, cancellation, or an incomplete preview defaults to denial. A revoked
window cannot answer a permission request; only the owning Work window can.

The browser stream and JSON chat store remain the source branch's first live
transport. Recent ACP updates and live text are bounded; JSON checkpoints occur
at most every 0.5 seconds during a run, then at completion. An abrupt process
crash can lose updates since the last checkpoint. The standalone SQLite document
and event-journal module is being verified separately and is not the active
store. Windows runtime behavior and provider permission UI have not yet been
certified for release.
