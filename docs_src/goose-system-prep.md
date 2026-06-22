# Preparing your system to run agentteams Goose teams

This guide gets a Goose user's machine ready to **run and test** the Goose teams
agentteams generates or bridges — including the **MCP extensions** and an
**easily-accessible OpenRouter ⇄ local-GPU switch**. It complements two existing
docs: [Goose Cloud Provider Guide](goose-cloud-providers.md) (basic provider
config) and the [Goose Cheat Sheet](goose-cheat-sheet.md) (sessions, recipes).

> **Security first:** never paste an API key into a recipe, `config.yaml`, or any
> committed file. Always reference keys by environment variable. The patterns below
> keep secrets out of files.

---

## 1. Prerequisites

| Need | Check | Get it |
|---|---|---|
| Goose CLI ≥ 1.37 | `goose --version` | https://block.github.io/goose/ |
| **One** LLM provider | see §2 | local GPU (Ollama) **or** OpenRouter |
| (MCP teams only) an MCP runner | `uvx --version` / `npx --version` | `brew install uv` (for `uvx`) or Node (for `npx`) |

A generated team validates with no provider, but **running** it needs a provider.

---

## 2. Choose a provider

Goose reads `GOOSE_PROVIDER` / `GOOSE_MODEL` (and provider keys) from the
**environment first**, then `~/.config/goose/config.yaml`. So your `config.yaml` is
the *default*, and environment variables are the *override* — which is exactly what
makes a switch easy.

### 2a. Local GPU (Ollama) — default, no key
```yaml
# ~/.config/goose/config.yaml
GOOSE_PROVIDER: ollama
GOOSE_MODEL: qwen3.6:35b-a3b      # any tool-calling model you have pulled
OLLAMA_HOST: http://localhost:11434
GOOSE_MODE: auto
```
`ollama pull qwen3.6:35b-a3b` (or your model) and you're ready.

### 2b. OpenRouter (cloud)
OpenRouter is OpenAI-compatible. Goose supports it natively. You need a
**tool-calling-capable** model id (a *vision-only* model will not work — Goose
requires tool calls), and the slug must use **hyphens, not Ollama `:tag` syntax**
(see the troubleshooting note in §6). Validate the model your `config.yaml` will
actually use:
```sh
python scripts/goose-openrouter-preflight.py        # exists? tool-capable? exact fix if not
```
Or check a specific id manually against the public catalog:
```sh
curl -s https://openrouter.ai/api/v1/models \
  | python3 -c "import sys,json;d=json.load(sys.stdin);m={x['id']:x for x in d['data']};\
mid='qwen/qwen3.6-35b-a3b';print(mid, mid in m and 'tools' in (m[mid].get('supported_parameters') or []))"
```
Set the key by env (never in a file): `export OPENROUTER_API_KEY=…`.

---

## 3. The easily-accessible switch (OpenRouter ⇄ local GPU)

Drop this in `~/.config/goose/goose-backend.sh` and `source` it from your shell rc.
It **defines functions only** (reads no secret at shell start), keeps `config.yaml`
as the local-GPU baseline, and reads the OpenRouter key **by reference** from a file
*you* control (e.g. a project `.env`) — never writing it anywhere.

```sh
# ~/.config/goose/goose-backend.sh   — source from ~/.zshrc (or ~/.bashrc)
: "${GOOSE_OPENROUTER_ENV_FILE:=$HOME/path/to/your/.env}"   # file holding OPENROUTER_API_KEY=...
_GOOSE_OR_MODEL_DEFAULT="qwen/qwen3.6-35b-a3b"               # tool-capable; override via GOOSE_OPENROUTER_MODEL

_goose_or_key() {  # extract ONLY OPENROUTER_API_KEY (no whole-file source; robust)
  { set +x; } 2>/dev/null
  local k=""
  [ -f "$GOOSE_OPENROUTER_ENV_FILE" ] && k="$(grep -m1 -E '^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=' \
      "$GOOSE_OPENROUTER_ENV_FILE" | sed -E 's/^[[:space:]]*(export[[:space:]]+)?OPENROUTER_API_KEY=//; s/\r$//; s/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/')"
  [ -n "$k" ] || k="$OPENROUTER_API_KEY"     # fall back to an already-set env var
  printf '%s' "$k"
}

goose-backend() {                # openrouter | local | status
  { set +x; } 2>/dev/null
  case "$1" in
    openrouter) local k; k="$(_goose_or_key)"; [ -z "$k" ] && { echo "no OPENROUTER_API_KEY found" >&2; return 1; }
      export GOOSE_PROVIDER=openrouter GOOSE_MODEL="${GOOSE_OPENROUTER_MODEL:-$_GOOSE_OR_MODEL_DEFAULT}" OPENROUTER_API_KEY="$k"
      echo "Goose → OpenRouter ($GOOSE_MODEL); key set (${#k} chars)";;
    local) unset GOOSE_PROVIDER GOOSE_MODEL OPENROUTER_API_KEY; echo "Goose → local GPU (config.yaml/ollama)";;
    status) echo "provider=${GOOSE_PROVIDER:-<config.yaml>} model=${GOOSE_MODEL:-<config.yaml>} key=$([ -n "$OPENROUTER_API_KEY" ] && echo set || echo unset)";;
    *) echo "usage: goose-backend {openrouter|local|status}  (or goose-or <args>)" >&2; return 2;;
  esac
}

goose-or() {                     # PREFERRED: OpenRouter for ONE run; key stays in goose's process
  { set +x; } 2>/dev/null
  local k; k="$(_goose_or_key)"; [ -z "$k" ] && { echo "no OPENROUTER_API_KEY found" >&2; return 1; }
  GOOSE_PROVIDER=openrouter GOOSE_MODEL="${GOOSE_OPENROUTER_MODEL:-$_GOOSE_OR_MODEL_DEFAULT}" OPENROUTER_API_KEY="$k" goose "$@"
}
```
Then:
```sh
echo 'source "$HOME/.config/goose/goose-backend.sh"' >> ~/.zshrc   # new shells get the commands
goose-or session            # run on OpenRouter (key scoped to this run) — preferred
goose-backend openrouter    # or switch the whole shell to OpenRouter
goose-backend local         # back to local GPU
goose-backend status        # see current
```
**Why `goose-or` is preferred:** it puts the key only in Goose's process, not your
interactive shell (smaller exposure). Use `goose-backend openrouter` when you want a
whole shell session on OpenRouter.

