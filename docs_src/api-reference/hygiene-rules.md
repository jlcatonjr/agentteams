# `hygiene_rules` — project hygiene rules in their own namespace

> *Source: `agentteams/hygiene_rules.py`*

A project numbers its own code-hygiene rules `CH-P01`, `CH-P02`, … in the user-owned **Project Extension Rules**
section of `references/code-hygiene-rules.reference.md`, after the managed `content` fence. The framework numbers
its rules `CH-01` onward and never uses the `CH-P` namespace, so a later release cannot collide with a project rule,
and `--update --merge` never rewrites that section.

Earlier catalogues told projects to "add CH-21+" inside the managed fence. The framework's own rules now run to
CH-31, so such a rule could share an id with a framework rule, and the next merge overwrote it.

## `migrate_project_rules(old_text, merged_text)`

Runs on the merge of `code-hygiene-rules.reference.md` (wired into [`emit`](emit.md)). Every `### …` rule section
of the old managed region whose heading the new render does not carry is a project rule: it is appended, verbatim,
to the Project Extension Rules section (created when absent), with a comment asking for it to be renumbered. A local
rule that reuses a framework id with a different title (for example a local `CH-29`) is moved too, and the
framework's `CH-29` is left as rendered.

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
