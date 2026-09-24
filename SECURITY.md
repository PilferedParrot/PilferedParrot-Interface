# Security Policy

## Supported versions

Security fixes are applied to the latest release and the current default branch. Older minor
release lines are not supported.

| Version | Supported |
| --- | --- |
| 0.8.1 (Linux/source and Windows preview) and the current default branch | Yes |
| 0.8.0 and older releases | No; upgrade to the latest release |

### Internal private preparation fixes in 0.8.1

The 0.8.0 internal private workspace preparation worker could reach writable
host paths through inherited descriptors and caller stdin, and its read-only
root view exposed host IPC. Version 0.8.1 narrows that worker's mount and IPC
view, restricts writes, and validates its returned stage path. There is no
provider or UI path to this primitive; isolated provider sessions and rewind
are still unavailable. Historical tags and archives remain unchanged.

### Execution fixes in 0.7.1

All earlier published versions through 0.7.0, including the 0.7.0 release candidates,
contain the execution-boundary defects corrected in 0.7.1. Upgrade source installations
and Windows packages to 0.7.1 or newer. Historical tags and archives remain unchanged.

The fixes limit compatible-provider shell access to explicit workspace/runtime mounts,
contain Git inspection in a read-only sandbox, and reject escaping symlinks in fallback
diff reads. They also remove prompt-text admission checks and stop validating unused
Codex extra write roots in full-access or read-only mode. Existing provider permission
choices remain authoritative. See [release notes](RELEASE_NOTES.md) for compatibility details.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's private
**Security advisories → Report a vulnerability** flow. If private reporting is unavailable, contact
a repository owner through GitHub and request a private channel before sharing exploit details,
credentials, local paths, or provider output.

Include the affected commit, prerequisites, impact, and a minimal reproduction. You should receive
an acknowledgement within seven days. Please allow time for a fix before public disclosure.

## Security model

PilferedParrot is a local, single-operator application. Its web server is intended to bind only to a
loopback address. It does not provide accounts, multi-user authorization, TLS, or safe exposure
through a LAN listener or reverse proxy. Processes running as the same OS user are inside the trust
boundary and can access PilferedParrot's state and local HTTP service.

The Windows executable is a portable console application. It does not request administrator rights;
the user must keep its console open while it runs. Windows state is kept under
`%LOCALAPPDATA%\PilferedParrot`, and the default project directory is `%USERPROFILE%\PilferedParrot Projects`.
Windows uses the account's normal profile ACLs for these files; the owner-only mode wording elsewhere
in this policy applies to POSIX systems. The preview package is currently unsigned; release downloads
include SHA-256 checksums.
Windows disables the Bubblewrap shell. Supported npm provider shims are resolved to their package
entry point and run through Node without passing arbitrary batch files to `cmd.exe`; provider CLIs
retain their own authentication, permissions, and network behavior.

Provider prompts and outputs may contain sensitive source code. Chats and run
metadata are stored locally with owner-only file permissions, but the selected provider CLI or
Qwen endpoint receives the prompt and any tool output needed for the task. Users are responsible
for the data-handling terms and configuration of those providers.

Local and OpenAI-compatible provider file tools resolve paths inside the selected workspace. Their shell tools require Bubblewrap;
the workspace, configured additional roots and ephemeral `/tmp` are writable. System binaries and
selected runtime configuration are mounted read-only; other host data is absent. Inherited
environment variables are reduced to a small allowlist. Network access is disabled unless the
provider's `shell_network` is explicitly enabled. Custom toolchains need an explicitly selected root.
Git inspection always uses a separate read-only, network-disabled Bubblewrap sandbox: repository
filters may run there but cannot modify persistent host files or read other host data. Fsmonitor,
hooks, external diff and textconv are disabled. Without that sandbox or accessible Git metadata,
only file-tool baseline diffs are available; there is no unsandboxed Git fallback.
Selecting the operator's entire home directory for one of these providers is rejected unless its
`allow_home_workspace` setting is explicitly enabled, because that selection exposes credentials
and documents that the normal home mask protects. A parent of home is always rejected because it
would grant still broader access. Additional workspace roots may be listed in the
provider's `additional_dirs`; they are mounted alongside the workspace, are configuration-only so a
prompt cannot grant itself new roots, and may not name the home directory or one of its parents even
when `allow_home_workspace` is set, so an extra root cannot become an indirect home mask bypass. Custom provider cards store only an API-key
environment-variable name; the key itself remains in the process environment and is never returned
to browser state or written to the provider-card store. A keyed non-loopback endpoint must use HTTPS,
and provider redirects cannot leave its configured origin or downgrade the connection. Remote endpoints receive the prompt and
tool results, so adding a card is also an explicit data-egress decision.
This limits accidents but is not a hardened boundary against hostile kernel,
Bubblewrap, compiler, or project inputs. Use a disposable VM or container for untrusted code.