---

## 3b. The same switch, built into agentteams (persistent default)

`agentteams` can switch Goose's **persistent default** source/model by editing
`config.yaml` for you — no shell function required. It finds `config.yaml` the way Goose
does (asks `goose info`, falling back to `$XDG_CONFIG_HOME`/platform default), backs the
file up before writing, and **preserves** your `extensions:` block and comments.

```sh
agentteams --goose-show                       # current provider/model + resolved path + sources
agentteams --goose-source ollama              # switch provider; applies that source's default model
agentteams --goose-source openrouter --goose-model qwen/qwen3-30b-a3b   # provider + explicit model
agentteams --goose-model qwen/qwen3.6-35b-a3b # change only the model (current provider)
agentteams --goose-config PATH --goose-show   # point at a non-default config.yaml
```

Each source carries a **default model**, so changing source without `--goose-model` uses
that source's default (built-ins: `ollama → qwen3.6:35b-a3b`,
`openrouter → qwen/qwen3.6-35b-a3b`). Add your own sources / override defaults in
`~/.config/agentteams/goose-sources.json`:

```json
{ "sources": { "groq": { "default_model": "llama-3.3-70b-versatile", "key_env": "GROQ_API_KEY" } } }
```

**Two complementary layers — know which one wins.** Goose reads provider/model **from the
environment first, then `config.yaml`**. So:

- `agentteams --goose-source …` sets the **persistent default** (`config.yaml`) — what plain
  `goose run` and the VS Code task use.
- `goose-or` / `goose-backend openrouter` set an **ephemeral env override** that **wins over
  `config.yaml`** for that shell.

If an env override is active, `agentteams --goose-source` warns that its `config.yaml` edit is
**masked** in that shell. It also rejects a model that doesn't match the provider's namespace
(an OpenRouter `vendor/slug` under `ollama`, or Ollama `name:tag` syntax under `openrouter`)
and reminds you to export a cloud provider's key. It never reads or writes the key itself.
For full OpenRouter model validation, run `python scripts/goose-openrouter-preflight.py`.

---

## 4. MCP servers (for teams built with MCP)

agentteams wires operator-specified MCP servers into Goose recipes **opt-in**:

- **Direct build:** `agentteams --framework goose --target-host-features goose:mcp …`
- **Bridge:** `agentteams --bridge-from <agents> --framework goose --bridge-merge
  --target-host-features bridge:<source>-to-goose:mcp …` — emits
  `.goose/recipes/bridge-orchestrator.yaml` with the `developer` (CLI) extension
  **by default**, plus any opted-in servers.

What you must prepare on your machine to *run* those servers:

1. **An MCP runner.** A `type: stdio` extension runs `cmd` (e.g. `uvx`, `npx`,
   `python`). Install what your servers use — `brew install uv` for `uvx`, or use
   `npx`-based servers if you have Node. If `uvx` is missing, a `uvx` server will
   fail to launch.
2. **Credentials by reference.** Servers declare `env_keys: [NAME]` (never inline
   secrets). Export those env vars before running, e.g. `export VK_PG_DSN=…`.
3. **Nothing else** — the `developer` (CLI/shell) extension is built in and is
   always present in agentteams-generated/bridged recipes.

Only **first-party, read-only, orchestrator-scoped** servers are auto-wired into the
bridge recipe; others appear as `# agentteams MCP: <id> not wired (<reason>)`
comments (use a direct build for full per-agent MCP). See
[`mcp_emit`](api-reference/mcp-emit.md).

---

## 5. Run a team

