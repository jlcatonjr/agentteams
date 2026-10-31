# `agentteams.vulnscan`: vulnerability scanning and agent-driven remediation

`vulnscan` checks your repositories, this workstation and your Linux servers for dependencies and
runtimes with published vulnerabilities, every day. It works out the smallest safe upgrade for each,
and can let an AI agent team carry those upgrades through to pull requests. The agents only decide.
Every change is made by a deterministic executor, and only under grants you sign with your own key.

It answers three questions:

1. **Are we running a version with a published vulnerability?**
2. **What is the smallest safe upgrade?**
3. **Has this check actually been running?**

No vulnerability data is built in. Each run reads live sources (OSV.dev for package advisories,
including malicious-package `MAL-*` records; nodejs.org, www.php.net and endoflife.date for runtimes), so
the same installation catches next month's disclosures without an upgrade.

```bash
python -m agentteams.vulnscan scan ~/code        # every repository under ~/code
python -m agentteams.vulnscan runtimes           # Node.js, PHP and Python on this machine
python -m agentteams.vulnscan fix ./my-repo      # dry-run fix plan for one working tree
```

Like the rest of agentteams, the package uses only the standard library. Agent mode and the confined
tools container also need Docker; `open-pr` needs the GitHub CLI (`gh`); server audits need `ssh`.

---

## Capabilities

### Scanning

