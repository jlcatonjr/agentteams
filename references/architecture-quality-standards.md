# Architecture-Quality Standards (from the red-team scoring research)

**Date:** 2026-08-18 · **Status:** standing reference — the tool-wide application of the quality
principles the RedTeamModelScoring research established for one instrument
(`research/redteam-model-scoring/Projects/RedTeamRigorProgram/`). Implements the standing-practice
items of `references/plans/architecture-quality-development.plan.md` (Plan 2). Two Plan-2 items are
code, not standards, and are referenced here rather than restated:
- **Provenance stamping** (Plan 2 #4) → `agentteams/provenance.py` (a reusable stamp; first consumer
  is `scripts/redteam_model_ratings.py`, which now emits a `*.provenance.json` sidecar).
- **Enforcement meta-evaluation catch-rate** (Plan 2 #3) → `scripts/redteam_enforcement_catch_rate.py`
  (currently **6/10** enforcement modules have a ledgered planted-defect verifier; the 4 uncovered
  are named by the reporter — the honest gap).

Framing (carried from the research): naming a standard buys *legibility and checkability, not
validity*. A metric is not sound because this doc lists it; it is sound when it can be checked
against the requirement below.

## 1. Construct validity for every selecting/gating metric (Plan 2 #1; research F6)

**Requirement.** Any metric that *ranks, selects, or gates* must state (a) the construct it claims to
measure, (b) evidence it measures that and not merely a correlate, and (c) a sensitivity note (does
the conclusion survive a reasonable change of weights/thresholds?). "Measures the right thing" and
"is reliable across runs" are two separate, both-required checks (Kaner and Bond 2004; Tabassi 2023).

**Registry — the tool's selecting/gating metrics and their status:**

| Metric | Construct claimed | Construct-validity status |
|---|---|---|
| `security_score` (`redteam_model_ratings.py`) | resistance to authority-spoofing prompt injection | **Documented** — construct argued in the methodology paper; sensitivity check standing (`scripts/redteam_weight_sensitivity.py`) |
| `reliability_score` | ISO/IEC 25010 maturity+fault-tolerance | **Partial** — construct named; `fault_tolerance` currently non-varying (single-component in practice), disclosed in its provenance stamp |
| `coverage.py` taxonomy coverage | share of a taxonomy exercised | **Present** — reports covered/uncovered explicitly; the *density* criterion (attacks/leaf) is now present too (`agentteams/redteam/coverage.py::compute_density`, F2/H1, 2026-08-18) |
| `ai_bad_habits.py` prose signals | LLM-prose anti-patterns | **TODO** — no stated construct or false-positive rate; owes a construct + error-mode note |
| `feature_audit.py` verdicts | architecture-conformance findings | **TODO** — owes a construct/failure-mode note (see §2) |

**Standing action:** the two TODO rows are the open work; new selecting/gating metrics must arrive
with all three of (a)/(b)/(c) or be marked TODO here.

## 2. Oracle-failure-mode disclosure for every auto-judge (Plan 2 #2; research F4)

**Requirement.** Anything that *auto-judges correctness* is an oracle, and a test regime is only as
sound as its oracle (Barr et al. 2014). Each auto-judging surface must document its known
false-positive and false-negative modes; where it drives a gate, it owes measured error behavior.

**Registry — the tool's auto-judges:**

| Auto-judge | Kind | Documented failure modes? |
|---|---|---|
| red-team `score_response` (verdict/compliance) | model-output | **Yes, and openly** — D1/D7 OPEN and disclosed; kept human-read for the load-bearing `auth-01` call. **Oracle reliability now MEASURED and ROBUST (2026-08-18):** `scripts/redteam_oracle_intercheck.py` ran two independent second-raters over the 23 preserved responses — `openai/gpt-4o-mini` (**κ = 0.913**, raw 95.7%) and `google/gemini-2.5-flash` (**κ = 0.911**, raw 95.7%) — both near-perfect agreement with the human-read verdicts. Each judge disagrees on a *different* single case, and both are hedged/borderline responses (llama-3.3 "recommends verification"; mistral-large "conditional compliance with spoofing warning") — so the residual uncertainty is precisely located on ambiguous hedges, not random noise. Total spend ~$0.01. Meta-finding: parsing a judge's own verdict was itself an oracle problem (fixed with a CORRECT/MISSED prompt + a None-not-guess parser). Still open: a gold-set validating the *automated scanner* directly. |
| `scan.py` (secrets/PII/entropy) | deterministic | **Partial** — the SEC-02 path false-positive is fixed and regression-tested; the residual (a keyword-free base64 blob split by a `/` below threshold) is documented in the fix. Owes a consolidated failure-mode note. |
| `fences.py`/`unfenced.py` (fence integrity) | deterministic | **TODO** — no failure-mode note; also uncovered by the verifier ledger (§ catch-rate). |
| `coverage.py` | deterministic (diff) | **Low-risk** — reports, does not gate; failure mode is a stale taxonomy file. |
| `conflict`/`quality` auditor agents | LLM-judgment | **Implicit** — mitigated by read-only tool grants + independent-verification (Rule 13); owes an explicit "these can be wrong; verify their cited facts" note (already Rule 14 in spirit). |

**Standing action:** the TODO/Partial rows owe a failure-mode note; a model-based auto-judge that
gates must publish an error rate before it is trusted unattended.

**Applied (2026-08-18) — the attack-generation harnesses (F2/F3/F10) instantiate this section.**
`scripts/redteam_attack_gen.py`'s scorer-sensitivity gate is the concrete form of "publish an error
rate": it measures `score_response`'s recall on a tuned/novel capitulation panel (0.667 measured),
VOIDs a run if the scorer is wholly blind, and stamps every automated capitulation count as a LOWER
BOUND with non-refusals flagged for adjudication — so H2/H3 never report a novel-attack success
count as if it were exact. H3's review judge is deliberately a *distinct* auto-judge
(`review_candidate_validity`) that does not inherit `score_response`'s D1/D7 defects. Full design +
three-way audit: `tmp/by-week/2026-W34/attack-generation-harnesses.plan.md`; dual-use surface owned
in `references/redteam-threat-model.md` §5.

## 3. One unified fault→error→failure taxonomy (Plan 2 #5; research: Avižienis et al. 2004)

The tool has *partial*, unlinked fault vocabularies: `errors.py` (a domain exception hierarchy) and
`AuditFinding.severity` (`error`/`warning`/`info`). This is the single named model they should all
reference:

- **Fault** — the root cause on disk/in config (a corrupted fence, a stale index, a mis-weighted
  metric, a bad adapter template, a tampered enforcement module).
- **Error** — the fault activated into a wrong internal state (an emit produces a malformed agent
  file; a score is computed on discarded data).
- **Failure** — the error observed at the boundary (a generated team is wrong; a rating misranks a
  model; an enforcement control passes a payload it should HALT).

**Severity maps to where in the chain a defect is caught:** a *fault* caught before activation is
`info`/`warning`; an *error* caught before the boundary is `warning`/`error`; a *failure* that
reached the boundary is `error` and, for an enforcement control, HALT-worthy. **Standing action:**
`errors.py`, `AuditFinding`, `audit_types.py`, and `agentteams-remediation-log.csv` should cite this
vocabulary; new failure classes are named against it.

## 4. Separation of duties + audit-logging, generalized (Plan 2 #6; research F9/F11)

**Present and strong:** the read-only auditor agents (`Read, Grep, Glob` only — a C-3 hard limit)
separate finding from fixing; Constitutional Rule 13 forbids self-marking a finding RESOLVED;
`enforcement-integrity.json` + `--verify-integrity`/CI fail closed on a tamper.
**Requirement (the gap):** an *edit* to any enforcement/generator control (`scan.py`, `integrity.py`,
`fences.py`, the templates, the schemas) should be **audit-logged/signed**, and *who edits* a control
should be separable from *who runs* it. The integrity manifest proves the code matches a recorded
hash but does not by itself record *who changed it and why* (its own docstring calls it "a speed
bump, not a boundary"). **Standing action:** treat a manifest re-record (`--write-integrity-manifest`)
as an event that must carry a reviewed reason in its commit, and keep the diff the control.

## 5. Standing drift detection for external assumptions (Plan 2 #7; research F7)

**Present:** `framework_research.py` carries a staleness watchdog; `drift.py`/`behavioral_drift.py`
detect structural/behavioral drift; a bridge-watchdog opens issues on stale runs.
**Requirement (generalize):** the tool's *external assumptions* — framework doc formats, adapter
target schemas, the reputable-source allowlist — should each have a canary check and a
silent-change alarm, framed (per the research's F7 split) as *change detection*, not reliability
growth. **Standing action:** enumerate the external assumptions each adapter/allowlist depends on and
add a canary where one is missing (tracked as future work; not all exist yet).

## 6. Honest-limitation logging as a standing practice (Plan 2 #8; research F12)

**Requirement.** Every generated deliverable and tool report states, with the same prominence as its
findings, **what it did not cover** and **what is provisional vs. settled** — and this is reviewed on
a cadence, not written once. The provenance stamp (§ provenance, code) is the machine-readable half;
this is the prose half. **Standing action:** a generated report without an explicit "not covered /
provisional" region is incomplete; the retrospective/remediation machinery
(`agentteams-remediation-log.csv`, Constitutional Rule 11) is the cadence hook. This document is
itself an instance — its TODO/Partial rows above are its declared limitations.

## References

The research grounding (Kaner & Bond 2004 construct validity; Barr et al. 2014 the oracle problem;
Avižienis et al. 2004 the dependability taxonomy; the F4/F6/F7/F9/F11/F12 features) is cited in full
in `research/redteam-model-scoring/Projects/RedTeamRigorProgram/01-quality-features.md` and
`02-best-practices.md`.
