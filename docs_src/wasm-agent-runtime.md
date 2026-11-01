# WASM agent runtime (`--framework wasm`)

**Beta.** `--framework wasm` renders a team for agentteams' own agent runtime instead of a vendor CLI. Each agent runs
as a sequence of WASM guest steps; the host process calls the model provider you choose (Anthropic, OpenAI-compatible
services, Gemini, local servers, or an offline stub). The runtime is described in
[`wasm_agent`](api-reference/wasm-agent.md); this page covers rendering a team and running it.

## Render a team

```
agentteams --description brief.json --framework wasm --project .
```

The framework choice turns WASM confinement on (reason `framework`). If the brief sets `wasm_sandbox.enabled: false`
or you pass `--wasm-sandbox off`, the render refuses: the runtime has no non-WASM mode. The render writes:

| Path | What it is |
|---|---|
| `.agentteams-wasm/agents/<slug>.md` | Front matter `name`, `description`, `tools`, `model`, `delegates`, then the agent's prompt |
| `.agentteams-wasm/TEAM.md` | Team instructions, put before every agent's prompt |
| `.agentteams-wasm/grants.json` | Each agent's mounts and limits, and how its read-only mounts are enforced (`read_only_via`) |
| `.agentteams-wasm/runtime.json` | Provider preset, tier-to-model map, budget, prices file, `host_os_confine` |
| `.agentteams-wasm/README.md` | How to run the team and what is enforced |
| `.agentteams/wasm-team.lock.json` | sha256 of every file above (control plane) |

Commit `.agentteams-wasm/` like any other team. Don't edit it by hand: the launcher compares every file with the lock
and refuses a team that drifted, so change the brief and re-render with `--update`.

### Tools

Each template's `tools:` scopes are translated, then narrowed to what the agent's grant can serve:

| Template scope | WASM tools | Dropped when |
|---|---|---|
| `read` | `read_file`, `list_dir` | the grant has no read path and no scratch dir |
| `search` | `search_files`, `list_dir` | the same |
| `edit` | `write_file` | the grant has no scratch dir (project files are never writable) |
| `execute` | `run_code` | never (it runs in a fresh sandbox with the agent's own grant) |
| `agent` | `delegate` | the agent has no team member to delegate to |
| `todo`, `retrieval` | none | always |

Any other scope refuses the render. A template without `tools:` gets no tools. Each drop is printed as a
`[WASM-SCOPE]` line. The default grant is compute only, so most agents start with `run_code` and nothing else; give an
agent read paths or a scratch dir in the brief's `wasm_sandbox.agents` block (a capability grant: `wasm-grant-widen`).

### Models

`model:` is a tier (`primary` or `cheap`), never a model id. `runtime.json` maps tiers to ids, and they stay `null`
unless the brief sets them. At run time `AGENTTEAMS_WASM_MODEL_PRIMARY` / `AGENTTEAMS_WASM_MODEL_CHEAP` override the
file. A tier with no model refuses the run, except on the `stub` provider.

### The `wasm_runtime` brief block

```json
"wasm_runtime": {
  "provider": "anthropic",
  "models": {"primary": "<model id>", "cheap": "<model id>"},
  "budget": {"max_model_calls": 20, "max_usd": 1.0},
  "prices_file": "references/model-prices.json"
}
```

Every key is optional; an unknown key or a bad value refuses the render. A brief never holds a key, and it never
chooses where a key goes: `base_url` is accepted only for a keyless local preset (`ollama`, `lmstudio`) on a loopback
host (`127.0.0.1`, `::1`, `localhost`). For any preset that sends a key the render refuses it; set the URL at run time
instead (see below). The prices file is hashed into the team lock, so editing it means re-rendering.

## Run an agent

```
agentteams --wasm-install all --project .          # once: the pinned runtime and interpreters
agentteams --run-wasm-agent orchestrator --wasm-task "Summarise the open issues." --project .
agentteams --verify-wasm-run <run-id> --project .
agentteams --revoke-wasm-run <run-id> --project . --revoke-reason "stop"
```

Provider keys are read from the environment only, by the launcher, and handed to the host over a pipe:

| Provider | Key variable |
|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `openrouter` | `OPENROUTER_API_KEY` |
| `xai` | `XAI_API_KEY` |
| `gemini` | `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) |
| `openai-compatible` | `AGENTTEAMS_MODEL_API_KEY` (needs an operator base URL) |
| `ollama`, `lmstudio`, `stub` | none |

`--wasm-provider NAME` picks another preset for one run. The run's output is model text and is printed as data.

The key is sent to the preset's own base URL. To send it elsewhere (a proxy, a self-hosted gateway), the operator sets
`AGENTTEAMS_WASM_BASE_URL` or passes `--wasm-base-url URL` (which wins) at run time: `https`, or `http` to a loopback
host, with no user info, query or fragment. A URL rendered from the brief is kept only while the keyless-loopback rule
holds for the run's preset, so `--wasm-provider anthropic` on an `ollama` team drops it (with a notice) and the key goes
to Anthropic's own host.

## What is enforced

- **WASM:** every step and every `run_code` is a fresh guest with an empty environment, no network, fuel, memory and
  time limits, and only the agent's mounts.
- **Host OS confinement:** with the OS sandbox switch on, `runtime.json` says `host_os_confine: auto` and the
  launcher wraps the host in a fresh OS sandbox (Seatbelt or bwrap) where the machine allows it. The guests then run
  inside the host's profile and the transcript reports them as `inherited`, which means "inside the host's profile",
  not a fresh guest sandbox. Where no OS sandbox can start (inside another sandbox, native Windows) the run says
  `WASM host not OS-confined`. With `--os-sandbox off` the file says `off` and the host is never confined; that
  switch is constraint-relaxing (`sandbox-os-off`) and needs a signed decision in a governed workspace.
- **Read-only mounts** are never mounted writable: `snapshot` is a size-capped copy per run (a read path over the cap
  refuses the run); `os` is a nested OS profile.
- **Delegation** (`delegate`) is refused by the host until the delegation runtime lands.
- **Grant authorization at launch:** the lock is a plain hash list, not a signature. Before every run the launcher
  re-checks the agent's grant read-only. In a governed workspace each read path or scratch dir must be covered by an
  active `wasm-grant-widen:wasm` exception whose bound Ed25519 decision still verifies, and limits above the defaults
  must equal the ones the render's sandbox gate recorded; otherwise the run is refused. In an ungoverned workspace a
  `SANDBOX RELAXATION` notice names the widening. Every load also re-runs the credential scan over each read path.

### What the lock does and does not prove

`.agentteams/wasm-team.lock.json` proves the team files match what the last render wrote. It holds no key, so anyone who
can write `.agentteams/` can re-hash an edited team. Its integrity therefore rests on two things: R1, which
write-protects `.agentteams/` for an agent session whenever WASM is on (only in an OS-confined session: Claude's
deny list, the launcher's read-only binds or the Seatbelt control-plane denies), and the launch-time authorization
check above, which a re-hashed `grants.json` cannot pass in a governed workspace. Outside an OS-confined session, treat
`.agentteams/` as trusted only as far as you trust every process that can write it.

## Limits of v1

- Generate only: `--framework wasm` is not a convert, bridge, interop or absorb target or source.
- No team-builder agent, and no `.agentteams-wasm/run` shim.
- Templates were written for vendor CLIs. Every prompt gets a generated "Your WASM runtime" section that lists the
  agent's real tools and file access, but a prompt may still mention commands the runtime does not have.
- A widened grant needs the operator's signed `wasm-grant-widen` decision on every later render once the workspace is
  governed (the same rule as for every other framework).
