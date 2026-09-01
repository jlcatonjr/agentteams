# AGENTS.md Standard Infrastructure Expert Reference

Purpose: Canonical guidance for integrating the cross-tool **AGENTS.md** standard
into AgentTeamsModule. Authored 2026-08-15 (agent-doc-optimal-structure plan;
closes the agents-md gap in the per-framework expert-reference set — parity
report R6).

## Authoritative Documentation (verified live 2026-08-15)

- The standard: https://agents.md

## Verified Upstream Conventions (2026-08-15)

- **No required schema.** "AGENTS.md is just standard Markdown. Use any headings
  you like." No length guidance either.
- **Popular sections** (from the standard's own guidance): project overview,
  build and test commands, code style, testing instructions, security
  considerations, commit/PR guidelines, deployment steps.
- **Nested semantics (monorepos):** one AGENTS.md per subproject; agents read the
  nearest file in the directory tree — closest takes precedence (OpenAI's own
  repo carries 88).
- **Consumers:** 60k+ projects; Codex, Jules, Factory, Aider, Goose, opencode,
  Zed, Warp, VS Code, Devin, JetBrains Junie, Amp, Cursor, RooCode, Gemini CLI,
  GitHub Copilot, Windsurf, Augment Code, others.
- **Per-agent subfiles:** nothing in the standard about companion `.agents/`
  directories — they remain outside the standard (as our adapter already
  documents: `.agents/<slug>.md` files are detail files for humans, not parsed
  by standard consumers).

## Canonical Output Conventions (ours, current)

- Repo-root `AGENTS.md`: framework-neutral team brief (overview, conventions,
  roster + routing) — shared namespace with the goose and codex adapters.
- `.agents/<slug>.md`: per-specialist detail files (non-standard, documented as
  such).

## Known Deltas vs Our Adapter (`agentteams/frameworks/agents_md.py`)

| ID | Delta | Verification | Disposition |
|----|-------|--------------|-------------|
| A1 | Our emitted AGENTS.md is schema-compliant (there is no schema), but omits the two most-recommended practical sections: build/test commands and code style. Nested per-directory AGENTS.md is the standard's scoping mechanism; we emit root-only (a reasonable choice for a team brief — recorded, not a defect). | researcher-claimed | Tranche 2 — add build/test + code-style sections sourced from the brief |

## Integration Checklist

1. Keep root-only emission; document nested-file semantics for operators.
2. Tranche 2: extend the team-brief template with build/test-commands and
   code-style sections when the project brief carries them.
3. This surface serves goose (native context file), codex (instructions), and
   the Copilot CLI (native custom-instructions source) simultaneously — changes
   here propagate to three consumers.

## Observed Upstream Tokens — `agents_md` (Daily Pipeline)

Recorded by the daily pipeline on `2026-09-01` from `https://agents.md`.

- Upstream tokens observed: —
- Upstream locations observed: AGENTS.md
- Fetch status: `ok`
