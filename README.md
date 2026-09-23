# PilferedParrot Interface

This checkout includes the **0.7.0-rc.18** interface improvements. Work and Chat share transparent,
blue-tinted surfaces, message layouts, model/reasoning controls, and history styling.
Open **Preferences → Appearance** to adjust the shared look. Choices are saved for
the whole application and synchronize across independently opened browser profiles.
Minimal, Balanced, and Maximal change surface weight while keeping the same palette.
New installations default to **Darker / Minimal / Standard**; saved choices remain
unchanged. Imported artwork is shaded to keep bright details from obscuring text.
Every fenced text or code box has a **Copy** button, including expanded views.
In Work, **Run with AI** sends a reviewed shell block to the current session's
provider and shows the result in the conversation, preserving any unsent draft.
On Linux desktops with Zenity, provider commands receive a private sudo askpass
helper for desktop password entry. Existing `SUDO_ASKPASS` settings take priority.
This supports non-terminal sudo and `sudo -A`; an allocated terminal, explicit
`sudo -n`/`-S`, or a sandbox that blocks desktop access can prevent the dialog.
Preview downloads include these interface improvements.
[View the controls](docs/assets/appearance-preferences.png) ·
[Preview downloads](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/tag/v0.7.0-rc.18) ·
[Windows ZIP](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/download/v0.7.0-rc.18/PilferedParrot-0.7.0-rc.18-windows-x64.zip).
The stable download links below remain on 0.6.1.

