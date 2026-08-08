# Feature Audit — Handoff

**For:** the next agent continuing this collection of audits.
**Companion to:** `references/feature-audit.procedure.md` (what the audit *is*). This file is
what a *continuing* agent needs that the procedure does not say.

> **Volatile state is stamped, not asserted.** Everything in §4 is true as of commit
> `010959d` on branch `feat/feature-audit-effectiveness` (2026-08-08). Counts go stale within
> a day — re-derive them, do not trust them. Durable guidance (§2, §3, §7, §8) does not expire.

---

## 1. Read these first, in this order

1. `references/feature-audit.procedure.md` — the rules the audit must keep.
2. `agentteams/feature_audit.py` — the module docstring states three design constraints and
   why each exists. Constraints 2 and 3 were each violated inside this very implementation
   before being fixed; the comments record where.
3. **The plan.** `tmp/by-week/2026-W32/feature-audit-effectiveness.plan.md` — **read
   REVISION 2 at the end first**; revision 1's architecture was wrong and is kept only so
   the reasoning stays auditable.
   ⚠ **`tmp/` is gitignored (`.gitignore:45`).** The plan and its steps CSV exist **only in
   the main working clone**, under `<repo-root>/tmp/by-week/2026-W32/`. A git worktree will
   not contain them, and neither will a fresh clone.
4. This file.

## 2. The operating instruction that matters most

**Every plan in this workstream was audited before execution, and every one came back
`CLEARED FOR EXECUTION: NO` on the first pass.** Not once did a plan survive review
unchanged, and the findings were premise-level, not stylistic:

- a plan built on "125 features" when the inventory body enumerates **146**;
- a findings ledger whose storage location **could not exist** (branch protection);
- a live tier that **could not execute** (dependency in an unrequested extra);
- a justification citing a gap an **existing daily job already covered**;
- copying a precedent's filing cabinet while **skipping its routing**;
- and this very handoff, whose first draft told the reader to use a worktree *and* to read
  files a worktree cannot contain.

Run `@adversarial` and `@conflict-auditor` in parallel on every plan, and again after every
implementation block. Budget for the plan being wrong. It has been, every time.

## 3. The recurring defect shape

**Nearly every defect found here was a check that cannot fail** — not a crash. (The one
exception was a packaging `ImportError`, step E.6.) If you are hunting what breaks next,
look at the green things:

| Found | Shape |
|---|---|
| Zero-proof guard checked all tiers combined | One populated tier masked two empty ones |
| Summary table checked its total against **its own column** | Internally consistent, described nothing |
| `negative_control` was never executed | "Non-tautological" was a naming convention |
| A **skipped** proof reported PASS | pytest exits 0 on all-skipped |
| `xfail` reported PASS | Exits 0 while demonstrating the opposite |
| Conflict-marker guard regexed shell redirection only | A Python writer walked past it |
| Flat skills emitted for months | Degrade-to-grep made "absent" and "working" identical |
| Red-team E3 probe read a **gitignored** file | Green locally, red in CI, baseline machine-contaminated |

The question that catches all of these: **what would have to break for this to go red?** If
the answer is "nothing reachable," it is decoration.

## 4. State — as of `010959d`, re-derive before relying on it

| Artifact | Notes |
|---|---|
| `references/feature-registry.csv` | 151 rows, 6 proven — **derived** from the inventory body, not hand-written |
| `agentteams/feature_audit.py` | engine: per-tier guard, 4 outcome classes, executes negative controls |
| `scripts/run_feature_audit.sh` | driver: 0/1/2 outcomes, hard scope guard (see §7 trap) |
| `.github/workflows/feature-audit.yml` | daily 04:37 UTC, `contents: read`, tiers `unit,e2e,live` |
| `tests/test_feature_registry.py` | 31 tests, incl. 6 malformed-registry shapes → exit 2 |
| `tests/test_e2e_probes.py` | 4 offline CLI probes (~110s) |
| `tests/test_live_probes.py` | network; env-gated on `FEATURE_AUDIT_LIVE` |
| `tests/test_feature_audit_workflow.py` | workflow contract (no cancel, always-report, cron collision) |

