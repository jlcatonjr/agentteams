# Process-Bloat Review Reference

Knowledge base for the `devops-bloat` adopted agent: how to judge whether a process gate (an audit,
a clearance, a review, a required artifact) earns what it costs. It holds method only. It contains no
figures from any team's private governance logs. The agent itself, and the constitution it refers to
(the C-rules, the numbered Constitutional Rules, the security Mandatory Review Triggers), live in a
team's own local agent files, not in this repository's published tree.

The agent is **read-only and advisory**. Its findings are data (C-4): nobody acts on them without the
operator, and it never edits a rule.

## 1. What counts as bloat

Process work that persists without contributing to the outcome it was added for. Three generators
recur (the "safety clutter" model, source 7; unverified, known here from secondary summaries):

- **Duplication** — the same obligation or fact stated or recorded in several places.
- **Generalisation** — a rule written for risky work that fires on all work.
- **Over-specification** — an obligation to produce an artifact whatever the result (a log row for a clean pass).

Adding a rule is easy and removing one is hard, so a rule set grows unless something prunes it. For
agent teams the cost is also attention: adherence to instructions falls as their number rises (source 4).

## 2. The floor (out of scope for any recommendation)

The agent may report what these cost. It may never recommend weakening, scoping down, merging away or retiring them:

- the Constitutional Core (C-1 to C-5);
- the rules that protect audit integrity: closing with a conflict audit, auditing an exclusion's new
  permissions, cross-referencing a fix without self-resolving it, re-verifying an auditor's claims, and
  the constraint-relaxing authorization rule;
- the security Mandatory Review Triggers, secret and path scanning, destructive-action clearance, signed releases;
- the tier classifier in section 3.

A recommendation that would touch the floor is itself a finding to report, not a proposal to make.

## 3. Tiers: classify by what a path does

The tier is the highest that any touched path or operation reaches. It is derived, never declared.
Predict it at intake, re-derive it from the diff at close-out, and escalate if the derived tier is higher.
Whether version control tracks a file is irrelevant. Anything that cannot be placed is T3.
The security Mandatory Review Triggers apply at every tier.

| Tier | Qualifies | Proportionate gates |
|---|---|---|
| T0 Reversible and local | Typos, comments, scratch plan notes, docs prose making no API claim | None beyond the commit |
| T1 Ordinary change | Source or tests covered by the suite; one revert undoes it; a failure would be noticed | Tests and mechanical checks; one combined review at close-out; a written plan only when the work spans sessions or more than about five steps |
| T2 Shared contract | Public API or CLI, templates shipped to consumers, schemas, CI or release configuration | Plan; one review of the plan; one conflict audit at close-out; doc parity |
| T3 Irreversible, privileged or silent | Deletion, bulk action, cross-repository write, merge to the release branch or a release; any governance root, persona, hook or governance log; ignore rules; enforcement modules and red-team probes; a diff that weakens an assertion or adds a test skip | The full protocol |

Reuse what exists for placement: `agentteams/cli/governance_targets.py` recognises trust roots (it does
not place personas, hooks, enforcement modules or probes, which this table also puts in T3), and
`agentteams/api_impact.py` detects public-surface changes. Do not build a second classifier.

## 4. Admission test for a gate

Ask in order. At the first "no", the gate does not apply to that change (or the proposed rule is not added).

1. **Named failure.** Which specific defect does it catch? Has it happened more than once, or would once be irreversible?
2. **Marginal catch.** Would a cheaper or later gate catch the same defect before it does harm?
3. **Reversibility and detection.** If the defect escaped, would the fix be expensive, or would the
   defect go unnoticed? "Detect afterwards" is acceptable only when a revert fixes it *and* something
   would raise the alarm within days.
4. **Scope.** Can the trigger be derived from the change so that it fires only where the defect can arise?
5. **Cheapest form.** Mechanical check, then checklist line, then agent audit, then human approval. Use the first that works.

## 5. Gate-retention test

Keep a gate when

    catches per period x cost of one escape  >  firings per period x cost of one firing

The cost of a firing includes tokens, delay, the rework it triggers and the attention it takes from other rules.

