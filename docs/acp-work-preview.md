# ACP Work preview (unreleased source branch)

Codex and Claude Work sessions can opt into ACP v1 from **Provider dashboard →
Provider transport**. The existing provider path remains the default. The
dashboard checks adapter status when opened; its **Install ACP adapters** button
installs both pinned adapters into PilferedParrot's state directory only when
clicked. Opening the app does not install packages. Node.js 22 or newer is
required. Changing transport starts a new provider session on the next turn,
including within an existing Work session.

The dashboard also has a **Check GPUs** button. It reads one `nvidia-smi`
snapshot by GPU UUID when clicked; it does not poll, reserve a card, or route a
model automatically.

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
store. A standalone no-prompt ACP option probe reads dynamic model, effort and
mode lists, but the dashboard model picker is not yet connected to it. Windows
runtime behavior and provider permission UI have not yet been certified for
release.
