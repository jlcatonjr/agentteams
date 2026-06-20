# Systematic (Fleet) Agent-Update Lessons

**Purpose:** Hard-won lessons for running `agentteams --update --merge` across many repositories at once, and for *interpreting the results correctly* so a successful, non-destructive merge is never mistaken for a failure (or vice versa).

**Origin:** 2026-06-04 fleet update of ~38 local consumer repos under `~/githubrepositories/`. Every repo reported "ERROR" and tens of thousands of "outside-fence deletions"; a content audit of that run found **zero** user-authored content destroyed. The headline numbers were artifacts. This document exists so the next operator does not waste an hour re-deriving that.

> **Scope of the "no content lost" finding.** It is an *observation of the 2026-06-04 run under its flags* — `--update --merge` with the **default `--shrink-policy=preserve`** and **no `--prune`/`--overwrite`** — not a property of the tool under all flags. `--shrink-policy=warn`/`allow` will write a smaller body (warn keeps a `.lost.<sid>.md` recovery sidecar; allow does not), `--overwrite` replaces whole files, and `--prune` deletes orphaned agents. The audit signals below tell you whether *this* run was safe; the guarantee holds only while those default flags do.

Binds: **@orchestrator**, **@repo-liaison**, **@security**, **@quality-auditor**, **@technical-validator**. Read alongside [Fleet-Update Authorization Policy](fleet-update-authorization-policy.md), [Fleet-Update Scope Boundary](fleet-update-scope-boundary.md), and `docs_src/update-lifecycle-guide.md`.

---

## Lesson 1 — Exit code ≠ merge outcome

`agentteams --update --merge` writes every agent file first, then writes a set of **post-merge attestation artifacts** (delivery-receipt → eval-suite → memory-index, and model-routing **only when `--cost-routing` is passed**). A failure in any *post-merge* step exits non-zero **after the merge already succeeded and wrote every file non-destructively**.

Concretely, in the 2026-06-04 run the batch invoked `python3 build_team.py` under an interpreter that **lacked `jsonschema`**. The first attestation step (`_write_delivery_receipt`) did a hard `import jsonschema`, raised `ModuleNotFoundError`, escaped the non-fatal handler in `main()`, and crashed the process with a traceback — uniformly across the fleet. The merges were complete and correct. (The per-repo counts cited in this doc — e.g. "one USER-EDITABLE deletion," generated-file growth — are observations of that run, retained in session `tmp/`, not load-bearing constants.)

**Rules:**
- **Never equate `exit != 0` with "merge failed" or "content lost."** Derive merge safety from a *content* audit (Lesson 3), not the exit code.
- **Drive fleet updates through the `agentteams` console entry point**, not `python3 /path/to/build_team.py`. The console script runs under the interpreter where agentteams (and its deps, including `jsonschema`) is installed; an ad-hoc `python3` may resolve to a different environment that lacks them.
- The durable fix is in the tool itself: `_require_jsonschema` now degrades a missing module to the writer's own non-fatal error (see `build_team.py`), so the run exits 0 and prints `!  … write failed (build-log healed)`. If you see that notice, the **merge is complete**; only the attestation artifact was skipped and the next `--update` re-emits it. Using the console entry point *reduces* the chance of a missing dep but does not eliminate it (if agentteams was installed into the same interpreter that lacks `jsonschema`, the console script crashes identically) — the degrade is the real guarantee.

## Lesson 2 — A crash at the first attestation step skips the rest

Attestation writers run in sequence: delivery-receipt → eval-suite → memory-index (then model-routing, only under `--cost-routing`). The original hard crash at step 1 meant **none** of these were refreshed across the fleet. They were left **stale, not damaged** (pre-existing copies untouched). After updating the tool, re-running the fleet through the `agentteams` console entry refreshes them — notably `references/memory-index.json`, which `--query-index` retrieval depends on. Treat an interrupted fleet run as "agent files current, indexes possibly stale" until a clean re-run.

## Lesson 3 — Interpreting bulk diffs: the real safety signals

A naïve "any deleted line outside an `AGENTTEAMS:BEGIN/END` fence = data loss" check is **wildly misleading** at fleet scale (it reported 72,500 "deletions" for a run that lost nothing). Two reasons:

1. **Fully-generated, fenceless files** — `references/pipeline-graph.md`, `references/ref-*.reference.md`, `SETUP-REQUIRED.md`, `build-log.json` — have *no* fences because the **entire file** is regenerated each run. Every regenerated line counts as an "outside-fence" delete/add. This is intended behavior, not loss.
2. **Volatile intelligence churn** — `security-vulnerability-watch.*`, CISA KEV / NVD / EPSS rows in `security.agent.md`, `framework-watch.*` — refreshes every run by design.

