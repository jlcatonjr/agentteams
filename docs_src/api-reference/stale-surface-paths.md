# `stale_surface_paths` — AgentTeamsModule

The merge-time notice for unfenced lines, left by an older render, that name another surface's agents directory
(F10, 1.3.0). See the [section-fencing guide](../section-fencing-guide.md) ("Merge left stale generated content").

> *Source: `agentteams/stale_surface_paths.py`*

A `--update --merge` keeps every line outside the fences. A line is reported when it is outside every fence and
outside the Project-Specific Notes and Rules regions, names another registered surface's agents directory, and is not
in the new render. `emit.emit_all` prints one notice per file, on the real run and the dry run. The notice changes
nothing; the fix is `--update --overwrite` (which keeps the Notes and Rules regions) or a hand edit.

| Name | Purpose |
|---|---|
| `surface_dirs()` | Every registered framework's agents directory (from `frameworks.registry`), longest first. |
| `own_surface(output_dir)` | The agents directory being rendered into, or `None` (then nothing is reported). |
| `stale_lines(existing, fresh, own)` | `(line number, other surface dir, line)` for each stale line. |
| `notice(rel_path, existing, fresh, own)` | The notice text for one file, or `None`. Quotes at most `MAX_QUOTED` lines. |
