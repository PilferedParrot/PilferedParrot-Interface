# Pinned ACP adapters

This private npm manifest is used only when the operator explicitly starts an
ACP adapter installation. `pilferedparrot.acp_adapters.AdapterManager.install()`
runs `npm ci --ignore-scripts` in a private staging directory beside the PPI
state store. It uses the checked-in lockfile's registry integrity hashes and
does not install npm packages globally. The adapter commands are `node` plus
each package's declared JavaScript `bin` entry. `node --check` verifies the
installed entry files without starting either provider or making a model call.

The pinned packages and their published metadata are:

- [`@agentclientprotocol/codex-acp@1.13.1`](https://registry.npmjs.org/@agentclientprotocol%2Fcodex-acp/1.13.1)
- [`@agentclientprotocol/claude-agent-acp@0.81.1`](https://registry.npmjs.org/@agentclientprotocol%2Fclaude-agent-acp/0.81.1)

To change versions, edit `package.json`, regenerate `package-lock.json` with
`npm install --package-lock-only --ignore-scripts --no-audit --no-fund`, and
update the expected versions and published `dist.integrity` values in
`pilferedparrot/acp_adapters.py`. Existing versioned installs remain in the
PPI state directory for rollback; cleanup is a separate operator action.
