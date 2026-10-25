# Project tour

This page maps the repository for contributors: where each part of agentteams lives and which parts
need extra care. For how the pieces work together, read [How it works](../how-it-works.md).

## The main directories

| Path | What it holds |
|---|---|
| `agentteams/` | The Python package. |
| `agentteams/templates/` | The agent templates every team is rendered from. See [Template authoring](../template-authoring.md). |
| `agentteams/frameworks/` | One adapter per supported agent tool. |
| `agentteams/cli/` | The command-line interface, split out of `build_team.py` by concern. |
| `agentteams/schemas/` | JSON schemas for project briefs and team manifests. |
| `build_team.py` | The command-line entry point. |
| `examples/` | Example project briefs. |
| `tests/` | The test suite. See [Tests and CI](tests-and-ci.md). |
| `docs_src/` | This website's source. See [Working on the docs site](docs-site.md). |
| `scripts/` | Maintenance and CI helper scripts. |

## The generation pipeline

A brief passes through four stages, each its own module:

1. `ingest` reads and validates the brief.
2. `analyze` selects the agents and builds the team manifest.
3. `render` fills the templates.
4. `emit` writes the files for the chosen framework.

The [API reference](../api-reference/index.md) documents each module, grouped the same way as the site
navigation.

## Paths that need extra care

Some files enforce the security model, so changes to them get closer review:

- **CI workflows** (`.github/workflows/`) and the security policy are owned by the maintainer in
  `.github/CODEOWNERS`.
- **Modules that enforce the constitution** are covered by an integrity hash manifest; see the
  [`integrity` API page](../api-reference/integrity.md). Changing one means updating the manifest, and
  reviewers will ask why.
- **Security references and gates**, such as `agentteams/security_refs.py`, are listed in CODEOWNERS
  too.

If your change touches any of these, say so in the issue before starting.

## Where to look next

- [Tests and CI](tests-and-ci.md)
- [What your pull request needs](what-your-pr-needs.md)
- [How it works](../how-it-works.md)
