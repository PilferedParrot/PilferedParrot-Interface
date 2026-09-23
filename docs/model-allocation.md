# Model allocation — September 22, 2026

For this installation, use GPT-6 Sol for ordinary project work and GPT-6 Luna for
bounded supporting tasks. Astra remains available for consequential uncertainty.
This is a practical starting policy, not a measured claim that the models have
equal quality or consume a particular fraction of the weekly subscription limit.

| Work | Preferred model and reasoning |
| --- | --- |
| Pipeline planning, implementation, debugging, routine evaluation | GPT-6 Sol, high |
| Bounded investigation, extraction, straightforward edits, check execution | GPT-6 Luna, medium; high when justified |
| Lightweight Chat with no saved selection | GPT-6 Luna, low |
| Consequential unresolved assumptions, disputed evaluation, difficult architecture or diagnosed Sol failures | GPT-6 Astra, normally high |
| Actual offline/privacy need or demonstrated local advantage | Local model, explicitly selected |

Choose a stronger model immediately when warranted; there is no required sequence
of failed cheaper attempts. An independent review need not always use Astra.
Avoid a standing reviewer for every task, copied parent histories, or model-driven
polling. Preserve the model explicitly selected for an existing session.

For Hill Country Driver, Sol can lead the 360-video-to-game pipeline and evaluate
ordinary results. Use Astra selectively when uncertain camera projection,
calibration, scale or an architecture choice could invalidate a large amount of
reconstruction work, or when the acceptance criteria themselves need independent
scrutiny. All models must inspect actual evidence. Passing tests or attractive
still frames do not establish source fidelity, moving-view quality, coverage,
collision, or the game's frame-time target.

## What changed locally

- The ignored `config.json` selects `gpt-6-sol` / high for Codex, `gpt-6-luna` for new
  Chat, and disables Qwen's on-demand automatic start. Existing local model files
  and the manual launcher remain available. No model download or rebuild is needed.
- The running app's saved Codex Work preference was changed to `gpt-6-sol` through
  its preference endpoint. Existing conversations keep their selected models.
  Work windows also retain their latest explicit model/reasoning selection for
  subsequent sessions; select Sol there to replace an inherited Astra choice.
  Python defaults and on-demand startup configuration load on the next ordinary
  app restart; this update does not interrupt running provider work.
- The portable Chat default is Luna. The retained `sol-luna` compatibility preset
  and example now request Sol high, Luna medium, then Sol high / Astra high for
  explicit retries. Explicit custom presets and stored policy snapshots remain
  intact. The manual Harness UI remains retired; this preset does not route
  ordinary Work or automatically spawn native workers.
  An explicitly saved Chat model or reasoning selection continues to take
  precedence, including when starting another Chat from that conversation.
  Codex Chat model changes now update only the Chat preference, so using Luna
  in Chat does not overwrite the separate saved Sol Work preference.
- Local Codex guidance in `~/.codex/AGENTS.md` records the allocation above.
  `~/.codex/config.toml` remains unchanged; the Parrot Work default is local to
  Parrot. The existing selected session model and reasoning remain authoritative.
- Local Hill Country instructions now recognize the installed 5070 Ti. The
  3060 Ti 1080p/120 fps requirement remains; report 5070 Ti results separately.

Codex's installed catalog already advertises Sol, Luna and Astra, including their
supported reasoning choices. The picker, native dispatch and GPU-independent
orchestration need no model-specific integration. Keep using catalog metadata;
API context limits and reasoning choices are not a substitute for Codex's actual
advertised capabilities. Native Codex owns its API requests. The compatible
Chat Completions adapter was not repurposed for these reasoning models.

## Hardware and local inference

Read-only inspection found both the RTX 5070 Ti (16,303 MiB reported by
`nvidia-smi`) and RTX 3060 Ti (8,192 MiB), with no local LLM running. Qwen's
automatic launch setting applied only when that provider was selected; it was
not evidence of a resident server. The existing idle-stop timer was left intact.

Use available VRAM for reconstruction and rendering. Choose and record the actual
GPU for each production tool, and validate its runtime and peak memory on a
bounded job before scaling. This audit did not run inference, rebuild CUDA tools,
change drivers, benchmark either GPU, remove models or alter game settings.
Historical 3060 Ti/local-model results are not 5070 Ti results.

## Evidence and limits

Official documentation describes [Sol](https://developers.openai.com/api/docs/models/gpt-6-sol)
as suited to complex coding and agentic work and
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) to focused, high-volume
tasks. OpenAI's [model selection guidance](https://developers.openai.com/api/docs/guides/model-selection)
supports choosing models by task difficulty and usage constraints. These support
the policy's direction, not a project-specific quality or subscription-savings claim.

Any later comparison should use the same bounded project task and evidence, an
independent acceptance check, actual elapsed time and reported usage, and include
review/rework. Keep missing usage unknown. API prices do not determine the user's
weekly Codex allowance. Hill Country's existing subscription-only rule remains;
this change authorizes no paid API calls, rentals or purchases.
