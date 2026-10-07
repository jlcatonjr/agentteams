# `user_regions` — AgentTeamsModule

The user-editable regions of generated files, and how they're carried across `--overwrite` (P5a).

> Source: `agentteams/user_regions.py`. Used by `emit.emit_all` on the overwrite path and by
> `project_notes._ensure_project_notes_section`.

## Regions

| Region | Where |
|---|---|
| `## Project-Specific Notes` | The end of every agent persona: Markdown, a Codex `developer_instructions` body, and (P5a) the end of a Goose recipe's `instructions: \|` block |
| `## Project-Specific Rules` | Rendered instruction files: `CLAUDE.md`, `.claude/CLAUDE.md`, `AGENTS.md` and `.github/copilot-instructions.md` |

A region runs from its heading to the first of:
- a later `AGENTTEAMS:BEGIN` fence;
- a TOML `'''` line;
- a line indented less than the heading (the end of a YAML block scalar);
- the end of the file.

## `--overwrite`

`--merge` has always kept these regions, because they sit outside every fence. `--overwrite` now carries them
too:
- **Carry.** Each region on disk replaces the same region in the new render. For a recipe it is re-indented
  to the new block indentation.
- **Report.** Every carried region is printed and recorded as a notice. Carried text is instruction-bearing
  (C-4), so review it. `--dry-run` reports "would carry".
- **Refuse rather than lose text.** If the new render has no such region, that file isn't overwritten, and
  an error says so.
- **Opt out.** `--discard-user-regions` drops the regions instead. Use it when the region itself is the
  thing being reset, for example injected text.

| Name | Purpose |
|---|---|
| `carry_regions(old, new) -> (content, carried, problems)` | Carry each region of `old` into `new`. |
| `find_region(text, heading)` | Locate one region: `(start, end, indent)`. |
| `ensure_recipe_notes(recipe, notes_section)` | Add the notes region to a Goose recipe's `instructions` block. Idempotent. |
