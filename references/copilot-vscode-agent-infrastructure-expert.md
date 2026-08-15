# Copilot VS Code Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating GitHub Copilot **VS Code** custom-agent
infrastructure into AgentTeamsModule. Split from the former shared
`copilot-agent-infrastructure-expert.md` on 2026-08-15 (agent-doc-optimal-structure
plan; parity report R6) because the two Copilot surfaces have distinct upstream docs
and conventions — and the merged file obscured the CLI adapter's divergence.

## Authoritative Documentation (verified live 2026-08-15)

- VS Code custom agents (current; `.chatmode.md` is explicitly legacy — "rename to `.agent.md`"):
  - https://code.visualstudio.com/docs/copilot/customization/custom-agents
- VS Code custom instructions:
  - https://code.visualstudio.com/docs/copilot/customization/custom-instructions
- GitHub cross-surface custom-agents configuration reference (shared with CLI/cloud):
  - https://docs.github.com/en/copilot/reference/custom-agents-configuration

## Canonical Output Conventions

- Agent files:
  - Location: `.github/agents/` (workspace); `~/.copilot/agents` (user-level). VS Code also detects Claude-format `.claude/agents` files.
  - Extension: `.agent.md`
  - Structure: YAML front matter + Markdown body
- Project instructions (documented tiering):
  1. `.github/copilot-instructions.md` — project-wide starting tier (what we emit)
  2. `.github/instructions/*.instructions.md` — scoped rules with `applyTo` globs (not emitted by us)
  3. `AGENTS.md` — natively supported for multi-agent interop (not emitted by the copilot-vscode adapter; the agents-md/goose adapters share that path)

## Verified Upstream Front-Matter Contract (2026-08-15)

Documented keys: `name` (defaults to filename), `description`, `argument-hint`,
`tools` (list or comma-separated string, case-insensitive aliases; `["*"]` = all,
`[]` = none), `agents`, `model` (string OR prioritized array), `user-invocable`
(default `true`), `disable-model-invocation` (default `false`; replaces retired
`infer`), `target`, `mcp-servers`, `handoffs` (+ `label`/`agent`/`prompt`/`send`/`model`),
`hooks` (preview). On the VS Code page the entire header is optional; GitHub's
cross-surface reference marks `description` required. 30,000-char body limit
(cross-surface reference). `handoffs`/`argument-hint` are unsupported on the cloud
agent — `target: vscode` warranted where used.

## Known Deltas vs Our Adapter (`agentteams/frameworks/copilot_vscode.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| P3 | We use `user-invokable`; docs use `user-invocable`. Unrecognized keys are ignored → invokability intent silently fails. Blast radius: 46 files under `agentteams/` + `schemas/`. | **re-verified** (quote checked 2026-08-15) | Tranche 2 — fleet-wide key migration (remediation log) |
| P4 | We require {name, description, user-invokable, tools, model}; upstream requires at most `description`. Over-requiring is harmless but overstates the contract. | researcher-claimed + partially re-verified | Tranche 2 |
| P5 | Our default `model: ["Claude Sonnet 4.6 (copilot)"]` — **valid**: VS Code documents string OR prioritized array. Cross-surface note: GitHub's cloud-agent reference documents string form. | **re-verified** (partially refutes original finding) | No action; note only |
| P6 | Newer keys we don't surface: `disable-model-invocation`, `target`, `argument-hint`, `mcp-servers`, `hooks`. | researcher-claimed | Tranche 2 (optional pass-through, not a conformance gap) |
| P8 | 30,000-char body limit unenforced at render time. | researcher-claimed | Tranche 2 |

## Function-Level Conformance Requirements

- Ensure YAML front matter exists and is normalized; preserve `agents:`/`handoffs:` team wiring.
- Pipeline must finalize output paths via adapter rules (`.agent.md`, `../copilot-instructions.md` → `.github/copilot-instructions.md`).
- Emission and tests must validate front-matter contract and body preservation.

## Integration Checklist

1. Keep `.github/agents/*.agent.md` + `.github/copilot-instructions.md` as the emitted surface.
2. Track the `user-invocable` migration (Tranche 2) before widening the front-matter contract.
3. When adding new-key pass-through, follow the cross-surface reference's cloud-agent caveats (`target`).
4. Maintain transformation-parity tests across Copilot targets.
