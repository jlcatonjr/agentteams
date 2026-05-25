# `framework_research` — AgentTeamsModule

Build live framework-drift placeholders and propose module-core
documentation updates for the daily pipeline.

Mirrors the contract of [`security_refs.build_security_placeholders`](security-refs.md)
so the daily pipeline's upstream-research stage is transmitted to
consumer repositories through `--update --merge`. Detects drift
between published Claude Code / GitHub Copilot agent-infrastructure
specifications and this project's framework-adapter constants;
produces an advisory observation stanza for the relevant expert
references when drift is observed.

> *Source: `agentteams/framework_research.py`*

---

## Constants

### `FRAMEWORK_REGISTRY`

> *Source: `agentteams/framework_research.py`*

Dict[str, dict] keyed by framework id. Each entry declares:

- `label` — human-readable name.
- `source_url` — the upstream documentation URL to fetch.
- `expert_ref` — path (relative to repo root) to the tracked expert
  reference where the observation stanza is appended.
- `expected_keys` — allow-listed front-matter keys whose presence is
  scanned for in the upstream payload.
- `expected_locations` — allow-listed filesystem locations whose
  mention is scanned for.

Three frameworks ship: `claude`, `copilot_vscode`, `copilot_cli`.

### `SNAPSHOT_REL`

> *Source: `agentteams/framework_research.py`*

`"tmp/daily-pipeline/framework-research/latest.json"` (gitignored —
operator-local state). Single canonical path the refresh and build
functions share.

### `STALE_DAYS`

> *Source: `agentteams/framework_research.py`*

`7` — threshold past which the rendered reference shows a STALE
banner.

### `ALLOWED_EXPERT_REFS`

> *Source: `agentteams/framework_research.py`*

The allow-list `apply_module_patch` enforces. Currently:

- `references/claude-agent-infrastructure-expert.md`
- `references/copilot-agent-infrastructure-expert.md`

Any change targeting a path outside this set is rejected.

---

## Functions

### `refresh_snapshot(repo_root, offline=False) -> dict`

> *Source: `agentteams/framework_research.py`*

Fetches (or reuses) the multi-framework snapshot.

**Args:**

- `repo_root` (`Path`) — agentteams repository root. The snapshot is
  written to `<repo_root>/tmp/daily-pipeline/framework-research/latest.json`
  (gitignored).
- `offline` (`bool`, default `False`) — when True, skip network
  fetches and reuse the prior cached snapshot if one exists.

**Returns:** the snapshot dict that was written (or the cached
snapshot when all fetches were skipped/failed). Schema 1.1:

- top-level `framework`, `source_url`, `generated_at`,
  `generated_on`, `fetch_status`, `upstream_tokens`,
  `local_adapter`, `keys_diff` — preserved for back-compat with the
  schema-1.0 single-framework readers.
- `frameworks` — dict keyed by framework id; each value carries
  `label`, `source_url`, `expert_ref`, `expected_keys`,
  `fetch_status`, `upstream_tokens`.

**Notes:**

- Stdlib-only fetch (`urllib.request`); no third-party dependency.
- Per-framework failures degrade individually to `fetch_status:
  "failed"` without affecting other frameworks.
- When every framework's fetch is unsuccessful, returns the prior
  cached snapshot rather than overwriting it with empty data.

### `build_framework_placeholders(output_dir, offline=True) -> dict[str, str]`

> *Source: `agentteams/framework_research.py`*

Return placeholder values for the `framework-watch.reference.md`
template.

**Args:**

- `output_dir` (`Path`) — consumer team output directory. Used only
  for symmetry with `security_refs.build_security_placeholders`; the
  snapshot itself lives under the agentteams module tree.
- `offline` (`bool`, default `True`) — when True, reuse the
  existing snapshot without re-fetching. The build pipeline defaults
  to True so consumer repos do not need network access on every
  `--update --merge`. The daily pipeline passes `False` once per day.

**Returns:** dict with these placeholders:

- `FRAMEWORK_RESEARCH_FRAMEWORK`
- `FRAMEWORK_RESEARCH_SOURCE_URL`
- `FRAMEWORK_RESEARCH_GENERATED_ON`
- `FRAMEWORK_RESEARCH_FETCH_STATUS`
- `FRAMEWORK_RESEARCH_TABLE` — markdown table with one row per
  framework.
- `FRAMEWORK_RESEARCH_STALE_BANNER` — empty when fresh, an explicit
  warning when older than `STALE_DAYS`.
- `FRAMEWORK_RESEARCH_DIFF_SUMMARY`.

### `propose_module_patch(repo_root) -> dict`

> *Source: `agentteams/framework_research.py`*

Produce a v1 module-core patch proposal. Targets the expert
reference Markdown files only — never mutates
`agentteams/frameworks/*.py` constants.

**Args:**

- `repo_root` (`Path`) — agentteams repository root.

**Returns:** dict with `schema_version: "1.1"`, `generated_at`,
`snapshot_generated_on`, `frameworks` (list of framework ids
covered), and `changes` — a list of one change per affected expert
reference. Each change is:

```
{
  "frameworks": ["claude"],
  "path": "references/claude-agent-infrastructure-expert.md",
  "operation": "append_or_replace_section",
  "section_heading": "## Observed Upstream Tokens — `claude` (Daily Pipeline)",
  "old_text": "...",
  "new_text": "..."
}
```

When the rendered observation block matches the file on disk,
returns `{"changes": [], "reason": "no drift to record"}`.

### `apply_module_patch(proposal, repo_root) -> dict`

> *Source: `agentteams/framework_research.py`*

Apply a proposal in place.

**Args:**

- `proposal` (`dict`) — the structure returned by `propose_module_patch`.
- `repo_root` (`Path`) — agentteams repository root.

**Returns:** `{"applied": [list of paths]}` on success.

**Refusal semantics:**

- Raises `RuntimeError` when a `change["path"]` is not in
  `ALLOWED_EXPERT_REFS`, or `change["operation"]` is not
  `append_or_replace_section`.
- Raises `RuntimeError` when `CI=true` is set in the environment
  AND `AGENTTEAMS_ALLOW_CI_APPLY=1` is NOT set. The
  `.github/workflows/framework-auto-update.yml` workflow sets the
  marker intentionally; never set it elsewhere.

---

## Operator workflow

```bash
# Refresh the snapshot (or reuse the cache offline)
python scripts/research_claude_code_docs.py [--offline]

# Generate a proposal — writes tmp/.../proposal.json (gitignored)
python scripts/research_claude_code_docs.py --propose

# Apply locally — runs targeted pytest, reverts on failure
python scripts/research_claude_code_docs.py --apply
```

In CI, the supervised auto-PR pattern in
`.github/workflows/framework-auto-update.yml` performs the same
sequence on a transient branch and opens a PR for human review.
Branch protection on `main` ensures the PR cannot be merged without
explicit operator action.