Plan: 9 done, 1 blocked, 2 skipped, 22 pending. Branch `feat/feature-audit-effectiveness`
carried **5 commits ahead of `origin/main`, unpushed, no PR**. Suite was green at this commit.

**Ratchet constants live in `tests/test_feature_registry.py:31-32`** (`MIN_PROVEN`,
`MAX_UNPROVEN`) — operator-maintained on purpose, never written by the audit.

### Coverage — read the split, not the percentage

```
  COVERAGE           : 6/151 proven (4.0%) — 1 product, 5 audit self-check
```

**Product-proven is 1.** Five of six are the audit's own machinery. `MIN_PROVEN` was ratcheted
1→6 funded entirely by self-registration; an auditor caught it, and the line was split so it
can never be quoted as product assurance. **Do not let the headline rise on self-check rows.**

## 5. How to actually run it

```bash
# From the MAIN CLONE — not a worktree (§7).
pip install -e '.[research]' -e '.[test]'      # both, or the live tier ImportErrors

python -m agentteams.feature_audit --tiers unit,e2e      # structural + proofs
python -m agentteams.feature_audit --structural-only     # parity/resolution only
bash scripts/run_feature_audit.sh                        # driver, with 0/1/2 classification

FEATURE_AUDIT_TIERS=unit,e2e,live bash scripts/run_feature_audit.sh
FEATURE_REGISTRY=/path/fixture.csv python -m agentteams.feature_audit   # test override
```

`FEATURE_AUDIT_LIVE=1` is set automatically when the `live` tier is requested (both in
`_main()` and the driver). Setting it by hand turns the general suite into a network suite.

## 6. Live finding, still open

`python -m agentteams.research search` returns `[]` and **exits 0** — measured 2026-08-07
from a residential IP, so not merely a datacenter-IP effect:

```
provenance: backend=none cached=false query_used='python packaging' tried=duckduckgo,ddg_lite
[]
```

Both DuckDuckGo backends dead; `scholar` unaffected. Logged at
`references/agentteams-remediation-log.csv`. **Search is deliberately NOT registered as
proven** — registering a dead feature is the tautology the registry exists to prevent.

An auditor argued `backend=none` should **not** classify as `UNREACHABLE`, because it is this
repo's own statement that its entire curated backend list is useless — a broken integration,
not weather. Unresolved, and it is the substance of **E.15**. As it stands, **the audit can
never go red for the only real failure it has found.** Decide this early.

## 7. Traps — each cost real time

- **⚠ The audit refuses to run in a git worktree.** `scripts/run_feature_audit.sh:49` requires
  `basename "$ROOT_DIR" == "agentteams"`, so a worktree named anything else exits **2 =
  HARNESS BROKEN** inside a perfectly valid checkout. Use a worktree to *edit* alongside a
  concurrent session, but change directory to the main clone to *run* the audit — or relax
  the guard to a marker-file check.
- **`--self` defaults `--output` to `.github/agents`** (`agentteams/cli/app.py:330-331`). A
  `--self --refresh-code-index` silently builds the *copilot* tree and reports success.
- **`check_parity` is bidirectional** (`feature_audit.py:298-301`). A registry row with no
  inventory entry is a permanent finding: **inventory and registry must move in the same
  commit, always.**
- **The summary table is generated.** Hand-editing it fails a test; regenerate from the registry.
- **Cadence in a filename is a recorded anti-pattern** (`run_redteam_audit.sh:5-10`, renamed
  away from `run_daily_*`). Do not reintroduce it.
- **A bot cannot push to `main`** (`required_pull_request_reviews`). Any design needing durable
  committed state from a workflow is unbuildable as-is; `advisory-pr.yml:71-80` commits to a
  side branch, where per-finding `first_seen` never accrues.
