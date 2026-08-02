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
| **AgentTeamsModule-itself remediation backlog** (Post-Deliverable Retrospective findings about the tool itself — template library, pipeline, CLI) | `references/agentteams-remediation-log.csv` | tracked | Self-referential destination exception documented in `agentteams/templates/universal/retrospective-remediation.reference.template.md`; ordinary generated projects log to their own `<output_dir>/references/agentteams-remediation-log.csv` instead (created via `agentteams/liaison_logs.py`'s `init_csv_stubs()`). Append-only; `status` transitions are maintainer-owned. |
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
- **`AGENTS.md`** — the SHARED, multi-tool standard entry file. When this project
  is bridged or generated for Goose (or the `agents-md` framework) it is written
  to the repo root by design (Goose reads it via `CONTEXT_FILE_NAMES`). It is a
  bridge-owned, fenced canonical entry file, not a stray document. See
  [bridge-refresh-safety.md](bridge-refresh-safety.md).

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

- **`security-waiver-remediation-plan.md`** — **resolved**: relocated to
  `references/plans/security-waiver-remediation.plan.md` (retained-local) and the
  temporary `tests/test_root_doc_hygiene.py` allowlist entry removed.
- **`docs_src/structural-update-plan.md`** — tracked, in the MkDocs nav, *and*
  carries a (now-ineffective) `.gitignore` line. Decide: publish it (drop the
  ignore line) or relocate it to `references/plans/`. Left untouched here.

---

## Close-out obligation: reconcile the remediation log

A round that fixes a defect recorded in `references/agentteams-remediation-log.csv` **must close
that row before the round closes** — setting `status` to `shipped`, with `resolved_date` and a
`resolved_evidence` string that points at something a later reader can check (a test path, a
module, a commit or PR reference, or a stated verification result).

**Why this is a rule and not a nicety.** The log spent most of its life with every row reading
`open`, including rows fixed months earlier. On 2026-07-30 it gained closure columns for exactly
that reason. Over the three rounds that followed, roughly a dozen more items were fixed and *not
one* row was closed, because nothing in the process invoked the new columns — so the next
enumeration reported 48 open rows of which 13 were already done, and the reader had no way to
tell which. Prioritising from that list wastes effort on solved problems, and every estimate
built on it is wrong by an unknown margin.

Enforced by `tests/test_session_closeout_obligation.py`, which checks that closures are
evidenced, that dates are coherent, and that the reconciled share of the log does not collapse
back toward zero. It deliberately does **not** try to guess which rows *should* be closed — an
automated guess that closes a live row is worse than a stale open one.

Statuses follow the documented lifecycle in
`agentteams/templates/universal/retrospective-remediation.reference.template.md`:
`open` → `triaged` → `shipped` | `wontfix`.

---

## Scope drift: when an end-to-end exit criterion forces out-of-scope work

A plan whose exit criterion is a **live end-to-end run** will, often enough to be worth planning
for, discover real defects in modules it declared out of scope — and its Non-goals then become
false mid-flight while the diff proceeds anyway.

Observed 2026-07-24 in `goose-retrieval-capability-gap`: the plan declared "not redesigning
`agentteams.research` itself", then its end-to-end step surfaced two genuine defects *inside* that
module (an HTTP-202 challenge indistinguishable from a no-results response; a 40 KB download cap
silently returning navigation chrome). Both had to be fixed for the exit criterion to be reachable
at all. The mid-flight `10b`/`10c` step renumbering was the visible symptom of scope the plan's
structure had not anticipated.

When this happens:

1. **Amend the Non-goal explicitly.** Edit it to say what is now in scope and why the exit
   criterion forced it. Leaving a Non-goal standing while the diff contradicts it makes the plan
   unusable as a record — a later reader cannot tell whether the scope changed or the rule was
   ignored.
2. **Record the scope change as its own numbered step** in the steps CSV, with the exit criterion
   that forced it in `notes`. Renumbering existing steps to slot work in (`10b`, `10c`) hides the
   decision inside what looks like ordinary sequencing.
3. **Notify `@repo-liaison` if the newly-touched module is shared with other repositories.** The
   original plan's blast-radius assessment was made against the declared scope, and no longer
   holds.

This is not a licence to widen scope opportunistically. It applies only where the *declared exit
criterion cannot be reached* without the out-of-scope fix — that condition is what makes the
change forced rather than chosen, and it belongs in the amended Non-goal as the justification.
