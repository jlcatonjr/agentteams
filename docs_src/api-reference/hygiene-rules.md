# `hygiene_rules` — project hygiene rules in their own namespace

> *Source: `agentteams/hygiene_rules.py`*

A project numbers its own code-hygiene rules `CH-P01`, `CH-P02`, … in the user-owned **Project Extension Rules**
section of `references/code-hygiene-rules.reference.md`, after the managed `content` fence. The framework numbers
its rules `CH-01` onward and never uses the `CH-P` namespace, so a later release cannot collide with a project rule,
and `--update --merge` never rewrites that section.

Earlier catalogues told projects to "add CH-21+" inside the managed fence. The framework's own rules now run to
CH-31, so such a rule could share an id with a framework rule, and the next merge overwrote it.

## `migrate_project_rules(old_text, merged_text)`

Runs on the merge of `code-hygiene-rules.reference.md` (wired into [`emit`](emit.md)).

- **What moves.** Each `### CH-…` rule section of the old managed region that is not a current framework rule is
  appended, verbatim (comments and code blocks included), to the Project Extension Rules section. The section is
  created when absent, and a comment asks for the rules to be reviewed and renumbered.
- **A local rule that reuses a framework id** with its own title (for example a local `CH-29`) moves too. The
  framework's `CH-29` stays as rendered.
- **Framework headings** are matched against the packaged template, with each placeholder as a wildcard, so CH-03,
  which names the output directory, is never mistaken for a project rule.
- **Other sections** the new render lacks are named in a notice but not moved, so the managed region still restores
  non-rule text.
- **The notice** says the moved text is unreviewed and now kept across merges, and that a framework rule a release
  renamed or removed also lands there. It quotes any high-severity finding from `scan.scan_content` over the moved
  text.

Returns `(merged_text, notices)`; `emit` adds each notice to the run report, prefixed with the file path. A second
merge moves nothing.

## `duplicate_rule_ids(text)`

Returns `(rule id, first line, repeat line)` for each `### CH-…` heading used twice. HTML comments are ignored, so
the commented example does not count.

## `framework_ids_outside_fence(text)`

Returns `(rule id, line)` for each framework-range id (`CH-` followed by one or two digits) used after the managed
fence, that is, a project rule that should be renumbered `CH-P…`.

Both checks back the `--doctor` **Hygiene rule ids** section ([`doctor`](doctor.md)), which names the file and the
line numbers. `--check` does not report them yet.

## `visible_text(text)`

Returns `text` with each line-start HTML block comment blanked to its newlines, so line numbers are kept and a
commented-out example heading does not count as a rule.
