# Detecting Stale Docs & Scripts (`--stale-check`)

`agentteams --stale-check` is a **read-only** scan that flags stale agent
documentation and stale code/scripts in a repository. It never edits files. The
companion `--stale-remediate` prints a *suggested* remediation plan (still no writes).

```bash
# Scan the current repo (or pass --output / --project to point elsewhere)
agentteams --stale-check

# Also print a guided remediation plan
agentteams --stale-check --stale-remediate

# Skip the git-history signal (hermetic / CI / non-git targets)
agentteams --stale-check --stale-no-git
```

The exit code **is** the verdict: `0` when clean, `1` when any **Tier-1 (blocking)**
finding is present.

## What it detects, by reliability tier

Findings are ranked by how confidently the signal implies staleness — and the tier
governs what action is safe.

| Tier | Code | Meaning |
|---|---|---|
| **1 — blocking** | `VCS_CONFLICT_MARKER` | A complete, ordered git merge-conflict triad (`<<<<<<< … ======= … >>>>>>>`). Setext underlines and fenced example blocks are excluded. |
| **1 — blocking** | `BROKEN_REF` | A markdown-link target that resolves to no file on disk. |
| **1 — blocking** | `INTEGRITY` | A generated file is `FENCE-BROKEN` / `TRUNCATED` / `MISSING` (reuses the `--verify-integrity` check for every discovered `build-log.json`). |
| **1 — blocking** | `SOURCE_DRIFT` | A bridge's source agents diverged from the recorded manifest snapshot (only when the source is present on this machine). |
| **2 — advisory** | `STALE_VS_CODE` | Referenced code changed in a commit **after** the doc's last commit (substantive, whitespace-filtered diff). The doc *may* describe outdated behavior. |
| **2 — advisory** | `BROKEN_REF` | An inline-code path or an anchored reference (`file.py#L10`) that does not resolve. |
| **0 — info** | `PROVENANCE_ABSENT` | No build-log / bridge manifest found — generated-file integrity can't be verified. |
| **0 — info** | `BRIDGE_SOURCE_UNAVAILABLE` | A bridged consumer repo whose `source_dir` is not present here; run `--bridge-check` where the source lives. |

## How freshness is judged from git history

The `STALE_VS_CODE` signal uses commit history, not file mtimes (which a checkout
resets). For each doc that references code, it asks: *did the referenced file change in
any commit strictly after the doc's last commit, with a substantive diff?* This is
deliberately **advisory** — a recent code commit is a suspicion, not proof, so it never
triggers an automatic edit. The scan **self-disables** the git signal on:

- non-git targets (`recency: unavailable:non-git`),
- shallow clones (`unavailable:shallow`) — commit times are unreliable there,
- paths with uncommitted changes (an in-progress fix would otherwise false-positive).

A revert or cherry-pick that restores the documented content is filtered out, because
the whitespace-filtered diff against the doc's commit comes back empty.

## What it scans

In a git work-tree the file set comes from `git ls-files`, so gitignored trees
(`tmp/`, backups, local `references/plans/`) are excluded automatically. The
`examples/`, `workSummaries/`, and `tmp/` directories are always skipped (fixtures /
archival / scratch). Known live-data / generated files are suppressed from all but
conflict-marker findings.

## Suppressing known-acceptable findings (`.agentteams-stale-ignore`)

Some "broken" references are intentional — cross-repo links in a dated incident review,
or files inside a read-only captured package. Put a gitignore-style file named
`.agentteams-stale-ignore` at the scan root to suppress them:

```
# cross-repo references in a historical incident review
docs/aws_ecs_failure_review_2026-04-14.md
# a read-only verbatim capture package (whole dir)
docs/canonical-agency-package/
```

A finding is suppressed when its **referrer file** matches a pattern, or (for a broken
reference) the **referenced target path** matches. Patterns support an exact path, a
**directory prefix** (`dir` or `dir/` matches the dir and everything under it), and `*`
globs. Suppressed findings are **counted and shown** (`suppressed: N`) — never silent.

Two important properties:

- **`VCS_CONFLICT_MARKER` is never suppressible** — a complete conflict triad is always
  broken, so an ignore pattern can't hide it (even if it matches that file).
- **Suppression can change the exit code.** Dropping a Tier-1 `BROKEN_REF` removes a
  blocking finding, so a previously-`1` exit becomes `0`. CI authors: a passing
  `--stale-check` may reflect entries in `.agentteams-stale-ignore`.

Precedence: the always-skipped trees (`examples/`/`workSummaries/`/`tmp/` and gitignored
files) are pruned *before* scanning, so an ignore entry can only suppress findings that
were actually produced — it can't "un-skip" those trees.

## Remediation & revision

`--stale-remediate` (without `--yes`) is a **preview** — it prints, per actionable
finding, the safe next step and writes nothing.

Adding **`--yes`** promotes it into an **applied, backup-protected revision pass**:

```bash
agentteams --stale-check --stale-remediate            # preview (no writes)
agentteams --stale-check --stale-remediate --yes      # apply safe revisions
agentteams --stale-restore                            # recover the latest snapshot
```

Before touching anything, the revision pass writes a **sha256-verified safety snapshot**
of every file it will modify to `.agentteams-backups/stale-fix-<timestamp>/`. Then it
performs only safe, deterministic revisions:

- `BROKEN_REF` → **repaired** when the missing target has exactly one relocated match
  (a moved file), by rewriting the link — **never** inside a fenced or USER-EDITABLE
  region. With no unambiguous target it stays manual.
- `SOURCE_DRIFT` → **re-merged** via the canonical fence-aware `--bridge-merge` writer
  (never `--bridge-refresh`).
- `INTEGRITY` → **routed**: the exact `agentteams --update --merge …` command is printed,
  not auto-run (it needs the brief and could rewrite the whole team).
- `VCS_CONFLICT_MARKER` → **manual only**; conflict markers are never auto-resolved.

Exit `3` signals "revision applied, but blocking items still need manual/routed handling."

### Recovery (safety protocol)

If a revision goes wrong, recover from the snapshot:

```bash
agentteams --stale-restore            # restore the latest stale-fix snapshot
agentteams --stale-restore 20260619-161940   # or a specific snapshot
```

`--stale-restore` verifies each backup's sha256 before writing and refuses (writing
nothing) if a backup is corrupt — a fail-safe restore. In a git work-tree, `git checkout`
remains an additional backstop.

> **Tip:** ensure `.agentteams-backups/` is gitignored in the target repo so snapshots
> aren't accidentally committed.

> Methods report, implementation plan, and the adversarial + conflict audits that
> shaped this feature live under `references/plans/stale-detector-*` in the source repo.
