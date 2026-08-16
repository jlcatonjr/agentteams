# CLI Reference

All flags for the `agentteams` command (entry point: `build_team.py`).

---

## Synopsis

```
agentteams [--description PATH] [--project PATH] [--framework NAME]
           [--output DIR] [--convert-from DIR] [--interop-from DIR]
           [--interop-source-framework NAME] [--interop-mode MODE]
           [--bridge-from DIR] [--bridge-source-framework NAME]
           [--bridge-check] [--bridge-refresh] [--bridge-merge] [--bridge-no-skills]
           [--dry-run] [--json] [--overwrite] [--merge] [--yes]
           [--no-scan] [--cost-routing] [--update] [--prune] [--adopt-orphans] [--check]
           [--refresh-index] [--query-index TEXT] [--query-k N] [--query-strategy {lexical,vector}]
           [--refresh-code-index] [--query-code TEXT] [--code-query-k N]
           [--code-query-strategy {lexical,vector}] [--code-kind {local,api,doc,all}]
           [--refresh-graph] [--refresh-architecture]
           [--install-git-hooks] [--no-git-hooks] [--code-index-hook]
           [--allow-foreign-output] [--pin-templates]
           [--reconcile-front-matter] [--reconcile-apply]
           [--fail-on-legacy-skip] [--no-vscode-tasks] [--no-add-fence-markers]
           [--scan-security] [--check-budget] [--self] [--allow-external-self-output]
           [--post-audit] [--auto-correct] [--enrich]
           [--strict-manual-placeholders] [--no-strict-manual-placeholders]
           [--no-backup] [--shrink-policy {preserve,warn,halt,allow}]
           [--list-backups] [--restore-backup TIMESTAMP]
           [--add-fence-markers PATH] [--in-place]
           [--prune-backups [KEEP]] [--keep-within-days DAYS] [--backup-mirror DIR]
           [--verify-waivers] [--verify-integrity] [--verify-backup [TIMESTAMP]]
           [--redteam] [--redteam-probes MODULE] [--redteam-report DIR]
           [--accept-probe-baseline]
           [--stale-check] [--stale-remediate] [--stale-no-git] [--stale-restore TS]
           [--recipe-check]
           [--target-host-features TOKENS]
           [--capture-baseline PATH] [--baseline-label LABEL] [--check-baseline PATH]
           [--security-offline] [--security-max-items N] [--security-no-nvd]
           [--migrate] [--revert-migration]
           [--fleet DIR] [--fleet-frameworks {github,claude,goose,both,all}] [--fleet-report DIR]
           [--fleet-allow-no-verify]
           [--goose-source NAME] [--goose-model ID] [--goose-show] [--goose-config PATH]
           [--version]
```

---

## Options

### `--description PATH` / `-d PATH`

Project description file (`.json` or `.md`). Required unless `--self` is used.

### `--project PATH` / `-p PATH`

Existing project directory to scan. Overrides `existing_project_path` in the description file. When set, the directory tree is scanned to supplement missing description fields (README content, tools, structure).

### `--framework NAME` / `-f NAME`

Target agent framework. Choices: `copilot-vscode` (default), `copilot-cli`, `claude`, `goose`, `agents-md`, `codex`, plus `canonical` (interop-only pseudo-framework — pair with `--interop-from`).

| Value | Format | Description |
|-------|--------|-------------|
| `copilot-vscode` | `.agent.md` with YAML front matter | VS Code Copilot agents with full handoff support |
| `copilot-cli` | `.agent.md` with YAML front matter (same shape as `copilot-vscode` since the P1 convergence 2026-08-15) | Copilot CLI custom agents; handoff sections/keys stripped (VS-Code-desktop-only), preserved in `references/runtime-handoffs.json` when present |
| `claude` | Claude front matter `.md` | Claude Projects; output includes `CLAUDE.md` instructions and preserves handoffs in `references/runtime-handoffs.json` when present |
| `goose` **(beta)** | Recipe YAML (`.goose/recipes/*.yaml`) | Block / AAIF Goose recipes; orchestrator delegates via `sub_recipes`, deeper edges become `summon` `load(...)`; team brief written to repo-root `AGENTS.md` + `.goosehints`. Handoffs are encoded natively in the recipes (no `runtime-handoffs.json` sidecar). **Beta** — see the feature-support matrix below |
| `agents-md` | Plain `.md` | Cross-tool **AGENTS.md** standard (AAIF / Linux Foundation). Emits a single framework-neutral repo-root `AGENTS.md` — the canonical file read by ~10 tools (Continue, Cursor, Cline, Codex, Zed, Aider, …) — plus per-specialist detail under `.agents/`. Routing preserved in `references/runtime-handoffs.json`. Generate-only for convert/bridge; the CAI interop path supports it as a target (best-effort: its rendered output carries no front matter). |
| `codex` | Plain `.md` | OpenAI Codex CLI (thin, prep-scoped target). Delegates AGENTS.md rendering to the agents-md adapter; the generated notice documents Codex's nested-directory `AGENTS.md` walk. `config.toml` MCP server emission is opt-in via `--target-host-features codex:mcp` (see [`codex_mcp_emit`](api-reference/codex-mcp-emit.md)). Same generate-only convert/bridge posture as `agents-md`. |
| `canonical` | Exploded CAI directory | Durable on-disk canonical format (`team.cai.json` + `agents/<slug>.md` + `skills/<slug>/SKILL.md` + `references/**`), default `<project>/.agentteams/canonical/`. Interop-only: there is no generate/convert/bridge path for it. |

`references/runtime-handoffs.json` is a framework-neutral sidecar manifest emitted when extracted handoffs exist for frameworks that do not keep inline VS Code handoff syntax in the final agent file.

#### Feature support by framework

`--framework` selects the **target** of each pipeline. Not every framework is a valid target for every pipeline:

| Framework | Generate | Convert target | Interop target | Bridge target |
|-----------|:--------:|:--------------:|:--------------:|:-------------:|
| `copilot-vscode` | ✓ | ✓ | ✓ | ✓ |
| `copilot-cli` | ✓ | ✓ | ✓ | ✓ |
| `claude` | ✓ | ✓ | ✓ | ✓ |
| `goose` **(beta)** | ✓ | ✓ <sup>1</sup> | ✓ <sup>2</sup> | ✓ |
| `agents-md` | ✓ | ✗ <sup>3</sup> | ✓ <sup>3</sup> | ✗ <sup>3</sup> |
| `codex` | ✓ | ✗ <sup>3</sup> | ✓ <sup>3</sup> | ✗ <sup>3</sup> |
| `canonical` | ✗ <sup>4</sup> | ✗ <sup>4</sup> | ✓ <sup>4</sup> | ✗ <sup>4</sup> |

**✓** available · **✗** not a valid target (by design)