```sh
# Generated team (full recipes):
goose run --recipe .goose/recipes/orchestrator.yaml

# Bridged team (CLI + opted-in MCP guaranteed at session start):
goose run --recipe .goose/recipes/bridge-orchestrator.yaml

# Plain session (reads .goosehints → @AGENTS.md): just
goose session
```
On OpenRouter, prefix with `goose-or` (e.g. `goose-or run --recipe …`).

### 5b. Verified delegation (the last Phase-1 sign-off)

A generated **direct-build** orchestrator carries a `sub_recipes:` block (the
bridge entry recipe is a pointer with none), so its recipe is the one that
exercises native delegation. To verify end-to-end that the orchestrator actually
routes/delegates — not just that the recipe validates — run its W6 probe prompt
non-interactively against a real provider:

```sh
# requires a configured OpenRouter key (export OPENROUTER_API_KEY=… or goose-or)
GOOSE_MODE=chat goose-or run \
  --recipe .goose/recipes/orchestrator.yaml --no-session --max-turns 4
```

PASS = the orchestrator states its role and, for "produce a deliverable for this
team", names the correct **workflow** (Workflow 1: Produce a Deliverable) and the
**first agent** it routes to (`@primary-producer`, the `primary_producer`
sub_recipe) — observable delegation to a named child session.

**Judge the OUTPUT, not the exit code, and distinguish the two error classes:**

| Class | goose exit | Meaning |
|---|---|---|
| **Missing key** (no `OPENROUTER_API_KEY`) | **1** | Fails at config-resolution, *before any LLM call* — a setup problem, not a delegation result. |
| **Provider error** past config-resolution (`not a valid model` / 400, `401`/unauthorized, hit `--max-turns`) | **0** | goose exits 0 even on these — classify by output (model / auth / inconclusive), never the exit code. |

The repeatable check is `tests/test_goose_live_delegation.py`. It is
**skip-by-default**: a mandatory `@pytest.mark.skipif` skips it whenever
`OPENROUTER_API_KEY` is not resolvable (env or `GOOSE_OPENROUTER_ENV_FILE`) — and
when `goose` is absent — so CI / a keyless repo stay offline-green. With a key it
runs the probe above and asserts delegation; a model/auth fault or a max-turns
miss is treated as environment/transient (skip), not a wiring regression. The key
is resolved by reference and passed only into the goose subprocess — never logged
or serialized.

> **Verified 2026-06-22** against `openrouter` / `qwen/qwen3.6-35b-a3b`:
> `tests/test_goose_live_delegation.py` PASSED (109s) — the generated orchestrator
> ran on OpenRouter and delegated to the named `primary_producer` sub_recipe. This
> closes the master integration plan's final Phase-1 sign-off (the previously
> "not yet run" live delegation).

---

## 6. Validate & troubleshoot

| Symptom | Fix |
|---|---|
| **Query stops early & quickly on OpenRouter / "not a valid model ID"** | Your `GOOSE_MODEL` uses **Ollama tag syntax** (`model:tag`). On OpenRouter `:` means a *variant* (`:free`), so e.g. `qwen/qwen3.6:35b-a3b` (colon) doesn't exist — use the **hyphen** slug `qwen/qwen3.6-35b-a3b`. Run `python scripts/goose-openrouter-preflight.py` for the exact fix; `--fix` applies it (backup first). |
| `goose recipe validate <f>` fails | check `version: "1.0.0"`, non-empty `instructions:`, no `model:` key |
| "No provider/model configured" | set a provider (§2/§3) — env override or `config.yaml` |
| OpenRouter 401 | `OPENROUTER_API_KEY` unset/invalid; `goose-backend status` to check |
| Model errors / no tool calls | your OpenRouter `GOOSE_MODEL` isn't tool-capable — pick a tool model (§2b) |
| stdio MCP server won't start | install its runner (`uv`/`uvx` or `npx`); export its `env_keys` creds |
| validate a recipe before running | `goose recipe validate .goose/recipes/<slug>.yaml` |

> **Ollama tag vs OpenRouter slug — the #1 early-stop trap.** The same model is
> addressed differently per provider: Ollama uses `name:tag` (e.g. `qwen3.6:35b-a3b`,
> correct in §2a), OpenRouter uses `vendor/model-variant` with **hyphens** (e.g.
> `qwen/qwen3.6-35b-a3b`). Pasting the Ollama tag into the OpenRouter `GOOSE_MODEL`
> makes OpenRouter reject the model and the query stops before doing any work.

Verify your OpenRouter model and reproduce/diagnose the early-stop in one command
(static check needs no key; `--live` runs an end-to-end goose tool probe):
```sh
python scripts/goose-openrouter-preflight.py          # validate config.yaml's GOOSE_MODEL
python scripts/goose-openrouter-preflight.py --live   # also run a real goose tool probe
```
Or the minimal manual probe (no shell exec):
```sh
GOOSE_MODE=chat goose-or run --no-session -t "Reply with exactly: OK"
```
