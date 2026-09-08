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