> **`goose` is in beta** — generate/convert/bridge are validated against the Goose CLI, and the interop path (CAI) now preserves the handoff graph Goose needs; the `goose` adapter API is not yet covered by the [stability policy](https://github.com/jlcatonjr/agentteams/blob/main/STABILITY.md).

- **Generate** — `--framework X --description …` (or `--self`).
- **Convert target** — `--convert-from <team> --framework X` (format migration).
- **Interop target** — `--interop-from <team> --framework X` (CAI pipeline).
- **Bridge target** — `--bridge-from <team> --framework X` (lightweight pointer artifacts).

<sup>1</sup> `goose` convert wires full orchestrator delegation (`sub_recipes`) from `copilot-vscode` sources (which keep handoffs inline in their agent files). `claude` / `copilot-cli` sources strip handoffs at their own generation, so they currently convert to valid but **flat (un-delegated)** recipes; recovering that delegation from the `runtime-handoffs.json` sidecar is part of the planned handoff-recovery work.

<sup>2</sup> `--interop-from … --framework goose` is supported as of the durable-canonical-agent-format plan (Phase F): CAI captures handoffs and the goose adapter renders `sub_recipes` from them natively. Goose recipe configuration (`recipe_parameters` / `recipe_response` / `recipe_retry`, builtin extension scoping) round-trips through the CAI `framework_extensions.goose` bucket.

<sup>3</sup> `agents-md` / `codex` are generate-only for the convert/bridge paths (their instructions-file emission would mislabel there), but the CAI interop path supports them as targets: `import_from_cai` writes the framework-owned `AGENTS.md` beside the target agents dir. Import from an agents-md/codex source is best-effort by nature — no front matter means capabilities/handoffs land inferred-or-empty, surfaced via `compatibility-report.md` in bundle mode.

<sup>4</sup> `canonical` is an interop-only pseudo-framework — the durable exploded CAI directory (`team.cai.json` + `agents/` + `skills/` + `references/`). Export: `--interop-from <src> --framework canonical`. Import: `--interop-from <canonical dir> --interop-source-framework canonical --framework <target>`. Bundle mode is refused for the canonical target; its carried MCP servers re-validate against `mcp-server.schema.json` (security-review hard gates included) at import time.

Auto-detected **source** frameworks (for `--convert-from` / `--interop-from` / `--bridge-from` without an explicit `--*-source-framework`) are `copilot-vscode`, `copilot-cli`, `claude`, `goose` (`.goose/recipes`), `agents-md` (`.agents`), and `canonical` (any directory holding `team.cai.json`). `codex` shares the agents-md source shape and takes an explicit `--interop-source-framework codex`.

### `--output DIR` / `-o DIR`

Output directory for generated agent files. Defaults by framework:

- `copilot-vscode`: `<project>/.github/agents/`
- `copilot-cli`: `<project>/.github/agents/` (same directory as `copilot-vscode` — see the multi-framework sync caution in the README before pinning both together)
- `claude`: `<project>/.claude/agents/`
- `goose`: `<project>/.goose/recipes/` (team brief written to repo-root `AGENTS.md` + `.goosehints`)
- `agents-md`: `<project>/.agents/` (canonical team brief written to repo-root `AGENTS.md`)

### `--convert-from DIR`

Convert an existing team from `DIR` into the target `--framework` instead of rendering from a brief.

- Preserves agent body prose.
- Replaces front matter and framework wrappers.
- Converts instructions naming (`copilot-instructions.md` / `CLAUDE.md` / repo-root `AGENTS.md` for goose) based on target, and emits target sidecars (e.g. goose's `.goosehints`).
- Supports the six directional combinations between `copilot-vscode`, `copilot-cli`, and `claude`, **and converting any of them to `goose`** (writes `.goose/recipes/*.yaml` + repo-root `AGENTS.md`). Orchestrator delegation (`sub_recipes`) wires from sources that preserve handoffs in their agent files — i.e. `copilot-vscode`; `claude`/`copilot-cli` sources strip handoffs at their own generation, so they convert to valid but flat (un-delegated) recipes.
- Non-dry-run conversions run the same live security freshness preflight as the main render path; stale or unavailable security intel blocks writes unless a valid signed waiver exists in `references/security-waivers.log.csv` and `AGENTTEAMS_WAIVER_SIGNING_KEY` is configured.

### `--interop-from DIR`

Run the CAI-based interop pipeline from an existing source team.

- `direct` mode writes target framework files.
- `bundle` mode writes target files and compatibility artifacts under `references/interop/<source>-to-<target>/`.
- All six registered frameworks (`copilot-vscode`, `copilot-cli`, `claude`, `goose`, `agents-md`, `codex`) are valid interop targets, as is `canonical` (the durable exploded CAI directory). `--framework canonical` is interop-only and requires `--interop-from`; bundle mode is refused for the canonical target.
- Non-dry-run interop runs also enforce the live security freshness preflight before writing, with the same signed-waiver exception path.

### `--interop-source-framework NAME`

Optional source framework override for interop runs. When omitted, source framework is auto-detected (directory shape; a directory holding `team.cai.json` detects as `canonical`). Accepted values are the registered frameworks plus `canonical` — the latter reads an exploded canonical directory as the interop source.

### `--interop-mode MODE`

Interop mode selector:

- `direct` (default)
- `bundle`

Bundle artifacts:
- `team-manifest.cai.json`
- `interop-manifest.json`
- `routing-map.json`
- `instructions-map.json`
- `compatibility-report.md`

### `--bridge-from DIR`

Generate lightweight target-framework bridge artifacts that reference source canonical agents without regenerating source agent documentation.

Bridge artifacts are written under:

- `references/bridges/<source>-to-<target>/`

Bridge supports all six directional combinations between `copilot-vscode`, `copilot-cli`, and `claude`.
- Bridge generation runs the live security freshness preflight before writing (including signed-waiver exception support); `--bridge-check` remains read-only and only verifies source-file freshness.

### `--bridge-source-framework NAME`

Optional source framework override for bridge mode. If omitted, source framework is auto-detected.

### `--bridge-check`

Validate bridge freshness against source files by comparing source-file checksums with the bridge manifest.

### `--bridge-refresh`

Refresh bridge artifacts by **destructively overwriting** existing bridge outputs **and target-framework entry files** (`CLAUDE.md`, `.claude/agent-team.md`, `.claude/quickstart-snippet.md`, `.claude/README.md`, etc.) at the output root. Use for initial generation or when consumer entry files are known-disposable. For non-destructive refresh, use `--bridge-merge`.

### `--bridge-merge`

Non-destructive bridge update. Regenerates bridge-internal artifacts under `references/bridges/<src>-to-<target>/` (always overwrites those — bridge-owned). For target-framework entry files, only re-renders content inside `<!-- AGENTTEAMS-BRIDGE:BEGIN <region> v=N --> ... <!-- AGENTTEAMS-BRIDGE:END <region> -->` fences. Content outside fences is preserved verbatim. Files lacking any bridge fence are skipped with notices written to `bridge-merge.report.md`. First-time consumers should use `--bridge-refresh`; subsequent refreshes should use `--bridge-merge` to preserve consumer customization.

### `--bridge-no-skills`

Suppress emission of `.claude/skills/recall/SKILL.md` (Claude target only). The recall skill wraps `agentteams --query-index` for in-session memory-index retrieval; disable when your team manages skills via another channel.

---

## How These Three Options Differ

If you are deciding between the interoperability options:

1. `--convert-from` performs direct format migration between framework outputs.
2. `--interop-from` performs Canonical Agent Interface (CAI) normalization and re-emission.
3. `--bridge-from` creates a lightweight runtime bridge that preserves source canonical agent docs.

Choose by intent:

1. Use `--convert-from` for straightforward target-format rewriting.
2. Use `--interop-from` when you need canonical transport and optional compatibility bundle artifacts.
3. Use `--bridge-from` when you need target-runtime access without replacing source documentation.

---

## Bridge Automation Procedures

This repository includes automated bridge upkeep:

1. `.github/workflows/bridge-maintenance.yml` (daily + manual) runs `scripts/run_daily_bridge_maintenance.sh`.
2. The script runs security maintenance first, then performs `--bridge-refresh` and `--bridge-check` for maintained pairs, and writes run summaries to `tmp/bridge-maintenance/`.
3. `.github/workflows/security-maintenance.yml` is retained as a manual fallback (`workflow_dispatch`) for incident response or ad-hoc reruns.
4. `.github/workflows/bridge-watchdog.yml` opens a deduplicated issue if the latest successful bridge maintenance run is stale.

For mode comparison and architecture-level guidance, see [Interoperability](interoperability.md).

---

## Explicitly Excluded Option Pairs

The CLI rejects incompatible pairs explicitly.

Global exclusions:
- `--convert-from` and `--interop-from` cannot be used together.
- `--bridge-from` cannot be used with `--convert-from` or `--interop-from`.
- `--auto-correct` requires `--post-audit`.
- `--prune` requires `--update`.
- `--bridge-check`, `--bridge-refresh`, and `--bridge-merge` are mutually exclusive; at most one may be passed.
- `--refresh-index` and `--query-index` are mutually exclusive.
- `--query-k` must be `>= 1`.
- `--fleet` requires `--update` and `--merge`, forbids `--shrink-policy=allow`, and is mutually exclusive with `--self`, `--project`, `--description`, `--output`, `--overwrite`, `--prune`, `--migrate`, `--revert-migration`, `--adopt-orphans`, `--bridge-from`, `--bridge-refresh`, `--convert-from`, `--interop-from`, `--refresh-index`, `--query-index`, `--list-backups`, `--restore-backup`, `--add-fence-markers`, `--capture-baseline`, and `--check-baseline` (it operates on many workspaces, each resolved independently).

Excluded with `--convert-from`, `--interop-from`, or `--bridge-from`:
- `--description`, `--project`, `--self`, `--no-scan`, `--update`, `--prune`, `--check`, `--refresh-index`, `--query-index`, `--scan-security`, `--post-audit`, `--auto-correct`, `--enrich`, `--merge`, `--migrate`, `--revert-migration`, `--list-backups`, `--restore-backup`

### `--dry-run`

Show what would be generated without writing any files. Useful for previewing output before committing.

### `--json`

With `--dry-run`: emit the per-file action plan as a single JSON document on stdout. No-op without `--dry-run`.

### `--overwrite`

Overwrite existing agent files without prompting. Default behavior: prompt for each existing file.

### `--merge`

Update only template-fenced regions in existing agent files, preserving all user-authored content outside fence markers. Skips legacy files (no fence markers) with a warning. This is the default behavior for `--update`; pass `--merge` explicitly if you want to make this intent clear in scripts or CI. Use `--overwrite` only when intentional full-file regeneration is needed (requires security clearance).

### `--yes` / `-y`

Non-interactive mode: answer yes to all prompts automatically.

### `--no-scan`

Disable project directory scanning even when `existing_project_path` or `--project` is set.

### `--cost-routing`

OFF by default. When set, emit `references/model-routing.json` — a framework-neutral per-agent model-tier contract (governance agents → `cheap`, producers/experts → `primary`). Generated agent files are unchanged either way; this only adds the opt-in contract artifact.

### `--update`

Re-render drifted agent files and emit newly added agents without touching unchanged files. Preserves manually filled `{MANUAL:*}` values from existing files. Agents removed from the taxonomy are reported but not deleted (use `--prune` to also remove them).

A backup of the output directory is created automatically before any writes. By default, `--update` uses merge mode (equivalent to `--update --merge`): only template-fenced regions are re-rendered, and user-authored content outside fence markers is preserved. To perform a full destructive re-render, pass `--update --overwrite` (this invokes the security gate and requires a clearance in `references/security-decisions.log.csv`). Use `--no-backup` to suppress the backup.

On a successful (non-dry-run) `--update`, AgentTeams writes a delivery receipt to `references/delivery-receipt.json` (schema: `schemas/delivery-receipt.schema.json`) recording the project name, framework, manifest fingerprint, and fingerprint algorithm version of the delivered build. When no material drift is detected but the build-log baseline is stale (for example after a `FINGERPRINT_ALGO_VERSION` bump), the baseline is healed in place: the build-log is rewritten first, the delivery receipt is then written against the healed baseline (heal-first-attest-second), and `--update` prints `✓  Healed build-log baseline (no material drift; fingerprint refreshed).` after both writes complete. Receipt write failures warn on stderr but do not fail the run.

### `--prune`

Used with `--update`: also delete agent files that are no longer part of the team taxonomy.

### `--adopt-orphans`

Register pre-existing agent files that the generated taxonomy does not produce (e.g. bespoke custom agents) into the team roster — the orchestrator's handoff list and domain routing — **without** generating or overwriting their files. The opposite of `--prune`: integrate orphans instead of removing them. Requires the orchestrator to be (re)rendered, so use with `--overwrite` or `--migrate` (under `--merge` the orchestrator front matter is preserved and adoption would not surface).

### `--check`

Check for template drift and structural changes without writing any files. Exits with code `1` if drift or structural changes are detected, `0` otherwise. Suitable for CI gates.

When the structural diff reports a manifest-promotion event (manifest fingerprint changed, fingerprint unavailable, or `fingerprint_algo_version` bumped), `--check` runs the full render pipeline in memory and reconciles each promoted file against its on-disk content; fingerprint-only promotions whose rendered output matches disk byte-for-byte are demoted back to unchanged. `--check` and `--update --dry-run` report the same `has_changes` set for the same inputs.

### `--refresh-index`

Rebuild only `references/memory-index.json` in the output directory. This mode does not emit/update agent templates and is intended for fast memory-index refresh after editing source history documents (for example `workSummaries/`, `CHANGELOG.md`, `README.md`, `docs_src/*.md`, or `references/*.md`).

### `--query-index TEXT`

Query an existing `references/memory-index.json` and print ranked hits (title, path, score, snippet). Exits `0` when at least one hit is found and `1` when no matches are found.

### `--query-k N`

Number of ranked results to return with `--query-index`. Default: `5`.

### `--query-strategy {lexical,vector}`

Retrieval strategy for `--query-index`. Default: `lexical`.

- `lexical` — BM25 term-frequency ranking. High precision for keyword/exact-term queries ("when was X decided?", "where is the delivery doc?").
- `vector` — Sparse tf·idf cosine similarity. Better recall for thematic/semantic queries ("what's our policy on error handling?", "find prior work on resource management"). Returns documents related to ALL query terms. Stdlib-only, <100ms at typical corpus sizes.

Start with `lexical`; if results are low-confidence, retry with `vector`.

---

## Code & API Index

A second index, separate from the memory index above. It covers repository scripts and the
external API modules and documentation they use. The cache lives in `references/code-index/`
and is **gitignored** — it is a local artifact, never committed.

### `--refresh-code-index`

Rebuild the code & API index only, then exit. Useful after editing scripts or bumping
dependencies. Standalone: no `--description` required.

### `--query-code TEXT`

Query the code & API index and print ranked hits. Auto-refreshes a stale local partition
first. Requires a pre-existing cache — run `--refresh-code-index` (or one `--update`) once
before the first query.

### `--code-query-k N`

Number of ranked results to return with `--query-code`. Default: `5`.

### `--code-query-strategy {lexical,vector}`

Query strategy for `--query-code`. Default: `lexical` (BM25 — better for identifier and
keyword matching); `vector` uses cosine similarity. Same trade-off as
`--query-strategy` for the memory index.

### `--code-kind {local,api,doc,all}`

Filter `--query-code` by source kind — `local` (repository scripts), `api` (external API
modules), `doc` (API documentation), or `all`. Default: `all`.

---

## Generated Maps and Git Hooks

Both maps are regenerated on every `--update`. Between updates they drift whenever an agent
file or module is edited by hand, which is what the pre-commit hook closes.

### `--refresh-graph`

Standalone: regenerate `references/pipeline-graph.md` — the agent topology — from the agent
files on disk (`.github/agents/` or `.claude/agents/`), then exit. Writes only when the
topology actually changed. Offline; no `--description` needed. This is what the installed
pre-commit hook calls. The target tree is resolved from `--output` / `--project`, defaulting
to the current directory.

### `--refresh-architecture`

Standalone: regenerate `references/architecture-graph.md` — a module-dependency map of the
repository's own Python package, auto-detected and built from its import statements — then
exit. Writes only when the module graph changed. Offline. Refreshed by the same pre-commit
hook on any staged `.py` change.

### `--install-git-hooks`

Standalone: install (or sentinel-merge) the pre-commit hook that refreshes
`references/pipeline-graph.md` whenever agent files are part of a commit, then exit.
Idempotent, and it preserves any pre-existing hook body outside its sentinel markers. Target
repo resolved from `--output` / `--project`, defaulting to the current directory.

### `--no-git-hooks`

Opt **out** of the default behaviour where a successful generate or update auto-installs that
pre-commit hook. Pass this for repositories that manage git hooks manually, or in
environments where hooks are undesirable.

### `--code-index-hook`

Opt **in** to a pre-commit warm-up that refreshes the code & API index cache when scripts or
dependency manifests are committed. The cache is gitignored, so this clause never stages
anything — unlike the graph refreshes. Off by default, because `--query-code` already
rebuilds a stale partition on demand. Applies both to `--install-git-hooks` and to the
auto-install on generate/update.

### `--fail-on-legacy-skip`

Exit with non-zero status if `--merge` skipped any files due to missing fence markers (legacy files). Use in CI to enforce that template updates always propagate to downstream repositories.

Without this flag, legacy skips are reported in the end-of-run summary but the exit code remains `0`. The summary block also fires without the flag — the flag only changes the exit code so CI can fail builds on detected propagation gaps.

**Remediation** for files that appear in the legacy-skip block:

- `agentteams --add-fence-markers <path> [--in-place]` — retrofit AGENTTEAMS fence markers so the next `--merge` run updates the file.
- `agentteams ... --overwrite` — replace unconditionally (will discard any local edits to those files; use only after backup).

### `--no-vscode-tasks`

Suppress generation of `.vscode/tasks.json`. By default, `agentteams` emits a `tasks.json` at the project root containing discovered project commands (npm scripts, Makefile PHONY targets, etc.) and `agentteams` meta-tasks. Pass this flag for repositories that manage `tasks.json` manually.

### `--no-add-fence-markers`

Opt **out** of the default behaviour where `--update --merge` (with `--yes`) auto-retrofits AGENTTEAMS `content` fence markers onto legacy (unfenced) files so their template region becomes mergeable instead of being skipped. Each retrofit is backed up first and the shrink-guard still suppresses material template shrinks, so the legacy body is recoverable. Pass this flag to keep the conservative skip-legacy behaviour (distinct from the standalone per-file `--add-fence-markers PATH` retrofit).

### `--scan-security`

Scan generated agent files for security issues: PII paths (absolute paths containing usernames), credential patterns (API keys, tokens, passwords), and unresolved `{MANUAL:*}` or `{UPPER_SNAKE_CASE}` placeholders.

### `--check-budget`

Audit live `.agent.md` files for token-budget overrun and prompt-cache prefix volatility. Read-only. Exits `1` on fail-class findings; `0` on warn-class only. Routes remediation to `@agent-refactor`.

### `--self`

Operate on the module's own agent team using `.github/agents/_build-description.json`. Equivalent to running `agentteams` with the module's internal description file.

### `--allow-external-self-output`

Permit `--self` to write self-maintenance artifacts to an `--output` path outside the AgentTeams module source tree. Required to prevent accidental writes into consumer repositories.

### `--post-audit`

Run a post-generation audit after emit. Performs static checks (unresolved placeholders, YAML integrity, required-agent coverage) and, if the `copilot` CLI is authenticated, an AI-powered conflict and presupposition review via GitHub Models.

### `--auto-correct`

Used with `--post-audit`: after audit finds issues, invoke the standalone `copilot` CLI in non-interactive mode to repair generated team files, then rerun the audit to confirm.

### `--enrich`

After generating the team, scan for default template elements (unresolved `{MANUAL:*}` placeholders, underdeveloped sections, incomplete tool metadata) and attempt context-aware auto-enrichment. Exports a `defaults-audit.csv` to the `references/` directory. Combine with `--post-audit` to also run AI-powered enrichment.

### `--strict-manual-placeholders`

Preserve unresolved `{MANUAL:*}` placeholders for optional governance fields instead of replacing them with usability defaults.

This mode is the default for `--self` runs.

### `--no-strict-manual-placeholders`

Disable strict manual placeholder preservation and apply usability-oriented defaults for optional governance placeholders:

- `REFERENCE_DB_PATH` -> `N/A - no citation database configured for this project`
- `STYLE_REFERENCE_PATH` -> `N/A - no formal style guide defined for this project` (or the configured `style_reference` value)

This mode is the default for non-self runs.

---

## Backup Options

By default, `--overwrite`, `--merge`, and `--update` all take an automatic backup of the output directory before writing. Backups are stored at `<output_dir>/.agentteams-backups/YYYYMMDD-HHMMSS/`.

### `--no-backup`

Skip the automatic backup. The write proceeds without creating a backup.

### `--shrink-policy {preserve,warn,halt,allow}`

*(T2.D5)* Controls behaviour when a fenced-region merge would lose
concrete references (paths, identifiers, CVE IDs, list items) from
the on-disk fence body relative to the freshly rendered content.

- `preserve` (default): keep the existing enriched body for that
  fence (the shrink is suppressed and a Notice is emitted) while
  still updating every non-shrinking fence in the file. Respectful
  and non-destructive — nothing is lost and no sidecar is produced.
- `warn` (back-compatible): log the shrink notice into the
  emit notices stream and proceed with the smaller content. The
  notice is also appended to
  `tmp/daily-pipeline/shrink-events/<date>.md` (gitignored) with
  the backup directory path so the operator can recover lost
  content.
- `halt`: log the notice, refuse the write, and list the blocked
  file in `EmitResult.shrink_blocked` and on stderr. Returns the
  emit step with the file untouched. Used by the self-team daily
  script (`scripts/run_daily_security_maintenance.sh`) to enforce
  strict fence preservation. Recovery: re-run once with
  `--shrink-policy=allow` (or `warn`), commit the resulting state,
  then halt enforcement resumes on the next cycle.
- `allow`: suppress notices and write the smaller content silently.
  Intended only for that one-time recovery sequence after a
  legitimate upstream-driven shrink (e.g., a retired CVE feed
  entry).

All invocations of `build_team.py` default to `preserve`. The flag
is plumbed into both emit code paths (the `--update` branch and the
post-emit main path).

Under `warn`, the full pre-merge body of every shrunken fence is
written to `<backup>/<rel_path>.lost.<sid>.md` (the backup is taken
automatically before the merge) and the shrink Notice is annotated
with `— recovery: <sidecar-path>` so the operator can recover
dropped hand-edits without diffing the whole-file backup. The fence
allowlist `_LIVE_DATA_FENCES` (`threat_intelligence`, `threat_data`)
is exempt — those fences are filled each run from live CISA KEV /
NVD / OSV feeds; CVE rotation is expected.

### `--target-host-features TOKENS`

Comma-separated `<namespace>:<feature>` subselectors that gate
opt-in emission paths. Tokens flow onto the manifest and are
consumed by feature-gated emitters. Default emission is unchanged
when omitted. Recognised tokens:

| Token | Effect |
|---|---|
| `bridge:copilot-vscode-to-claude:subagents` | Per-agent Claude subagent stubs under `<project>/.claude/agents/`. |
| `bridge:copilot-vscode-to-claude:hooks` | `.claude/settings.agentteams.example.json` + `.claude/hook-guard.sh`. |
| `bridge:copilot-vscode-to-claude:cache-split` | Cache-aware `CLAUDE.md` (preamble + boundary + dynamic stanza). |
| `bridge:copilot-vscode-to-claude:schedule` | `.claude/schedules.agentteams.json` for the `/schedule` skill. |
| `bridge:copilot-vscode-to-claude:todo-projection` | `.claude/skills/todo-from-plan/SKILL.md` skill. |

Unknown tokens are syntactically valid but produce no emission.
See [`host_features`](api-reference/host-features.md) for parser
contract.

### `--capture-baseline PATH`

Capture a deterministic SHA-256 manifest of the output tree and
write it to `PATH` (e.g. `tests/baselines/<team>-<framework>.json`).
Used by regression tests to detect emission drift across phases.
Skips the normal generation pipeline.

### `--baseline-label LABEL`

Label embedded in the captured baseline manifest. Defaults to the
`--framework` value when omitted.

### `--check-baseline PATH`

Compare the current output tree against the baseline at `PATH` and
exit non-zero on any diff. Lists added / removed / changed files to
stderr.

### `--list-backups`

List all available backups for the output directory (newest first) and exit. Prints timestamp, path, and file count for each backup.

### `--restore-backup TIMESTAMP`

Restore a specific backup into the output directory. `TIMESTAMP` is the directory name shown by `--list-backups` (e.g. `20250601-143022`). Use `latest` to restore the most recent backup.

### `--prune-backups [KEEP]`

Standalone retention sweep that bounds backup growth: delete old timestamped backups under `<output_dir>/.agentteams-backups/`, keeping the newest `KEEP` (default `10`). Resolves the output directory from `--output`/`--project` (else CWD) and needs no `--description`.

- The **single newest backup is never deleted**, even with `--prune-backups 0` (you always retain at least one recovery point).
- Combine with `--keep-within-days DAYS` to additionally retain anything younger than `DAYS` (union of the two rules). A backup whose age cannot be determined (unparseable name *and* unreadable mtime) is kept — the sweep is fail-safe toward retention.
- Pair with `--dry-run` to preview the deletions without touching disk.

Distinct from [`--prune`](#--prune), which removes stale *agent files* during an `--update`, not backups. Exit `0` (this is a maintenance op, not a verdict); mutually exclusive with `--verify-integrity` and `--verify-backup`.

### `--keep-within-days DAYS`

Modifier for `--prune-backups`: in addition to the newest `KEEP`, retain any backup younger than `DAYS`. Errors if passed without `--prune-backups`.

### `--backup-mirror DIR`

Modifier for `--update` (and the other write modes): after each automatic backup is written, also copy it to `DIR/<output-slug>/<timestamp>/` — e.g. a NAS, external drive, or synced folder — so the recovery net survives a local disk loss. The `<output-slug>` namespaces by output directory so many workspaces can mirror to one target without collision (this is also what makes `--fleet --backup-mirror` safe). Best-effort and **non-fatal**: a mirror failure warns on stderr but never breaks the primary operation. Overrides the `AGENTTEAMS_BACKUP_MIRROR` environment variable (set the variable to mirror without passing the flag every run).

---

## Integrity Verification

Read-only checks that detect silent corruption of generated files and confirm a backup is restorable. Both resolve the output directory from `--output`/`--project` (else CWD) and require no `--description`.

### `--redteam`

Run the **standing red-team audit** and exit: the constitutional probe battery (phase 1), the
review (phase 2), a remediation skeleton (phase 3) and the six self-audit checks that evaluate
the red team itself (phase 6). It **measures and reports — it never remediates.** Phases 4, 5
and 7 are human- or agent-driven off the emitted artifacts; an unattended job that writes
remediation code is a larger risk than the one it closes.

Artifacts land in `tmp/redteam/YYYY-MM-DD/` (or `--redteam-report`): `findings.json`
(schema: `schemas/redteam-findings.schema.json`), `discoveries.md`, `remediation.plan.md`,
`selfaudit.md`.

**Exit code:**

| Code | Meaning |
|---|---|
| `0` | clean — no live exploit, no self-audit finding, live agent tree untouched |
| `1` | a finding — a measured attack is live, or phase 6 found a defect in the red team |
| `2` | **the harness is broken** — a control probe failed, the probe module would not import, a corpus claim no longer matches the scanner, the run modified the live agent tree, or the audit died with a traceback |

Code `2` outranks `1`. A battery whose controls fail reports "no exploits" exactly as loudly as
one that found none, so *indeterminate is never a pass*. Honours `--dry-run`, which computes
everything and writes nothing.

Runs daily via `.github/workflows/redteam-audit.yml`. Procedure:
`references/redteam-audit.procedure.md`.

### `--redteam-probes MODULE`

Dotted import path of the probe module, e.g. `tests.constitutional_redteam_battery`.

**There is deliberately no default.** A consumer of this package has no
`tests.constitutional_redteam_battery`, and a command whose default target does not exist would
hand every consumer a permanently red check. Omitted, `--redteam` runs **phase 6 only** — the
six self-audit checks are repo-agnostic — and reports the probe population as *unmeasured*
rather than clean. A *named* module that fails to import is exit `2`.

### `--redteam-report DIR`

Where to write the four artifacts. Defaults to `tmp/redteam/YYYY-MM-DD` under `--project`
(else CWD) — gitignored and ephemeral, per `references/filing-conventions.md`.

### `--accept-probe-baseline`

Re-record `references/redteam-probe-baseline.json` from a fresh probe run, then exit. Requires
`--redteam-probes`.

**Operator command. The daily audit never does this**, and
`tests/test_redteam_audit_workflow.py` asserts that no step in the workflow can. A probe can
start passing because the control got better *or because the probe got blinder* — two probes
flipped to a false `DEFENDED` exactly that way — and only a reviewed diff tells those apart. A
scheduled job that re-baselined itself would clear its own flag every night and measure
nothing. Refused under `--dry-run`.

Record what you concluded in the affected probe's `note` field before committing.

### `--verify-waivers`

Read-only: report the validity (signature, expiry, use-limit, conditions) of every security waiver in `references/security-waivers.log.csv` under `--output`/`--project` (else CWD). Never mints or consumes a waiver. Exits non-zero if any waiver is invalid. Requires `AGENTTEAMS_WAIVER_SIGNING_KEY` to verify signatures; without it, rows report as unverifiable.

### `--write-integrity-manifest`

Re-record `references/enforcement-integrity.json` from the enforcement modules on disk, then exit.

`agentteams/integrity.py` keeps a SHA-256 manifest over the modules that enforce C-1..C-5. The red-team battery's probe E4 compares the modules against it, so an unrecorded edit to a control shows up as a measured exploit.

**Run this only *after* an intended change to a control, then review the diff.** Regenerating first turns the manifest into a record of whatever happens to be on disk — which is precisely what an attacker would want it to be. Keeping regeneration a separate, deliberate act is what makes the resulting `git diff` the actual control; an auto-refreshed manifest verifies nothing.

**Exit code:** `0` after a successful write whose verification comes back clean; `1` if verification still reports mismatches, which would mean the write did not take.

> The manifest's own `note` field has named this command since the manifest was written, and the flag was not implemented until 2026-08-07 — found when probe E4 correctly flagged an intended change and the documented recovery path turned out to be unavailable. `tests/test_integrity_manifest_cli.py` now asserts every command the manifest names is one the parser accepts.

### `--verify-integrity`

Classify every generated output file against the build-log `file_hashes` baseline and exit. Each recorded file is reported as one of:

| Status | Meaning |
|---|---|
| `OK` | Current hash matches the build-time hash. |
| `MODIFIED` | Content changed since the last build — a legitimate `USER-EDITABLE` edit **or** drift. Undifferentiated (a whole-file hash cannot say *where* it changed), so it is **advisory** and listed for review. |
| `TRUNCATED` | A recorded file is now empty — a strong corruption signal. |
| `MISSING` | A recorded file is absent (or unreadable). |
| `FENCE-BROKEN` | Content changed **and** the file's `AGENTTEAMS` fences no longer parse (unclosed/duplicate/mismatched) — a strong corruption signal. |

**Exit code:** non-zero on any `TRUNCATED` / `MISSING` / `FENCE-BROKEN`; `0` otherwise (`MODIFIED` does not fail). Unlike `--update` — where a non-zero exit can be a benign post-merge attestation crash — **`--verify-integrity`'s exit code IS the integrity verdict and must be heeded.** If no build-log baseline exists yet, it reports "cannot verify" and exits `0` (run `--update` to establish one).

**Enforcement manifest:** when `references/enforcement-integrity.json` exists at the resolved root, `--verify-integrity` also re-checks every enforcement module against it and exits non-zero on any mismatch — the CLI counterpart to [`--write-integrity-manifest`](#--write-integrity-manifest)'s "review the diff before regenerating" contract. (Before 2026-08-13 the only check of that manifest lived inside the red-team battery.)

### `--verify-backup [TIMESTAMP]`

Verify a backup's own integrity — each backed-up file's bytes against the `source_sha256` recorded in the backup's `_manifest.json` — confirming the backup is restorable (catches bit-rot/tamper). Defaults to the latest backup; pass a `TIMESTAMP` (as shown by `--list-backups`) for a specific one. Exits non-zero on any mismatch.

---

## Security Intelligence Options

These flags control the live vulnerability feed used when rendering security-reference agent files.

### `--security-offline`

Use the cached security vulnerability snapshot only — no network fetch. Useful in CI environments or when working without internet access.

If live security data cannot be fetched and there is no cache to fall back to, the security snapshot is marked stale and write-capable commands block until the feed is refreshed. A valid signed waiver in `references/security-waivers.log.csv` can authorize a bounded exception when `AGENTTEAMS_WAIVER_SIGNING_KEY` is configured.

### `--security-max-items N`

Maximum number of current vulnerabilities to include in generated security references. Default: `15`.

### `--security-no-nvd`

Skip NVD CVSS enrichment. Avoids approximately 7 seconds of per-CVE rate-limit sleep. CISA KEV and EPSS data are still fetched.

---

## Legacy Fencing Migration

### `--migrate`

One-step migration for repositories that have legacy (unfenced) agent files. Performs two operations atomically:

1. Creates a git tag `pre-fencing-snapshot` at the current HEAD commit — this is the safety rollback point.
2. Runs `--overwrite --yes` to regenerate all agent files with fenced templates.

After completion, prints a **quality-audit checklist** guiding you to:

- Review `git diff pre-fencing-snapshot HEAD` for lost project-specific content
- Restore any custom rules to the `USER-EDITABLE` zone in `orchestrator.agent.md`
- Commit the migrated files
- Switch to `--merge` for all future updates

Requires `--description`. The project directory must be a git repository.

```bash
agentteams \
  --description .github/agents/_build-description.json \
  --framework copilot-vscode \
  --project /path/to/project \
  --migrate
```

### `--revert-migration`

Undoes a previous `--migrate` run. Runs `git reset --hard pre-fencing-snapshot` in the project directory and deletes the `pre-fencing-snapshot` tag. All overwritten agent files are restored to their pre-migration state.

Requires the project directory to be a git repository with the `pre-fencing-snapshot` tag present. Use `--project` to specify a different directory than `cwd`.

```bash
agentteams --revert-migration --project /path/to/project
```

> **Note:** `--revert-migration` only resets the working tree and index. If you have already pushed the migrated commit to a remote, a force-push is required. That step is intentionally left to the user.

---

## Stale Detection & Revision

Read-only scan (and optional guided/applied revision) for stale agent docs and code. See the [Stale Detection Guide](stale-detection-guide.md) for tiers, suppression, and recovery.

### `--stale-check`

Read-only: scan `--output`/`--project` (else CWD) for stale agent docs and code/scripts (VCS conflict markers, broken references, git-recency divergence, provenance-gated generated-file integrity). Exits non-zero on any Tier-1 (blocking) finding. Never edits files.

### `--stale-remediate`

Modifier for `--stale-check`: also print a guided remediation plan (suggestions only; does **not** edit files, unlike `--auto-correct`). Adding `--yes` promotes it into an applied, backup-protected revision pass; exit `3` signals "revision applied, but blocking items still need manual/routed handling."

### `--stale-no-git`

Modifier for `--stale-check`: skip the Tier-2 git-recency signal (hermetic/CI or non-git targets).

### `--stale-restore TS`

Standalone: restore files from a `--stale-remediate --yes` safety snapshot (`.agentteams-backups/stale-fix-<TS>/`; default: latest). Recovery path for a revision that went wrong. Verifies each backup's sha256 before writing and refuses if a backup is corrupt.

---

## Fleet Update (multi-workspace)

Run `--update --merge` across **every** agent-infrastructure workspace under a parent directory (and its subfolders) in one command. Replaces ad-hoc batch scripts and encodes the fleet-update lessons in [`references/systematic-update-lessons.md`](https://github.com/jlcatonjr/agentteams/blob/main/references/systematic-update-lessons.md).

```bash
agentteams --fleet /path/to/parent --update --merge            # dry-run preview (no writes)
agentteams --fleet /path/to/parent --update --merge --yes       # apply
```

How it works, per discovered workspace:

1. **Discovery** — finds dirs containing `.github/agents/` and/or `.claude/`, pruning `node_modules`, `.git`, `.worktrees`, and `archive`, and never recursing into `.github`/`.claude` internals.
2. **Snapshot (git commit)** — before applying, each git workspace's agent-infra state is committed as `chore(fleet): pre-update snapshot` (or left at `HEAD` when already clean). This is the recoverable rollback point and the diff base. (Non-git workspaces rely on the automatic `.agentteams-backups/` snapshot.)
3. **In-process update** — re-enters the standard update path per target with `--update --merge` (copilot-vscode `.github/agents/`, or a native Claude team's `.claude/agents/`) or `--bridge-merge` (for bridge-consumer `.claude/`). No subprocess is spawned, so a successful merge is never misreported because of an interpreter/exit-code quirk; a failure in one target is isolated and the run continues.
4. **Diff analysis** — after applying, `git diff <snapshot>` is classified by the **authoritative content signals** — shrink Notices and deletions inside `USER-EDITABLE` regions — **not** the process exit code. Per-workspace `.diff` files plus `report.json` and `summary.md` are written under `<DIR>/.agentteams-fleet/<run-id>/`.

Statuses per `(workspace, target)`: `OK` (only fenced/generated regeneration), `REVIEW` (shrink Notice or USER-EDITABLE deletion — inspect the diff), `FAIL` (the merge itself errored), `SKIP` (ambiguous `.claude` with no bridge signal and no descriptor), `WOULD-UPDATE` (dry-run).

**Safety:** fleet mode is non-destructive by construction. It is **merge-only** — `--overwrite`, `--prune`, `--migrate`, `--bridge-refresh`, and `--shrink-policy=allow` are rejected, and `.claude/` is only ever **bridge-merged**, never bridge-refreshed. Descriptor resolution prefers `.agentteams/brief.json` over the thin `_build-description.json` stub.

### `--fleet DIR`

Update every agent-infrastructure workspace under `DIR` and its subfolders. Requires `--update --merge`. Defaults to a dry-run preview; pass `--yes` to apply.

### `--fleet-frameworks {github,claude,goose,both,all}`

Which infrastructures to update per workspace. Default: `both`.

- `both` (default) — copilot-vscode (`.github/agents/`) + claude (`.claude/`), backward-compatible.
- `all` — adds goose to `both` (copilot-vscode + claude + goose).
- `goose` — Goose workspaces only.
- `github` / `claude` — restrict to that single infrastructure.

### `--fleet-report DIR`

Directory for the fleet report. Default: `<DIR>/.agentteams-fleet/<run-id>/`.

---

## Output Safety

### `--allow-foreign-output`

Permit a relative `--output` that resolves onto a non-empty, git-tracked directory showing no
sign of being an agentteams-generated tree. Without this the run **refuses**: a relative path
resolves against the current working directory, which is how a scratch render once overwrote
a real agent tree.

---

## Template Trust

### `--pin-templates`

Record the installed template digests this project trusts, in
`.agentteams/template-pins.json`, and commit that file. This is the **only** thing that
writes the pin. Every later run compares against it and reports a mismatch, but never updates
it — a pin that follows what it checks records nothing.

---

## Front-Matter Reconciliation

### `--reconcile-front-matter`

Report where a deployed team's YAML front matter diverges from its templates, and change
nothing. Front matter cannot be fenced, so an edited file keeps its own values and a
template's capability change stops there silently. This makes that visible without a full
update run.

### `--reconcile-apply`

With `--reconcile-front-matter`, take the template's value for each diverging key.
**Never implied by the report.** `allowed-tools` is a capability grant, and widening one is a
privileged change (Constitutional C-3), so it requires saying so explicitly.

---

## Canonical Absorb (Bidirectional Sync)

### `--absorb-from DIR`

Source directory to absorb native framework edits from. The framework is
auto-detected by directory shape when `--absorb-source-framework` is omitted.
Requires a canonical directory (auto-detected as
`<parent>/<parent>/.agentteams/canonical/` or specified via
`--absorb-canonical-dir`).

### `--absorb-source-framework NAME`

Explicit source framework for `--absorb-from` (auto-detected when omitted).
Accepts any registered framework: `copilot-vscode`, `copilot-cli`, `claude`,
`goose`, `agents-md`, `codex`.

### `--absorb-canonical-dir DIR`

Explicit canonical directory path. Defaults to
`<source_dir>/../../.agentteams/canonical/` when omitted.

### `--absorb-apply`

Apply native-moved (non-capability) changes to canonical. Without this
flag, the absorb is report-only — nothing is written. Capability-bearing
fields (tools, model, user-invocable, permissionMode, agents) are **never**
auto-applied; they always appear in the report as proposals for human
review (§6.1 capability carve-out).

---

## Other Options

### `--fleet-allow-no-verify`

Allow fleet snapshot commits to bypass pre-commit hooks (`--no-verify` /
`core.hooksPath=/dev/null`). Off by default — hooks run normally and a warning is printed if
a hook blocks the snapshot. Use only when workspace hooks are known-safe to skip, e.g. a
commit-signing hook that would reject the ephemeral internal snapshot commit.

### `--recipe-check`

Validate generated Goose recipe YAML files in the `--output` directory (or `.goose/recipes/` by default). Checks: version string, no `model:` key, non-empty instructions, `sub_recipe` path resolution. Writes `recipe-check.report.md` and exits `1` on FAIL. Requires `--framework goose`.

### `--version`

Print the version and exit.

---

## Goose Source / Model Switch **(beta)**

Standalone helpers that edit Goose's own `config.yaml`. They do not generate or update a
team; they change which provider and model the Goose CLI will use.

### `--goose-source NAME`

Switch Goose's provider in `config.yaml` (e.g. `ollama`, `openrouter`). Applies that source's
default model unless `--goose-model` is also given.

### `--goose-model ID`

Set Goose's model in `config.yaml`. With `--goose-source`, that source's model; alone, the
current provider's model.

### `--goose-show`

Show the resolved `config.yaml` path, the current provider and model, any masking environment
override, and the known sources.

### `--goose-config PATH`

Override the `config.yaml` path. Otherwise resolved via `goose info` / XDG.

---

## Portable Team Package

Package a native-framework source team as one portable `.zip`: a durable canonical directory
(`.agentteams/canonical/` — `team.cai.json` plus one `agents/<slug>.md` file per agent, plus
`skills/<slug>/SKILL.md` when the source has first-class skills) plus its generic bridge
(framework-agnostic inventory, quickstart, entrypoint, and domain-boundary prose), both derived
from the same source read. A repo with zero `agentteams` integration can unpack the zip and read
the canonical tree and generic bridge directly; a repo with a native adapter can later get
full-fidelity native rendering via `--interop-source-framework canonical`. Standalone mode,
mutually exclusive with every other standalone op (`--convert-from`/`--interop-from`/
`--bridge-from`/`--self`/`--fleet`/the Goose switch flags/`--recipe-check`/`--stale-check`/and
the other integrity-and-retention standalone ops) — see [`package_team`](api-reference/team-package.md)
for the full list.

### `--package-team SOURCE_DIR`

Source team directory to package. Must be a native framework source (`.github/agents`,
`.claude/agents`, etc.), not a canonical directory — a canonical source has no native framework
left to render the bundled generic bridge from; use `--bridge-from <canonical dir>
--bridge-source-framework canonical --framework generic` directly for that case instead.

### `--package-source-framework NAME`

Optional source framework override for `--package-team` (auto-detected when omitted). Requires
`--package-team`.

With `--package-team`, `--output` names the destination `.zip` **file** path (default:
`./team-package.zip`), not a directory as it does for every other mode. `--overwrite` allows
replacing an existing zip; without it, a second run against the same path fails rather than
silently clobbering the first.

---

## Multi-Framework Pinned Sync

Keep every agentic interface in sync through the canonical hub under one model: frameworks are
peers, a clean one-sided change in any framework projects to all others, and a genuine conflict
is **always** decided in favor of the bootstrap pin (and logged to
`.agentteams/sync-conflicts.log.csv` for after-the-fact review). Change detection is
commit-to-commit. See [`multi_sync`](api-reference/multi-sync.md) and
[`sync_pin`](api-reference/sync-pin.md).

### `--sync-init`

Bootstrap pinned multi-framework sync: seed the canonical hub from the `--pin` framework, project
it to every framework in the sync set, and record per-framework baselines. Writes
`.agentteams/pin.json`. Requires `--pin`.

### `--pin FRAMEWORK`

The bootstrap-pin framework for `--sync-init`. It seeds canonical and wins every conflict
thereafter (each conflict is logged for review). Peer *capability* changes are never silently
fanned out — they are withheld and logged, so capability grants only originate from the pin.

### `--frameworks FW1,FW2,...`

Comma-separated sync set for `--sync-init` (default: every registered framework). Required
whenever the default set would collide: `copilot-vscode` and `copilot-cli` render to the same
physical `.github/agents` directory and can genuinely disagree (handoffs kept vs. stripped), so
`--sync-init` refuses that combination outright rather than risk one silently overwriting the
other. List only one of the two. `--sync` reuses whatever set was recorded by `--sync-init`; it
has no separate `--frameworks` flag of its own.

### `--sync`

Run one on-demand sync pass: detect the frameworks changed since the last synced commit,
reconcile each into canonical (pin first, authoritatively; peers absorb clean non-capability
changes), project canonical to every framework, log conflicts, and advance the sync anchor. A
no-op when nothing changed.

### `--sync-since COMMIT`

Override the change-detection anchor (default: the pin's recorded `last_synced_commit`).

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error (validation failure, file not found, drift detected with `--check`, security issues with `--scan-security`, or a Tier-1 blocking finding with `--stale-check`) |
| `2` | Baseline drift detected (with `--check-baseline`) |
| `3` | Remediation attempted but unresolved (with `--stale-check --stale-remediate`) |
