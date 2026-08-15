# Copilot CLI Agent Infrastructure Expert Reference

Purpose: Canonical guidance for integrating the GitHub Copilot **CLI** (the modern
standalone `copilot` CLI) agent infrastructure into AgentTeamsModule. Split from the
former shared `copilot-agent-infrastructure-expert.md` on 2026-08-15
(agent-doc-optimal-structure plan; parity report R6).

## Authoritative Documentation (verified live 2026-08-15)

- Create custom agents for the CLI:
  - https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli
- Invoke custom agents:
  - https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents
- Custom instructions for the CLI:
  - https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions
- Cross-surface custom-agents configuration reference (shared with VS Code/cloud):
  - https://docs.github.com/en/copilot/reference/custom-agents-configuration

Dead citation (do not use): `docs.github.com/en/copilot/github-copilot-in-the-cli/about-github-copilot-in-the-cli`
— 301-redirects to a generic responsible-use page (verified 2026-08-15).

## Verified Upstream Conventions (2026-08-15)

- **Agent files: `.github/agents/<slug>.agent.md`** — the SAME directory and format
  as VS Code (shared cross-surface reference; YAML front matter + Markdown body;
  user-level `~/.copilot/agents/`; user/repo/org-enterprise precedence). Confirmed by
  direct fetch: the CLI doc names only `.github/agents/`; **`.github/copilot/` appears
  nowhere in current docs.**
- Custom instructions: the CLI reads `.github/copilot-instructions.md`,
  `.github/instructions/**/*.instructions.md`, `AGENTS.md`, `CLAUDE.md` /
  `.claude/CLAUDE.md`, `GEMINI.md`, `$HOME/.copilot/copilot-instructions.md`,
  `$HOME/.copilot/instructions/**/*.instructions.md`, plus
  `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`; supports `@relative/path` file inclusion.

## Known Deltas vs Our Adapter (`agentteams/frameworks/copilot_cli.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| P1 | **High.** We emit plain-Markdown `.github/copilot/<slug>.md` with all front matter stripped. Current CLI reads `.github/agents/*.agent.md` with YAML front matter — our emitted files sit in a location the CLI does not read; the convention appears in no upstream doc. Correct fix is convergence: one `.agent.md` emission serves both Copilot adapters (differentiable via `target:`). | **re-verified** (direct fetch 2026-08-15) | Tranche 2 — adapter redesign (remediation log) |
| P2 | Stale citation URL in adapter docstring / registry / AUTHORING-GUIDE template. | **re-verified** (301 confirmed) | Tranche 1 — fixed 2026-08-15 |
| P7 | The CLI natively reads `AGENTS.md` and scoped instructions files — emitting AGENTS.md (already done by agents-md/goose adapters) makes teams legible to the CLI at zero cost. | researcher-claimed | Tranche 2 note |

## Function-Level Conformance Requirements (current adapter, pending P1)

- The shipped adapter still emits plain-Markdown prompts to `.github/copilot/`;
  until the P1 redesign lands, tests must continue to validate that behavior —
  the delta is tracked in the remediation log, not silently half-migrated.
- Handoff extraction to `references/runtime-handoffs.json` is unaffected by P1.

## Integration Checklist

1. Treat the P1 convergence (CLI ← `.agent.md` shared surface) as the headline
   Tranche-2 item for this framework; coordinate with the copilot-vscode adapter.
2. Until then, document clearly in generated output that `.github/copilot/` files
   are NOT read by the modern CLI.
3. Point all references at the four live URLs above.
