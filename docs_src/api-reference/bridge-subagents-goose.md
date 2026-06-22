# `bridge_subagents_goose` — AgentTeamsModule

Opt-in subagent-stub-recipe emitter for the `… → goose` bridge — parity with
`bridge_subagents` (the claude path).

> Source: `agentteams/bridge_subagents_goose.py`

---

Gated by the `bridge:<source>-to-goose:subagents` host-feature token (default off, so
the pointer bridge stays byte-identical). Emits one thin pointer recipe per canonical
source agent into `.goose/recipes/<slug>.yaml` (valid via
`agentteams.frameworks.goose._emit_recipe`), skipping reserved/bridge-owned slugs
(`orchestrator`, `team-builder`, `bridge-orchestrator`) and never overwriting an
existing recipe. The lightweight alternative to `--convert-from … --framework goose`.

## Public surface

- `emit_goose_subagent_stubs(*, source_dir, output_root, source_framework="copilot-vscode", dry_run=False)`
  — emit stub recipes; returns a `StubEmissionResult` (`written` / `skipped` / `errors`).
