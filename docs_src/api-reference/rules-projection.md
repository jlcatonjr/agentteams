# `rules_projection` — one project's rules in every provider's entry file

> *Source: `agentteams/rules_projection.py`*

An instance runs on several providers, and each loads its own entry file. A standing rule recorded in one provider's
`## Project-Specific Rules` region bound that provider only, and nothing reported the gap: in baseAgent every standing
operator decision lived in `.claude/CLAUDE.md` alone (P1, P2, 2026-11-02). This module keeps a generated copy of one
source region in every other enabled provider's entry file. It runs after every generate and `--update`, and on
`--sync-project-rules`; `--check` reports drift (see the [CLI reference](../cli-reference.md#-sync-project-rules)).

| Provider | Target | Notes |
|---|---|---|
| Copilot | `.github/copilot-instructions.md` | |
| Claude | `.claude/CLAUDE.md`, else `CLAUDE.md` | Claude Code loads both, so a source in either covers Claude. |
| Codex | `AGENTS.override.md` when it exists, else `AGENTS.md` | Codex loads the override *in place of* `AGENTS.md`. Past the 32 KiB budget the block is a binding reference plus the rules' titles (`mode=reference`). |
| Goose | `.goosehints` | Goose's own file, so a Codex freeze of the shared `AGENTS.md` doesn't stop Goose getting new rules. |

| Name | Purpose |
|---|---|
| `SOURCE_CANDIDATES` | Where the source is looked for when the brief's `project_rules_source` names none. |
| `rules_body(text)` | The operator's own rules: the Rules region without its heading, the USER-EDITABLE note and any projected block. |
| `find_source(root, configured)` | The source file and its rules. `configured` must be one of `SOURCE_CANDIDATES`; without it, the source the copies record, else the only candidate with rules of its own. Raises `ValueError` rather than let the source move, or choose between two files with rules. |
| `targets(root, source, frozen)` | The entry files to project into; frozen surfaces and the source itself left out. |
| `table_of_contents(body)` | The rules' titles (headings, else bold list leads, else list items). |
| `projection_content(source, body, mode)` | The block's text: the rules (`copy`) or a binding reference (`reference`). |
| `project(root, *, configured, frozen, write)` | Project into every target. Returns a `Report` with one `Outcome` per target (`current`, `updated`, `added`, `refused`, `skipped`); `Report.drift` is what `--check` fails on. |
| `pointer_files(root)` / `sync_pointers(...)` | The `security` and `orchestrator` agent files, and a `PROJECT-RULES-POINTER` block in each one's notes. |

**Safety.** The block markers (`<!-- PROJECTED-RULES:BEGIN source=… source-sha256=… copy-sha256=… -->`) are not
`AGENTTEAMS` fences, so the Rules region still carries across `--overwrite` and the target's own rules beside the block
are kept. A block whose text no longer matches its recorded `copy-sha256` was edited by hand and is refused, as is a
file with two Rules regions, a symlinked target, a target or source with CRLF line endings, and a source with two Rules
regions or a projection marker in its rules. The source region ends at the next `## ` section. An updated copy's
outcome carries the rules' diff, which every run prints, so what spreads to every provider is visible. Rule titles
(the Codex reference's table of contents) are labels: capped at 100 characters, with comment markers and backticks
removed. The notes pointer names the rules file only; it says the rules extend the agent's instructions and never
weaken its Invariant Core or the Constitutional Core.
