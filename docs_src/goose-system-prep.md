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

**Newer Goose versions write a different `config.yaml` schema.** `goose configure` on
recent Goose CLIs (1.37+) writes a nested `providers:` block with `active_provider:` at
the top level, instead of the older flat `GOOSE_PROVIDER:`/`GOOSE_MODEL:` keys.
`--goose-show` reads both schemas transparently. `--goose-source`/`--goose-model` **read**
both too, but only **write** the older flat schema — against a `providers:` config they
refuse (exit 2, no write, no backup) rather than add dead top-level keys Goose would never
read, and print the exact `model:` line to edit by hand under `providers:\n  <provider>:`.

---

## 3c. Bridge a Goose team to/from other frameworks

Goose is a first-class bridge **source and target** — a `.goose/recipes/` team can be
bridged to claude/copilot, and any source can be bridged to Goose.

```sh
# Bridge a Goose-native team OUT to Claude (auto-detected from the .goose/recipes path):
agentteams --bridge-from <project>/.goose/recipes --framework claude --output <target> --bridge-merge

# Per-agent stub recipes (opt-in): one thin .goose/recipes/<slug>.yaml per source agent,
# each a pointer to the canonical source. Default off; reserved/owned slugs are skipped and
# existing recipes are never overwritten. (For full per-agent recipes, use --convert-from.)
agentteams --bridge-from <project>/.github/agents --framework goose --output <target> \
  --bridge-merge --target-host-features bridge:copilot-vscode-to-goose:subagents
```

The Goose-source inventory is read from each recipe's `title:`/`description:`; only `.yaml`
recipes are hashed for `--bridge-check` (build artifacts and junk are excluded, same as the
markdown-source path).

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

### 5a. Running through the resilient wrapper (optional)

Every team agentteams generates or bridges ships with `scripts/goose-run-resilient.py` (repo
root, alongside your other project scripts) — a thin wrapper around `goose run` that detects the
dead-turn symptom in §6 below (a turn that ends with no error and no output) and automatically
resubmits `"Continue"` in the same session, up to a retry cap:

```sh
python3 scripts/goose-run-resilient.py --recipe .goose/recipes/orchestrator.yaml
python3 scripts/goose-run-resilient.py -t "your prompt" --provider openrouter --model <model>
```

