# Provider compatibility

PPI keeps the interface independent from the selected model through its provider adapter
contract. The current implementation has these integration paths:

| Integration | Connection | Conversation continuity | Setup |
| --- | --- | --- | --- |
| OpenAI Codex | Local Codex CLI | CLI session resume | Install and authenticate the CLI |
| Anthropic Claude | Local Claude Code CLI | CLI session resume | Install and authenticate the CLI |
| Google Gemini | Local Gemini CLI | CLI session resume | Install the CLI; Gemini API key or Vertex AI only |
| Local LLM | OpenAI-compatible HTTP endpoint | Locally stored message history | Start a compatible model server |
| Additional providers | OpenAI-compatible HTTP endpoint | Locally stored message history | Add a provider card and configure its credential environment variable |

The Add provider form includes Google AI Studio/Gemini API, OpenRouter, Mistral/Devstral,
LM Studio, Ollama, and a custom connection template.

PPI 0.8.2 and earlier also shipped a Google Antigravity CLI adapter and an xAI/Grok template.
Both were removed, for provider terms and by the owner's choice: Google's Antigravity FAQ says
wrapping `agy` in third-party applications "is not supported under consumer plans". Settings
saved by an earlier release that name either one still load, and PPI falls back to another
provider.

## Platform status

The v0.6.0 Windows 10/11 x64 package is a portable preview. It bundles Python and opens the local
interface with Chrome, Chromium, or Microsoft Edge; providers remain separately installed and
configured. Browser Chat and file tools are supported in the preview. The Bubblewrap shell is
disabled on Windows, and supported npm `.cmd` shims are resolved to their package entry points and
run through Node without invoking `cmd.exe`.

The Windows preview has automated browser, platform, and executable checks. No real Windows
provider account, CLI, or model validation is claimed. The provider rows and live results below
therefore describe integration behavior and Linux or synthetic evidence where stated, not a
Windows certification.
A template supplies connection defaults; it does not certify every model offered by that service.
Use an exact model ID available to your account. If model discovery is unavailable, enter the ID
manually. A successful model-list request does not establish that the model can execute a work turn.

## Account access and subscriptions

An editor subscription or consumer chat login does not supply an API credential for a separate
service. PPI uses native CLI authentication only through its supported CLI integrations; API
cards require the credentials issued for the endpoint you choose. It never extracts editor
tokens or routes requests through undocumented subscription endpoints.

### Gemini CLI: API key or Vertex AI only

Google ended consumer Google sign-in for Gemini CLI on June 18, 2026
([deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)).
Gemini CLI's terms bar third-party software from using its OAuth, and its FAQ names "a Vertex AI
or Google AI Studio API key" as "the supported and secure method" for third-party agents. PPI
therefore runs Gemini CLI only with a key:

- **Gemini API key** (default): set `GEMINI_API_KEY` in the environment that launches PPI, then
  restart PPI. To read the key from another variable, set `gemini.api_key_env` to its name.
- **Vertex AI**: set `gemini.auth_type` to `"vertex-ai"`, and set `GOOGLE_API_KEY`, or
  `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`, in the environment that launches PPI.

In headless mode Gemini CLI uses the auth type saved in its settings before it looks at the
environment, so a key in the environment cannot displace a saved Google sign-in. Before each run
PPI reads every settings file the CLI merges (system defaults, `~/.gemini/settings.json`, the
project's `.gemini/settings.json`, system settings) and refuses to start, with a message, when any
of them saves a different auth type. To switch, run `gemini` in a terminal and choose **Use Gemini
API key** with `/auth`, or edit `security.auth.selectedType` in the named file. PPI also sets the
CLI's auth-selection variables (`GOOGLE_GENAI_USE_GCA`, `GOOGLE_GENAI_USE_VERTEXAI`,
`GEMINI_CLI_USE_COMPUTE_ADC`, `CLOUD_SHELL`, `GOOGLE_GEMINI_BASE_URL`) for each run. Gemini CLI
never lets a `.env` file override a variable that is already set, so a `.env` file cannot switch
the run to another auth type. An installed CLI is reported as unverified until a request runs.

### Gemini API terms

