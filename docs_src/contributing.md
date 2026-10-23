# Public API and template policy

This page states two policies a pull request is held to: what counts as the public API, and the rules
every agent template follows. It is a reference for contributors and reviewers. For how to contribute,
start with [Contribute to agentteams](contribute/index.md). For the full review checklist, see
[What your pull request needs](contribute/what-your-pr-needs.md).

## The public API

agentteams uses a **curated public API**:

- The supported API surface is the set of modules and symbols documented under
  `docs_src/api-reference/`.
- Symbols not documented there are internal implementation details and may change without notice.
- A package or subpackage may have one API page (for example, the framework adapters) when its import
  surface is intentionally package-level.

The canonical module list is `docs_src/api-reference/index.md`. A pull request that makes an internal
module or symbol public must add it to that index, in the table of the group it belongs to, and add
complete API documentation for it. CI checks that documented signatures match the code. The
[stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md) states which parts of
the public API semantic versioning covers.

## Template rules

Templates live in `agentteams/templates/` and follow the placeholder conventions in
[`PLACEHOLDER-CONVENTIONS.md`](https://github.com/jlcatonjr/agentteams/blob/main/agentteams/templates/PLACEHOLDER-CONVENTIONS.md):

1. Use `{UPPER_SNAKE_CASE}` for tokens the pipeline resolves automatically.
2. Use `{MANUAL:UPPER_SNAKE_CASE}` for tokens that need a human to fill them in.
3. Every `.template.md` file starts with YAML front matter that has `name`, `description`,
   `user-invocable`, `tools` and `model`.
4. Every agent template contains an **Invariant Core** section marked with ⛔.

A new domain archetype is registered in the `select_archetypes()` selector in `agentteams/analyze.py`
and in the team-manifest schema. The [template authoring guide](template-authoring.md) has the full
procedure, including [registering a new template](template-authoring.md#registering-a-new-template).