It forwards `--provider`/`--model`/any other `goose run` args unchanged and has no hardcoded
provider or model default — when you don't pass one, goose's own env/`config.yaml` resolution
decides, same as calling `goose run` directly. **This is an addition, not a replacement** — the
bare `goose run` commands above keep working exactly as documented, and the wrapper only helps
if a session dies silently in the specific way §6 describes; it does not cover `goose session` or
the VS Code extension's interactive path. Its dead-turn detection was confirmed against
OpenRouter + a reasoning-capable model; on other providers (including local Ollama) it fails
closed — if it can't confidently classify a turn as dead, it takes no action, so at worst it's a
no-op passthrough, never a false "Continue."

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
| **Query stops early & quickly on OpenRouter / "not a valid model ID"** | Your `GOOSE_MODEL` uses **Ollama tag syntax** (`model:tag`). On OpenRouter `:` means a *variant* (`:free`), so e.g. `qwen/qwen3.6:35b-a3b` (colon) doesn't exist — use the **hyphen** slug `qwen/qwen3.6-35b-a3b`. Run `python scripts/goose-openrouter-preflight.py` for the exact fix; `--fix` applies it (backup first). On a newer `providers:`/`active_provider:` `config.yaml` (§3b), `--fix` refuses instead of guessing — it prints the exact `model:` line to edit by hand. |
| **A turn silently ends with no error and no output** — not the above (your model id is valid), `goose doctor` is clean, and nothing appears in `~/.local/state/goose/logs/cli/<date>/*.log` | Different, deeper failure: the model traps its own tool call inside `<tool_call>` text in the reasoning stream instead of the structured tool-call field, so nothing actionable reaches goose (`finish_reason: stop`, no error anywhere). **This is largely a matter of which OpenRouter backend served you** — on one real payload, measured leak rates ranged from 0/12 to 3/12 across backends serving the *same* model. **Best available mitigation (not a fix):** select the backend via `scripts/goose-openrouter-route-proxy.py` + `OPENROUTER_HOST` — see [goose-cloud-providers.md](goose-cloud-providers.md#reliability-choosing-which-upstream-backend-serves-you). This covers **all** goose surfaces including VS Code. **It does not eliminate the failure** — on 2026-07-24 the leak recurred on Morph, one of the backends measured 12/12 clean, with routing verifiably active; an 8-run end-to-end test still saw 2 silent dead turns. **CLI-only secondary:** `scripts/goose-run-resilient.py` (§5a) detects the dead turn and auto-continues — but it wraps `goose run`, so it does **not** protect the VS Code extension (which runs `goose acp`). Note `OPENROUTER_PARAMETERS` is **inert in Goose 1.37.0** and cannot be used for this. |
| **The agent scrapes a homepage, floods its own context with navigation HTML, and never finds the answer** | It has no *search* tool in its tool list — `web_scrape` needs a URL it already knows, so it guesses one. Measured 2026-07-24: a single scraped homepage was 29,654 chars, **54% of the whole conversation**, and contained none of the answer (goose's 50 KB tool-output limit then truncated it to a temp file). Use `python -m agentteams.research search "<query>"` to *find* the page and `... fetch "<url>"` to get extracted text rather than raw HTML. Bridged Goose teams built before 2026-07-24 don't mention this module in their `AGENTS.md` — re-run `agentteams --bridge-from … --framework goose --bridge-merge` to add it. |
| **`agentteams.research search` returns `[]` for a long, specific query** | Not "no results": DuckDuckGo answers a challenged request with **HTTP 202** and an interstitial page, which parses to nothing. Fixed 2026-07-24 — it now retries once with a broadened query and prints a `note:` to stderr explaining what happened. If you see that note, the empty list is a block, not an absence. Short keyword queries are challenged far less. |
| **`agentteams.research fetch` returns a few hundred chars of menus** | `--max-bytes` caps the *download*, and the old 40 KB default stopped inside a large page's `<head>`/navigation — 342 chars and zero body content from a Wikipedia article, with no error. Default is now 400,000. Raise `--max-bytes` for very large pages; `--max-chars` separately bounds what enters your context. |
| `goose recipe validate <f>` fails | check `version: "1.0.0"`, non-empty `instructions:`, no `model:` key |
| "No provider/model configured" | set a provider (§2/§3) — env override or `config.yaml` |
| OpenRouter 401 | `OPENROUTER_API_KEY` unset/invalid; `goose-backend status` to check |
| Model errors / no tool calls | your OpenRouter `GOOSE_MODEL` isn't tool-capable — pick a tool model (§2b) |
| stdio MCP server won't start | install its runner (`uv`/`uvx` or `npx`); export its `env_keys` creds |
| validate a recipe before running | `goose recipe validate .goose/recipes/<slug>.yaml` |

### Recover a broken project tracker and local route proxy

Two independent faults can appear together:

Run the bounded recovery utility first from the AgentTeamsModule repository:

```sh
python3 scripts/goose-recover.py --check  # diagnose only
python3 scripts/goose-recover.py          # repair/start when safe
```

The check performs no writes, process signals, credential reads, or non-loopback requests. It
returns `0` when both components are healthy, `1` when a recognized repair or proxy start is
needed, `2` when the state is unsupported or unsafe to change, and `3` for an operating-system
or subprocess failure. Normal mode can repair only the observed tracker corruption shape: a
schema-valid `projects` object followed by `: null` and extra closing braces. It refuses
truncated, non-UTF-8, structurally invalid, or otherwise unknown content.

When port `8791` is empty, normal mode starts
`scripts/goose-openrouter-route-proxy.py` detached with that proxy's built-in provider defaults.
Those defaults may differ from a prior narrowed `--only` command; use the manual command below
when exact backend selection matters. When the tracked route proxy is already healthy,
`--restart` preserves its exact command. It refuses an empty port, an unknown listener, a
process/health identity mismatch, or a proxy that cannot answer its local `/healthz` endpoint.
An older proxy started before this endpoint was added forwards `/healthz` upstream and commonly
returns HTTP `403`; treat that as a legacy process requiring the verified-owner manual migration
below, not as proof that an unknown listener is safe to terminate.
The process identity check and `SIGTERM` remain separate operating-system operations, so an
unavoidable PID-reuse race exists despite the immediate recheck.

The utility prints the exact mode-`0600` tracker backup and proxy log paths it creates. It does
not install a login service, so a reboot can require another normal invocation.

- `Failed to parse projects.json file` means Goose cannot decode
  `~/.local/share/goose/projects.json`. In the observed failure, a complete valid object was
  followed by `: null` and extra closing braces. Back up the file, retain only the first valid
  top-level object, then validate it with both a general JSON parser and Goose itself:

  ```sh
  tracker="$HOME/.local/share/goose/projects.json"
  cp -p "$tracker" "$tracker.bak-$(date +%Y%m%d-%H%M%S)"
  python3 - "$tracker" <<'PY'
  import json
  import os
  from pathlib import Path
  import sys

  path = Path(sys.argv[1])
  value, end = json.JSONDecoder().raw_decode(path.read_text())
  if not isinstance(value, dict) or not isinstance(value.get("projects"), dict):
      raise SystemExit("refusing repair: first JSON value is not a Goose project tracker")
  temporary = path.with_suffix(path.suffix + ".tmp")
  temporary.write_text(json.dumps(value, indent=2) + "\n")
  os.replace(temporary, path)
  PY
  jq empty "$tracker"
  goose projects
  ```

  `goose projects` must show the project menu instead of a parse error. If it does not, restore
  the timestamped backup rather than making further speculative edits.

- An immediate connection refusal on port `8791` means `OPENROUTER_HOST` points to the local
  route proxy but nothing is listening. The proxy is **opt-in and nonpersistent**: it is not a
  login service and does not survive a reboot. Confirm the configuration and listener, then
  restart it with a backend that currently serves the configured model:

  ```sh
  grep '^OPENROUTER_HOST:' ~/.config/goose/config.yaml
  lsof -nP -iTCP:8791 -sTCP:LISTEN
  python3 scripts/goose-openrouter-preflight.py --providers <openrouter-model>
  python3 scripts/goose-openrouter-route-proxy.py --port 8791 --only "<current-backend>"
  ```

  If `goose-recover.py --restart` reports `proxy health endpoint failed`, first confirm the PID
  and command shown by `lsof -nP -iTCP:8791 -sTCP:LISTEN` and
  `ps -ww -p <pid> -o command=`. Stop it with `kill <pid>` only when it resolves to one of the
  tracked route-proxy script, then run `python3 scripts/goose-recover.py`. Do not kill an
  unknown listener. Avoid restarting while an active Goose turn is using port `8791`; the
  in-flight request will be interrupted.

  Keep that process running. Alternatively, remove `OPENROUTER_HOST` from `config.yaml` and
  restart Goose to use OpenRouter directly without backend pinning. The proxy is a reliability
  layer for provider selection, not a universal OpenRouter connectivity requirement.

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
