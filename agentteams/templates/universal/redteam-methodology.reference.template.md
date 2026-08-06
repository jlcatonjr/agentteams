<!--
SECTION MANIFEST — redteam-methodology.reference.template.md
| section_id                | designation   | notes                                       |
|---------------------------|---------------|---------------------------------------------|
| redteam_cycle             | FENCED        | The seven phases, the tier model, the outcome classes |
| redteam_failure_modes     | FENCED        | F-1..F-6: the six ways a red team fools itself |
| redteam_project_extension | USER-EDITABLE | Project-specific probes, tiers, and accepted residues |

NOT in fences._TEMPLATE_AUTHORITATIVE_FENCES, deliberately. The documented bar there
(fences.py) is "the project has no legitimate reason to extend its body", and a downstream
project has an obvious legitimate reason to add its own probes and its own failure modes to a
red-team methodology. The fenced sections restore on --update --merge; the extension section
is the project's.
-->

# Red-Team Methodology — {PROJECT_NAME}

How this project audits whether its own constitutional controls actually hold, on a standing
cadence, and — the part that is easy to skip — how it audits the audit.

<!-- AGENTTEAMS:BEGIN redteam_cycle v=1 -->

## The cycle

```
  1 ATTACK ──► 2 REVIEW ──► 3 PLAN ──► 4 AUDIT PLAN ──► 5 IMPLEMENT
                                                              │
                    7 REMEDIATE ◄── 6 EVALUATE THE RED TEAM ◄──┘
                          │
                          └──────────► (re-enter at 1 until convergence)
```

| Phase | Input | Output | Gate that must pass |
|---|---|---|---|
| **1 Attack** | probe registry + payload corpus | `findings.json` | every attack probe has a paired control; controls all `DEFENDED`, else the harness is broken, not the target |
| **2 Review** | `findings.json` | `discoveries.md` | every finding carries tier, article, evidence and reproduction; numerator **and** denominator |
| **3 Plan** | `discoveries.md` | `remediation.plan.md` | one item per finding, each naming its verifier and its rehearsal target |
| **4 Audit plan** | the plan | plan audit | adversarial + conflict pass before any code is written |
| **5 Implement** | revised plan | code + tests | each fix rehearsed on an isolated copy of a **real** target; the complete expected end state asserted, not merely "no error" |
| **6 Evaluate the red team** | everything above | `selfaudit.md` | the six checks below, run against the red team's own output |
| **7 Remediate errors** | `selfaudit.md` | corrections | re-enter phase 1; converge when phase 6 finds nothing new for two consecutive rounds |

**Phase 6 is what distinguishes this from running a test suite.** It is the phase that was
missing the first time, and every one of its checks exists because a remediation shipped
something that looked like a control and was not.

## What a scheduled run does, and does not

A standing audit runs phases 1, 2, 6 and emits a phase-3 *skeleton*. It **measures and
reports; it never remediates.** An unattended job that writes remediation code is a larger
risk than the one it closes, and a plan that arrives pre-decided invites approval rather than
review. Phases 4, 5 and 7 are human- or agent-driven, off the emitted artifacts.

## Attack the real infrastructure — in an isolated copy of it

Synthetic fixtures measure each control against inputs its author imagined. Real agent files
are the honest target. But the copy must be **isolated**, and re-running the generation
pipeline is **not** a way to undo an attack on the live tree:

- a capability grant escalated in YAML front matter **survives `--update --merge`** — front
  matter cannot be fenced, so there is no restore-on-update guarantee;
- deleting a fence's markers makes the merge **refuse to write**, so the mutation stays;
- renaming a fence keeps the weakened body **and** re-inserts the real one;
- appending to a fenced body can pin that fence indefinitely under a preserve policy.

A merge preserves on-disk divergence by design — that is what protects operator enrichment —
and an attack *is* on-disk divergence. So: snapshot the real agent infrastructure into a temp
root, attack that, discard it. Assert afterwards that the live tree is byte-identical.

The merge is still useful, as a **measurement** rather than a safety net: run it against the
copy and classify each mutation `RESTORED` / `PRESERVED` / `REFUSED`. That turns "can the
pipeline heal this?" from an assumption into a number.

## Tiers

| Tier | Attacker capability |
|---|---|
| **T0** | can supply content the agent reads — a file under review, a retrieved result, fetched web content |
| **T1** | an in-repo agent with write and execute |
| **T2** | the operator's shell and environment |

