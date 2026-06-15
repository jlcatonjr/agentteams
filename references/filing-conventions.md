# Repository Filing Conventions

> **Read this before creating any plan, report, investigation, or summary document.**
> Enforced by `tests/test_root_doc_hygiene.py`. Linked from the root `CLAUDE.md`.

## TL;DR

**Plan / investigation / feasibility / change-report documents must NEVER be
written to the repository root.** Write them to one of the two homes below.
The repo root holds only a small, fixed set of canonical project files.

## Where each document type belongs

| Document kind | Home | Tracked? | Notes |
|---|---|---|---|
| **Active operational plans + step CSVs** (the orchestrator's "every multi-step request generates a plan" output) | `tmp/by-week/YYYY-Www/<slug>.plan.md` + `.steps.csv` | ignored (ephemeral) | The channel the generated-team orchestrator targets ([orchestrator.agent.md](../.github/agents/orchestrator.agent.md)). |
| **Retained local plans, feasibility reports, investigations, change reports** | `references/plans/<slug>.plan.md` / `.report.md` / `.steps.csv` | ignored (local, not for publication) | Where a plan rests once it is durable but still internal. May contain machine-specific paths / org names. |
| **Durable, published reference material** (policies, bridge docs, advisories, audit closeouts) | `references/*.md`, `references/<topic>/` | tracked | Published with the package. No machine-specific paths. |
| **User-facing documentation** | `docs_src/` (built to the MkDocs site) | tracked | Add to `mkdocs.yml` nav. |
| **Daily / weekly / monthly work summaries** | `workSummaries/` | ignored | Per-date session records. |
| **Scratch / one-off** | `tmp/` | ignored | Sanitize before promoting anything out of `tmp/`. |

### Naming convention inside `references/plans/`

- Plans: `<slug>.plan.md` (optionally `<slug>-YYYY-MM-DD.plan.md`)
- Reports/investigations: `<slug>.report.md`
- Step lists: `<slug>.steps.csv`

### The only documents allowed at the repository root

`README.md`, `CHANGELOG.md`, `CLAUDE.md`, `SECURITY.md`, `STABILITY.md`,
`LICENSE`, `MANIFEST.in`, `mkdocs.yml`, `pyproject.toml`, and the two
deliberately-canonical artifacts:

- **`build-team-plan.md`** — the implementation/architecture plan; it is part of
  the memory-index source set (`MEMORY_INDEX_EXTRA_DOC_NAMES` in
  [artifacts.py](../agentteams/cli/artifacts.py)) and is referenced by the
  generated team instructions. Its companion `build-team-steps.csv` stays with it.
- **`bridge-offline-investigation.md`** — a deliberately-audited investigation
  kept at root by maintainer decision (commit `9716b47`). The lone allowlisted
  investigation; revisit whether it should move to `references/plans/`.

Any other `*.md` at the root fails the guard.

## Why this exists — root cause of the "stray plan docs" problem

Multiple concurrent autonomous Claude Code sessions run against this repo. Each
follows the standing rule *"every multi-step request must generate a plan."* That
rule lived **only** inside the generated-team instructions
(`.github/agents/orchestrator.agent.md`, `.github/copilot-instructions.md`),
which target `tmp/by-week/…`. A **direct** in-repo session does not read those
generated agent files; the root `CLAUDE.md` said nothing about plan placement;
and **no guard existed**. So each session wrote its plan to the current working
directory — the repo root. The accumulation (and the one-off `.gitignore` line
for `pypi-release-plan.md`, an ignore-in-place band-aid) was the symptom.

The fix is three-part: (1) state the convention where in-repo sessions read it
(root `CLAUDE.md` → this file), (2) a guard that fails on root strays, (3) the
canonical homes above.

> **Note on already-running sessions:** a session that is already running has its
> context loaded and will not absorb this convention mid-flight. It prevents
> *future* sessions from dropping strays; the guard catches any that slip through.

## Remediation record (2026-06-15)

Relocated to `references/plans/` (un-tracked from the published repo, kept locally):

| Was (root) | Now (`references/plans/`) |
|---|---|
| `refactor-plan.md` | `refactor-security-code-hygiene.plan.md` |
| `refactor-next-phases.md` | `refactor-next-phases.plan.md` |
| `refactor-remaining-plan.md` | `refactor-remaining.plan.md` |
| `goose-integration-plan.md` | `goose-integration.plan.md` |
| `continue-dev-integration-report.md` | `continue-dev-integration.report.md` |
| `CHANGES_2026-05-27.md` | `change-report-2026-05-27.report.md` |
| `pypi-release-plan.md` | `pypi-release.plan.md` |

Also: updated stale citations in `agentteams/frameworks/goose.py` and
`bridge-offline-investigation.md`; removed the `pypi-release-plan.md` `.gitignore`
band-aid; un-tracked the two legacy tracked files under `references/plans/` so the
directory is uniformly local.

### Known follow-ups

- **`security-waiver-remediation-plan.md`** (root) — left in place because a live
  session owned it during remediation; **temporarily allowlisted** in
  `tests/test_root_doc_hygiene.py`. Move it to
  `references/plans/security-waiver-remediation.plan.md` and remove the temporary
  allowlist entry once that session is done.
- **`docs_src/structural-update-plan.md`** — tracked, in the MkDocs nav, *and*
  carries a (now-ineffective) `.gitignore` line. Decide: publish it (drop the
  ignore line) or relocate it to `references/plans/`. Left untouched here.
