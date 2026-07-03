# Goose Backend Switcher

Operational reference for `~/.config/goose/goose-backend.sh` — the shell
function library that switches Goose between OpenRouter (cloud) and a local
Ollama GPU without touching any file.

## Files

| File | Role |
|---|---|
| `~/.config/goose/goose-backend.sh` | Function library — source this in `.zshrc`/`.bashrc` |
| `~/.config/goose/config.yaml` | Baseline Goose config (OpenRouter active; Ollama block commented out) |

## Baseline (no env override)

`config.yaml` sets OpenRouter + `qwen/qwen3.6-35b-a3b` as the default. Any
shell that has NOT sourced `goose-backend.sh` (or that has not called
`goose-backend local`) uses OpenRouter automatically. No key is needed in the
shell; Goose reads it from the config or from the environment.

## Usage

```sh
# Source once (e.g. in .zshrc):
source ~/.config/goose/goose-backend.sh

# Switch this shell to local Ollama GPU:
goose-backend local
# → exports GOOSE_PROVIDER=ollama, GOOSE_MODEL=qwen3.6:35b-a3b, OLLAMA_HOST=http://localhost:11434
# → unsets OPENROUTER_API_KEY

# Switch this shell to OpenRouter (also exports the key into the shell):
goose-backend openrouter
# → exports GOOSE_PROVIDER=openrouter, GOOSE_MODEL=qwen/qwen3.6-35b-a3b, OPENROUTER_API_KEY=<key>
# → unsets OLLAMA_HOST

# Show active provider/model (key shown as set/unset only, never printed):
goose-backend status

# Run ONE goose invocation on OpenRouter; key lives only in goose's process,
# never exported into the parent shell — preferred over goose-backend openrouter:
goose-or <goose args…>
```

## Switching config.yaml to Ollama permanently

`config.yaml` contains the Ollama block as comments immediately below the
active OpenRouter lines. To make Ollama the permanent baseline:

```yaml
# Comment out these two:
# GOOSE_PROVIDER: openrouter
# GOOSE_MODEL: qwen/qwen3.6-35b-a3b

# Uncomment these three:
GOOSE_PROVIDER: ollama
GOOSE_MODEL: qwen3.6:35b-a3b
OLLAMA_HOST: http://localhost:11434
```

After editing, `goose-backend local` becomes a no-op (the config already
matches), and `goose-backend openrouter` overrides the local default with
OpenRouter env vars.

## Knobs

All overrides are set in the environment before sourcing or calling the
functions; none require editing the script.

| Env var | Default | Effect |
|---|---|---|
| `GOOSE_OPENROUTER_MODEL` | `qwen/qwen3.6-35b-a3b` | OpenRouter model used by `goose-backend openrouter` and `goose-or` |
| `GOOSE_OPENROUTER_ENV_FILE` | *(a local `.env` file containing `OPENROUTER_API_KEY=<value>`)* | File the key is read from by reference; set per-shell to your own key file |
| `GOOSE_OLLAMA_MODEL` | `qwen3.6:35b-a3b` | Ollama model used by `goose-backend local` |
| `GOOSE_OLLAMA_HOST` | `http://localhost:11434` | Ollama host URL; sets `OLLAMA_HOST` in the subprocess |

## API key security

The OpenRouter key is **never** written to any file and **never** exported into
the parent shell by `goose-or`. It is read by reference from the file named in
`GOOSE_OPENROUTER_ENV_FILE` (only the `OPENROUTER_API_KEY` line, never the whole
file) using `_goose_extract_key`. If that file is absent, the functions fall back
to the inherited `OPENROUTER_API_KEY` env var.

`goose-backend openrouter` does export the key into the current shell (noted
in its output). Prefer `goose-or` for one-off runs to avoid key exposure in
`env` listings.

## Provider/model name formats

OpenRouter and Ollama use different name formats for the same model family:

| Backend | Model name format | Example |
|---|---|---|
| OpenRouter | `provider/model-version` | `qwen/qwen3.6-35b-a3b` |
| Ollama | `name:tag` | `qwen3.6:35b-a3b` |

To add a new model for either backend, set the appropriate knob (`GOOSE_OPENROUTER_MODEL`
or `GOOSE_OLLAMA_MODEL`) and optionally update the `_DEFAULT` variables in the
script for persistence across sessions.

## History

Created 2026-06-23. Prior to this date, `goose-backend local` was broken: it
`unset GOOSE_PROVIDER GOOSE_MODEL` which caused Goose to fall through to
`config.yaml` (openrouter baseline) while also unsetting `OPENROUTER_API_KEY`,
producing an auth failure. Fixed by having `local` affirmatively export the
three Ollama env vars instead of relying on the config.yaml fallback.
