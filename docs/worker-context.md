# Worker context audit

Read-only local sample and instruction cleanup, September 10, 2026. No provider
benchmark was launched; these observations do not establish token or cost savings.

## Findings

- Among the 20 latest content-completed Codex sessions in a shortlist of 50 recent
  files, nine recorded spawn requests specified context: five used `none`, four
  used one turn. None requested full history. Three oversized files were skipped.
  This convenience sample is not a complete delegation tree; parent and child
  transcripts can overlap and their usage must not be summed blindly.
- A separate metadata-only sample of 11 readable recent sessions recorded base
  instructions of 17,730–21,261 characters. All six child sessions in that sample
  recorded 21,261 characters. These are character counts, not token measurements
  or complete request sizes; tool definitions and other context can add overhead.
- Ordinary native Work uses the provider's session and delegation machinery.
  PPI does not clone a transcript into a separate native worker itself. The
  caller's context selection controls native worker history inheritance.
- The retired manual Harness path creates fresh delegated sessions with no parent
  transcript. It still adds the one-time whiteboard discovery note through shared
  dispatch. That small remaining overhead is distinct from recursive forwarding.
- Compatible API requests include coding instructions, routing identity, tool
  definitions, and their own accumulated conversation. That loop does not establish
  parent-history inheritance and is separate from the Codex/Claude Harness path.

## Local policy change

The installation's global Codex `~/.codex/AGENTS.md` was reduced from 2,719 to 1,903
UTF-8 bytes (30%), retaining model preferences, lead accountability, verification,
scope, and approval boundaries. The original was backed up outside instruction
discovery paths.

The policy now defaults worker launches to `fork_turns="none"` with a self-contained
assignment. Limited recent turns are reserved for tasks that need them; full history
requires task dependence. Leads supply relevant excerpts rather than routinely
forwarding logs, whiteboard pointers, or planning history. Lead duties are scoped
to the lead, and workers delegate further only when explicitly assigned to.

These are instruction defaults, not enforced provider isolation. Fresh context
still includes provider and applicable project instructions. New conversations
load the revised local policy; existing histories retain earlier instructions.
No provider tools or permission controls were removed, and model selection was
not changed. The earlier [whiteboard guidance update](whiteboard.md) remains in place.

## Verification and limits

Reviewed the revised policy against its backup for preserved obligations and
checked the dispatch and Harness session construction paths. This cleanup changes
instructions and documentation only, so no application tests were needed.
The audit used aggregate metadata and did not load whiteboard note contents.

Expected savings are modest when workers already use fresh context and avoid
irrelevant reads. The more valuable effect is preventing future history copying,
unnecessary note reads, and repeated lead work. Measuring that requires comparable
tasks with complete worker usage and review/rework accounting; the counts above
do not demonstrate a percentage reduction in overall usage.
