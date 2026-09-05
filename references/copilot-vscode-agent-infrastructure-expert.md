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
| P3 | ~~We used `user-invokable`; docs use `user-invocable`.~~ **Fixed 2026-08-15**: adapter renamed to `user-invocable`, matching docs. Already-deployed consumer repos migrate automatically on their next `--update --merge` / fleet run via the key-succession mechanism in `front_matter_merge.py` / `front_matter_reconcile.py`. | **re-verified** (quote checked 2026-08-15) | Shipped — see `references/agentteams-remediation-log.csv` |
| P4 | We require {name, description, user-invocable, tools, model}; upstream requires at most `description`. Over-requiring is harmless but overstates the contract. | researcher-claimed + partially re-verified | **Closed 2026-08-15, as a documentation fix, not a code relaxation.** Deliberately did NOT loosen `_REQUIRED_YAML_KEYS`/`required_front_matter_keys()`: doing so would stop auto-injecting `user-invocable`/`tools`/`model` defaults for any template missing them (verified — `_YAML_DEFAULTS` only covers those three), a real safety-net regression for zero practical benefit (our own templates always declare all five keys). The actual defect was attribution, not validation: this table previously implied GitHub *mandates* all 5 keys. Fixed by labeling the split explicitly, right here — `description` is upstream-mandated; `name`/`user-invocable`/`tools`/`model` are our own internal quality bar, stricter than upstream by choice. |
| P5 | Our default `model: ["Claude Opus 4.8 (copilot)"]` — **valid**: VS Code documents string OR prioritized array. Cross-surface note: GitHub's cloud-agent reference documents string form. | **re-verified** (partially refutes original finding) | No action; note only |
| P6 | Newer keys we don't surface: `disable-model-invocation`, `target`, `argument-hint`, `mcp-servers`, `hooks`. | researcher-claimed | **Closed 2026-08-15 — already worked, verified empirically, no code change.** Directly tested both paths that could plausibly strip an unrecognized key: `CopilotVSCodeAdapter.render_agent_file` (fresh generation) passes all five through untouched when a source/template sets them, and `front_matter_merge._merge_front_matter` is key-agnostic (`_FM_KEY_RE` matches any `key: value` line, not a fixed set) — an on-disk-only key with no template/baseline counterpart merges through cleanly, confirmed with `target`/`argument-hint`/`mcp-servers` directly. Nothing strips these; templates simply don't emit them by default, which is correct ("additive only, no default values injected"). |
| P8 | 30,000-char body limit unenforced at render time. | researcher-claimed | **Closed 2026-08-15 — enforcement added as a warning, not a hard failure.** `CopilotVSCodeAdapter`'s `_check_body_length` (inside `_ensure_yaml_front_matter`, covering all three real callers: `cli/render_pipeline.py`, `convert.py`, `interop.py`) now emits a `UserWarning` when a rendered body exceeds 30,000 characters. **Not a raise**: a first pass raised `ValueError` and discovered this project's OWN orchestrator template already renders a ~46,500-character body — 55% over the limit, already true before this check existed. Raising would make fresh `copilot-vscode`/`copilot-cli` generation fail for every project using the standard orchestrator template, not flag an edge case. Logged as a separate, correctly-scoped follow-up: `references/agentteams-remediation-log.csv` — shrinking the orchestrator template to fit is real content-editing work with its own blast radius (every generated team, every golden snapshot), not part of adding enforcement. |

## Function-Level Conformance Requirements

- Ensure YAML front matter exists and is normalized; preserve `agents:`/`handoffs:` team wiring.
- Pipeline must finalize output paths via adapter rules (`.agent.md`, `../copilot-instructions.md` → `.github/copilot-instructions.md`).
- Emission and tests must validate front-matter contract and body preservation.
- Body length: `_check_body_length` warns (non-fatally) past 30,000 characters; front
  matter is excluded from the count.

## Integration Checklist

1. Keep `.github/agents/*.agent.md` + `.github/copilot-instructions.md` as the emitted surface.
2. `user-invocable` migration shipped 2026-08-15 — already-deployed consumer repos migrate
   automatically on their next `--update --merge` / fleet run.
3. New-key pass-through (`target`, `argument-hint`, `mcp-servers`, `disable-model-invocation`,
   `hooks`) already works end-to-end (P6, verified 2026-08-15) — no adapter change needed to add one.
4. Maintain transformation-parity tests across Copilot targets.
5. The orchestrator template's body-length overage (P8) is tracked separately — do not
   silently grow it further without checking `_check_body_length`'s warning.