- **`workflow_dispatch` requires the workflow on the default branch.** This is why **E.4 is
  blocked** — the 10× runner measurement cannot be taken from a feature branch. **It unblocks
  automatically once this merges**, and the first daily runs supply the distribution.
- **`_looks_unreachable` must stay narrow.** It is the excuse mechanism; widen it and the live
  tier stops meaning anything. Currently transport errors, `403`/`202` challenges, `429`/`503`
  rate-limit/outage, and `backend=none`. A test asserts a plain `AssertionError` is *never* excused.
- **A concurrent agent session shares this clone.** It has swept commits into unrelated ones,
  switched branches under an active session, and pushed. See `references/git-procedures.md`
  §A.2.1. Prefer a worktree for editing (subject to the first trap).
- **The security gate scans every write.** Embedding an absolute home path HALTs it — the first
  draft of this very file was rejected for exactly that. Use repo-relative paths.

## 8. Deliberate non-decisions — do not "fix" these

- **The audit never writes a fix.** Both `run_redteam_audit.sh` and the procedure state why: an
  unattended job that writes remediation code is a larger risk than the one it closes. Routing
  findings to a human is the goal; auto-fixing is not.
- **No goose/OpenRouter on runners.** That secret would create a standing credential surface
  days after `3901093` ("no default credential path") removed one. Local tier only.
- **The pre-commit hook was left alone.** `maybe_install_git_hooks` is default-on, so any change
  ships to every consumer, and the block is non-blocking by contract (`exit $_at_rc`). A
  fleet-wide commit-time scan needs its own clearance.
- **`UNPROVEN` is not a finding.** It is a coverage gap with its own ratchet. Conflating it with
  regressions would bury real failures under 145 backlog items on day one.
- **~100 features will likely stay UNPROVEN.** Each `negative_control` must be hand-authored and
  cannot be derived; honest end state is roughly 30–45 proven. Say so rather than implying a
  campaign that stalls at batch two.

## 9. What to do next, in order

**E.11 → E.18 is the half that turns this from a status board into maintenance.** Findings
currently die in a job log. The routing mechanism is already **implemented** in this repo
(implemented and precedented — not *proven*; nobody has watched it fire):
`.github/workflows/redteam-audit.yml:60` grants `issues: write`, and the step at `:115-201`
opens, labels (`:130`), dedupes (`:154`), updates (`:169-172`) and closes (`:174-191`) a
labelled issue. Copy that.

Order is C-5 and matters: `@security` clearance (E.11) → **recorded** in
`references/security-decisions.log.csv` (E.12) → then implement (E.13). Clearance precedes the
capability, and a clearance existing only as turn text is not recorded.

Then **E.20**, the time-bound ratchet — `MIN_PROVEN`/`MAX_UNPROVEN` only forbid *growth*, so
145 features can stay invisible forever with every check green. A ratchet that fails when
`MAX_UNPROVEN` has not decreased in N weeks is what makes maintenance *regular*. Note the date
constant is itself bypassable by a one-line bump; derive it from `git log -S MAX_UNPROVEN` or
it is ceremony.

Then E.21 (watchdog as a **separate** workflow — inside the audit it never runs when the audit
stops), E.22 catch-up, E.23 CHANGELOG (`changelog-link.yml` gates PRs), then coverage batches.

## 10. Open questions for the operator

1. **Should `backend=none` gate?** (§6) Today the audit cannot go red for its only real finding.
2. **`pytest-cov`** as a dev dependency to map feature→module→test — touches the stdlib-only
   style rule. Steps E.28/E.29.
3. **Push and PR.** The branch was unpushed with no PR at handoff.
4. **`transcriber`** (unrelated, still outstanding): no descriptor, no git, 0 `SKILL.md`.

## 11. Related

`references/feature-audit.procedure.md` · `references/redteam-audit.procedure.md` (the pattern
this follows) · `references/agentteams-remediation-log.csv` (open findings) ·
`references/git-procedures.md` §A.2.1 (shared-clone protocol) ·
`docs_src/api-reference/feature-audit.md`