Codex executes through its own CLI. Its sandbox, permissions, authentication, and network behavior
remain an independent security boundary. The selected project is writable in `workspace-write`
mode. Operators may configure narrowly scoped `codex.additional_write_dirs`; PilferedParrot validates
them and passes them as Codex `--add-dir` roots on new and resumed `workspace-write` turns.
These extra roots are ignored in `read-only` and `danger-full-access` modes. A natural-language
path mention does not change the selected workspace or permission configuration; actual operations
are governed by the provider's sandbox and approval policy. Every configured writable root expands
the model's write authority, so prefer project directories and do not grant the whole home folder
without deliberately accepting that scope.

Claude Code executes through its own CLI and retains its authentication, permissions, settings,
and network behavior. PilferedParrot does not copy credentials into browser state. Provider sign-in
runs the official CLI flow in the background, automatically confirms its browser handoff when
needed, and lets the CLI open the system's default browser. Provider sign-out clears that CLI's
stored credentials and is therefore confirmed in the browser first. CLI credentials are shared by all PilferedParrot windows
for the same OS user. Harness is an explicit Work action using the same dashboard capability,
window ownership checks and provider dispatch. Planning does not execute models. Its file references
are validated against the project, including symlink resolution, but allowed write scope is an
assignment contract rather than an OS file-level sandbox. Provider permissions remain authoritative.
There is no automatic provider router or second-model review loop. Package records stay in the
owner-only chat store; portable presets and examples contain no private state.

Gemini likewise executes through its local CLI in headless mode and keeps Google's authentication,
tool policy, and project-scoped session files under Gemini's control. PilferedParrot does not copy
Gemini credentials into its browser state or chat store.

The optional Chat window is a separate, read-only session with the selected provider. User messages go to that session
directly; PilferedParrot does not inject technical messages or conversation metadata. Chat cannot
switch, interrupt, rewrite, or relay technical requests, widen
filesystem permissions, or bypass the technical provider's normal sandbox.

All mutating browser requests require a scoped per-window capability plus loopback peer, exact Host,
and Origin checks. Capabilities travel in URL fragments, are omitted from server state, and are
revoked when isolated windows exit. Keep `web.host` on a loopback address; remote exposure is not
supported.

Completed assistant responses may offer a terminal button for single-line fenced commands. A click
first shows the exact command and project folder for confirmation, then launches the stored command
in a graphical terminal rooted at the conversation's project folder; the browser cannot supply a
replacement command or working directory. The command runs as
the operator and may request elevation through `sudo`, so inspect it before clicking. This action is
not covered by the Qwen Bubblewrap sandbox.

## Optional product feedback

[Feedback policy 1](docs/feedback.md) keeps four categories off by default. It
stores bounded aggregate counters separately from chats and never uploads them.
Work/Chat controls use the existing capability and same-origin protection;
terminal commands require explicit opt-in. Local-change inspection reads only
bounded listed shipped sources after separate consent and an explicit preview.
Written notes are voluntary, included verbatim, and require review before export.
Turning categories off purges their counts; consent revisions fence stale writes
and previews. Downloaded or shared copies are controlled by their holders.