**The authoritative signals are:**
- **Deletions inside a `USER-EDITABLE` region** of a file that *also* has fences (i.e. hand-authored content). This is the only category that represents real user-content risk. In the 2026-06-04 run there was exactly **one**, and it was an in-place *enhancement* (a line expanded, not removed).
- **Shrink-guard notices** (`Notice: … fence 'content' … lost concrete refs … template update suppressed`). The default shrink policy suppresses material template shrinks and keeps the enriched body, so a *real* content shrink is surfaced as a notice and *not applied*. Generated reference files should **grow or stay stable**, never materially shrink.
- **Net line-count per generated file** — every regenerated reference file in the safe run *grew*. A file that shrank materially is the thing to inspect.

Verification recipe used (kept in session `tmp/`): classify each deleted line by whether its old line number falls inside a fence / inside a USER-EDITABLE block in the `HEAD` version; flag only USER-EDITABLE deletions and material shrinks; spot-check that generated reference files grew.

## Lesson 4 — `--merge` reverts consumer-side workarounds that live in generated regions

`--merge` regenerates fenced/generated regions. If a consumer hand-edited *generated* content as a local workaround, regeneration reverts it. Observed case: `vk-services-local` had stripped the surrounding backticks from `#file:` references (commit "Align agent file references with audit parser") to satisfy a **buggy local audit parser**; the merge restored the canonical backtick-wrapped form and the repo's `pre-push` hook then blocked the push.

- **Canonical form is backtick-wrapped:** `` `#file:references/foo.reference.md` `` (agentteams' own agent docs use this — e.g. `.github/agents/code-hygiene.agent.md`).
- A consumer audit parser must extract the ref as `#file:([^\s`]+)`, **not** `#file:(\S+)` — the greedy `\S+` swallows the trailing backtick and reports a false "broken reference."
- **Do not** "fix" this by stripping backticks from the generated docs; that workaround is reverted on every update. Fix the consumer's parser (or refresh its `scripts/audit_agent_drift.py`).

## Lesson 5 — Per-repo `pre-push` hooks can block a fleet push

Some consumer repos install `.githooks/pre-push` (a structural drift audit + invariant guard). A fleet push must expect this and **surface the block**, not blanket-`--no-verify`. The hook is a safety mechanism; overriding it bypasses the invariant-section guard too. Treat a hook block like any other per-repo exception: report it, diagnose the specific finding, and resolve at the source.

## Lesson 6 — Descriptor selection (cross-reference)

The thin `.github/agents/_build-description.json` stub vs. the canonical `.agentteams/brief.json` trap is covered in depth in `docs_src/update-lifecycle-guide.md` ("Picking the Right Descriptor"). At fleet scale: **prefer `.agentteams/brief.json` whenever present** (run with `--no-scan --project <repo>` per the project's `.agentteams/README.md`), and never `--prune` a repo with hand-authored specialist agents.

---

## Backups — the correct path

Each `--update` writes a timestamped backup **relative to the output directory** (see `agentteams/emit.py`):

```
<output_dir>/.agentteams-backups/<YYYYMMDD-HHMMSS>/
```

i.e. for the default copilot-vscode layout (`--output .github/agents`): `.github/agents/.agentteams-backups/<timestamp>/`. A repo built with a non-default `--output` will have its backups under *that* directory, not `.github/agents/` — any "verify backup exists" check must derive the path from the run's output dir, not hardcode it. (It is **not** a top-level `.backups/` directory — older governance docs that said so were wrong; corrected 2026-06-04.) Do not commit backup directories.

## Fleet-update operator checklist (corrected)

- [ ] Run through the **`agentteams` console entry point** (deps present), or confirm the chosen interpreter imports `jsonschema`.
- [ ] Per-repo auto-backup present under `.github/agents/.agentteams-backups/<ts>/`.
- [ ] `--update --merge` (non-destructive); no `--prune`; `--overwrite` only with explicit clearance.
- [ ] Status derived from a **content audit**, not exit code: zero `USER-EDITABLE` deletions, zero material shrink notices, generated reference files did not shrink.
- [ ] Per-repo `pre-push` hook blocks surfaced and resolved at source (not blanket `--no-verify`).
- [ ] Indexes/attestations refreshed by a clean (exit-0) run before relying on `--query-index`.
