# Harness review

## Status

The manual **Harness** window was retired in 0.7.0-rc.12. Its work-package
records, API, and stored schema remain for compatibility with existing local
sessions. Saved worker conversations remain readable, with navigation back to the
parent session. Planning, artifact review, and retry controls are no longer
available in the UI; this preserves records, not the retired workflow. The
historical workflow is retained in [the previous guide](harness.md);
it is no longer the recommended way to start Work.

The review found that the window imposed a second, user-operated workflow on top
of an existing provider run. Before one run, it required an exact task, category,
acceptance check, artifact, and stop conditions. Input references and write scope
were optional. The five prospective estimates were optional too, but all five
were needed for its cost-comparison route to choose delegation. After a run, it
required an operator review and, when rejected, a separately planned retry. Its
portable default was `manual`, which intentionally needs an explicit configured
lead and worker before it can plan a package.

That structure is useful for controlled comparisons and retained records. It is
poor default interaction for ordinary coding work: the user has to repeat task
information, predict model effort, and manually advance a process that the
selected provider already performs in one Work turn.

## What continues to run behind Work and Chat

Ordinary Work is not an unstructured wrapper. It validates the selected project,
provider, model, reasoning setting, prompt size, and one-active-run rule before
starting a provider invocation. It keeps a same-provider, same-model session when
the provider supports resume; otherwise compatible providers keep their own
message transcript. CLI providers retain their native tool loop, session behavior, and configured
permission model. Qwen and compatible API endpoints instead use PPI’s own loop,
described below.

Chat is a separate read-only conversation. Supported providers receive their
provider-appropriate planning or read-only setting. Work and Chat both expose
cancellation, progress, provider errors, persisted history, and context-use
information. Codex can supply live context telemetry; where it is unavailable,
the interface labels its context calculation as an estimate.

The ordinary execution path already has these useful protections:

- one active run per Work session and explicit cancellation;
- project and provider validation before the first task;
- provider-owned resume and native tools rather than a copied transcript or a
  second agent tree;
- context visibility with a near-limit indicator; and
- persisted messages and interrupted-run recovery, so a local restart does not
  silently lose the request state.

## Where PPI owns the tool loop

For Qwen and compatible API endpoints, `run_compatible_agent` in
[`qwen.py`](../pilferedparrot/qwen.py) is the unseen harness. It supplies a short
coding instruction, tools, workspace boundaries, and the accumulated conversation.
The instruction already asks the model to inspect before editing, make focused
changes, run proportionate checks, and report only supported results. Tool errors
are returned to the model so it can correct them within the same run. The default
limit is 24 model/tool rounds, with bounded tool output and read-only tool filtering
for Chat. Windows excludes the Linux-only shell tool.

This is the best place to investigate application-level efficiency improvements.
First measure whether tasks finish and verify within the round limit, whether tool
errors are actionable, and whether long transcripts cause avoidable failures.
Do not increase the limit or add more instructions without evidence. Current
compatible-provider `token_usage` is overwritten with the most recent completion’s
usage; it is useful as a context sample, but is **not the total cost of a multi-round
run**. An evaluation needs separate accumulated usage rather than relabeling that
context sample. No prompt or tool-loop behavior changed in this interface release.

## Limits that remain

An exit code of zero means that a provider run completed. It is not independent
evidence that a change satisfies the user's request. The application does not
run a generic acceptance check because such a check is task-specific.

There is no automatic retry or backoff for ordinary Work or Chat. A generic retry
could rerun an editing task after an ambiguous provider failure and duplicate a
change. Interrupted work remains visible as retryable, leaving the user in
control of the next request.

Provider allowance information is display data, not a reservation or admission
control. CLI streams can be cancelled but do not receive a universal application
deadline; a safe duration depends on the provider and task. Context warnings do
not force compaction or a new session because the provider owns that behavior.

## Direction for a lean harness

The next design should make ordinary Work more reliable without recreating the
manual package form. Keep a single provider-native run as the default unit of
work. First evaluate the existing completion, verification, and continuation
experience using automatically available local results such as selected
provider/model, elapsed time, completion state, and reported tokens where
available. It must not silently choose a provider or model, add a second
reviewer, automatically retry, create a recursive agent loop, or wrap every
prompt in policy text.

Evaluate any proposed change with small, representative fixture tasks before
optimizing. Give each task a clear acceptance check, use comparable conditions,
and record completion, verification outcome, needed continuation, elapsed time,
and provider-reported tokens where available. A result is useful only with its
limits: fixtures do not establish general coding quality, and missing or
differently scoped token telemetry must remain unknown. No benchmark conclusion
is claimed by this review.

See [the next-session evaluation prompt](next-session.md) and the product-facing
[design language](design-language.md).
