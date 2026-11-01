# `wasm_enforcement`

Per-provider "use `run_wasm`, not native exec" (WASM sandbox, plan P13; decision D2: enforce where possible, never a
silent claim). When WASM is on, every agent [`wasm_config.wasm_eligible`](wasm-config.md) accepts (it holds an
execute-class tool) follows the brief's `wasm_sandbox.native_exec` policy, and the render records an honest verdict per
framework as `manifest["wasm_enforcement"]` and prints it as `[WASM-ENFORCEMENT]` lines.

> Source: `agentteams/wasm_enforcement.py` (integrity-pinned), with `agentteams/frameworks/_codex_wasm_gate.py` for
> Codex. Called by `cli.render_pipeline` (canonical narrowing), the Claude and Goose renderers (P11 eligibility), the
> relaxation gate (`cli.os_sandbox_switch.gate_exceptions`), the post-emit report and `--check-wiring`.

## Policy

| `native_exec` | Effect on an eligible, non-exempt agent |
|---|---|
| `replace` (default) | The canonical `execute` token is removed where the framework gives the agent `run_wasm` (P11 `wasm_wiring` `wired-per-agent` / `wired-shared`). Elsewhere the agent keeps native exec and is listed as a **fallback**. |
| `deny` | `execute` is removed wherever the framework has a tool channel (not AGENTS.md), with or without `run_wasm`. |
| `keep` | No change; every eligible agent is a `native-exec-keep` effect. |

Never narrowed: the fixed exemptions (`orchestrator`, recognised by its `orchestrator.template.md` template rather
than the slug, and the team builder) and every `native_exec_keep` entry (D12 default `navigator`, `git-operations`).
`retrieval` (Claude's scoped `Bash(python -m agentteams.research:*)`) is kept.

## Verdict (`wasm_enforcement.<framework>`)

`subagents` is one of `enforced`, `conditional`, `declared`, `advisory`. `enforced` and `conditional` require the OS
sandbox on **and** the METAPLAN R1 control-plane rules merged; otherwise the cell is `advisory` with `reason`:

| Framework | Narrowed sub-agents | Precondition check |
|---|---|---|
| claude | `enforced` (Bash removed from subagent `tools`) | `_sandbox_drift.verify_control_plane_merged` and `wasm_mcp.control_plane_advisory` on the live settings |
| goose (grant) | `enforced` on a direct `goose run --recipe`; `delegates` always `advisory` | `wasm_mcp.control_plane_advisory` (`.goose/sandbox.sb`) |
| goose (legacy) | `advisory` (whole `developer` extension) | — |
| codex | `conditional` under the confined launcher's role gate (WASM mode, or the write-policy gate), else `declared` | the launcher's `--protect` + self-probe at each launch |
| grok | `declared` under `deny`; `advisory` under `replace` (no `run_wasm`) | — |
| copilot-vscode / copilot-cli | `advisory` (no `run_wasm`; no verified control-plane protection) | — |
| agents-md | `advisory` | — |

`top_level` is always `advisory`. Codex's `default_tools_approval_mode = "prompt"` is a configured default and never
counts as enforcement. Other fields: `mechanism`, `evidence` (`in-repo` / `spike` / `unverified`), `os_sandbox`,
`control_plane_merged`, `exempt`, `kept_native`, `fallback`, `changed_agents`.

## Codex WASM-mode gate

Without `write_policy` but with WASM on, `codex:sandbox` requested and `native_exec` not `keep`, the Codex runner
emits the pinned role gate and `.codex/hooks.json` (no read-only server) and starts Codex with
`AGENTTEAMS_CODEX_WASM=1` and `AGENTTEAMS_CODEX_NATIVE_KEEP=<exempt + native_exec_keep>`. A spawned agent outside the
keep list is denied the shell tool names (`Bash` probed live on codex-cli 0.160.1; `shell`, `local_shell`,
`exec_command`, `unified_exec` unprobed and defensive) and steered to `run_wasm`; any other tool passes, so an unknown
shell name passes too. A malformed keep list denies everything.

## Public Surface

- `KEY`, `ACTIONS_KEY`, `TERMS`, `NARROW` / `KEEP` / `FALLBACK` / `EXEMPT`.
- `role_exempt(manifest, slug) -> bool`, `exec_policy(manifest, slug) -> str`,
  `run_wasm_available(manifest, framework) -> bool`, `agent_action(manifest, slug, framework, tools) -> str | None`.
- `begin(manifest)`, `apply_canonical(content, slug, manifest, framework) -> str`, `actions(manifest) -> dict`,
  `wiring_tools(manifest, slug, tools) -> list[str]` (only the in-process record counts; JSON input cannot widen).
- `planned_actions(manifest, framework) -> dict` (`--check-wiring`), `gate_keep_set(manifest) -> frozenset[str]`
  (`native-exec-keep` effects), `codex_gate_keep(manifest) -> tuple[str, ...]`.
- `enforcement_report(manifest, framework, *, project_root=None, acts=None) -> dict | None`,
  `record(...)`, `notices(framework, row) -> list[str]`, `report(...)`.
- `frameworks._codex_wasm_gate`: `codex_wasm_gate_enabled`, `codex_wasm_gate_emitted`, `runner_env_flags`,
  `wasm_gate_output_files`.
