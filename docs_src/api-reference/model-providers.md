# `model_providers`

One stdlib-only client for the model APIs the [WASM agent host](wasm-agent.md) calls (WASM sandbox plan P06). The
host owns the conversation and calls `complete` once per model turn. Guests never call a provider and never see a key.

> Source: `agentteams/model_providers/` — `__init__.py` (public API and `complete`), `_config.py` (presets, keys, base
> URLs, tiers), `_common.py` (shared types and limits), `_validate.py`, `_transport.py` (HTTPS, retries, caps), one
> module per wire family (`_anthropic.py`, `_openai.py`, `_gemini.py`) and `_stub.py`. Operator guide:
> [WASM agent runtime](../wasm-agent-runtime.md).

## Presets

| Preset | Wire family | Endpoint | Key variable |
|---|---|---|---|
| `anthropic` | `anthropic` | `{base}/messages` (Messages API, `anthropic-version: 2023-06-01`) | `ANTHROPIC_API_KEY` |
| `openai`, `openrouter`, `xai` | `openai` | `{base}/chat/completions` | `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `XAI_API_KEY` |
| `ollama`, `lmstudio` | `openai` | `{base}/chat/completions` on loopback | none |
| `openai-compatible` | `openai` | `{base}/chat/completions`; the operator supplies the base URL | `AGENTTEAMS_MODEL_API_KEY` |
| `gemini` | `gemini` | `{base}/models/{model}:generateContent` | `GOOGLE_API_KEY`, else `GEMINI_API_KEY` |
| `stub` | `stub` | none: offline and deterministic | none |

## Behaviour worth knowing

- **Neutral shape.** Messages use the Anthropic Messages block shape (`text`, `tool_use`, `tool_result`), strictly
  alternating `user` / `assistant` and starting with `user`. Reply blocks may carry two reserved keys,
  `_provider_id` and `_provider_sig` (continuation data a provider needs back verbatim, such as Gemini's
  `thoughtSignature` or Anthropic thinking blocks). They are model output (C-4 data); the host strips them before a
  guest sees a block and restores them only from its own transcript.
- **No default model ids.** `complete` requires `model`. `model_for_tier` resolves `primary` / `cheap` from
  `AGENTTEAMS_WASM_MODEL_<TIER>` or the brief's configuration, and raises `config` when neither names one.
- **Key custody.** A key lives only in a `Provider` (redacted `repr`, not picklable). `get_provider` reads it from
  the environment; `Provider.from_key` is for the host stage that receives it over a pipe. Error text is scrubbed of
  the key and capped. The package writes no files.
- **Egress.** HTTPS only, except plain HTTP to a loopback host. A base URL may not carry user info, a query or a
  fragment; redirects are refused (one would carry the key header to another host); responses are capped at
  `MAX_RESPONSE_BYTES`; request bodies are built from an allowlist (no streaming, no provider-side tools).
- **Retries.** Up to `MAX_RETRIES`, with backoff capped at `BACKOFF_CAP_S` (or the provider's `Retry-After`), on a
  retryable HTTP status and on a network error or timeout before a response arrives; never once a response body has
  started (that call may already be billed).
- **CI guard.** A live (non-stub) call with `CI` set is refused unless `AGENTTEAMS_ALLOW_LIVE_MODEL_IN_CI=1`.
- Provider facts not settled by the official docs are marked `UNVERIFIED:` in the code (for example xAI tool-call
  parity) and covered by synthetic fixtures; the opt-in live tests are the check.

## Public Surface

### `complete(provider, model, messages, *, system, tools, max_tokens, timeout, temperature)`

Run one model turn and return a `ModelReply`. Stateless: everything the provider needs is derived from `messages`.
`max_tokens` is 1..1,000,000; `timeout` is the per-attempt socket timeout; `temperature`, when given, is in [0, 2].
A `refusal` stop returns normally. Raises `ModelProviderError`.

### `get_provider(name, *, environ, base_url, script_path)`

Build a `Provider` from a preset, loading its key from `environ` (default `os.environ`). `base_url` overrides the
preset's and is vetted the same way. `script_path` (stub only) names a JSON list of replies for smoke tests. Raises
`ModelProviderError` (`config`) for an unknown preset, a bad base URL or a missing key.

### `model_for_tier(tier, configured, *, environ)`

The model id for `tier` (`primary` or `cheap`): `AGENTTEAMS_WASM_MODEL_<TIER>` in `environ` wins over `configured`.
Raises `ModelProviderError` (`config`) when neither names one.

### `check_base_url(url)`

Return the vetted base URL (scheme, host, no user info, query or fragment, no `%` in the path). Raises
`ModelProviderError` (`config`).

### `is_loopback_url(url)`

Whether `url` points at a loopback host (`127.0.0.1`, `::1`, `localhost`).

### `load_key(preset, environ)`

The preset's key from the first present variable in `Preset.key_env`. Raises `ModelProviderError` (`config`) when a
keyed preset has none.

### `preset_needs_key(name)`

Whether the preset sends a key (false for `ollama`, `lmstudio` and `stub`).

### Types and constants

- `Provider` (`Provider.from_key(name, key, *, base_url=None)`), `Preset` (`name`, `family`, `base_url`, `key_env`,
  …), `PROVIDERS` (preset name to `Preset`), `StubProvider`.
- `ModelReply` (`content`, `stop`, `raw_stop`, `usage`, `provider`, `model`, `request_id`, `dropped_blocks`),
  `Usage`, `ToolSpec` (`name`, `description`, `input_schema`).
- `ModelProviderError` (`kind` in `KINDS`; `rescrub(key)` re-scrubs its text).
- `STOPS` (`end_turn`, `tool_use`, `max_tokens`, `refusal`, `other`), `TIERS`, `TIER_ENV_PREFIX`, `CI_ALLOW_ENV`,
  `ANTHROPIC_VERSION`, `MAX_RETRIES`, `BACKOFF_CAP_S`, `MAX_RESPONSE_BYTES`, `MAX_PROVIDER_ID_CHARS`,
  `MAX_PROVIDER_SIG_CHARS`.

## Related pages

- [`wasm_agent`](wasm-agent.md) — the host that calls `complete` and holds the transcript.
- [WASM agent runtime](../wasm-agent-runtime.md) — choosing a provider, keys and base URLs for a run.
