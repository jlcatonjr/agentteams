# `brief_schema` — the project-description schema, checked on load

> *Source: `agentteams/brief_schema.py`*

`ingest.load` runs `schemas/project-description.schema.json` on every JSON description it reads (IM item 13a,
G-17-2). Before that, `ingest.validate` checked only `project_goal`, component slugs and tool `version_source`, and the
schema had drifted from the code: 98 of 159 descriptors in the workspace failed it. With the schema brought in line,
23 still fail, and those are real brief problems.

**Warn only, this release.** A mismatch prints one `[WARN]` per description file per process, with the count and the
first five issues. Nothing is refused and the exit code never changes, `--check` included. Refusal comes in a later
release, once the census is clean ([known limitations](../known-limitations.md)).

| Name | Purpose |
|---|---|
| `known_archetypes()` | The archetype slugs agentteams renders: every `templates/domain/<slug>.template.md` that is an agent, not a `tool-*.doc` or `*-reference` template. The schema's `x-agentteams-known-archetypes` list equals it, and `tests/test_brief_schema_on_load.py` keeps them equal, so the list can't drift again. |
| `schema_errors(description)` | Each violation as `path: message`, in document order; empty when the description conforms. |
| `unrendered_archetypes(description)` | The `selected_archetypes` slugs with no domain template. agentteams does not render them; another layer (researchteam's `interpretation-advisor`, for example) may provide them. |
| `warn_on_load(path, description)` | Prints the `[WARN]` for schema issues (each clipped to 160 characters) and an `[INFO]` line for unrendered archetypes, once per path per process. An error while checking is a `[WARN]` too; it never raises. |
| `schema_warning_lines(output)` | The schema warnings in one render's captured output, without the prefix. The fleet stores them in each target's `schema_warnings` (on the first target of a workspace, since the warning prints once per process). |

Schema changes in the same release:
- `selected_archetypes` takes any lowercase slug, so an archetype another layer provides is not a schema error.
- `tools[].category` accepts `build` and `infrastructure`. Both are passive unless the tool is a known specialist by
  name, as the code already treated them.
