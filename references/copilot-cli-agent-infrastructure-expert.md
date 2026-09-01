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
| P1 | ~~**High.** We emit plain-Markdown `.github/copilot/<slug>.md` with all front matter stripped. Current CLI reads `.github/agents/*.agent.md` with YAML front matter.~~ **Closed 2026-08-15.** `CopilotCLIAdapter.render_agent_file` now delegates to `CopilotVSCodeAdapter` for the `.agent.md`-with-front-matter shape, then strips handoffs (still VS-Code-desktop-only per upstream). Output path, `interop.py`'s capability round-trip, and `multi_sync`'s path-collision handling all updated together — see the remediation log. | **re-verified** (direct fetch 2026-08-15) | Closed — see `references/agentteams-remediation-log.csv` (P1 row) |
| P2 | Stale citation URL in adapter docstring / registry / AUTHORING-GUIDE template. | **re-verified** (301 confirmed) | Tranche 1 — fixed 2026-08-15 |
| P7 | The CLI natively reads `AGENTS.md` and scoped instructions files — emitting AGENTS.md (already done by agents-md/goose adapters) makes teams legible to the CLI at zero cost. | researcher-claimed | Open — not part of the P1 convergence, still a candidate item |

## Function-Level Conformance Requirements (current adapter, post-P1)

- The shipped adapter emits `.agent.md` files with YAML front matter to
  `.github/agents/` — the same physical directory and shape as `copilot-vscode`,
  minus handoffs. Tests validate this directly (`tests/test_frameworks.py`,
  `tests/test_interop.py`, `tests/test_learnpython_generation.py`).
  Pre-existing `.github/copilot/*.md` files from before the convergence are left
  in place as harmless orphans — no auto-deletion.
- Handoff extraction to `references/runtime-handoffs.json` is unaffected by P1:
  `handoff_delivery_mode()` still returns `"manifest"`.
- **Multi-framework sync caution:** because `copilot-cli` and `copilot-vscode` now
  share one physical output directory, `agentteams.multi_sync` refuses to sync
  both in the same set — see that module's `_reject_directory_collisions` and the
  CLI's `--frameworks` flag for the escape hatch.

## Integration Checklist

1. ~~Treat the P1 convergence (CLI ← `.agent.md` shared surface) as the headline
   Tranche-2 item for this framework~~ — **done 2026-08-15.** Any future adapter
   change here should keep `copilot_cli.py` delegating to `copilot_vscode.py`
   rather than reintroducing an independent render path, per the precedent this
   fix established (matching `codex.py`'s delegation to `agents_md.py`).
2. Point all references at the four live URLs above.

## Observed Upstream Tokens — `copilot_cli` (Daily Pipeline)

Recorded by the daily pipeline on `2026-09-01` from `https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli`.

- Upstream tokens observed: —
- Upstream locations observed: .github/agents
- Fetch status: `ok`
