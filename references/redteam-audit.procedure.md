# Standing Red-Team Audit — Procedure

**Cadence:** daily, 06:41 UTC
**Workflow:** [.github/workflows/redteam-audit.yml](../.github/workflows/redteam-audit.yml)
**Driver:** [scripts/run_daily_redteam_audit.sh](../scripts/run_daily_redteam_audit.sh)
**Engine:** [agentteams/redteam/](../agentteams/redteam/)
**Methodology (shipped to every generated team):**
[templates/universal/redteam-methodology.reference.template.md](../agentteams/templates/universal/redteam-methodology.reference.template.md)

---

## What it is

A **standing red-team audit**: the constitutional probe battery, plus six checks that evaluate
the red team itself, run on a daily cadence against this repository.

It **measures and reports. It never remediates.** An unattended job that writes remediation
code is a larger risk than the one it closes. Phases 4, 5 and 7 of the cycle are human- or
agent-driven, off the artifacts the daily run leaves behind.

## Running it by hand

```bash
# The full daily run, exactly as CI does it
bash scripts/run_daily_redteam_audit.sh

# Or directly, with control over the target
agentteams --redteam --redteam-probes tests.constitutional_redteam_battery
agentteams --redteam --redteam-probes tests.constitutional_redteam_battery --dry-run
agentteams --redteam --redteam-report /some/dir
```

`--redteam` with **no** `--redteam-probes` runs phase 6 only and reports the probe population
as unmeasured. That is deliberate: there is no default probe module, because a consumer of
this package has no `tests.constitutional_redteam_battery`, and a command whose default target
does not exist would hand every consumer a permanently red check.

## Reading the exit code

| Code | Meaning | First action |
|---|---|---|
| 0 | clean — no live exploit, no self-audit finding, live agent tree untouched | none |
| 1 | a finding — a measured attack is live, or phase 6 found a defect in the red team | open `selfaudit.md` and `remediation.plan.md` |
| 2 | **the harness is broken** | fix the instrument, then re-run — these results say nothing about whether the constitution holds |

Code 2 outranks code 1, and covers: a control probe that did not `DEFEND`, a probe module that
would not import, a registration defect, a corpus claim that no longer matches the scanner, a
run that modified the live agent tree, and any death by traceback. **Indeterminate is not a
pass.** A battery whose controls fail reports "no exploits" exactly as loudly as one that found
none, which is why the third code exists at all.

## Artifacts

