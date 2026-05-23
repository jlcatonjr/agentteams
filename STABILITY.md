# API Stability Policy

Starting with **1.0.0**, `agentteams` follows [Semantic Versioning 2.0.0](https://semver.org/).
This document enumerates the public surface that the version contract applies to
and the surfaces that are explicitly **not** covered.

## What SemVer covers

A change is **breaking** (requires a major version bump) if it removes or
changes the contract of any of the following:

### 1. Command-line interface
- The `agentteams` console-script entry point.
- Any documented flag in [`agentteams.1`](agentteams.1) or `agentteams --help`.
- Exit codes documented in the man page.
- The `build-team` deprecation alias is retained through the **1.x** series and
  will be removed at **2.0** (it emits a stderr deprecation notice on every
  invocation).

### 2. Python API
The supported import surface is everything documented under
[`docs_src/api-reference/`](docs_src/api-reference/). Notable modules:
- `agentteams.ingest`, `agentteams.analyze`, `agentteams.render`, `agentteams.emit`
- `agentteams.security_refs`, `agentteams.model_routing`, `agentteams.drift`
- `agentteams.memory_index`, `agentteams.fence_inject`, `agentteams.liaison_logs`
- `agentteams.frameworks.{copilot_vscode, copilot_cli, claude}`
- `build_team.main`, `build_team.__version__`

Symbols not documented in `docs_src/api-reference/` are **internal** and may
change without notice — including modules whose name starts with `_`,
functions starting with `_`, and any helper not listed in the reference docs.

### 3. JSON schemas
The committed [`schemas/`](schemas/) directory is part of the contract.
Backwards-incompatible changes to any schema (removing a required field,
changing a field's type, narrowing an enum) require a **major** bump.
Additive changes (new optional fields, new enum values that consumers are
expected to ignore-or-pass-through) are **minor**.

**Limitation (known, 1.0):** schemas do not yet carry a per-file
`schema_version` field. The package version is the only schema version.
Per-schema versioning is planned for a future minor release; until then,
schema changes are governed by package SemVer.

### 4. On-disk artifacts
The following file formats produced by `agentteams` are part of the
contract — readers (other tools, CI, downstream automation) may rely on
their structure:
- `_build-description.json`
- `_intake-notes.md` fence markers
- The build-log header / structural-diff format
- The `pre-fencing-snapshot` git-tag protocol used by `--migrate` /
  `--revert-migration`
- Agent file fence markers (`<!-- agentteams:fence ... -->`) — adding new
  fence kinds is minor; renaming or removing existing kinds is major.

### 5. Template contracts
Template *names* under [`agentteams/templates/`](agentteams/templates/) and
their slot variables (`{UPPER_SNAKE_CASE}` tokens) are part of the contract.
Renaming a template file or removing/renaming a slot is breaking.
Adding optional slots with default values is non-breaking.

## What SemVer does NOT cover

The following may change in any release — pin exactly if you depend on them:
- Behavioral-drift fingerprint algorithm (`FINGERPRINT_ALGO_VERSION` is
  versioned independently; bumps trigger build-log healing, not SemVer).
- Wording of human-readable output (`print(...)` strings, prose in
  diagnostics, progress messages).
- File layout under `references/`, `tmp/`, `workSummaries/`, `docs/`,
  `docs_src/`, `examples/` — these are repository-internal and not shipped.
- Test fixtures and test-only helpers under `tests/`.
- Memory-index relevance evaluation corpus and thresholds (used only for
  developer-side calibration; CI skips the relevance test).
- The set and content of generated agent files for the `agentteams` repo's
  own team (under `.github/agents/`, gitignored).
- Skill files emitted under `.claude/skills/` (their *existence and contract*
  is stable; their internal phrasing is not).

## Deprecation policy

A deprecation must:
1. Land in a **minor** release.
2. Emit a runtime warning when the deprecated surface is used.
3. Be documented in the `[Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md)
   with the planned removal version.
4. Survive at least one full minor cycle before removal.

Removal of a deprecated surface always requires a **major** bump.

## Release cadence

- **Patch** (1.0.x): bugfixes, doc fixes, security fixes. No new public
  surface. No behavior change for any documented input.
- **Minor** (1.x.0): new features, new flags, new templates, new schemas,
  additive changes to existing schemas, new deprecation notices.
- **Major** (x.0.0): removals, contract changes, schema breaking changes,
  removal of any previously-deprecated surface.

## Pre-release versions

`-rc.N` suffixes (`1.0.0rc1`, `1.0.0rc2`, …) are soak releases. They are
considered functionally complete but may receive bugfix-only changes
before promotion to the final version. No new features land between
`-rc.N` and the matching final release.

## Reporting issues

Stability-related issues (a documented behavior changes silently across
a minor version, an undocumented internal becomes load-bearing) should
be filed at <https://github.com/jlcatonjr/agentteams/issues> with the
label `stability`.
