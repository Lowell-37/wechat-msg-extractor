# OpenAI-Compatible Model Settings Design

## Goal

Add a persistent model-settings page so users can configure and verify any
OpenAI-compatible chat-completions service before enabling AI analysis during
export. Missing credentials must never result in a silent no-op.

## User Experience

The persistent wizard header gains a **模型设置** link. The settings page
contains:

- a user-facing provider name;
- an API base URL;
- a model name;
- an enabled switch;
- an API Key replacement field that is always blank on render;
- current credential and connection status;
- a **保存并测试** action.

Saving validates the non-secret fields and tests a minimal request against the
configured service before atomically replacing the active settings and Key.
When no new Key is entered, the existing encrypted Key is reused. A failed
test preserves the last working configuration. Disabling AI is the exception:
it persists immediately without an external request and clears readiness. The
response reports success or a sanitized, actionable error without echoing
credentials or upstream response bodies.

The preview/export page shows the active provider and model. AI analysis is
disabled when the settings are disabled, incomplete, or have not passed a
connection test. It links directly to model settings. Export requests also
validate readiness server-side so crafted requests cannot silently bypass the
UI guard.

## Architecture and Storage

Create a focused model-settings service that owns validation, persistence,
readiness, and connection-test state. Non-sensitive settings are stored in a
Git-ignored local YAML or JSON file under `local/settings/`. The API Key is
stored separately as a Windows machine-scope DPAPI blob under
`local/secrets/`, using the existing credential-store abstraction. The Key is
never returned to templates, logs, exceptions, URLs, or Git.

Generalize the analyzer configuration boundary to an OpenAI-compatible model
record. The existing DeepSeek defaults remain compatible: provider name
`DeepSeek`, base URL `https://api.deepseek.com`, and model `deepseek-chat`.
The analyzer posts to `/v1/chat/completions`, using an explicit immutable
settings snapshot captured when an export job is created.

Add a settings router and template rather than adding more responsibilities to
the wizard action router. Application startup composes the settings service,
analyzer factory, and existing export service.

## Data Flow

1. GET model settings loads only non-sensitive fields plus boolean status.
2. POST save-and-test validates provider, loop-safe HTTPS/HTTP URL rules,
   model, enabled state, and optional replacement Key.
3. A minimal chat-completions request tests the proposed settings and either a
   newly submitted Key or the decrypted existing Key.
4. Successful validation atomically persists non-sensitive settings, the new
   DPAPI ciphertext when supplied, and a successful readiness state. Failure
   preserves the last working configuration, keeps the editable form, and
   reports a sanitized error. Disabling persists without testing and clears
   readiness.
5. Preview reads readiness and renders the AI option enabled or unavailable.
6. Export rejects requested AI analysis when readiness is false. A ready
   export snapshots the settings and invokes the analyzer for each selected
   task date.

## Error Handling

- Missing Key, invalid URL, missing model, disabled settings, and failed test
  are distinct user-facing states.
- API timeouts, authentication failures, rate limits, and malformed responses
  are converted into concise safe messages.
- Runtime AI failures emit a warning progress event and write an explicit
  failure note instead of silently copying context as AI output.
- Local-only export remains available when AI settings are unavailable.

## Security

- Never prefill or reveal the API Key.
- Store only DPAPI ciphertext, using machine scope as previously selected for
  WechatExplorer credentials.
- Exclude settings and secret paths from Git.
- Do not log request authorization headers, upstream response bodies, task
  content, or decrypted Keys.
- Accept arbitrary OpenAI-compatible endpoints only after strict URL parsing;
  permit HTTPS by default and loopback HTTP for local model servers.

## Testing and Acceptance

Tests cover settings validation and atomic persistence, DPAPI Key replacement
and reuse, secret-free rendering/errors, connection-test success and failure,
DeepSeek backward-compatible defaults, preview readiness, server-side export
rejection, immutable job snapshots, actual analyzer invocation, and runtime
failure warnings. Full pytest, Ruff, Jinja compilation, browser checks, and a
live configured-model smoke test are required before completion.

Acceptance criteria:

- The header exposes a usable model-settings page.
- A user can save and test an OpenAI-compatible provider without editing YAML.
- Secrets never appear in HTML, logs, configuration files, or Git.
- AI cannot appear enabled while the backend will skip it.
- A successful export visibly runs AI analysis and writes model output to the
  analysis column.