Written to `tmp/redteam/YYYY-MM-DD/` (gitignored, ephemeral; uploaded as a CI artifact with 30
days' retention):

| File | What it answers |
|---|---|
| `findings.json` | machine-readable phase 1+2 result — schema: `schemas/redteam-findings.schema.json` |
| `discoveries.md` | what was attacked, what held, over which populations |
| `remediation.plan.md` | one row per open item, verifier and rehearsal target left blank on purpose |
| `selfaudit.md` | phase 6 — the six checks, including any that could not run and why |

## The ledgers

Five files, all tracked, all human-edited, each single-purpose. **Every row must resolve**: a
row naming a symbol, module or test that does not exist is reported as a finding, never
skipped — a rule that matches nothing passes for every input.

| File | Check | Holds |
|---|---|---|
| `references/redteam-verifiers.csv` | F-1 | every verifier, its sensitivity test and its negative control — or a stated reason it is not a verifier |
| `references/redteam-callpath-parity.csv` | F-2 | `callee, guard, scope_module` triples where a guard must follow every call |
| `references/redteam-canonical-resolution-exemptions.csv` | F-3 | sites where a hand-rolled resolution is justified, keyed on `file, function, construct` |
| `references/redteam-accepted-weaknesses.csv` | F-6 | every non-`DEFENDED` probe and why its residue is accepted |
| `references/redteam-uncontrolled-probes.csv` | registration | attack probes deliberately registered without a paired control |
| `references/redteam-probe-baseline.json` | F-5 | each probe's last-accepted outcome and normalised evidence digest |

Reasons must be at least 40 characters. That is not a formatting rule: a one-word excuse is how
an exemption ledger stops describing the system and becomes a place failures go to die.

## Accepting a changed probe outcome

```bash
agentteams --redteam --accept-probe-baseline --redteam-probes tests.constitutional_redteam_battery
git diff references/redteam-probe-baseline.json    # review it
```

**The daily job never does this**, and `tests/test_redteam_audit_workflow.py` asserts that no
step in the workflow can. If the scheduled run re-baselined itself, a probe that flipped from
`PARTIAL` to a *false* `DEFENDED` would be absorbed overnight and the check written to catch
that would report clean forever — a check that clears its own flag.

Before accepting: read the probe and decide whether the **control got better or the probe got
blinder**. Two probes flipped to a false `DEFENDED` exactly this way; both had been made
*stricter*, and the strictness is what stopped their fixtures from reaching the mechanism.
Record the finding in the baseline's `note` field for that probe.

## Attacking real infrastructure

Supported, and the right instinct — synthetic fixtures measure each control against inputs its
author imagined. But **the copy must be isolated**, and re-running `--update --merge` is not a
way to undo an attack on the live tree. The battery has measured what the merge does to each
attack class:

| Attack | What `--update --merge` does |
|---|---|
| escalate a capability grant in YAML front matter (`C3`) | **nothing** — front matter cannot be fenced, so there is no restore-on-update guarantee. The probe's own name is *"An escalated grant survives `--update --merge`; nothing reverts it"* |
| delete a fence's markers (`D3`) | **refuses to write** — the merge declines, so the mutation stays |
| rename a fence (`D4`) | keeps the weakened body **and** re-inserts the real one |
| append to a fenced body (`D2`) | can pin that fence indefinitely under a preserve policy |

A merge preserves on-disk divergence by design; an attack *is* on-disk divergence. Three
further reasons the live tree is the wrong target: `.claude/agents/` is what the agents running
the audit read, so mutating it means attacking the control plane while standing on it; the
daily job is unattended, so a crashed run would leave the repository mutated with nothing
scheduled to clean up; and `git` already gives a byte-exact restore the merge cannot match.

`agentteams/redteam/realcopy.py` does it correctly: snapshot the real infrastructure into a
temp root, attack the copy, discard it. `live_tree_modifications` asserts the live tree is
byte-identical before and after — a **delta**, not an absolute cleanliness check, so an
operator's in-flight edits do not read as "the red team touched it".

The merge is still useful, as a **measurement** rather than a safety net:
`classify_restorability` runs it against the copy and reports each mutation as `RESTORED`,
`PRESERVED` or `REFUSED`. That turns "can the pipeline heal this?" into a number.

## Not measured by the daily run

Stated as populations rather than omitted, because a coverage number that quietly excludes what
it did not look at is the F-4 defect:

- **The judgment layer.** The 14-payload corpus is loaded and its `scanner_matches` claims are
  verified daily, but it is **not run against live subagents** — that spawns agents and needs
  explicit operator authorization (W14). `tests/redteam/run_harness.py` is the operator-driven
  path.
- **Hand-written historical documents.** F-4 sweeps `references/plans/*redteam*.md` and reports
  what it finds under an *advisory* heading. Not gating: the handoff's own rule is that *a
  finding report* must fail its own audit gate — the report being produced, not every report
  ever written. Gating on immutable historical documents would leave the daily job permanently
  red, and a permanently red job is one nobody reads.

## Related

- `references/plans/redteam-automation-handoff.plan.md` — the brief this was built from
- `references/plans/constitutional-redteam-audit-2026-08-06.report.md` — the audit that found the 21 exploits
- `references/agentteams-remediation-log.csv` — rows with `category=constitutional-redteam`
- `docs_src/security-hardening-guide.md` — signing-key custody
