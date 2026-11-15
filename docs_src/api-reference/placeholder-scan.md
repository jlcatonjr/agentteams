# `placeholder_scan` — the checklist is built from the emitted text

> *Source: `agentteams/placeholder_scan.py`*

`SETUP-REQUIRED.md` used to come only from the brief-derived `manual_required_placeholders` that `analyze` collects,
so a `{MANUAL:X}` a template writes literally never reached it (IM item 13b, G-17-3). On dev, every example emitted
`PROJECT_REDTEAM_NOTES`, `ADJACENT_REPO_PATH` and `ADJACENT_REPO_AGENTS_PATH` without listing them. The `--post-audit`
check and the `--enrich` scan also skipped the last two, because they sit in inline code.

`render_pipeline._build_final_rendered`, the render step shared by generate, `--update` and `--check`, now calls
`complete_setup_required`. On every run, the checklist and the emit summary's count include every open token. The
`--post-audit` check (`audit._check_unresolved_manual_placeholders`) and `enrich.scan_defaults` use the same rule.
Nothing is refused.

| Name | Purpose |
|---|---|
| `INSTRUCTIONAL_TOKENS` | `TOKEN`, `UPPER_SNAKE_CASE`, `PLACEHOLDER_NAME`: the names templates use to explain placeholders. They are the only names never listed. `tests/test_placeholder_completeness.py` pins the files each may appear in, so a new instructional example, or a real placeholder named like one, fails a test. |
| `scan_manual_tokens(files)` | Each `{MANUAL:X}` in `(path, content)` pairs: first occurrence per token per file, with line and context, sorted by name and then file. It skips `SETUP-REQUIRED.md` and the instructional names, and has no backtick or code-fence rule. |
| `complete_setup_required(manifest, files)` | Appends each scanned token whose name the manifest doesn't list to `manual_required_placeholders` (in sorted order), then re-renders the `SETUP-REQUIRED.md` entry. Idempotent: run on its own output, it changes nothing. |

**Write policy unchanged.** Only the content of `SETUP-REQUIRED.md` changes, not how it is written:
- `--check` stays read-only.
- On `--update` (merge), an operator note appended to the file survives, and an edit inside the generated list is
  replaced. That was already true before this change.

A template that writes a new `{MANUAL:X}` name must list it in `templates/PLACEHOLDER-CONVENTIONS.md`, or add it to
`INSTRUCTIONAL_TOKENS`. The completeness test fails until it does one or the other.

The related tool change: for tools a brief names, `tool_metadata_catalog.WELL_KNOWN_TOOL_DOCS` supplies the official
`docs_url` of git, gh, PostgreSQL, Python, Docker, Terraform and GNU make. It is not looked up by Python import name.
