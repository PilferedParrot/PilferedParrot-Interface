PilferedParrot Interface for Windows
====================================

This is the 0.7.1 unsigned preview for Windows 10/11 x64. It is a portable package:
extract the ZIP and run PilferedParrot.exe. Python and the application are included,
so no Python installation, installer, or administrator rights are required.

Install Chrome, Chromium, or Microsoft Edge before starting the app. Keep the console
window open while PPI runs and keep the extracted directory together. The executable
is unsigned; the release includes SHA-256 checksums.

On first launch PPI creates:

  %LOCALAPPDATA%\PilferedParrot\config.json

Edit this generated file when configuring providers. Preserve its web.chat_store and
ledger paths. The bundled config.example.json is a reference for provider options and
is not a replacement for the generated configuration. New projects default to:

  %USERPROFILE%\PilferedParrot Projects

Provider CLIs (Codex, Claude, Gemini, and Antigravity), Node.js, and local model
servers are separate software. The Windows preview supports the browser interface,
Chat, and file tools. Bubblewrap is a Unix-only shell and is disabled on Windows;
Windows provider accounts and CLIs are not live-certified by this package.

To update, close PPI, extract the new ZIP into a new or clean directory, and run the
new PilferedParrot.exe. Update shortcuts that point to the old extracted directory.
Use PilferedParrot.exe --help for command-line options.

New installations use Darker / Minimal / Standard appearance in Work and Chat.
Saved choices survive upgrades. Copy buttons preserve code/text whitespace, and
Run with AI sends a reviewed shell block to the current Work session provider.

The unreleased source branch adds optional product feedback, off by default. Users
can enable individual categories, inspect a local report, then download it for
voluntary sharing. Nothing is uploaded. This feature is not included in the
published 0.7.1 ZIP. See docs/feedback.md for policy 1, local-change detection,
retention, and turning all feedback off.

The same unreleased source branch adds Project workrooms. The sidebar can pin and
switch folders while keeping each session's draft and original workspace. Reopening
the app restores the selected project; the published 0.7.1 ZIP does not include it.
