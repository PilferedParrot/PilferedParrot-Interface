# Next session: native-provider evaluation

Copy the following prompt into a focused implementation session:

```text
Evaluate a lean, native-provider Work harness for PilferedParrot. Do not restore
the retired manual Harness form or add blanket prompt wrappers, hidden model or
provider routing, automatic retries/backoff, automatic second-model review,
recursive agents, queues, or a generic acceptance loop.

First inspect the ordinary Work and Chat lifecycle, provider adapters, session
resume/context behavior, cancellation, persisted run state, and current tests.
Distinguish CLI-native execution from PPI's Qwen/compatible API tool loop in
qwen.py. Preserve configured permissions and native CLI session behavior. For the
API loop, evaluate task completion within its 24-round default, actionable tool
errors, transcript growth, and final verification before changing instructions.
Current compatible token_usage retains only the latest completion; accumulate
usage separately for evaluation, and keep context samples separate from run totals. Treat provider-reported tokens and timing as optional
telemetry with explicit scope and provenance; do not infer costs or savings.

Before proposing an interface or a new evidence record, evaluate one ordinary
provider run end-to-end: task completion, the task's predeclared verification,
and any continuation needed after the first result. Use automatically available
local measurements where possible: selected provider/model, run outcome, elapsed
time, and reported token telemetry with its scope and provenance. Do not claim
that a provider exit code establishes acceptance.

Before optimizing or adding automation, design and run only small representative
fixture tasks with predeclared acceptance checks. Compare like-for-like runs and
record completion, verification outcome, continuation needed, elapsed time,
token telemetry, and limitations. Do not add a user evidence form or manual
routing to collect those results. Fixtures are evaluation evidence, not a claim
of general coding quality. Do not call paid providers or publish results unless
separately asked.

Return an evidence-backed recommendation: keep ordinary Work unchanged, make one
bounded improvement to the ordinary path, or stop because the evidence does not
justify a change. Preserve old Harness records/API/schema compatibility unless
an explicit migration is approved.
```

The prompt intentionally starts with inspection and a bounded proposal. It does
not assume a benchmark has already run.
