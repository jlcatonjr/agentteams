# `user_regions` — AgentTeamsModule

The user-editable regions of generated files, and how they're carried across `--overwrite` (P5a).

> Source: `agentteams/user_regions.py`. Used by `emit.emit_all` on the overwrite and merge full-replace paths and by
> `project_notes._ensure_project_notes_section`.

## Regions

| Region | Where |
|---|---|
| `## Project-Specific Notes` | The end of every agent persona: Markdown, a Codex `developer_instructions` body, and (P5a) the end of a Goose recipe's `instructions: \|` block. A fresh Copilot `.agent.md` render places it just before the body's first fence instead, inside GitHub's 30,000-character body limit (when its first fence is a named section; an agent wrapped whole in the legacy `content` fence keeps them last) |
| `## Project-Specific Rules` | Rendered instruction files: `CLAUDE.md`, `.claude/CLAUDE.md`, `AGENTS.md` and `.github/copilot-instructions.md`; also `AGENTS.override.md` and `.goosehints` when [`rules_projection`](rules-projection.md) adds one |

A region runs from its heading to the first of:
- a later `AGENTTEAMS:BEGIN` fence;
- a TOML `'''` line;
- a line indented less than the heading (the end of a YAML block scalar);
- the end of the file.

## `--overwrite`

`--merge` keeps these regions, because they sit outside every fence. The one exception was a non-Markdown file
with no fence on disk or in the render (a Goose recipe built from an unfenced template, such as the
work-summarizer): `--merge` full-replaces it, and until #533 that dropped its notes with no notice. The
full-replace now carries the regions too (`merge_carry`). It refuses the write, keeping the file, when a region
can't be carried, and `--discard-user-regions` replaces the file as before. A merge carries silently, as it keeps
out-of-fence text everywhere else. `--overwrite` carries them too:
- **Carry.** Each region on disk replaces the same region in the new render. For a recipe it is re-indented
  to the new block indentation.
- **Report.** Every carried region is printed and recorded as a notice. Carried text is instruction-bearing
  (C-4), so review it. `--dry-run` reports "would carry".
- **Refuse rather than lose text.** If the new render has no such region, that file isn't overwritten, and
  an error says so. A region holding only generated text (the USER-EDITABLE note and a projected rules block,
  which the next projection writes again) is dropped instead.
- **Opt out.** `--discard-user-regions` drops the regions instead. Use it when the region itself is the
  thing being reset, for example injected text.

| Name | Purpose |
|---|---|
| `carry_regions(old, new) -> (content, carried, problems)` | Carry each region of `old` into `new`. |
| `merge_carry(rel_path, existing, content, errors, *, discard=False, dry_run=False) -> str` | The `--merge` full-replace step: `content` with `existing`'s regions carried, or `existing` itself (write nothing) with one error per region that can't be carried (#533). |
| `recipe_shape_problems(fresh, carried) -> list[str]` | Parser-free check, used by `merge_carry` on a recipe, that a carry changed only the regions: the text after each region matches the fresh render and every region line keeps the space indent (#533). |
| `find_region(text, heading)` | Locate one region: `(start, end, indent)`. |
| `ensure_recipe_notes(recipe, notes_section)` | Add the notes region to a Goose recipe's `instructions` block. Idempotent. |
| `reindent(region, old_indent, new_indent)` | Move a region between indents (YAML block scalars); `None` when a line is under-indented. `_reindent` is the older name. |
| `skip_banner(lines, start=0)` | Index of the first line after a region's generated USER-EDITABLE note, so callers see only the operator's text. |
| `insert_missing_regions(rel_path, fresh, merged) -> (content, notices)` | On `--update --merge`, give an instruction file the empty `## Project-Specific Rules` region its template declares but the file predates, with its SECTION MANIFEST row. A heading already present in any spelling is left alone. |
