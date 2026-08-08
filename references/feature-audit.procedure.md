# Feature Audit — Procedure

**Status:** Standing procedure. Effective 2026-08-07.
**Binds:** `@orchestrator`, `@code-hygiene`, `@test-suite-expert`, `@security`
**Engine:** `agentteams/feature_audit.py` · **Driver:** `scripts/run_feature_audit.sh`
**Registry:** `references/feature-registry.csv` · **Workflow:** `.github/workflows/feature-audit.yml`

---

## What this is

`docs_src/api-reference/feature-inventory.md` enumerates 146 features across 14 capability
areas. Until 2026-08-07 nothing verified that any of them still worked, and the inventory
said so itself: deriving its counts "requires a machine-readable feature set, which the
module does not yet carry."

`references/feature-registry.csv` is that set. It is **derived from the inventory body**, not
hand-written beside it — every feature there is `<n>. **Name**` under a `## Category`, so the
prose document remains the single authored source and the registry cannot drift from it
silently. A second hand-maintained list would have rotted exactly as the summary table did.

## What it found on day one

**1 of 146 features is proven. 145 are `UNPROVEN`.**

That is the honest baseline, and recording it is the point. A registry claiming 146/146 on
day one would be the same defect as a hand-written total that does not equal its own addends:
a number that looks computed and is not.

`UNPROVEN` does not mean broken. It means *nothing specifically demonstrates it works*. The
suite is large and much of it exercises these features incidentally; what is missing is the
binding that says which test proves which feature, and — critically — which test would fail
if that feature broke.

## The three rules that stop this becoming theatre

**1. `proven` is self-enforcing.** A row counts as proven only when it names both a
`proof_test` and a **distinct** `negative_control`. `load_registry` downgrades any row that
does not, so the word cannot be written into the column. A proof with no negative control
demonstrates that a test runs, not that it can fail.

**2. A tier that executes zero proofs is a finding, not a pass.** Without this an
all-`UNPROVEN` registry satisfies every structural check and exits clean — success declared
over nothing proven.

**3. Unreachable is a value, not an exception.** `agentteams/research/` is degrade-don't-raise
by policy, so a live probe asserting only "no error" passes on an empty degraded response.
Live proofs must assert **positive content**. A genuinely unreachable dependency reports
`UNREACHABLE`, which is never a failure and never gates — someone else's outage is not a
regression here.

## Outcomes

| Code | Meaning |
|---|---|
| 0 | **clean** — registry agrees with the inventory; every executed proof passed |
| 1 | **findings** — a proof failed, the registry drifted, or a tier executed zero proofs |
| 2 | **harness broken** — registry malformed or missing, engine died, unclassified exit |

**Indeterminate is not a pass. Code 2 outranks code 1.**

A `CLEAN` verdict answers *"did anything we claim to prove stop working?"* — **not** *"is
everything proven?"*. The report prints coverage separately and unmissably so the two are
never conflated.

## Cadence, and why it differs from the red team

The red-team audit is weekly because `tests/test_constitutional_redteam.py` runs its battery
on every CI run. That reasoning does not transfer, but the honest gap here is narrower than it
looks, and most of it is already covered daily:

- `security-maintenance.yml` (13:00 UTC) already drops `--security-offline` when the snapshot
  is ≥24h stale — CISA-KEV/EPSS live refresh is covered.
- `bridge-maintenance` (05:41), `bridge-watchdog` (06:11), `advisory-pr` (07:47),
  `pr-reminders` (08:00) are already daily.

Genuinely uncovered, and the whole justification for this job: `agentteams.research`
search/fetch/browser/scholar allowlists; **NVD proper** (both existing flag sets pass
`--security-no-nvd`); live PyPI enrichment; and registry↔inventory↔code drift on an idle
repository.

goose/OpenRouter is deliberately excluded from the workflow — neither the binary nor the key
exists on a runner, and adding that secret would create a standing credential surface days
after `3901093` removed one. It is a `local` tier.

## Raising coverage

The correct way to close an `UNPROVEN` row:

1. Identify a test that **specifically** demonstrates the feature.
2. Identify a **different** test that fails when the feature breaks.
3. Set `proof_test`, `negative_control`, `status=proven`.
4. Lower `MAX_UNPROVEN` / raise `MIN_PROVEN` in `tests/test_feature_registry.py`.

Do **not** point both columns at the same test — `load_registry` will downgrade it, silently
producing the opposite of the intended effect.

Genuinely unprovable items take `kind=not-provable` with a non-empty `notes`, mirroring the
`not-a-verifier` rows in `references/redteam-verifiers.csv`, so they do not jam the ratchet.

## What the audit never does

- **Never remediates.** An unattended job that writes fixes is a larger risk than the one it
  closes.
- **Never re-baselines itself.** The ratchet lives in the test suite as operator-maintained
  constants, not a file the workflow updates. A job that re-baselines itself absorbs the drift
  it exists to detect.
- **Never runs outside this repository.** A hard scope guard refuses any root that is not the
  agentteams checkout, so a consumer that vendored the script cannot fire it against itself.