Both Gemini paths, Gemini CLI with a key and the Google AI Studio / Gemini API card, run under the
[Gemini API terms](https://ai.google.dev/gemini-api/terms). Among them:

- You must be 18 or older to use the APIs.
- Google AI Studio and the Gemini API are meant for developers building for professional or
  business purposes, not for consumer use.
- On the free tier, Google uses the content you submit and the responses to provide, improve and
  develop its products, and asks you not to submit sensitive, confidential or personal
  information. A paid key is recommended.
- Users in the European Economic Area, Switzerland or the United Kingdom must use paid services.

### Google AI Studio / Gemini API card

The Google AI Studio/Gemini API template uses
`https://generativelanguage.googleapis.com/v1beta/openai` and `GEMINI_API_KEY` as documented
in [Google's OpenAI compatibility guide](https://ai.google.dev/gemini-api/docs/openai).
Choose an account-accessible model with function calling. This is a separate API connection
with its own access and usage limits. Create a key in
[Google AI Studio](https://aistudio.google.com/apikey), make it available as
`GEMINI_API_KEY` in the environment that launches PPI, and restart PPI. In **Providers →
Add provider**, choose **Google AI Studio / Gemini API**. The form expects the variable name
`GEMINI_API_KEY`, not the secret value. Model discovery lists IDs; it does not verify which
tier a model is billed on. See [Gemini API billing](https://ai.google.dev/gemini-api/docs/billing).

## Coding API and local setup

For **DeepSeek or GLM**, choose **OpenRouter**, supply `OPENROUTER_API_KEY`, and enter an
exact model ID from OpenRouter's [tool-capable catalog](https://openrouter.ai/models?supported_parameters=tools).
PPI preserves the provider's opaque reasoning continuation fields between tool calls and
later user turns, as required by OpenRouter's
[reasoning continuation protocol](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
Free access depends on the exact model variant and account limits; ordinary IDs are not
implicitly free. Check the [free-variant documentation](https://openrouter.ai/docs/guides/routing/model-variants/free).

For **Mistral Devstral**, choose **Mistral / Devstral**, supply `MISTRAL_API_KEY`, and select
an account-accessible Devstral model. The template uses `https://api.mistral.ai/v1` and
Mistral's [Chat Completions API](https://docs.mistral.ai/api). PPI retains tool-call IDs and
structured assistant content for continuation, while displaying text content only.

For **LM Studio**, load a model with native tool support and start its local server from the
Developer tab, or run `lms server start`. Choose **LM Studio** in PPI; its default endpoint is
`http://127.0.0.1:1234/v1` and no API credential is configured by default. If server authentication
is enabled, enter the environment-variable name holding its token. Use the exact model ID
returned by discovery. See [LM Studio tool support](https://lmstudio.ai/docs/developer/openai-compat/tools)
and [headless installation](https://lmstudio.ai/docs/developer/core/headless).
PPI does not start LM Studio automatically; the existing Qwen automatic-start setting remains separate.

These paths share PPI's contained file/tool loop. Protocol regression tests are separate from
live model certification; see [provider expansion validation](provider-expansion-validation.md)
for the tested combinations and remaining account prerequisites.

## Compatible endpoint requirements

The current adapter sends non-streaming JSON requests to `POST /chat/completions` beneath the
configured base URL. It supplies `model`, `messages`, `tools`, `tool_choice`, `max_tokens`, and
`stream`. The endpoint must accept these fields and return a Chat Completions response with
`choices[0].message`. Work models must support function tool calls. Chat uses the same protocol
with only read-only tools. A service that only supports a different native API needs a new adapter;
changing the base URL alone is insufficient.

Optional model discovery uses `GET /models` and reads IDs from `data`. Missing usage telemetry
does not prevent execution; the interface uses estimates where supported. Compatible responses
are displayed after each HTTP request completes, with progress between tool steps.
Cancel returns control to the interface and stops subsequent tool steps. A non-streaming
HTTP request already submitted to a compatible provider may continue there until its
response or timeout; cancellation does not guarantee that remote generation or billing stops.

API keys remain in the server process environment. Enter the environment-variable name in the
provider card, never the key value. Restart PPI after changing its inherited environment. Remote
keyed endpoints require HTTPS, and redirects cannot move credentials to another origin.
Adding a remote provider authorizes sending that session's prompts and tool results to it.

On Linux, shell execution requires Bubblewrap. On Windows, the Bubblewrap shell is disabled.
Read/write tools stay within the approved workspace roots; Chat removes write and shell tools.
Native CLI providers retain their own permission systems. On Windows, only the supported npm
provider packages are translated from npm shims to direct Node entry-point execution; arbitrary
batch files are rejected.
See [Security policy](../SECURITY.md) for the complete local trust boundary.

## Compatibility validation

The automated suite uses synthetic provider responses and isolated application state. It verifies
adapter behavior, session handling, credential redaction, and browser authorization without using
real accounts or spending model tokens. Browser tests use a fake provider and require Chromium
and loopback sockets.

Before claiming a particular provider/model combination works end to end, verify sign-in or API
credentials, model selection, a reply, continuation, cancellation, and a contained tool operation
against that integration. Provider updates can change CLI or API behavior. No release can guarantee
support for every present and future LLM merely from a shared UI or a compatible model-list endpoint.

New protocols should implement the existing adapter contract and capability reporting, keeping
provider-specific request formats and authentication outside the browser interaction code.