**Rule of three.** After *n* firings with no catch, the catch rate is below about 3/*n* at 95%
confidence (30 clean firings: under 10%; 300: under 1%). Keep the gate only if
`(3/n) x cost of one escape` still exceeds the cost of one firing.

Conditions, all required:

- A firing count must exist. Where firings are not logged, say "no denominator" and stop.
- A clean record may be caused by the gate (authors comply in advance), and firings inside one plan
  are not independent. Never recommend a cut on absence of catches alone: first a seeded defect or a
  red-team probe must show the gate still fires.
- A logged catch is a claim. Count findings later resolved as "no action needed" separately.
- The cost of an escape is an estimate. State it, state its basis, and expect it to be re-checked.

This test decides whether to keep a gate. To **order** the resulting recommendations, rate each with
the existing net-benefit rubric (`agentteams/templates/universal/net-benefit-prioritization.reference.template.md`,
which a team may not have rendered; `agentteams/plan_priority.py`), including its safety floor. Do not introduce a second ranking.

## 6. Rule lifecycle

- One reversible incident is a note, not a rule. A rule needs recurrence or irreversibility.
- Every rule carries: named failure, tier, form, origin, review date.
- Record catches; count clean passes without narrating them.
- A prose rule that no machine checks and nobody measures is a candidate to mechanise or demote.
- State each rule once; link to it elsewhere.
- Budget the cost of rules, not their number, and cap each tier's total: offsetting by count lets a trivial removal pay for a heavy addition (source 6; unverified, search summaries only).
- Re-audit on events (a step's outcome differed from the plan), not on every step.

## 7. Evidence the agent uses

- `python scripts/gate_catch_ledger.py` — counts-only catch ledger derived from existing logs. It
  reports its own coverage; treat a gate it lists as "without denominator" accordingly. The agent does
  not run it: it reads a report someone produced, and says `UNVERIFIED: no ledger report` if none is supplied.
- `python scripts/redteam_enforcement_catch_rate.py` — verifier coverage of the mechanical enforcement modules: how many have a ledgered verifier with a sensitivity test and a negative control.
- Version-control history and CI timings, when supplied.

## 8. Output contract

```
PROCESS-BLOAT REVIEW — <scope>   (ledger: <date | UNVERIFIED>)

| Gate | Tier it fires on | Firings | Catches | Verdict | Clause affected | Relaxes a constraint? |
|------|------------------|---------|---------|---------|-----------------|-----------------------|

Verdicts: keep | scope down | change form | merge with <gate> | retire | no denominator
Below the floor (reported, not recommended): <list>
Unverified in this review: <list>
Self-check (admission test applied to this agent): <one line>
```

No rewritten rule text. No edits.

Every row other than "keep" names the exact clause it would change and answers the last column
honestly: wording such as "merge", "demote" or "scope down" does not make a relaxation something else.
Retiring or editing a rule is the operator's own action.

## 9. The agent applies this to itself

It is one more persona, so it must pass section 4. It runs on a schedule, when a new rule is proposed,
or on request; never per change. It carries a review date, and a review that cannot show it changed a
decision is evidence for retiring it.

## Sources

Fetched and checked against the claim made here:

1. Google, *The Site Reliability Workbook*, "Example Error Budget Policy" (S. Thurgood, 2018): releases proceed while the service meets its objective and halt, except for the most urgent fixes, when the four-week error budget is exceeded. An example policy, not a normative one. https://sre.google/workbook/error-budget-policy/
2. Google, *Site Reliability Engineering*, ch. 5 "Eliminating Toil" (V. Rau, 2017): toil is manual, repetitive, automatable, tactical work, devoid of enduring value, that scales linearly as a service grows. https://sre.google/sre-book/eliminating-toil/
3. R. Wilsenach, "Ship / Show / Ask" (2021): the change's author chooses between merging without review, opening a pull request and merging immediately, or asking before merging. https://www.martinfowler.com/articles/ship-show-ask.html
4. D. Jaroslawicz, B. Whiting, P. Shah, K. Maamari, "How Many Instructions Can LLMs Follow at Once?" (2025): the best models reach 68% accuracy at 500 instructions, with a bias toward earlier instructions. Abstract only. https://arxiv.org/abs/2507.11538
5. M. Cemri et al., "Why Do Multi-Agent LLM Systems Fail?" (2025): 14 failure modes in three categories, one of them task verification. Abstract only. https://arxiv.org/abs/2503.13657

Secondary or not retrieved; treat as leads, not evidence:

6. Regulatory offsetting ("one-in, x-out"): known here only from search summaries of an OECD working paper and commentary. Not fetched.
7. A. Rae, D. Provan, D. Weber, S. Dekker, "Safety clutter" (2018): the source of section 1's three generators. The publisher's page refused retrieval; described from secondary summaries.
8. Team Topologies, "When DORA metrics meet governance in banking": reports that DORA found no evidence that more formal approval lowers change failure rates, and attributes a 2.6x likelihood of low performance to the 2019 State of DevOps report. Fetched, but it is a summary of the research, not the research.