> **v0.6.1 · stable Linux release and Windows 10/11 x64 preview**
>
> Version 0.6.1 fixes terminal visibility and shows the working folder and exact command
> before execution. See [release notes](RELEASE_NOTES.md).
>
> PilferedParrot Interface is a local interface for coding CLIs and compatible local or remote
> models. It keeps the provider you choose visible while giving you one browser workspace for
> technical work and provider-matched Chat.
>
> [Project page](https://pilferedparrot.github.io/PilferedParrot-Interface/) ·
> [v0.6.1 release](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/tag/v0.6.1) ·
> [Windows preview](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/download/v0.6.1/PilferedParrot-0.6.1-windows-x64.zip) ·
> [Source archive](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/download/v0.6.1/pilferedparrot-0.6.1-source.tar.gz) ·
> [Report a bug](https://github.com/PilferedParrot/PilferedParrot-Interface/issues/new?template=bug_report.yml) ·
> [Share feedback](https://github.com/PilferedParrot/PilferedParrot-Interface/issues/new/choose)

![Work with the shared blue surfaces and an example conversation](docs/assets/work-preview.png)
![Chat with the same blue surfaces and an example conversation](docs/assets/chat-preview.png)

*Actual Work and Chat interface screenshots with example conversations.*

Linux remains the stable, best-validated platform, with the strongest evidence on Linux Mint with
X11. Version 0.6.1 also provides a portable Windows 10/11 x64 preview. The Windows executable
bundles Python, needs no administrator rights, and uses Chrome, Chromium, or Microsoft Edge. Keep
its console window open while it runs. Windows state is stored under `%LOCALAPPDATA%\PilferedParrot`
and new projects default to `%USERPROFILE%\PilferedParrot Projects`.

Windows automated coverage includes browser interactions, platform integration, and executable
startup. Real provider-account or provider-CLI validation is not claimed for Windows. Providers are installed and authenticated
separately. Browser Chat and file tools are available; the Bubblewrap shell is disabled on Windows.
Supported npm provider shims are resolved directly through Node without invoking `cmd.exe`.

Google Antigravity currently supports **Work** only; other provider accounts and compatible
endpoints require your own setup and credentials. See [provider compatibility](docs/provider-compatibility.md)
and the [provider validation notes](docs/provider-expansion-validation.md) for current limits.

Work runs directly through the selected provider. See the [backend Harness review](docs/harness-review.md) for the retired manual planner and the execution safeguards that remain.

When work remains unfinished, the agent is instructed to leave a copyable **Next session
prompt** and save a whiteboard handoff when writing is allowed. See the
[continuation rule](docs/continuation.md) for what it includes and its limits.

## Optional product feedback

Open **Preferences → Help improve PilferedParrot** for independent, off-by-default
choices about usage, problems, preferences, and local app changes. Review and
download a report to share voluntarily; nothing is uploaded. Terminal users can
run `pilferedparrot feedback --help`. See the [feedback policy](docs/feedback.md)
for the exact fields, retention, revocation, and local-fix reporting limits.

## What it supports

PilferedParrot is a local browser interface for coding engines and compatible model APIs:

- **Local Qwen**, served through an OpenAI-compatible endpoint and equipped with contained file,
  diff, and Linux shell tools.
- **OpenAI Codex**, run through the authenticated Codex CLI with its normal model, sandbox, and
  approval behavior.
- **Claude Code**, run through the authenticated Claude CLI in non-interactive print mode while
  preserving its provider session between turns.
- **Google Antigravity**, through the current Antigravity CLI (`agy`), with model discovery,
  streamed Work progress, native conversation continuation, and cancellation. Read-only Chat
  is not yet supported for this integration.
- **Google Gemini**, through Gemini CLI with supported API or organization authentication,
  or through the Google AI Studio API template. Consumer Google sign-in no longer provides
  Gemini CLI access; see [provider compatibility](docs/provider-compatibility.md).

The operator chooses the provider. Harness applies an explicit, configurable delegation policy
locally; planning and recording reviews use no model calls.

The browser also opens **Chat** in its own floating window, powered by a separate read-only session
from a supported provider active in the work window that opened it. Chat offers that provider's model menu;
Antigravity currently supports Work only.
It is an ordinary conversation kept separate from work sessions; it is not an interpreter or hidden
router.

The source project runs on Linux and Windows with Python 3.12 or later. Chrome or Chromium is the preferred
Linux browser; the Windows preview also detects Microsoft Edge. Bubblewrap is needed when a Linux
provider uses API or local shell tools; it is not needed just to open the browser interface and is
disabled on Windows. The Python application has no third-party package dependencies.

## Install and configure

### Windows preview

Download `PilferedParrot-0.6.1-windows-x64.zip` from the [v0.6.1 release](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/tag/v0.6.1),
extract it to a directory you control, and run `PilferedParrot.exe`. The portable package includes
Python and does not require installation or administrator rights. Keep the console open while the
interface is running. On first launch, the app creates
`%LOCALAPPDATA%\PilferedParrot\config.json`; edit that generated file when customizing providers
and preserve its `web.chat_store` and `ledger` paths. The bundled `config.example.json` is a
reference for provider options, not a replacement for the generated config. Install and configure
any provider CLI, Node.js, or local model server separately.

For a Windows source checkout, install Python 3.12 or newer and run `python -m pilferedparrot`
from the repository, or run `PilferedParrot.cmd`. The source launcher checks the Python version and
passes arguments to the Windows entry point; it does not bundle Python.

### Linux release

On a Linux machine with Python 3.12 or later and Chrome or Chromium installed, open a terminal and
run:

```bash
git clone --branch v0.6.1 https://github.com/PilferedParrot/PilferedParrot-Interface.git PilferedParrot-Interface
cd PilferedParrot-Interface
cp config.example.json config.json
```

Start the interface:

```bash
./bin/pilferedparrot
```

In the browser, open **Providers** and select a configured, authenticated provider. If you use a
compatible endpoint instead, choose **Add provider** and enter its endpoint and credential
environment variable. Provider access, model availability, pricing, and limits belong to the
account or service you configure; this release does not promise free provider usage or universal
compatibility.

`config.json` is ignored so machine-specific paths stay local. Start your Qwen server yourself,
or set `qwen.auto_start` to `true` and provide `qwen.start_command` as an argument array. The
committed example leaves automatic startup disabled. Existing config files are tightened to mode
`0600` when loaded on POSIX systems. All defaults are in
`pilferedparrot/config.py`.

New Codex Chat defaults to GPT-6 Luna. Model choices come from the installed Codex
catalog; explicit selections and existing conversations retain their models.
The optional Sol/Luna compatibility preset uses GPT-6 models. See
[model allocation](docs/model-allocation.md) for the local Sol/Luna/Astra policy,
its evaluation limits, and the distinction between Work and that preset.

It listens on `http://127.0.0.1:8765` and opens a dedicated app-style browser window when the
launcher can find Chrome or Chromium. Chats and run metadata persist under
`~/.local/state/pilferedparrot/` with owner-only permissions. On first start after upgrading, the
app imports existing chats from the former state directory. The desktop installer also moves the
known legacy state files and browser profile into the new directory without overwriting newer data.
Closing the last PilferedParrot work window stops its local server and cancels any active provider
run; closing only the optional Chat window does not.

Codex, Claude, Gemini, and Antigravity jobs have no total runtime limit imposed by PilferedParrot.
They can continue through long tasks and periods without output until the provider exits, you cancel,
or the application shuts down. Legacy `request_timeout_seconds` settings for these providers are
ignored. Network requests, startup checks, and individual tools retain their own timeout behavior.

Use **Providers** in the sidebar to open the provider dashboard. It shows connection state, lets you
choose a model, and can start another maximized app window with that fixed provider and model.
Every LLM integration is one card. Use **Add provider** to add xAI/Grok, OpenRouter,
Google AI Studio/Gemini API, Mistral/Devstral, LM Studio, Ollama, or any
service with an OpenAI-compatible chat-completions and tool-calling API. Presets fill the standard
base URL and credential environment-variable name; leave Model ID blank to discover available
models automatically. PilferedParrot stores settings beside the chat store (or at
`web.model_catalog_store` when configured), but never stores the API key itself. Each card has a
**Remove provider** action. Removal hides the card without deleting its settings, and **Add
provider** offers it for one-click restoration.
Keyed remote endpoints must use HTTPS. Provider HTTP redirects are restricted to the originally
configured origin so a redirect cannot carry an API key to another host or downgrade it to plaintext.
Each provider window shows that provider's persistent session history, including after the window is
closed and reopened; context usage remains local to each session. Codex and Claude maintain
separate CLI credentials, so they can remain signed in and run in different windows at the same
time; Local Qwen needs no account. Signing out affects every PilferedParrot window using that
provider, but does not sign out the other providers. Selecting **Sign in** starts the provider's
official CLI flow in the background and opens its login page in the system's default browser; no
terminal link or command is part of the user flow. The dashboard detects completion and presents a
clear **Use Provider** action. Authentication and account choices remain owned by the provider CLI;
PilferedParrot never receives the account password.

Use **Preferences → Change theme** in the sidebar to open Chrome's theme gallery with
PilferedParrot's dedicated browser profile. PilferedParrot runs as a private Chrome app window, and
this control does not change the Chrome theme used for normal browsing. The selected theme persists for the main app window and
PilferedParrot shows the original theme colors and available new-tab, frame, toolbar and attribution
artwork when the user returns to the app. Artwork keeps its native size, alignment and tiling; readable
text panels leave the surrounding artwork visible. The isolated Chat window uses the same selected theme without sharing browser state.
Theme changes replace colors and artwork together. On Linux/X11, desktop app windows use a transparent title bar over the same page background
and theme artwork, with the logo, app name, and window controls placed above it. Drag the title bar
to move the window, double-click it to maximize or restore, or use its minimize, maximize/restore
and close buttons. Window edges remain resizable. On Windows, the main app's dedicated Chrome
profile supplies the theme to Chrome's own title bar and controls. Isolated provider and Chat
windows keep their browser-owned frames; their page content uses the shared theme. Browser tabs
and unsupported window systems retain their normal browser or desktop controls.
Chrome theme installation requires Chrome or Chromium; it is unavailable when using Edge.

The model picker sits beside **Reasoning** at the bottom of the composer in both work and Chat.
For Codex, choose a supported effort for the next message; the choice is saved with that session
and can change between turns without resetting the conversation. **Codex default** leaves work
sessions on the configured `codex.reasoning_effort`, or the CLI's settings when no override is set.
**Chat default** uses `web.chat_reasoning_effort` (low by default), independently of the CLI's default.
These choices do not rewrite your Codex terminal configuration. Supported levels come from Codex's
local model catalog, with low/medium/high as a fallback when metadata is absent. A structured
Codex `model_options` entry can override `reasoning_efforts`; an empty array hides the control.
Providers without a supported reasoning control keep their model picker alone. Switching to a model
that cannot use the selected effort resets reasoning to Default.

New work sessions inherit the model and reasoning choice from the most recently used session in
that provider window, including an explicit Default choice. These choices survive restarting PPI;
an incompatible inherited reasoning level resets to Default when choosing another model.

Expand **Response details** beneath a new Work or Chat reply to inspect its requested model and
destination. Compatible APIs also record the model IDs returned by the server, including tool-loop
responses. Requested and reported identifiers are neutral details: aliases and model-file paths
can name the same model differently, so a text difference does not trigger a mismatch warning.
Local models receive their configured provider, model, and endpoint information in the system
prompt. A loopback destination shows that PPI contacted this machine, but a proxy can forward the
request elsewhere. Server-reported IDs and the model's own description do not independently prove
the loaded weights. CLI replies show the configured request without a server-reported model ID;
older replies have no routing record.

Session history uses the sidebar's available space. Expand **Context window** for token estimates
and context limits, or **Preferences** for notifications and appearance. The collapsed context
summary keeps estimated usage visible without occupying a full settings panel.

The model picker sits beside **Reasoning** at the bottom of the composer in both work and Chat.
For Codex, choose a supported effort for the next message; the choice is saved with that session
and can change between turns without resetting the conversation. **Codex default** leaves work
sessions on the configured `codex.reasoning_effort`, or the CLI's settings when no override is set.
**Chat default** uses `web.chat_reasoning_effort` (low by default), independently of the CLI's default.
These choices do not rewrite your Codex terminal configuration. Supported levels come from Codex's
local model catalog, with low/medium/high as a fallback when metadata is absent. A structured
Codex `model_options` entry can override `reasoning_efforts`; an empty array hides the control.
Providers without a supported reasoning control keep their model picker alone. Switching to a model
that cannot use the selected effort resets reasoning to Default.

New work sessions inherit the model and reasoning choice from the most recently used session in
that provider window, including an explicit Default choice. These choices survive restarting PPI;
an incompatible inherited reasoning level resets to Default when choosing another model.

Expand **Response details** beneath a new Work or Chat reply to inspect its requested model and
destination. Compatible APIs also record the model IDs returned by the server, including tool-loop
responses. Requested and reported identifiers are neutral details: aliases and model-file paths
can name the same model differently, so a text difference does not trigger a mismatch warning.
Local models receive their configured provider, model, and endpoint information in the system
prompt. A loopback destination shows that PPI contacted this machine, but a proxy can forward the
request elsewhere. Server-reported IDs and the model's own description do not independently prove
the loaded weights. CLI replies show the configured request without a server-reported model ID;
older replies have no routing record.

Session history uses the sidebar's available space. Expand **Context window** for token estimates
and context limits, or **Preferences** for notifications and appearance. The collapsed context
summary keeps estimated usage visible without occupying a full settings panel.

The browser shows:

- three independent spaces for usage/history, technical work, and optional provider-matched Chat;
- the selected provider and model;
- the project folder and conversation history;
- live provider commentary, commands, and tool completion events in a bounded work-details panel;
- a native desktop notification when a work-session or Chat response finishes (after browser
  notification permission is granted);
- Copy controls for fenced text/code, plus Run with AI for completed shell blocks in Work;
- whether local Qwen is ready;
- Codex, Claude, and Gemini CLI sign-in/availability status;
- Codex's provider-reported included-usage amount and reset time, plus a clear unavailable state
  when a provider has no supported live allowance source.

The project folder shown in the header is the primary writable workspace. Change it before the
first message when starting work in another checkout. PilferedParrot rejects a clear first-turn request
to modify a different, unapproved project before spending a provider turn.

### Work execution and the retired Harness planner

Describe your task in Work and send it to the selected provider. The manual
Harness button and window were removed in rc.12. Existing records and API remain;
saved worker sessions are readable and can return to their parent, but the retired
planning, review, and retry controls are no longer in the UI.

Read the [backend review](docs/harness-review.md) and [next-session prompt](docs/next-session.md)
for the distinction between CLI-native execution and the app's compatible API tool loop.

### Safe Markdown rendering

The work and Chat surfaces use the same dependency-free renderer and escape all input before
adding supported formatting. The supported subset is ATX headings, paragraphs and hard line
breaks, `*`/`_` emphasis, `**`/`__` strong text, inline code, flat ordered and unordered lists,
blockquotes, horizontal rules, delimiter-row tables, complete triple-backtick fenced code blocks,
and absolute `http`, `https`, and `mailto` links. Raw HTML, images, relative links, nested inline
formatting, indented code, and the rest of CommonMark are displayed as text rather than interpreted.

Fences must open and close on their own lines; an optional single language name is allowed.
Unmatched or inline backticks stay visible as text. Tables require at least two columns and a valid
hyphen delimiter row, and `\|` preserves a literal pipe within a cell, so ordinary pipe-containing
prose is not treated as a table. Quotes are parsed to 16 levels; deeper quote markers remain visible
escaped text. Long responses are not silently truncated by the renderer, and unsafe HTML remains
escaped regardless of length.

The terminal action is narrower than display Markdown: it appears only for a complete, top-level
fence in a completed assistant response when the fence is unlabeled or uses a recognized shell
language, contains exactly one non-empty line, and is no longer than 4,000 characters. Fences in
quotes, user messages, unmatched fences, multiline commands, oversized commands, and non-shell
languages are display-only. The server revalidates the stored message and these same conditions
before launching anything.

Codex can also work across a small, explicit set of related checkouts without disabling its
sandbox. Add those directories to the machine-local `config.json`:

```json
{
  "codex": {
    "sandbox": "workspace-write",
    "additional_write_dirs": ["~/related-project"]
  }
}
```

In `workspace-write` mode, PilferedParrot passes each path with Codex's supported
[`--add-dir` option](https://learn.chatgpt.com/docs/developer-commands?surface=cli) on both new and resumed turns.
Paths must already exist and be writable by the operator. Prefer individual project roots over the
whole home directory. Extra write roots are ignored in `read-only` and `danger-full-access`
modes. Keep task-specific removable drives out of this global list so unrelated work does not
depend on them. Mentioning a path in a prompt does not change the selected project or grant access;
the provider enforces permissions on actual operations.

Other commands remain available for diagnostics and scripts:

```bash
./bin/pilferedparrot repl
./bin/pilferedparrot budget
./bin/pilferedparrot run --provider qwen "inspect this repository"
./bin/pilferedparrot run --provider codex "implement the agreed fix"
./bin/pilferedparrot run --provider claude "review the implementation"
./bin/pilferedparrot run --provider gemini "summarize the design"
./bin/pilferedparrot run --provider antigravity "inspect this repository"
```

Run `./bin/install-pilferedparrot-desktop` to replace the old Mint/Cinnamon start-menu entry and
its letter-C icon with the PilferedParrot launcher and parrot artwork.

## Drafts and the shared whiteboard

Unsent Work text is saved per session. Switching sessions, reloading, or reopening the app restores
that session's draft. Empty, unused sessions older than 24 hours are removed by a daily local cleanup
while the app is open. Drafts, conversations, live selections and Harness activity are preserved.

Open **Whiteboard** at the top of Work to read recent notes or leave a message. Models on all jobs
can discover the shared board and use it when useful. Leads are instructed to give workers relevant
excerpts and share board access only when a worker's task requires it. Notes are
local to this installation and are read on demand; the board is not copied into every prompt. See
[access and limits](docs/whiteboard.md).

## Provider usage display

The sidebar reports the percentage **left in Codex's weekly included-usage window**. It does not
describe that number as a percentage of the subscription. For example, “Weekly included usage ·
98% left” means Codex reported 2% used in that weekly allowance at the time shown. Other
provider-reported windows are retained for diagnostics but are not rendered as sidebar bars.

PilferedParrot reads this through the Codex app server's account status method. The probe does not send
a model prompt or create a Codex conversation. It runs at startup, when manually refreshed, and
once per minute while the browser is open. OpenAI notes that local messages and cloud chats share
a rolling five-hour window and that additional weekly limits may apply; exact consumption depends
on the model and task size. See the [official Codex pricing and usage documentation](https://learn.chatgpt.com/docs/pricing).

Claude Code authentication is entirely owned by the Claude CLI. PilferedParrot reads only the
non-secret result of `claude auth status --json`; it does not read or write Claude credential files,
access OAuth tokens from the environment, refresh tokens, or call private token or allowance
endpoints. Anthropic does not provide a supported live personal plan-allowance interface for this
use, so a signed-in Claude installation remains ready to run while the sidebar explains that live
allowance data is unavailable. It shows no Claude allowance bar or reset countdown. Per-turn token
and context telemetry reported by Claude execution remains available and is separate from account
allowance reporting.

Authentication normalization trusts only a supported `loggedIn` boolean. Claude's current CLI may
return `loggedIn: false` with exit status 1 for a normal signed-out result, which PilferedParrot
recognizes as signed out. Missing CLI, timeout, malformed JSON, a nonzero result without that
supported signed-out shape, and a successful result without the boolean normalize to unknown. The
supported status output has no reliable token expiration state, so expiration-like failures also
normalize to unknown instead of being inferred from free-form stdout or stderr.

This release removes the former direct Claude OAuth allowance integration. Existing chat history
and historical ledger records are left intact; older ledger snapshots may still contain Claude
windows recorded by an earlier version, but they are historical data and are not reused as a live
quota. Organization analytics reporting is not implemented.

## Context and token behavior

PilferedParrot deliberately keeps the provider path thin:

All execution goes through a `ProviderAdapter` contract for run/resume/cancel, normalized progress,
per-turn token and context usage, capabilities, authentication, execution availability, allowance
reporting availability, and model discovery. Codex, Claude, Gemini, Antigravity, and OpenAI-compatible endpoints
retain separate implementations behind that contract.

- Each new provider conversation receives a compact [shared whiteboard](docs/whiteboard.md) pointer
  once. Board contents are read only on demand; there are no background model calls. Leads are
  instructed to give workers relevant excerpts and share board access only when their task requires
  it. An ordinary Codex turn otherwise receives the user's prompt directly.
  An explicitly launched Harness package receives its compact, visible contract once for that attempt.
  Chat is informational and does not interpret, rewrite, or relay technical requests.
- Continuing with the same provider and model resumes that provider session. A new conversation,
  provider change, or model change starts fresh context.
- Chat has its own persisted, read-only provider session. It inherits the provider and selected model
  from the work window that opens it, then lets the user choose another model in that provider family.
  It receives the Chat message directly and does not receive hidden work-session snapshots. Resetting
  Chat does not reset a work session.
- Chat and each work session show estimated next-request live-context usage; expand **Context window**
  for the detailed pie against the usable
  model limit. A new work session starts at zero and begins accounting when its first request
  starts. After a request, the estimate uses the provider's final per-request input plus its
  latest output; this includes injected instructions, tools and relevant results, workspace context,
  attachments, and other prompt inputs. Before that telemetry exists, PilferedParrot estimates the
  transcript and known provider/workspace overhead at roughly four characters per token. Reserved
  output headroom is shown separately and is not counted as used. Completed-turn aggregate compute
  totals remain separate because they are not current context occupancy. Each provider can define
  per-model `context_window` and `max_context_window` metadata in `model_options`; Codex also uses
  its local model catalog's `max_context_window` when available. The browser lets Codex users choose
  25%, 50%, 75%, or 100% of that maximum. The selected
  usable-token limit is passed to Codex as `model_context_window`. Set `qwen.context_window_tokens`,
  `codex.context_window_tokens`, or `claude.context_window_tokens` to preserve a manual maximum
  override when a provider does not publish the correct limit locally. The matching
  `context_window_percent` settings default to `100`.
- Model menus refresh through the selected provider adapter whenever the picker opens. Friendly
  names and exact IDs are shown when useful; Claude's shorter menu shows concrete numbered model
  names only, so a saved selection does not silently move to a newer release. The
  most recently chosen model and context-window allowance become the initial selections for the
  next matching provider window or Chat thread and persist across app restarts.
- There is no automatic last-look, model-to-model ping-pong, background delegation, or generated
  maintenance work.
- Browser progress is local display data. It is bounded and is not copied into later prompts.
- Run history records a prompt hash rather than the prompt text.

OpenAI-compatible providers maintain their conversation history locally under provider-neutral
state. Their tool results are capped, shell duration
and loop length are bounded, and the browser stores only a bounded progress view.

## Qwen tools and isolation

When selected, Qwen runs an agent loop with file, shell, and Git diff tools. File access is limited
to the selected project and configured additional roots. Shell commands run under Bubblewrap with
those roots and an ephemeral `/tmp` writable. Only system binaries and selected runtime configuration
are mounted read-only; other host data, including unrelated homes and removable drives, is hidden.
Sensitive environment variables are removed, and networking is disabled by default. Use system
`python3` or a toolchain inside an explicitly selected root; home-installed shims are hidden.

Git review runs in a separate read-only, network-disabled sandbox, including in Chat. Repository
filters cannot modify host files or access other host data. If Linux/Bubblewrap or the repository's
Git metadata is unavailable within that sandbox, review falls back to changes recorded by the file
tools. This fallback does not provide a complete Git diff; external Git worktree metadata is hidden.

Selecting the entire home directory would defeat that isolation, so it is rejected by default.
The machine-local `qwen.allow_home_workspace` setting can opt in to that exact directory when the
broad authority is genuinely intended. Selecting one of home's parents is always rejected because
it grants an even broader scope.

A task that spans several projects usually does not need the home directory at all. List the extra
roots in `qwen.additional_dirs` instead: each one is mounted into the sandbox and reachable by
absolute path, while other project data stays hidden. The list is configuration-only, so a prompt
cannot widen its own authority, and it cannot contain the home directory or one of its parents —
that remains a deliberate `allow_home_workspace` decision rather than something an extra root can
smuggle in.

Because a sandboxed provider cannot open in the home directory, a provider window launched from a
window that *is* sitting there would previously fail to open at all, leaving nowhere to correct the
folder. Such a launch now falls back to `qwen.default_workspace` when it is set, and otherwise opens
the window and asks for a project folder before starting the first chat.

Qwen endpoints must also be loopback URLs by default. Set `qwen.allow_remote_egress` to `true`
only when you intentionally want prompts and tool results sent to the configured remote server.

Set `qwen.shell_network` to `true` only when a task needs network access. Bubblewrap is a useful
accident boundary, not a substitute for a disposable VM when working with hostile projects,
compilers, or kernel inputs.

Codex retains its own sandbox, authentication, approvals, and network behavior. PilferedParrot reasserts
the configured working directory, sandbox mode, and additional write roots on new and resumed
turns; it does not disable those controls.

Claude Code likewise owns its authentication, tool permissions, settings, and network behavior.
PilferedParrot uses Claude's print-mode CLI and resumes only the session ID returned by Claude.
Account actions use `claude auth login`, `claude auth status`, and `claude auth logout` when
supported by the installed CLI. Allowance-reporting availability never gates Claude execution.

The browser API uses separate dashboard and Chat capabilities, delivered in URL fragments and
kept out of `/api/state`. Browser mutations must come from the server's exact origin; a capability
for one window cannot operate the other window's controls.

## Branding

PilferedParrot is developed by PilferedParrot Global Industries, makers of 3D Bumper Billiards.
The app uses the company logo and compact parrot emblem from those branding assets.

## Development

Run the same checks used by CI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
python3 -m compileall -q pilferedparrot tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
Provider requirements and integration limits are listed in
[Provider compatibility](docs/provider-compatibility.md).

## License

PilferedParrot is available under the [Apache License 2.0](LICENSE). Redistributions must preserve
the attribution notices described in the license and [NOTICE](NOTICE) file.

If PilferedParrot is useful to you, you can support its development on
[Patreon](https://www.patreon.com/PilferedParrot).

When a work session or Chat opens, PPI checks the selected provider's CLI for updates
in the background. Codex, Claude Code, and Gemini CLI checks compare the installed
version with the public npm registry. The notice above the composer reports the
result and an update command when a newer version is available; PPI does not install
updates automatically. Offline or failed checks leave the session usable. Remote
models and manually managed local models report that no automatic CLI check applies.
Reopening a session or reloading the window checks again. During a work response,
the status line shows the latest reported activity; expand Work details for the log.

### Usage displays

Work and Chat show all allowance windows reported by Codex, with percentage used,
percentage left, reset times, and freshness. Five-hour and weekly windows depend
on the account and model bucket; unavailable windows are not invented. API-key
billing is separate from ChatGPT included usage. These displays follow
[OpenAI's current usage documentation](https://learn.chatgpt.com/docs/pricing).

Context usage is a next-request estimate. Codex updates it from local usage records
during a response; other providers use their available telemetry or a labelled
local estimate. Refreshing these displays sends no prompts or model requests.
Allowance checks use the supported account endpoint and share a short cache.
