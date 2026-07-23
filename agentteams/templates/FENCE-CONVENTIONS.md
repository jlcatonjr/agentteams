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
3. **`v=<version>`** is an integer starting at `1`, present only on the BEGIN marker. It is a **per-section, author-maintained content-revision marker**: the template author increments it by 1 whenever that section's fenced content is deliberately revised (see Versioning Strategy below). The merge engine does not read, validate, or compare the integer — it accepts any `v=` value without error.
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
| Legacy file (no fence markers) | The default `--update --merge --yes` **auto-retrofits** a `content` fence onto the unfenced body (a `.bak` backup is written first), so future merges can update it in place. Opt out with `--no-add-fence-markers` to leave the file untouched. (`--migrate` remains available for an explicit `pre-fencing-snapshot`-tagged `--overwrite`; `--revert-migration` undoes it.) |
| Duplicate `section_id` in a single file | Parse error; merge is aborted for that file. Record in `MergeResult.parse_errors`. |
| Unclosed fence (BEGIN with no matching END) | Parse error; merge is aborted for that file. Record in `MergeResult.parse_errors`. |
| END marker with no matching BEGIN | Orphan END is ignored; the file is treated as unfenced (no parse error). |
| Mismatched `section_id` on END marker | Parse error; merge is aborted for that file. |
| `v=` value differs between on-disk and new render | Expected, not an error — merge replaces the section body by `section_id` and adopts the new render's `v=` verbatim (see Versioning Strategy). |
| File is new (does not exist on disk) | Written fresh; no merge needed. |

---

## Versioning Strategy

The `v=` attribute is a **per-section, author-maintained content-revision marker**, not a global merge-protocol version. Each `section_id` carries its own `v=`, independent of every other section — including other copies of the same `section_id` in different templates. `agentteams/fences.py` never reads, compares, or sets this integer; the merge engine matches purely by `section_id` and replaces content, so `v=` carries no runtime behavior. This corrects an earlier draft of this document that described an engine-managed protocol-version mechanism — no such mechanism was ever implemented, and none is currently planned.

- **Bump a section's `v=` by 1 whenever you deliberately revise its fenced content** (add a rule, materially reword guidance, etc.) — this is the concrete action behind AUTHORING-GUIDE.md §3.3's instruction to route a contract revision through "a version bump" rather than a silent in-place edit.
- Start a newly-added section at `v=1`; don't bump on first introduction.
- Sibling copies of the same `section_id` across different templates legitimately sit at **different** `v=` values when revised on different schedules (e.g. `memory_index_consultation` currently ranges `v=2`–`v=4` across templates) — this reflects real, asynchronous drift, not an error to reconcile on sight.
- A `v=` mismatch between an on-disk file and a freshly-rendered template is never a merge error — see the Edge Cases table above.

---

## What Is Not Fenced

The following content categories must **never** be placed inside fence markers:

- YAML front matter (`---` … `---`)
- The section manifest comment block itself
- The file's primary `#` heading
- Content that is entirely user-authored from first generation (e.g., `## Constitutional Rules` in `copilot-instructions.md`)
