## PilferedParrot Interface 0.7.1

0.7.1 repairs Work execution and local tool isolation across the Linux/source release and
unsigned Windows preview. Earlier release archives remain unchanged; install 0.7.1 to receive
these fixes. The named manual Harness UI was retired in 0.7.0-rc.12, while affected
provider and tool paths also exist in the 0.5.x and 0.6.x releases.

- Unused Codex additional write roots no longer block startup in `read-only` or
  `danger-full-access`. Existing, writable additional roots are passed only in `workspace-write`.
- Work no longer treats a path mentioned in a prompt as a request for file permission. The
  selected provider's sandbox and approval rules still govern actual operations.
- Qwen's Linux shell uses an isolated filesystem containing only required runtime files and
  selected write roots. Git review runs separately in a read-only, network-disabled sandbox;
  when Git metadata is unavailable there, review reports file-tool changes instead. File-tool
  diffs recheck resolved paths so a changed symlink cannot redirect a host read outside the
  selected roots.

Release archives are built from the verified release tree and listed with `SHA256SUMS` at the
[0.7.1 release](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/tag/v0.7.1).
See the [Actions runs](https://github.com/PilferedParrot/PilferedParrot-Interface/actions) and
release body for completed validation results.
Windows provider accounts and CLIs are not live-certified by this package.

## Unreleased

- Project workrooms add recent and pinned folders, a project-filtered session list, and
  selection that survives reopening. Existing sessions retain their original workspace.
- Work history loads compact summaries before the selected transcript. Live progress uses
  an authenticated event stream with polling fallback; a reconnect can reload a snapshot.
- An opt-in ACP Work preview for Codex and Claude streams text, tool details and diffs, and
  surfaces permission choices in the Work window. The legacy engine remains the default.
- New Codex Chat sessions default to GPT-6 Luna with low reasoning when no Chat selection is
  saved; an explicit saved model and reasoning selection takes precedence.
- Optional product feedback remains off by default. Users can review and download local reports
  for voluntary sharing; PPI does not upload them.
- Unfinished work can include a copyable next-session prompt and, when writing is allowed, a
  whiteboard handoff. Continuation guidance does not change provider permissions or retry work.

## PilferedParrot Interface 0.7.0

0.7.0 makes the current Work and Chat experience the stable Linux/source release,
bringing together the improvements tested throughout the 0.7.0 previews. The
Windows 10/11 x64 portable package includes the same application and remains an
unsigned platform preview.

### Highlights

- Work and Chat share a blue-tinted interface, consistent spacing and controls,
  and live theme artwork. Appearance settings synchronize across separate windows.
  Fresh installations use **Darker / Minimal / Standard**; saved choices survive upgrades.
- Copy preserves whitespace in fenced code and text, including expanded views.
  Wide tables and charts can expand to full screen. Work's **Run with AI** sends
  a reviewed shell block through the current provider and preserves unsent drafts.
- Codex allowance displays show each reported window's used and remaining
  percentages, reset time, and freshness. Context estimates update during responses
  from local usage records without extra model requests.
- Drafts and shared whiteboard notes persist locally. New conversations ask leads
  to give workers relevant excerpts and share board access only when needed.
- Linux desktop integration includes provider sudo password dialogs when supported,
  and repairs Chromium's restored window decorations to retain the custom title bar.

Work continues through the selected provider. The manual Harness controls have
been retired; existing task records and backend APIs remain available.

### Install or upgrade

Download the source archive or Windows x64 ZIP and verify it against `SHA256SUMS`.
Finish active jobs before closing the old app. On Linux, run
`bin/install-pilferedparrot-desktop` from the new extracted directory to update the
launcher. On Windows, extract the complete ZIP and update shortcuts to its
`PilferedParrot.exe`. Keep existing configuration, browser profiles, conversations,
and local state. Reopening an older installation does not install this release.

Provider CLIs, accounts, and local model servers are installed separately. Windows
provider accounts and CLIs have not been live-certified; its Bubblewrap shell is
disabled. See the [README](README.md) and [validation record](docs/release-readiness.md).

## PilferedParrot Interface 0.7.0-rc.18 preview

Work and Chat show every included-usage window reported by OpenAI, including
five-hour and weekly windows when available. Each bucket shows percentage used,
percentage left, reset time, and data freshness. Separate model allowances retain
their names; a missing five-hour window is never inferred from weekly usage.
API-key users see that API billing is separate from ChatGPT plan allowances.
Unavailable, stale, failed-refresh, and reset-due readings are labelled explicitly.
The terminal budget command also shows used percentages and reset times.

Codex context estimates update during a response from local per-request telemetry,
including when compaction reduces the count. The interface shows the source and
last observation time. Local log reads run at most once per second, and allowance
probes share a 30-second cache across windows. No prompts or model requests are
sent to refresh either display. Other providers retain labelled estimates when
per-request context telemetry is unavailable.

The changes are shared by Linux/source and Windows x64 packages. Finish active
jobs, close the old app, then reopen the updated app. This remains a preview
alongside stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.17 preview

The current interface is now the release baseline for Linux/source and Windows.
Fresh installations default to **Darker / Minimal / Standard**, matching the
maintainer's saved appearance. Existing saved choices remain authoritative across
upgrades and separate Work and Chat windows. Imported artwork receives adaptive
shading so bright details do not obscure text; its scale and placement stay intact.
Background themes remain user-selected.

Every fenced code or text block has a Copy button, including expanded views.
Copying preserves indentation and blank lines. Work's Run with AI action sends the
reviewed shell block through the current session's normal provider, keeps the
unsent draft, and prevents duplicate execution when a request is retried.
On Linux desktops with Zenity, a private provider-only sudo askpass helper supports
desktop password entry without passing passwords through PPI. Existing helpers,
terminal behavior, and provider sandbox restrictions continue to apply.

Linux/X11 keeps the custom title bar intact when Chromium restores native window
decorations. The repair follows the bound window identity and stops when released.

Finish active jobs, close the old app, then reopen the updated app. This remains
a preview alongside stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.16 preview

Work and Chat now use one transparent, blue-tinted design: shared headers, sidebar
groups, selected history rows, conversation cards, detail disclosures, and composer
controls. Model and reasoning selectors keep their blue tint instead of becoming
black in Minimal mode. Both windows share message alignment, typography, empty
states, and the same narrow-window sidebar breakpoint.

Appearance settings now belong to the application rather than individual browser
profiles. Changes synchronize across independently opened Work, provider, and Chat
windows, persist after restart, and recover visibly from failed saves. Existing
per-browser choices give way to the shared Original / Balanced / Standard default.
Theme artwork retains its placement and scale; the earlier chart fullscreen feature
remains available.

Linux/source and Windows x64 packages include the redesign. Finish active jobs,
close the old app, then reopen the updated app to load the shared-settings backend.
This remains a preview alongside stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.15 preview

Wide Markdown tables and preformatted charts now show an expand button at the upper
right in Work and Chat. The expanded view uses full screen when available, or fills the app window, keeps large content
scrollable, and closes with its upper-right button or Escape. Controls adapt when
messages arrive or the window and sidebar are resized.

Work details no longer add an opaque panel over the message surface. Work avatar
tiles inherit the theme colors, removing fixed dark boxes from imported themes,
including Minimal appearance. Chat keeps its existing color scheme.

Linux/source and Windows x64 packages include these corrections. Reopen the updated
app after active jobs finish. This remains a preview alongside stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.14 preview

Appearance preferences in Work and Chat now offer an original or darker tone,
Minimal, Balanced, or Maximal surfaces, and stronger text separation. Minimal
reduces large panel fills so the background can show through; Maximal makes
surface boundaries more pronounced. Defaults preserve the previous interpretation.
Preferences apply immediately, persist in the browser profile, and synchronize
between open Work and Chat windows. Original theme artwork placement is preserved.

The README screenshot and Linux/source and Windows x64 packages are updated.
Reopen the updated app after active jobs finish. This remains a preview alongside
stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.13 preview

Every imported Chrome theme now uses a 60/100 legibility balance. Translucent sidebar,
message, and composer surfaces reveal more of the original artwork. Low-contrast
foreground colors move only as far toward light or dark as needed, preserving more
of the theme's palette. Dialogs, form fields, and code retain solid reading surfaces.
Original artwork, alignment, tiling, and toolbar images are preserved.

Work and Chat share these rules. Sidebar actions, connection information, and history
are grouped consistently; Preferences opens from its own button at the bottom.
Linux source and Windows x64 packages include the same update. Reopen the updated
app after active jobs finish. This remains a preview alongside stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.12 preview

Work and Chat share consistent spacing, rounded controls, and theme-aware surfaces.
App dialogs place their close control at the upper right, keep it visible while scrolling,
and reserve the bottom for actions. Sidebar and conversation dividers give way to spacing.
Provider removal uses the same dialog pattern. The manual Harness button and planner are
retired; existing task records, navigation, and backend API compatibility are preserved.
See the [design language](docs/design-language.md) and [backend Harness review](docs/harness-review.md).

## PilferedParrot Interface 0.7.0-rc.11 preview

Work and Chat now leave space around their rounded headers and use consistent
gaps between sidebar sections, cards, and related controls. Composer and message
gutters align. A narrow-window layout fix keeps the Work message box and Send
button inside the visible window.

Linux/source and Windows x64 packages include the same spacing update. Reopen
the updated app after current jobs finish to load the new stylesheet. This remains
a preview alongside stable Linux 0.6.1.

## PilferedParrot Interface 0.7.0-rc.10 preview

Work session headers and Chat headers now have four rounded corners, matching the
other interface panels. Window controls and dialog notices also have rounded corners.
The Linux/X11 custom window title bar is transparent: it shares the page background color and
lets theme pictures continue behind the logo, app name, and window controls without
an extra image, tint, or divider.

The README screenshot has been refreshed. Linux/source and Windows x64 preview
packages include the updated interface and documentation. Windows receives the rounded
interface headers, but its outer title bar remains controlled by Chrome or Edge;
continuous page artwork there requires a different Windows window integration.

## PilferedParrot Interface 0.7.0-rc.9 preview

The README, project website and Windows readme now explain what the app does in
plain language, with clearer download and upgrade instructions for Linux and
Windows. This release includes the same application features and theme fixes as
rc.8; it does not change how providers or conversations work.

Linux/source and Windows x64 packages include the updated documentation. This
remains a preview alongside stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.8 preview

The original theme frame and toolbar artwork fix is included for Linux and Windows. A new
regression checks that a blue frame image remains visible over its black fallback color after
reopening Work and Chat. Themes with bright page backgrounds and dark content panels now keep
composer labels readable using the panel's foreground color.

Download and install this package before reopening the interface. Reopening an older copy does
not install a GitHub release. On Linux, run `bin/install-pilferedparrot-desktop` from the new source
directory to update the menu entry; on Windows, point shortcuts at the new extracted executable.
Keep existing configuration and conversation stores. See the README upgrade instructions.

This remains a preview alongside stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.7 preview

Work and Chat top bars now display the selected Chrome theme's original toolbar image. The
Linux custom title bar uses its original frame image and overlay. Artwork retains its natural
size and horizontal tiling, with the author's colors underneath; the full-bar translucent tint
is removed. Theme backgrounds retain their original size, alignment and repeat settings.

Text and controls remain readable with localized contrast support. Color-only themes retain
their authored surfaces and readable foregrounds. Switching or removing themes replaces the
images together without reloading Work or Chat or losing drafts.

Linux/source and Windows x64 packages contain the same interface update. Reopen the updated
application after current jobs finish to load this version. This remains a preview alongside
stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.6 preview

The selected theme artwork now continues through the Work and Chat top bars and the Linux
custom window title bar. Header text and window controls remain readable over the artwork.
The white divider strip and idle grab tab between sections are removed; pointer and keyboard
sidebar resizing still work, with a visible handle on hover or keyboard focus.

Linux/source and Windows x64 packages contain the same interface update. Reopen the updated
application after current jobs finish to load this version. This remains a preview alongside
stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.5 preview

Work and Chat share one theme background across session history and the conversation. Drag the
sidebar divider to change its width and the portion of the artwork inside it; keyboard resizing
and saved widths are supported. Text and controls retain readable surfaces over vivid artwork.

Theme selections and removal update automatically in visible windows after Chrome saves them.
Unchanged themes reuse their images, failed image loads retry, and theme updates preserve drafts
and session state. No page refresh is needed for subsequent theme changes.

Linux/source and Windows x64 packages contain the same interface update. Reopen the updated
application after current jobs finish to load this version. This remains a preview alongside
stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.4 preview

Work and Chat now center the parrot branding in the top bar and remove the duplicate
sidebar logo. Desktop windows with custom controls center the branding in the window title
bar; browser tabs and Windows use the application header. Narrow layouts keep session
information and actions accessible. Sidebar spacing and control styling are refined while
preserving selected theme colors and artwork.

Linux/source and Windows x64 downloads contain the same interface update. Close the previous
app after current jobs finish and reopen the updated application. This remains a preview
alongside stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.3 preview

Theme changes now replace colors and artwork together in Work and Chat. Background requests
use the selected theme version, outdated image requests cannot return a different theme's
artwork, and delayed refreshes cannot overwrite the current selection. Removing a theme clears
its images. Dialogs, controls and activity panels use the selected theme's readable colors.

Linux/X11 desktop app windows now have a themed title bar with minimize, maximize/restore and
close controls, title-bar dragging and resizing. The main Windows app keeps Chrome's native
frame, which uses the theme installed in its dedicated profile. Isolated Windows provider and
Chat windows keep their browser-owned frames while their page content follows the shared theme.
Browser tabs and unsupported window systems retain their normal native controls.

The Linux source and Windows x64 package include the same application changes. Close the old
application after current jobs finish and reopen it to load the updated desktop integration.
This remains a preview alongside stable 0.6.1.

## PilferedParrot Interface 0.7.0-rc.2 preview

Chrome theme artwork now loads in Work and Chat: the content security policy permits the
locally fetched image blobs. New-tab, frame, toolbar, overlay and attribution artwork retain
native size, alignment and tiling. Theme colors replace the previous dark color mixing;
text surfaces choose a readable foreground. Chrome controls the actual browser frame.

Work drafts survive session switching and reloads, with debounced local persistence and a
browser recovery cache. Failed submissions retain the original draft; successful submission
preserves text typed for the next message. Once every 24 hours while the app is open, cleanup
removes unused, empty sessions at least 24 hours old. Drafts, messages, named sessions,
provider continuations, running jobs and Harness activity are retained, as are live selections.

The new **Whiteboard** button opens persistent notes shared across models and jobs. Every new
provider conversation gets a compact discovery note, with no board history injected and no
background model calls. Native Work CLIs receive the dedicated board directory; compatible
models receive bounded read/post tools. Read-only Chat cannot post. See [whiteboard usage](docs/whiteboard.md).

The Windows x64 preview includes the same fixes. This remains a preview alongside the existing
stable 0.6.1 release; Windows provider accounts are still installed and authenticated separately.

## PilferedParrot Interface 0.7.0-rc.1 preview

Removed the 30-minute total runtime cutoff for Codex, Claude, Gemini, and Antigravity jobs.
Long-running jobs now continue until the provider exits, the operator cancels, or the app shuts
down. Legacy provider `request_timeout_seconds` settings no longer terminate jobs; explicit
cancellation remains available, including when a provider is silent or not reading its input.

The preview integrates the 0.6.1 Linux/Windows terminal fixes. It keeps stable downloads on 0.6.1.

### Bounded agent harness

Work now includes an explicit Harness workflow for economical delegation. It provides configurable
Sol/Luna and custom routing, compact contracts, fresh worker sessions, bounded evidence-based retries,
artifact verification records and conservative outcome measurement in existing session storage.
The provider adapters retain execution and permissions; no legacy hooks or maintenance queue are
restored. See [setup and limits](docs/harness.md), [migration](docs/harness-migration.md), and
[baseline evidence](docs/harness-baseline.md). Existing Work and Chat sessions remain compatible.

Package status now names the next action, completed workers return directly to parent review,
and the interface explains when the three-attempt limit requires a new approach. Acceptance
is an operator-recorded verdict, with its evidence visible. Six contained Codex fixture runs
passed independent checks and lead review without retries; this establishes functional coverage,
not savings. See [comparison evidence and limits](docs/harness-comparison-results.md).

# PilferedParrot Interface 0.6.1 release notes

Version 0.6.1 fixes terminal visibility and command display. Terminal actions request a normal-size visible
Linux window, show the exact working folder and command before execution or a sudo prompt, and
provide the same display behavior on Windows. Windows terminal launches retain console input and
output. The terminal dialog closes before launch; window focus is best effort and depends on the
desktop and window manager.

# PilferedParrot Interface 0.6.0 release notes

PilferedParrot Interface 0.6.0 keeps the stable Linux release and adds a portable Windows 10/11
x64 preview. The Windows asset is
[`PilferedParrot-0.6.0-windows-x64.zip`](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/download/v0.6.0/PilferedParrot-0.6.0-windows-x64.zip);
the source asset is
[`pilferedparrot-0.6.0-source.tar.gz`](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/download/v0.6.0/pilferedparrot-0.6.0-source.tar.gz).
The Windows package is portable and bundles Python, requires no administrator rights, opens with
Chrome, Chromium, or Edge, and keeps application state under `%LOCALAPPDATA%\PilferedParrot`.
Its default project directory is `~/PilferedParrot Projects`; keep the console window open while
the application runs.

The native folder picker recognizes Windows paths, terminal actions use PowerShell, and provider
cancellation stops the Windows process tree. File-tool diffs also work without a Git installation;
full repository status and diffs use Git when available. The ZIP includes runtime license notices
and is distributed with SHA-256 checksums. This preview executable is unsigned.

Windows providers remain separately installed and configured. The preview supports browser Chat
and file tools, disables the Bubblewrap shell, and resolves supported npm shims through Node
without `cmd.exe`. Windows CI covers browser interactions, platform checks, and the bundled executable.
This release makes no claim of real Windows provider-account or provider-CLI validation. Linux provider
evidence below remains unchanged.

## 0.5.1 history

The following 0.5.1 and 0.5.0 notes are retained as historical implementation and validation records.

# PilferedParrot Interface 0.5.1 release notes

PilferedParrot Interface 0.5.1 is the stable Linux release and repository/governance release.
It records the project rename from `PilferedParrot/ai-conductor` to
`PilferedParrot/PilferedParrot-Interface`, establishes repository ownership protections, and
ends the public preview designation. The runtime is unchanged apart from its version. The complete 440-test suite passed again
with mandatory Playwright and no skips. The earlier live-validation evidence below is preserved
from 0.5.0; no additional provider-account or platform coverage is claimed for 0.5.1.

Contributions now require a pull request, maintainer review, passing CI, and CodeQL checks.
Protected branches cannot be force-pushed or deleted, and published version tags cannot be
changed. Secret scanning, push protection, private vulnerability reporting, and dependency
security updates are enabled. CI checkouts no longer retain their GitHub credentials.

See the [v0.5.1 release](https://github.com/PilferedParrot/PilferedParrot-Interface/releases/tag/v0.5.1)
and [release readiness notes](docs/release-readiness.md). Linux Mint with X11 remains the best
validated environment. Google Antigravity supports Work only, and other provider accounts or
compatible endpoints require their own setup and credentials.

## 0.5.0 history

The following 0.5.0 notes are retained as the historical implementation record.

# PilferedParrot 0.5.0 release notes

PilferedParrot 0.5.0 is a public Linux preview of the local browser interface for coding CLIs and
compatible local or remote models. It is best validated on Linux Mint with X11. Google Antigravity
supports Work only, and other provider accounts or compatible endpoints require their own setup and
credentials. Bubblewrap is required for provider API or local shell tools, but not merely to open
the interface. See [provider compatibility](docs/provider-compatibility.md) and the
[provider validation notes](docs/provider-expansion-validation.md) for known limitations and
account-dependent coverage.

- Added Google Antigravity CLI for Work: streamed response/tool progress, exact conversation
  continuation, model discovery, and cancellation. Read-only Chat is disabled for this adapter.
- Added Mistral/Devstral and LM Studio connection templates. OpenRouter DeepSeek/GLM and other
  compatible models retain reasoning metadata and structured content through tool continuation.
- Added an opt-in `bin/check-provider` command for contained live API tool/continuation checks.
  See [provider validation](docs/provider-expansion-validation.md) for live versus offline coverage.
- Work and Chat preserve underscores inside identifiers such as `PARROT_UI_7281` while
  retaining Markdown emphasis.
- Claude's normal signed-out CLI result is recognized even when it exits with status 1.
- Gemini CLI status no longer treats cached credentials as verified access. Unsupported
  consumer-account requests explain the supported authentication alternatives.
- Add provider includes Google AI Studio/Gemini API using Google's documented compatible
  endpoint and an environment-variable credential.
- Provider setup now distinguishes missing CLI installations from sign-in, reports status-refresh
  progress, and keeps model-discovery failures visible in the provider dialog.
- Work and Chat expose supported reasoning choices beside the model picker. Work sessions inherit
  the most recently used model and reasoning setting; stale inherited reasoning capabilities fall
  back to Default instead of blocking a new session.
- Response details show the requested model and destination, plus compatible API model IDs when
  reported. These details describe routing evidence without claiming to prove the loaded weights.
- Known configured API keys are redacted from compatible-provider error paths. Model-discovery
  HTTP failures expose the status without reflecting upstream response bodies.
- Sidebar labels, provider headings, folder selection, and narrow-window layouts have been refined.
- Chat keeps its model, reasoning, and send controls within narrow windows. Resizing an open
  mobile sidebar back to desktop restores conversation interaction in both Work and Chat.
- Canceling a required project-folder prompt keeps that choice required before starting or
  submitting a work session.
- Browser checks can be installed and rerun through `bin/check-browser-ui`.
- Provider CLI update notices run independently of session loading and never install software
  automatically.

- Provider-neutral execution now keeps authentication, execution readiness, per-turn telemetry,
  live allowance reporting, and future organization reporting as separate capabilities.
- Claude authentication remains CLI-owned. PilferedParrot no longer reads or rewrites Claude
  credential files, consumes OAuth tokens from the environment, refreshes them, or calls private
  token and allowance endpoints.
- A provider without a supported live plan-allowance interface reports an explicit unsupported or
  unavailable state without blocking execution or inventing quota windows. Claude per-turn token
  and context telemetry remains available.
- The work and Chat surfaces share one safe Markdown renderer. It escapes HTML, limits links to
  absolute HTTP(S) and mailto destinations, supports a documented bounded subset, and exposes
  terminal actions only for validated top-level single-line shell fences.
- These changes reduce credential exposure and prevent quoted, malformed, multiline, or oversized
  content from being presented as a runnable terminal action; the server independently revalidates
  stored commands.
- Existing configuration, chat history, provider sessions, and historical ledger entries remain
  readable. Older Claude allowance snapshots remain historical records and are never reused as a
  current live quota. No organization analytics integration is included.
