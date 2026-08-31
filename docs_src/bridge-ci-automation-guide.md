# Bridge CI Automation

## When to Use This Guide

Read this guide if you:

- Want bridge artifacts to refresh automatically on a schedule rather than manually
- Need to know when bridge artifacts have gone stale without polling for them
- Are setting up the `bridge-maintenance.yml` and `bridge-watchdog.yml` GitHub Actions workflows in your own repository

---

## Overview

When you use `--bridge-from` to create lightweight target-framework entrypoints for a `copilot-vscode` source team, those bridge artifacts need to stay synchronized as the source team evolves. Manual refresh is error-prone. AgentTeams ships two GitHub Actions workflows and a supporting shell script that automate this:

| Component | Purpose |
|---|---|
| `scripts/run_daily_bridge_maintenance.sh` | Refresh and verify bridge artifacts for all target frameworks |
| `.github/workflows/bridge-maintenance.yml` | Run the maintenance script on a daily schedule |
| `.github/workflows/bridge-watchdog.yml` | Open a GitHub Issue if bridge maintenance has been stale for 48+ hours |

---

## How `run_daily_bridge_maintenance.sh` Works

The script runs three phases:

### Phase 1 — Security Preflight

Calls `scripts/run_daily_security_maintenance.sh` before any bridge operations. If security maintenance fails, the entire bridge maintenance run stops with a critical exit. Bridge artifacts are never refreshed against stale threat intelligence.

### Phase 2 — Bridge Refresh and Check (per target framework)

For each target framework (`copilot-cli`, `claude`):

1. **Refresh:** Runs `--bridge-refresh` to rewrite bridge artifacts against the current source manifest.
2. **Check:** Immediately runs `--bridge-check` to confirm the refreshed artifacts are consistent with the source.

Both steps are non-critical by default: a refresh or check failure increments a warning counter but does not halt the run. This allows partial success when one target framework has issues but another is healthy.

### Phase 3 — Summary Output

Writes two files to `tmp/bridge-maintenance/`:

- `summary.md` — human-readable table of each step's result
- `status.json` — machine-readable status for downstream tooling

---

## Workflow: `bridge-maintenance.yml`

Runs daily at 05:41 UTC and can be triggered manually via `workflow_dispatch`.

```yaml
on:
  schedule:
    - cron: "41 5 * * *"
  workflow_dispatch:
```

The workflow:
1. Checks out the repository
2. Installs the package and test dependencies
3. Runs `scripts/run_daily_bridge_maintenance.sh`
4. Uploads `tmp/bridge-maintenance/` as an artifact (even on failure)

The uploaded artifact lets you inspect the maintenance summary without reading raw workflow logs.

---

## Workflow: `bridge-watchdog.yml`

Runs daily at 06:11 UTC (30 minutes after bridge maintenance) to verify the maintenance workflow is healthy.

The watchdog:

1. Queries the GitHub Actions API for the most recent successful `bridge-maintenance.yml` run
2. If no success exists, or if the latest success is older than 48 hours, opens a GitHub Issue with label `bridge-watchdog`
3. Deduplicates: only one open `"Bridge Maintenance Stale"` issue exists at a time
4. If bridge maintenance is fresh, it leaves state unchanged unless a stale issue is already open; in that case it comments and closes the issue

Required permissions:

```yaml
permissions:
  contents: read
  actions: read
  issues: write  # required to create/deduplicate stale-maintenance issues
```

---

## Setting Up in Your Repository

### Option A: Adapt scripts and workflows for your repository

1. Copy `scripts/run_daily_bridge_maintenance.sh` and `scripts/run_daily_security_maintenance.sh` into your project's `scripts/` directory.
2. Copy `.github/workflows/bridge-maintenance.yml` and `.github/workflows/bridge-watchdog.yml` into your project's `.github/workflows/` directory.
3. Update repository root guards in the script so they match your repository layout (the stock script has an `agentteams`-specific guard).
4. Update `SOURCE_DIR`, fallback source path, and target framework list in the script to match your team's topology.
5. Commit and push. The schedule activates immediately.

### Option B: Run maintenance on demand

Use `--bridge-check` in CI to validate bridge freshness on every pull request:

```yaml
- name: Check bridge freshness
  run: |
    python build_team.py \
      --bridge-from .github/agents \
      --framework claude \
      --output . \
      --bridge-check
```

Use `--bridge-refresh` in a separate job triggered on pushes to your main branch:

```yaml
- name: Refresh bridge artifacts
  run: |
    python build_team.py \
      --bridge-from .github/agents \
      --framework claude \
      --output . \
      --bridge-refresh
    git config user.email "ci@example.com"
    git config user.name "CI"
    git add references/bridges/
    git diff --cached --quiet || git commit -m "chore: refresh bridge artifacts"
```

---

## CLI Reference for Bridge Operations

| Flag | Purpose |
|---|---|
| `--bridge-from DIR` | Source agent directory |
| `--bridge-source-framework NAME` | Override source framework detection |
| `--framework NAME` | Target framework for bridge artifacts |
| `--bridge-check` | Read-only freshness check; exits 1 if stale |
| `--bridge-refresh` | Rewrite bridge artifacts to match current source manifest |
| `--output DIR` | Root directory for `references/bridges/` output |

`--bridge-check` and `--bridge-refresh` are mutually exclusive.

---

## Troubleshooting

### Bridge maintenance workflow runs but bridge-check reports stale immediately after refresh

**Cause:** The refresh ran against a source directory that was updated between the refresh and the check steps.

**Fix:** This is a race condition in repositories where source agents are also updated by CI. Add a checkout pin or run bridge maintenance only after source generation has completed.

### Watchdog opened an issue but bridge maintenance ran successfully today

**Cause:** The watchdog queries by `conclusion = success`, and the run may have completed with warnings (non-zero `noncritical_failures`) which GitHub still records as `success`. Review the `summary.md` artifact from the flagged run to confirm the actual outcome.

### Security preflight blocked bridge maintenance

**Cause:** Threat intelligence is stale and no valid waiver exists. See the [Security Hardening Guide](security-hardening-guide.md) for waiver creation and offline mode.
