# `goose-config` — AgentTeamsModule

Locate and safely mutate Goose's `config.yaml` for source/model switching.

> Source: `agentteams/goose_config.py`

---

## Purpose

Powers the `agentteams --goose-source` / `--goose-model` / `--goose-show` CLI action. The module
has three concerns:

1. **Location protocol** — resolve which `config.yaml` Goose actually reads, and report *how* it was
   found so the choice is never silent.
2. **Source registry** — a per-source default model + provider-key env-var **name** (never a value),
   seeded for `ollama` + `openrouter` and extensible via `~/.config/agentteams/goose-sources.json`.
3. **Config mutation** — set only the top-level `GOOSE_PROVIDER` / `GOOSE_MODEL` scalars, with a
   timestamped backup written before the rewrite, and never touch the nested `extensions:` block.

`config.yaml` is the **persistent default** Goose reads when no env override is set. An active
`GOOSE_PROVIDER` / `GOOSE_MODEL` env (e.g. a `goose-or` shell) wins over it — callers must surface
that via [`current_status`](#current_statuspath-envnone) / [`env_override`](#env_overrideenvnone).

> **No secrets.** This module never reads or writes provider API keys. The source registry stores
> only the **name** of the env var that holds a key (`key_env`), never the key itself.

---

## Public Types

### `SourceSpec`

A Goose source (provider) with its default model and provider-key env-var name.

Frozen dataclass fields:

1. `default_model` (`str`): the model slug applied when switching to this source.
2. `key_env` (`str | None`): the **name** of the env var that holds the provider key — never a value (default `None`).
3. `host_env` (`str | None`): the **name** of the env var for the provider host/endpoint (default `None`).

### `BUILTIN_SOURCES`

`dict[str, SourceSpec]` — the seeded source registry. Ships with `ollama` and `openrouter`; merged
with (and overridable by) a user file in [`load_sources`](#load_sourcesuser_filenone).

### `NewSchemaTargetError`

`ValueError` subclass raised by
[`set_provider_model`](#set_provider_modelpath-providernone-modelnone) when the target
`config.yaml` already uses the newer `providers:`/`active_provider:` schema. See that
function's Raises section.

---

## Public Functions

### `load_sources(user_file=None)`

Built-in sources merged with an optional user JSON file (user wins per key).

Args:

1. `user_file` (`Path | None`): override path; defaults to `~/.config/agentteams/goose-sources.json`.

File shape: `{"sources": {"<name>": {"default_model": "...", "key_env": "...", "host_env": "..."}}}`.
Unreadable or invalid files are ignored and the built-ins are kept.

Returns:

- `dict[str, SourceSpec]`

---

### `resolve_goose_config_path(explicit=None, env=None, platform=None, runner=subprocess.run)`

Resolve Goose's `config.yaml` path and report which method found it.

Resolution order:

1. explicit flag / `AGENTTEAMS_GOOSE_CONFIG` env → method `"explicit"`.
2. `goose info` (authoritative — Goose's own resolver) → method `"goose-info"`.
3. `$XDG_CONFIG_HOME` / platform default → method `"xdg"` or `"platform-default"`.

Args:

1. `explicit` (`str | None`): explicit path override.
2. `env` (`dict[str, str] | None`): environment mapping (defaults to `os.environ`).
3. `platform` (`str | None`): platform string (defaults to `sys.platform`).
4. `runner`: `subprocess.run`-compatible callable, injectable for tests.

Returns:

- `tuple[Path, str]` — the resolved path and the resolution method.

---

### `parse_goose_info_config_path(text)`

Extract the `config.yaml` path from `goose info` stdout.

Tolerant of fixed-column trailing padding and a trailing `... missing (can create)` status token;
does **not** split on internal whitespace (a Windows path may contain spaces).

Args:

1. `text` (`str`): captured `goose info` stdout.

Returns:

- `str | None` — the parsed path, or `None` when no `config yaml:` line is present.

---

### `read_config(path)`

Parse top-level `GOOSE_*: value` scalars; ignore the nested `extensions:` block. Also parses
the newer `providers:`/`active_provider:` schema (2026-07-24) that recent `goose configure`
(1.37+) writes instead of flat keys.

Args:

1. `path` (`Path`): path to the `config.yaml` file.

Returns:

- `tuple[dict[str, str], dict[str, object] | None]` — the top-level `GOOSE_*` scalars (empty
  when the file is absent, unreadable, or uses only the newer schema), and the parsed
  `providers:` block (`{"active_provider", "model", "models_by_provider"}`) or `None` when the
  file uses the older flat-key schema.

---

### `set_provider_model(path, provider=None, model=None)`

Set top-level `GOOSE_PROVIDER` / `GOOSE_MODEL`, preserving everything else.

Writes a timestamped backup **before** the rewrite (no partial-write window). Creates a minimal
config if the file is absent. Anchors on column 0, so the nested `extensions:` keys are never
touched. Never reads or writes provider keys.

**Write support is flat-schema only.** Against a target already using the newer
`providers:`/`active_provider:` schema, this refuses instead of writing a flat
`GOOSE_PROVIDER:`/`GOOSE_MODEL:` pair goose's new-schema reader would never consult — no write,
no backup, file untouched. The exception message names the exact indented `model:` line to
edit by hand.

Args:

1. `path` (`Path`): path to the `config.yaml` file.
2. `provider` (`str | None`): new `GOOSE_PROVIDER` value (optional).
3. `model` (`str | None`): new `GOOSE_MODEL` value (optional).

Returns:

- `str | None` — the backup path, or `None` when the file was newly created.

Raises:

1. `ValueError` when neither `provider` nor `model` is supplied.
2. `NewSchemaTargetError` (a `ValueError` subclass) when `path` already uses the newer
   `providers:`/`active_provider:` schema — callers that need to distinguish this from the
   missing-args case (the CLI prints a different message for each) should catch it first.

---

### `model_provider_mismatch(provider, model)`

Return a human-readable reason when a model slug is namespace-incompatible with the provider.

`ollama` uses `name:tag` (no `/`); OpenRouter uses `vendor/slug` (hyphens, `:` only for real
variants). Catches the common trap of pasting an Ollama `:tag` into an OpenRouter slug.

Args:

1. `provider` (`str`): the source/provider name.
2. `model` (`str`): the candidate model slug.

Returns:

- `str | None` — a reason string, or `None` when the slug is compatible.

---

### `env_override(env=None)`

Return any active `GOOSE_PROVIDER` / `GOOSE_MODEL` env override (masks `config.yaml`).

Args:

1. `env` (`dict[str, str] | None`): environment mapping (defaults to `os.environ`).

Returns:

- `dict[str, str]` — the subset of `GOOSE_PROVIDER` / `GOOSE_MODEL` currently set in the environment.

---

### `current_status(path, env=None)`

Snapshot the `config.yaml` provider/model and any masking env override.

Prefers the newer `providers:`/`active_provider:` schema when `read_config` finds one; falls
back to the flat `GOOSE_PROVIDER`/`GOOSE_MODEL` keys otherwise.

Args:

1. `path` (`Path`): path to the `config.yaml` file.
2. `env` (`dict[str, str] | None`): environment mapping (defaults to `os.environ`).

Returns:

- `dict[str, object]` with keys `config_provider`, `config_model`, `config_mode`, `env_override`,
  and `schema_source` (`"v2"` | `"v1"` | `"none"` — which shape `config_provider`/`config_model`
  were resolved from).

---

## Notes

1. **Location** — prefers `goose info` over a guessed platform path; see `resolve_goose_config_path`.
2. **Mutation** — backup-before-write, column-0 anchored (the `extensions:` block is preserved); see `set_provider_model`.
3. **Env override** — always wins over `config.yaml`; see the Purpose note above and `current_status`.
4. **Schemas** — reads both flat-key and `providers:`/`active_provider:`; writes only flat-key; see `read_config` / `set_provider_model`.

## See also

- [`mcp_emit`](mcp-emit.md) — wires `mcp_servers[]` into the Goose recipe `extensions:` block this module deliberately leaves untouched.
- [`bridge_subagents_goose`](bridge-subagents-goose.md) — emits Goose subagent-stub recipes for a bridged source team.
