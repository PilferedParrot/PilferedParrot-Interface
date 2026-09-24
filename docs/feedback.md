# Optional feedback — policy 1

Feedback is off by default, including after an upgrade. Work, Chat, and the
terminal share the same implementation and saved choices on Linux/source and
Windows. Older installed releases need an update to gain these controls.
Ordinary conversation screens, provider selection, model requests, and saved
personal preferences are unaffected. There is no first-run prompt or reminder.

Open **Preferences → Help improve PilferedParrot** to choose categories. Each
checkbox gives independent permission to collect the information below locally.
Nothing is uploaded. Review the exact report, check that you have reviewed it,
and download it if you want to share it yourself. A download is not a submission.

## Four independent choices

| Choice | Information included after opt-in |
| --- | --- |
| Usage | Counts of message requests, session creation/reset, model/reasoning/context changes, whiteboard reads, and Run with AI requests; grouped as Work, Chat, or CLI. No selected model or provider identifiers. |
| Problems | Counts of provider/run failures, unexpected POST request failures, and cancelled runs. No exception messages, stacks, crash dumps, URLs, or response text. |
| Preferences | Counts of appearance tone, surface, readability, and notification choices made after opting in. Existing saved preferences are not backfilled. |
| Local changes | On explicit report preview only, compare listed shipped application source files with their distributed baseline. Report counts of matching, changed, and unavailable files in interface, core, and desktop components. No code, diffs, filenames, paths, hashes, Git history, or project files are included. |

Automatic counters never include prompts, responses, clipboard contents, keys,
project content, custom model names, session IDs, account details, or a persistent
installation identifier. Reports also contain the application version, broad OS
family, schema/policy version, consent choices, and collection window length.
No exact activity times or daily activity timeline are exported.
Session search filters the compact summaries already loaded for the selected Work
project. Search text stays in that window and is not added to feedback counters.

Local change detection is advisory. It cannot infer why a change was made,
distinguish a fix from customization, or establish that an installation is
unmodified. Coverage is limited to the manifest's named, bounded source files;
new files, binary edits, and removed/replaced baselines may be undetectable.
Missing files are reported as unavailable. Line endings are normalized for
Windows. No Git command is run and no working project is searched. This works
without requiring the user to publish a fork or pull request.

## Retention, revocation, and failure behavior

A separate `<chat-store filename>.feedback.sqlite3` file sits beside the configured chat store. Different chat-store paths keep independent consent choices.
It is created only when a user saves a feedback choice, with owner-only file
permissions where the OS supports them. On Windows it also relies on the user's
profile directory permissions. It contains consent records and daily aggregate
counters, capped at 10,000 for each event/surface/day. There are no event logs.
Counts older than 30 UTC calendar days are discarded on the next feedback store
access; the app cannot erase files while it is closed. Exports sum the retained
counts without exporting individual days.

Turning a category off deletes its counts and fences older queued observations.
**Clear stored aggregates** clears counts while keeping choices. **Turn all off
and clear** revokes all four choices and clears all counts. These actions apply
across windows and terminal processes using that store. Consent records remain
to preserve the off decision; they are not tracking identifiers. Reports already
downloaded or shared are separate copies and must be removed by their holders.

A small consent read uses no database lock wait; a bounded background queue handles counter writes. Busy,
unwritable, or corrupt feedback storage can lose counts; it does not prevent
normal use. Counts are approximate and a preview may omit pending writes.
`DO_NOT_TRACK=1` or `PILFEREDPARROT_TELEMETRY_DISABLED=1` overrides enabled
collection/report categories. These environment switches do not erase saved
choices: use **Turn all off and clear** for permanent revocation.
Local API consent revisions invalidate stale previews after another window clears data or changes a choice; these internal tokens are never exported. Changing the policy version requires fresh explicit consent. Preferences or
configuration migrations must never implicitly enable a category.

## Explain a problem or local fix

Written feedback is separate from automatic collection. It can be used with all
four categories off. Written form fields are not saved to disk by the app; only checking
**Include my written feedback in this report** includes it in a report preview.
The exact text is included, so remove private details or secrets before sharing.
Each of the two text fields is limited to 2,000 characters.

Useful feedback explains:

- What happened, what you expected, and who it affects.
- What you changed or tried, and why it helped. Leave this blank if no fix exists.
- Whether it is a personal preference, a general improvement, or still uncertain.

A short explanation often teaches more than a patch. Users can voluntarily
attach a separately reviewed patch to a GitHub issue or pull request; automatic
collection never extracts source code. Do not ask an agent to search a user's
projects or conversation history for fixes to share.

## Terminal controls

```text
pilferedparrot feedback
pilferedparrot feedback --help
pilferedparrot feedback enable usage --accept-policy
pilferedparrot feedback enable problems --accept-policy
pilferedparrot feedback enable preferences --accept-policy
pilferedparrot feedback enable local_changes --accept-policy
pilferedparrot feedback disable usage
pilferedparrot feedback clear
pilferedparrot feedback off
pilferedparrot feedback report
```

Enabling requires explicitly accepting policy 1. `feedback report` prints JSON
for inspection and manual sharing. No command uploads a report. Terminal usage
counts prompt invocations; full preference changes occur in Work/Chat. Existing
run ledgers and provider-side logging are separate from optional product feedback.

## Turning feedback into improvements

This is a recommended maintainer workflow, not an automated service or a promise
that a report has been received:

1. Triage voluntarily submitted reports regularly. Acknowledge the problem and
   record an owner, reproduction, and disposition in the existing issue tracker.
2. Separate defects, accessibility barriers, personal preferences, and proposals
   that expand the product's purpose. Give severe minority-use failures attention
   even when counts are small. Cancellation is not evidence of dissatisfaction.
3. Ask for expected behavior and a small reproduction. For local fixes, learn
   the intent before adopting the implementation; review code and add a regression
   test when it addresses a defect. Do not treat submitted files as trusted code.
4. Prefer a sound common default plus existing per-person controls. Compare the
   benefit, complexity, and maintenance cost before adding another feature or knob.
5. Link accepted changes and release notes back to the report; explain decisions
   on declined or deferred proposals. Responsiveness needs an owner and follow-up,
   not just more data.

Opt-in reports are a self-selected sample, not a population survey. Without
persistent identifiers there is no reliable unique-user denominator, retention
metric, or deduplication of repeated reports. Event counts describe actions, not
people or preferences held by silent users. Avoid ranking priorities solely by
volume. Cross-tabulation of rare combinations can still reveal something about
an individual; share summaries conservatively and retain raw submissions only
as long as needed.

A future private collection service needs an identified operator, destination,
retention/deletion process, and consent that describes network sharing. It should
minimize network metadata and avoid third-party trackers. This version contains
no transport or endpoint, so opting into local counters cannot silently become
permission for network reporting later.

## Maintaining local-change coverage

After changing shipped Python or web source files or the app version, run:

```text
python bin/update-feedback-baseline
python bin/update-feedback-baseline --check
```

The baseline is bundled as package data in the source and Windows builds. It is
an aid to voluntary reporting, not an integrity or security attestation.