- **Repositories.** `scan PATH` walks a repository or a folder of repositories and reads exact versions
  from the manifests it supports:

  | Ecosystem | Files |
  |---|---|
  | PyPI | `requirements*.txt` / `.lock` / `.in` `==` pins (also with a prefix such as `ci-requirements.txt`), `poetry.lock`, `uv.lock`, `Pipfile.lock`, and the PEP 621 `==` pins of a `pyproject.toml` next to requirements files |
  | npm | `package-lock.json`, `npm-shrinkwrap.json` (v1–v3), `yarn.lock` (classic and berry) |
  | Packagist | `composer.lock` |
  | crates.io | `Cargo.lock` (v1–v4; crates from the crates.io index only, every one counted as runtime; fixes are plan-only) |
  | Go | `go.mod` (`require` with `replace` applied; the Go standard library is not judged; fixes are plan-only) |
  | npm | `pnpm-lock.yaml` (v6, v9) and text `bun.lock` too (fixes are plan-only) |
  | RubyGems, Maven, NuGet | `Gemfile.lock` (rubygems.org gems), `gradle.lockfile`, `packages.lock.json` (fixes are plan-only) |

  Vendored and generated trees (`node_modules`, virtualenvs, build output, Lean's `.lake` package cache) and
  linked git worktrees are skipped. Nothing is passed silently: a manifest with no exact versions, such as an unpinned
  `requirements.txt` or a `package.json` with no lockfile, is reported as *unscannable*. So are the
  manifests of ecosystems vulnscan finds but does not read (a `go.sum` without `go.mod`, Maven `pom.xml`, Gradle
  and NuGet project files without their lockfile, `Gemfile` without `Gemfile.lock`, SwiftPM, CocoaPods, Pub, Hex,
  `bun.lockb`, Deno, CRAN, Hackage, Julia, Conan, vcpkg, conda, Lean, container images), so a project holding only
  those shows up as a coverage gap rather than
  as clean. For container manifests (Dockerfiles, compose files) the base images are judged: an end-of-life
  release line or OS (`python:3.9-slim`, `-buster`), a pinned patch tag behind its line, and an unpinned
  `latest` are findings. The image contents are not scanned.
  With `scan --unpinned-ranges` (off by default), the ranges of unpinned Python requirements are also
  checked against every advisory for each name; such a file stays unscannable, with a coverage note.
- **This machine's runtimes.** `runtimes` checks the installed Node.js, PHP and Python against security
  releases and end-of-life dates.
- **Servers.** The packaged server kit audits Linux hosts over ssh, read-only: installed packages,
  runtimes, host checks and indicators of compromise. `inventory LOG --host H` analyses an audit log you
  collected yourself.
- **Severity.** vulnscan scores CVSS v3.0/v3.1 and v4.0 vectors itself, takes the higher of that and the
  advisory's severity label, and counts an advisory with neither as `unscored_as` (default HIGH) rather
  than quietly treating it as moderate. A malicious package or a compromise indicator always exits 4.
- **CI-friendly exit codes.** `0` clean, `1` findings at or above `--fail-on`, `2` usage or setup
  error, `3` partially unscanned (a feed failed), `4` malicious package or compromise indicator.
  `--json` gives a machine-readable report.

### Fixing

- `fix PATH` plans the smallest upgrade that clears each finding, preferring the same major version
  (`allow_major` widens it). `fix PATH --apply` makes the edit in a working tree you control.
- Every fix remediates a vulnerability the scan detected, so Rule S-10's remediation carve-out applies:
  the 14-day release cooldown and the latest-stable rule do not hold it. A requirements fix still moves
  only to a final release that OSV reports clean.
- Edits are narrow by construction. A requirements file may only have `==` versions raised in place to a
  final release. An npm lockfile may not change its root package, and every entry must still resolve to
  the public npm registry over HTTPS. Hash-pinned requirements files are never edited.
- git runs hardened (no hooks, fsmonitor, or system and global config). A repository whose own config
  defines a filter driver or a conditional include is refused, apart from git-lfs's standard filter.

### Daily runs

- `init` writes a starter config; `daily` runs one full pass: scan every root, runtimes and servers,
  then write `summary.md` / `summary.json` marking what is NEW since the last run, with fix plans per
  repository and a *Remediation effectiveness* section (fixed and new findings, the PRs opened, their age
  and CI state, PRs ready to merge).
- `schedule install` sets up the daily job (launchd on macOS, else a systemd user timer, else cron).
  On macOS a catch-up agent re-runs a scan missed while the machine was off. Every command except
  `schedule install` and `schedule remove` warns when the last run is stale, missing, partial or found
  compromise indicators.
- Notifications (desktop, or a command of your choosing) carry fixed text only: ids, counts and known
  words, never advisory or repository text.

### Project enrolment

- `enroll` registers this machine's projects (grouped by owner, answered in bulk) in a local registry.
  Each project's own brief can set its level (`off`, `scan`, `remediate`), exclude paths, and name
  private packages that are never sent to OSV.dev. A brief can narrow what leaves the machine, never what
  is checked: the operator's enrolment wins.
- `roots` shows every candidate scan root and why it is scanned or skipped; `status --project DIR` shows
  one project's coverage, last scan, open findings, recorded fixes and grant coverage, without the
  network. Its `launcher` line says whether the project's `sandbox/confine-run.sh` is the current
  agentteams release, an older one (and whether a newer one is a security fix), modified (no released
  version: review it) or missing. Manifests the scan never reads (the config's `exclude`, the brief's
  `exclude_paths`) are not listed as gaps.

### Agent-driven remediation (opt-in)

With `agent.enabled`, each daily run hands the scan to a generated agent team running in a Docker
container on an internal-only network:

- **Runtimes.** goose (default), Claude Code, the GitHub Copilot CLI or Codex, each through its own
  driver, image build and isolation self-test (`agent-image build`, `agent-selftest`).
- **The agents' tools.** An MCP server inside the container lets the agents read the summary, findings
  and fix plans, and queue requests: `stage-fix`, `open-pr`, `close-superseded`, `server-audit`,
  `server-dryrun`, an escalation, or the run report. Everything they read from advisories or
  repositories is labelled as data.
- **Egress gateway.** The only route out of the container. It holds the model API key (the agent
  container has a dummy one), allows one route and the configured models, refuses requests carrying
  secrets and redacts identifying data (DLP), and enforces a spend cap and a token cap on every run.
  The launcher adds a time cap: at `agent.max_minutes` the container is removed.
- **Executor.** Runs on your account, outside the container, and serves the queued requests. It performs
  one only when a valid operator-signed grant covers that exact operation and target with uses left, no
  `@security` HALT is open, and no escalation blocks the target. It computes every change itself:
  `stage-fix` stages pin and lockfile upgrades in its own worktree, `open-pr` pushes a new `vulnscan/*`
  branch and opens a pull request. It never merges and never force-pushes.
- **Delivery modes.** Per project: open a PR (`pr`), write the plan only (`plan`), or hand the staged
  fix to the project's own orchestrator and wait for its approval (`handoff`).
- **Confinement.** git, npm and the server kit run in a separate tools container whose egress proxy
  allows only the one host each operation needs (`tools-image build`, `tools-selftest`).

### Operator controls

- **Grants.** `grant sign` issues a standing remediation grant (at most 30 days and 500 uses, scoped to
  repository roots, exact hosts and named operations), signed with your Ed25519 key. `grant sign
  --from-registry` proposes the projects you enrolled at `remediate`. `publish-grant` separately allows
  pushing run reports to a private ops repository.
- **Ledger.** Every decision is recorded before and after it runs, in an append-only, MAC-chained ledger
  whose key is never mounted into a container. A ledger that fails verification refuses everything.
- **Review and steering.** `review` is the after-action review (with an overdue notice until you `--ack`
  it); `halts` lists open escalations and HALTs and how to clear each; `resolve` clears an escalation;
  `say` leaves notes the agents read on the next run; `agent-chat` resumes a past run's goose or Claude Code session (Copilot and Codex runs keep none).

### What leaves your machine

| Destination | What |
|---|---|
| OSV.dev, PyPI, nodejs.org, www.php.net, endoflife.date | package names and versions; runtime versions |
| The npm registry (`stage-fix` on an npm lockfile) | the lockfile's dependency tree, from the tools container |
| The model API, through the gateway (agent mode only) | what the agents read and write: findings, fix plans, repository aliases, your notes, after DLP |
| GitHub (`open-pr`) | the fix branch and PR, only to `origin` of a repository a grant covers |
| Your servers | the server-kit scripts over ssh; audit and dry run only |
| Your private ops repo (publish grant) | the run's report, review and summary, scrubbed |

Your repository list, server list and ssh targets stay in your user config directory
(`~/.config/agentteams/`); results stay in your user state directory
(`~/.local/state/agentteams/vulnscan/`). Nothing is written into a repository except the executor's
`vulnscan/*` branches and, with a publish grant, the ops repository's `reports` branch.

---

## Getting started

```bash
python -m agentteams.vulnscan init --repo-root ~/code     # writes ~/.config/agentteams/vulnscan.json
python -m agentteams.vulnscan enroll --discover ~/code    # optional: enrol projects (needs a terminal)
python -m agentteams.vulnscan daily                       # one full run
python -m agentteams.vulnscan schedule install            # dry run; add --apply to install
```

A project can record its own level in its brief:

```bash
agentteams --set-vulnscan scan --project path/to/project
```

Agent mode takes more setup (a key file, the agent and tools images, self-tests, a signing key and a
grant). Follow the [automation walkthrough](../../docs_src/vulnscan-automation-walkthrough.md) step by
step.

## Using it from Python

The CLI is the supported interface; these are the functions it calls.

```python
from pathlib import Path
from agentteams.vulnscan import engine

report = engine.scan_path(Path("~/code").expanduser(), include_worktrees=False)
for finding in report.findings:
    # severity is a rank 0..4 (4: critical; a malicious package is also 4 and sets finding.malicious);
    # fix is the minimal upgrade target, "" when none is known
    print(finding.severity, finding.ecosystem, finding.name, finding.version, finding.fix)
print(report.exit_code("high", include_dev=False))   # the CLI's exit code for this report
```

---

## Module map

| Area | Modules |
|---|---|
| CLI | `__main__` (commands), `operator_cli` (grants, review, notes, resolve, publish), `project_cli` (`enroll`, `roots`, `status`), `selftest_cli` |
| Scanning | `engine` (scan orchestration), `manifests` (discovery and parsing, edit checks), `osv` (advisories, fixes), `cvss` (v3/v4 scoring), `versions` (ordering), `runtimes`, `report` (`Report`, `Finding`) |
| Fixing | `fix` (plans and `--apply`), `server_fixplan` |
| Daily run | `daily`, `schedule`, `notify`, `effectiveness`, `merge_digest`, `remediation_status`, `remediation_report`, `report_store`, `run_records`, `paths` |
| Projects | `registry`, `project_policy` (the brief's `vulnerability_scanning` block), `coverage`, `repo_alias`, `team_dir` |
| Agent runtime | `agent_run`, `agent_settings`, `agent_drivers` (+ `_codex`, `_copilot`, `agent_driver_base`), `agent_upstreams`, `agent_recipe`, `docker_mode`, `debrief`, `mcp` (the agents' tools), `selftest_canary`, `selftest_probe` |
| Egress gateway | `gateway`, `gateway_dialects`, `gateway_dlp`, `gateway_peer`, `gateway_context` |
| Executor | `executor`, `executor_serve`, `executor_guard`, `executor_supersede`, `retry_guard`, `handoff`, `ops` |
| Trust and audit | `grant`, `publish`, `ledger`, `escalations`, `notes`, `review` |
| Confinement | `subproc_confine`, `subproc_confine_ssh`, `tools_image` |

## Documentation

- **[API reference](../../docs_src/api-reference/vulnscan.md)**: architecture, every config key,
  commands and exit codes, JSON output, the MCP tools, grants, the ledger and the Python API.
- **[User guide](../../docs_src/vulnerability-remediation-guide.md)**: day-to-day use, reading the
  summary, handling a disclosure wave.
- **[Automation walkthrough](../../docs_src/vulnscan-automation-walkthrough.md)**: setting up agent mode
  from scratch.
- **[Operator scripts](../../scripts/vulnscan-operator/README.md)**: helper scripts for the operator.
