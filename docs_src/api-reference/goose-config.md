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

Parse top-level `GOOSE_*: value` scalars; ignore the nested `extensions:` block.

Args:

1. `path` (`Path`): path to the `config.yaml` file.

Returns:

- `dict[str, str]` — the top-level `GOOSE_*` scalars (empty when the file is absent or unreadable).

---

### `set_provider_model(path, provider=None, model=None)`

Set top-level `GOOSE_PROVIDER` / `GOOSE_MODEL`, preserving everything else.

Writes a timestamped backup **before** the rewrite (no partial-write window). Creates a minimal
config if the file is absent. Anchors on column 0, so the nested `extensions:` keys are never
touched. Never reads or writes provider keys.

Args:

1. `path` (`Path`): path to the `config.yaml` file.
2. `provider` (`str | None`): new `GOOSE_PROVIDER` value (optional).
3. `model` (`str | None`): new `GOOSE_MODEL` value (optional).

Returns:

- `str | None` — the backup path, or `None` when the file was newly created.

Raises:

1. `ValueError` when neither `provider` nor `model` is supplied.

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

Args:

1. `path` (`Path`): path to the `config.yaml` file.
2. `env` (`dict[str, str] | None`): environment mapping (defaults to `os.environ`).

Returns:

- `dict[str, object]` with keys `config_provider`, `config_model`, `config_mode`, and `env_override`.

---

## Notes

1. The location protocol prefers `goose info` (Goose's own resolver) over a guessed platform path,
   so the resolved file matches what Goose itself would use.
2. Mutation is backup-before-write and column-0 anchored, so the nested `extensions:` block and any
   surrounding content are preserved verbatim.
3. An env override always wins over `config.yaml`; surface it (via `current_status`) so a switch is
   never silently masked by an active `goose-or` shell.
