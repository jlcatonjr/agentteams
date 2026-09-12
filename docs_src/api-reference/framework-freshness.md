# `framework_freshness` — AgentTeamsModule

Cross-framework render-freshness scan.

Targets one slice of the silent render-staleness failure class: an operator regenerates one provider render for a project (e.g. `copilot-vscode` under `.github/agents/`) and never regenerates a sibling render (e.g. `claude` under `.claude/agents/`), so the forgotten render's build-log baseline predates a template change with **no signal**. The per-framework `--check` catches this only if someone remembers to run it against that specific framework; nothing surfaces the divergence across a project's renders at once. This module walks a project tree, finds every provider render (each carries its own `references/build-log.json`), and reuses [`drift.detect_drift`](drift.md) across all of them. It is strictly read-only — it never renders templates and never writes files. Surfaced via the `--framework-freshness` CLI flag (see the [CLI reference](../cli-reference.md)) with the same exit-code-1-on-stale contract as `--check`.

**Scope limitation (template-hash-based).** `detect_drift` compares a render's build-log `template_hashes` against the current template files, so this scan detects a render whose build-log genuinely *predates* a template change. It does **not** detect the case where an `--update --merge` refreshed the build-log to current hashes but `preserve_on_shrink` kept a stale operator-enriched fenced body (the recorded hash then equals the current hash). Catching that requires a fence-version-aware signal — a separate, deeper change to the merge engine, tracked as future work. A render whose build-log predates hash tracking is reported **unverifiable** (it cannot be compared), not stale.

> *Source: `agentteams/framework_freshness.py`*

---

## Classes

### `FrameworkFreshness`

> *Source: `agentteams/framework_freshness.py`*

Freshness verdict for a single provider render directory.

**Attributes:**

- `framework` (`str`) — Framework name recorded in the render's build-log (`"unknown"` when the log omits it).
- `agents_dir` (`Path`) — The render's agents directory (parent of `references/`).
- `generated_at` (`str | None`) — ISO-8601 timestamp the render recorded, or `None` for an older build-log that predates provenance stamping (build-log schema < 1.5).
- `agentteams_version` (`str | None`) — Generator version the render recorded, or `None`.
- `changed_templates` (`list[str]`) — Template paths whose content changed since this render's build-log baseline.
- `missing_templates` (`list[str]`) — Templates recorded by the render but absent on disk now.
- `unverifiable` (`bool`) — `True` when the build-log predates template-hash tracking, so drift cannot be computed.

**Properties:**

- `is_stale` (`bool`) — `True` when this render *provably* lags the current templates. An `unverifiable` render is never reported stale.

---

### `FrameworkFreshnessReport`

> *Source: `agentteams/framework_freshness.py`*

Aggregate freshness verdict across every render found in a project.

**Attributes:**

- `renders` (`list[FrameworkFreshness]`) — One entry per discovered live render.
- `errors` (`list[str]`) — Per-render load errors (a malformed build-log is recorded here, never aborting the scan).

**Properties:**

- `stale` (`list[FrameworkFreshness]`) — The renders that lag the current templates.
- `has_stale` (`bool`) — `True` when at least one render is behind.
- `freshest` (`FrameworkFreshness | None`) — The render with the newest `generated_at`, if any is stamped.

**Methods:**

- `exit_code()` (`int`) — `1` when any render is stale, else `0`.

---

## Functions

### `discover_render_dirs(project_root)`

> *Source: `agentteams/framework_freshness.py`*

Find every *live* provider render directory beneath `project_root` — any directory containing `references/build-log.json`. Backup, snapshot, git-worktree, canonical-hub, scratch (`tmp/`), VCS, build, and virtualenv trees are excluded (see `_EXCLUDE_DIR_PARTS`) so the scan reports only the renders an operator actually maintains, not the copies the tool makes of them.

**Args:**

- `project_root` (`Path`) — The project root to scan.

**Returns:** `list[Path]` — Sorted, deduplicated agents directories (the parent of each `references/`).

---

### `scan(project_root, templates_dir)`

> *Source: `agentteams/framework_freshness.py`*

Scan `project_root` for provider renders and report which lag the current templates. Reuses [`drift.detect_drift`](drift.md) per render directory — read-only, no rendering, no writes.

**Args:**

- `project_root` (`Path`) — Project root to scan for renders.
- `templates_dir` (`Path`) — Current templates directory to compare each render against.

**Returns:** `FrameworkFreshnessReport` — One entry per discovered render, plus any per-render load errors (which never abort the whole scan).

---

### `print_report(report, *, project_root)`

> *Source: `agentteams/framework_freshness.py`*

Print a human-readable freshness report to stdout, rendering each render path relative to `project_root` and naming the freshest render so lagging ones are obvious.

**Args:**

- `report` (`FrameworkFreshnessReport`) — The report from `scan()`.
- `project_root` (`Path`, keyword-only) — Project root, used to render paths relative to it.

**Returns:** `None`
