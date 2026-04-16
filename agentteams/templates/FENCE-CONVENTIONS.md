# Fence Conventions

This document is the authoritative contract for the section-fencing system used in AgentTeamsModule. It governs how generated content is demarcated in output agent files so that re-generation can update only template-owned regions while preserving all user-authored content.

Cross-referenced by: `PLACEHOLDER-CONVENTIONS.md`, `AUTHORING-GUIDE.md`

---

## Purpose

When `agentteams` generates agent files from templates, some content is owned by the module (rendered from templates + placeholder resolution) and some content is owned by the project team (added or edited after generation). Without clear demarcation, re-generation would destroy user-authored content.

Fence markers solve this by making the boundary explicit and machine-readable.

---

## Fence Marker Format

```
<!-- AGENTTEAMS:BEGIN section_id v=1 -->
...template-generated content...
<!-- AGENTTEAMS:END section_id -->
```

### Rules

1. **Syntax is exact.** The opening tag must match `<!-- AGENTTEAMS:BEGIN <section_id> v=<version> -->` precisely. No spaces inside `-->`.
2. **`section_id`** is `lower_snake_case`. It must be unique within a file, but the same `section_id` may appear in different files.
3. **`v=<version>`** is an integer starting at `1`. It is present only on the BEGIN marker. It is reserved for future format migration — the merge engine currently accepts any `v=` value without error.
4. **END marker** carries only the `section_id` and no version attribute.
5. **Markers are HTML comments** — they are invisible in VS Code Markdown previews and rendered documentation.
6. **No nesting.** Fenced regions must not contain other fenced regions.

### Example

```markdown
<!-- AGENTTEAMS:BEGIN agent_team_list v=1 -->
### Orchestrator
- `@orchestrator` — coordinates all agents

### Governance Agents
- `@security` — destructive operation clearance
<!-- AGENTTEAMS:END agent_team_list -->
```

---

## Section Designation Rules

Every template that uses fence markers **must** include a section manifest in a comment block at the top of the file (immediately after the YAML front matter, if present):

```markdown
<!--
SECTION MANIFEST
| section_id          | designation  |
|---------------------|--------------|
| agent_team_list     | FENCED       |
| authority_hierarchy | FENCED       |
| constitutional_rules| USER-EDITABLE|
-->
```

- **FENCED** — content is owned by the module and will be replaced on re-generation with `--merge`.
- **USER-EDITABLE** — content is owned by the project team and will never be touched by `--merge`.

---

## Partial Fencing

A fence may bracket only a **subset** of a section. For example, a routing table may have a template-generated body but a user-editable header or footer:

```markdown
## Domain Agent Routing

| Content Area | Agent | Key Indicators |
|---|---|---|
<!-- AGENTTEAMS:BEGIN routing_table_rows v=1 -->
| Primary deliverables | `@primary-producer` | New work in `src/` |
<!-- AGENTTEAMS:END routing_table_rows -->
| *(add project-specific rows below this line)* | | |
```

Partial fencing is valid and must be documented in the section manifest with the designation `FENCED (partial)`.

---

## Merge Semantics

Given a newly-rendered file and an existing on-disk file:

1. **Parse** the existing file for all `AGENTTEAMS:BEGIN … AGENTTEAMS:END` blocks.
2. **Parse** the new render for all `AGENTTEAMS:BEGIN … AGENTTEAMS:END` blocks.
3. For each fenced section in the new render:
   - If a matching `section_id` exists in the on-disk file → **replace** the on-disk region's content with the new render's content (markers are updated too, preserving the v= from the new render).
   - If no matching `section_id` exists in the on-disk file → **append** the new block at the end of the file (with a preceding blank line). Record in `MergeResult.sections_added`.
4. Fenced sections in the on-disk file with no match in the new render → **leave in place**. Record in `MergeResult.sections_orphaned`. These are sections that were removed from the template; the project team must decide whether to keep or delete them.
5. All content **outside** any fence marker in the on-disk file → **preserved unconditionally**.

---

## Edge Cases

| Situation | Behavior |
|---|---|
| Legacy file (no fence markers) | `--merge` emits `MergeResult.parse_errors` entry; file is skipped. Use `--migrate` for a one-step safe migration: it tags the current state as `pre-fencing-snapshot`, then runs `--overwrite`. Use `--revert-migration` to undo. |
| Duplicate `section_id` in a single file | Parse error; merge is aborted for that file. Record in `MergeResult.parse_errors`. |
| Unclosed fence (BEGIN with no matching END) | Parse error; merge is aborted for that file. Record in `MergeResult.parse_errors`. |
| Mismatched `section_id` on END marker | Parse error; merge is aborted for that file. |
| `v=` value differs between on-disk and new render | Accepted without error in v=1. Future versions may define migration logic. |
| File is new (does not exist on disk) | Written fresh; no merge needed. |

---

## Versioning Strategy

The `v=` attribute is reserved for future format evolution. The current protocol version is `1`.

- If a future version of the module changes the merge algorithm in a breaking way, it will increment `v=` to `2` and provide a migration path.
- The merge engine must not reject files with `v=1` markers after a version increment; it must migrate them silently or with a one-time warning.
- Template authors must not change `v=` manually. The rendering engine sets it automatically from the current protocol version.

---

## What Is Not Fenced

The following content categories must **never** be placed inside fence markers:

- YAML front matter (`---` … `---`)
- The section manifest comment block itself
- The file's primary `#` heading
- Content that is entirely user-authored from first generation (e.g., `## Constitutional Rules` in `copilot-instructions.md`)