A control is only meaningful against a stated tier. Recording a T2 result as a failure of a
T1 control makes the control look weaker than it is; the reverse makes it look stronger.

## Outcome classes

| Outcome | Meaning |
|---|---|
| `DEFENDED` | the control engaged and held |
| `PARTIAL` | the control fired, with degraded or mis-framed signal |
| `EXPLOITED` | the attack succeeded |
| `DOCUMENTED-LIMIT` | outside the control's stated scope, and the scope says so |
| `OUT-OF-TIER` | the attack needed a capability above the tier under test |

Every probe that is not `DEFENDED` needs a named reason on the record. Accepting a weakness
must cost a diff — otherwise it costs a tally, and tallies do not get read.

<!-- AGENTTEAMS:END redteam_cycle -->

<!-- AGENTTEAMS:BEGIN redteam_failure_modes v=1 -->

## The six ways a red team fools itself

Phase 6 checks these mechanically. Each is stated with the failure it exists to prevent, all
six measured in one session.

### F-1 · A verification that always passes

A shipped digest check hashed a tuple of keys the producer never emitted. Overlap: zero. So
`digest(payload) == digest({})` — a check that passes for every input, reported as
"verification shipped".

> **Rule.** Every verifier needs a test proving its output *changes* when its input changes,
> and a negative control proving it does *not* change for irrelevant input. A verifier with
> only one of the two can be a constant returning the convenient answer.

### F-2 · A fix wired to one of two paths

A migration was attached to one of two call sites of the same function. The command an actual
sweep runs never reached it: most targets migrated, some did not, and the run reported success.

> **Rule.** Parity is measured **per branch**, not per function — both call sites can live in
> one function. And a parity rule covering fewer than two call sites proves nothing and passes
> vacuously; that is itself a finding.

### F-3 · Hand-rolling a resolution the tool already provides

Three times in one session. A hand-written directory sweep missed most of the population; a
hand-rolled descriptor lookup read the thin stub instead of the rich brief; a `.git`
directory test would have excluded recoverable git worktrees as unrecoverable.

> **Rule.** Target, descriptor and VCS resolution go through the module's own APIs. Exemptions
> key on **provenance** (who owns this code), never on shape — a shape-keyed exemption is
> wearable as a costume.

### F-4 · A coverage claim with an unexamined denominator

*"0 agents on the ignored key across the fleet."* Arithmetically true, over a denominator that
was a fraction of the real population, hiding hundreds of exposed agents. It surfaced only
because someone asked a follow-up question.

> **Rule.** Every count is emitted with the population it was computed over, and the
> population comes from a canonical enumerator, never an ad-hoc list.

### F-5 · A probe that got blinder, not better

Two probes flipped to a false `DEFENDED` after their fix shipped: one was skipping the genuine
target, the other named an approver the new roster check rejected — hiding that the underlying
tier was still undefended. Both were made *stricter*.

> **Rule.** Re-running probes does not catch this. Compare each probe's outcome **and its
> normalised evidence** against a committed baseline, and require a human note when either
> changes. The baseline is operator-owned: a scheduled job that re-baselines itself clears its
> own flag every night and measures nothing.

### F-6 · Accepting a weakness without it costing a diff

> **Rule.** Every non-`DEFENDED` probe needs a ledger row with a substantive reason, and a
> probe that now defends must **lose** its exemption. Both directions, or the ledger stops
> describing the system.

## What to be suspicious of

- When a number looks clean, check what it was divided by.
- When a fix lands, ask which callers you did not touch — not "did the test pass".
- When a probe starts passing, ask whether the control got better or the probe got blinder.
- Rehearse against a real target, not a synthetic fixture.
- The tool very often already has the function you are about to write. Grep before adding a
  resolver, an enumerator, or a status check.
- A control you cannot see fail is not a control. Prove the alarm rings before trusting the
  silence.

<!-- AGENTTEAMS:END redteam_failure_modes -->

<!-- AGENTTEAMS:BEGIN redteam_project_extension v=1 -->

## Project extensions

*This section belongs to {PROJECT_NAME}.* Record here: probes specific to this project's
controls, additional tiers if the threat model needs them, and any failure mode this project
has measured that the six above do not cover. Content added here is preserved across
`--update --merge`.

{MANUAL:PROJECT_REDTEAM_NOTES}

<!-- AGENTTEAMS:END redteam_project_extension -->
