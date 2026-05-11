# Section Fencing & Safe Merges

## When to Use This Guide

Read this guide if you:

- Are writing or modifying agent templates
- Want to run `--update --merge` and need to understand what will and won't change
- Are troubleshooting a merge that overwrote user-authored content (or failed to update stale generated content)
- Are adding project-specific agent routing or workflow extensions to a generated team

---

## What Section Fencing Is

When AgentTeams generates an agent file from a template, some regions of that file are owned by the module (rendered from templates and placeholder resolution) and some regions should remain under your control (project-specific routing rows, workflow extensions, team policy additions).

Without explicit boundaries, re-generation would either overwrite everything or be unable to safely update anything. Section fences solve this with machine-readable HTML comment markers that survive Markdown rendering invisibly.

---

## Fence Marker Syntax

```
<!-- AGENTTEAMS:BEGIN section_id v=1 -->
...template-generated content...
<!-- AGENTTEAMS:END section_id -->
```

Rules:

- The opening tag must exactly match `<!-- AGENTTEAMS:BEGIN <section_id> v=<version> -->` — no spaces inside `-->`.
- `section_id` is `lower_snake_case`, unique within a file.
- `v=<version>` is an integer starting at `1`. The merge engine accepts any value; the field is reserved for future migration tooling.
- The END marker carries only `section_id` — no version attribute.
- Markers are HTML comments and are invisible in Markdown previews and rendered documentation.
- Fenced regions must not be nested.

---

## FENCED vs USER-EDITABLE Designations

Every template that uses fences must include a **section manifest** immediately after the YAML front matter (if present):

```markdown
<!--
SECTION MANIFEST
| section_id          | designation   |
|---------------------|---------------|
| routing_table_rows  | FENCED        |
| available_workflows | FENCED        |
| authority_hierarchy | FENCED        |
| constitutional_rules| USER-EDITABLE |
-->
```

| Designation | Meaning |
|---|---|
| `FENCED` | Module-owned. Replaced on every `--merge` run. |
| `USER-EDITABLE` | Your content. Never touched by `--merge`. |

---

## The USER-EDITABLE Gap

For sections like `Domain Agent Routing`, the template generates a base set of rows (fenced), but you may need to add project-specific rows below the fence. AgentTeams templates intentionally leave a gap between fenced and user-editable regions:

```markdown
## Domain Agent Routing

| Content Area | Agent | Key Indicators |
|---|---|---|
<!-- AGENTTEAMS:BEGIN routing_table_rows v=1 -->
| Primary deliverables | `@primary-producer` | New work in `src/` |
<!-- AGENTTEAMS:END routing_table_rows -->
> ⚙️ **Project-specific rules and extension points go here.** This section is USER-EDITABLE.
```

When `--merge` runs, it replaces only the `routing_table_rows` fenced block. Anything you added after the END marker — including the entire USER-EDITABLE gap below — is preserved untouched.

---

## Partial Fencing

A fence may bracket only a subset of a section. For example, a routing table may have a template-generated row set but a user-editable header and footer:

```markdown
## My Custom Section

*(Header: user-authored, not fenced)*

<!-- AGENTTEAMS:BEGIN my_custom_rows v=1 -->
| Row generated from template | ... |
<!-- AGENTTEAMS:END my_custom_rows -->

*(Footer: user-authored, not fenced)*
```

Only the content between the markers is replaced on merge. The header and footer survive.

---

## How `--update --merge` Uses Fences

When you run:

```bash
agentteams --description brief.json --project /path --framework copilot-vscode --update --merge
```

The merge engine:

1. Reads the latest template for each agent
2. Renders the fresh placeholder-resolved content for every `FENCED` section
3. In each output file: locates the matching BEGIN/END markers, replaces only that block with fresh content
4. Leaves all content outside fenced regions untouched
5. Writes backup files before any change (unless `--no-backup` is set)
6. Appends newly introduced fenced sections when the target file is already fence-managed and missing those sections

Files with no fence markers at all are treated as legacy/unmanaged for merge. In that case, `--merge` skips the file and reports a merge error for that path (except select machine-managed file exceptions).

---

## Adding Fences to a Template

If you are [authoring a new template](template-authoring.md):

1. Identify which sections in your template are module-owned (should regenerate on every `--merge`)
2. Identify which sections are project-specific (should survive `--merge`)
3. Wrap module-owned sections in BEGIN/END markers
4. Add a section manifest at the top of the file
5. Mark project-specific regions as `USER-EDITABLE` in the manifest

For detailed registration steps, see the [Template Authoring Guide](template-authoring.md#registering-a-new-template).

---

## Configuration

No CLI flags are required for fencing to work — it is always active when `--merge` is used.

| Flag | Effect |
|---|---|
| `--merge` | Enable merge mode (required for fence-based selective update) |
| `--update` | Fetch latest templates before merge |
| `--no-backup` | Skip backup file creation before writes (not recommended for production) |
| `--dry-run` | Show what would change without writing any files |

---

## Best Practices

- **Always `--dry-run` first** when running `--update --merge` on a team that has significant user-authored content. Review the diff before committing.
- **Add your project-specific rows in the USER-EDITABLE gap**, not inside fenced blocks. Content inside a fenced block will be overwritten on the next merge.
- **Keep section manifests accurate.** If you add a new fenced section to a template, update the manifest. Merge behavior is driven by `AGENTTEAMS:BEGIN/END` fence markers; manifests are governance metadata for template authors and reviewers.
- **Version-bump the `v=` attribute** when making a breaking change to a fenced section's content contract (changing the column structure of a table, for example). This signals to reviewers that a migration may be needed.

---

## Troubleshooting

### Merge overwrote my custom content

**Cause:** Your content was placed inside a `FENCED` block.

**Fix:** Move custom content to below the `<!-- AGENTTEAMS:END ... -->` marker, into the USER-EDITABLE gap.

### Merge left stale generated content

**Cause:** The output file was created before fence markers were introduced (pre-fencing era file). It has no markers to match.

**Fix:** Run `--migrate` to add pre-fencing snapshots and prepare the file for fence-based updates. See the [Migration Guide](migration-guide.md).

### "WARN: fenced section found in template but absent from output file"

**Cause:** The template was updated to add a new fenced section, but the output file predates that section.

**Fix:** Review the diff. In current merge behavior, new fenced sections are appended when the output file already has valid fence markers. If the file has no fences at all, use `--overwrite` or run `--migrate` first.
