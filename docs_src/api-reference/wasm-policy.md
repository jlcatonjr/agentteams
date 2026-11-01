# `wasm_policy` — AgentTeamsModule

Render-time WASM sandbox instructions for generated agents (WASM sandbox plan P14). When WASM is on, each agent that
P01's `wasm_eligible` accepts gets one section telling it what `run_wasm` runs (prebuilt `.wasm` modules, Python,
JavaScript) and cannot run (Bash, git, npm, any host program, the network), its grant, that guest output is data
(C-4), and what happened to its native shell. With WASM off every function is the identity, so default teams
render byte-identically.

> Source: `agentteams/wasm_policy.py` (stdlib only, integrity-pinned). It decides no eligibility and grants no
> tool (C-3): it reads `wasm_config.wasm_eligible`, P11's `wasm_wiring` row, and the agent's tools before and after
> P13's narrowing. The `wasm` framework (P08) is excluded; it renders its own runtime section.

## Called by the render pipeline

`cli/render_pipeline._build_final_rendered` calls, in order:

| Call | When | Effect |
|---|---|---|
| `begin(manifest)` | start of every render | resets the transient claim record |
| `populate_placeholders(manifest)` | before `render_all` | `{WASM_SECURITY_TRIGGER_ROWS}`, `{WASM_ROUTING_ROWS}`; `""` while off |
| `apply(content, slug, manifest, framework, *, before=None, builder=False)` | each agent and the builder, after `write_policy.apply` (and P13's narrowing) | appends the section |
| `apply_instructions(content, manifest, framework)` | the instructions file | appends the routing section |
| `verify_claims(manifest, framework)` | after the agents | raises `ValueError` if, on claude/goose, the agents whose text says `run_wasm` differ from those P11 wired |
| `reference_doc(manifest, framework)` | while on | `references/wasm-sandbox.reference.md` |

## Which section an agent gets

| Agent | Section |
|---|---|
| rendered from `orchestrator.template.md` (eligible) | *Routing Code and Grants*: laundering rule, native-exec policy, grant changes to `@security` |
| rendered from `security.template.md` | *Reviewing Grants and Relaxations*: `wasm-grant-widen`, `native-exec-keep`, `sandbox-wasm-off`, `wasm-to-os-switch`; no usage text |
| other eligible agent, `run_wasm` wired here | *Run Code Through `run_wasm`*: grant (project-relative mounts, limits), C-4, shell status |
| other eligible agent, no `run_wasm` here (copilot-*, grok, agents-md, the builder on claude/goose) | *You Have No `run_wasm`* (advisory) |
| not eligible (every read-only auditor) | none |

The shell line is read from the file: `execute` present before P13's narrowing and absent after is "removed";
otherwise the builder exemption, a `native_exec_keep` / `native_exec: keep` exception, or "kept". No section claims
enforcement; each points to `wasm_enforcement` and `[WASM-ENFORCEMENT]`. When the body already has fences the
section gets its own `wasm_sandbox` fence.

## Public names

`apply`, `apply_instructions`, `begin`, `enabled`, `populate_placeholders`, `reference_doc`, `routing_rows`,
`security_trigger_rows`, `verify_claims`; constants `CLAIMS_KEY`, `EXCLUDED_FRAMEWORKS`, `FENCE_ID`,
`REFERENCE_PATH`, `ROUTING_ROWS_PLACEHOLDER`, `SECURITY_ROWS_PLACEHOLDER`.
