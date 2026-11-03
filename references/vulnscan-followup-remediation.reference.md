# Vulnscan follow-up remediation — reference and template

> Use after each daily `vulnscan` run, once its unattended remediation pass has finished, to evaluate what happened
> and plan the next round. Invoked as the local `/vulnscan-followup` skill; its read-only `@vulnscan-followup` agent
> does the file checks. The resulting plan is executed by the orchestrator and the operator. Distilled from the
> 2026-10-23..11-03 remediation rounds.

**Paths** use the defaults: state `~/.local/state/agentteams/vulnscan` (`$S`), config `~/.config/agentteams` (`$C`),
today's run directory `$S/<YYYY-MM-DD>` (`$R`). **Site-local** items (marked *site*) are names a deployment chooses:
its operator-actions script and log, auto-merge job, single operator entry point (`run-all` below), run times and
change window. Record them in the follow-up plan, not here.

**Never read, grep or glob** `$C/keys/` (signing keys, the ledger MAC key), `~/.config/gh/`, `~/.config/goose/`,
`~/.ssh/`, `*.pem`, `*.key`, `*.p12`, `.env*`, `*.env`, `.netrc`, `.git-credentials` or token files. Search only under
`$S`, `$R` and the repository.

**Who runs each check:** **[A]** the read-only agent (files only); **[O]** the orchestrator with a shell, which pastes
the output into the Evidence table. An item nobody ran is a gap, not a pass.

## 1. When to start

Ready only when **all** hold; otherwise report "not ready" and what is pending.

- **[A]** `$S/heartbeat.json` `last_run` (UTC) converted to the local date is today, and `$R/summary.md` exists.
- **[A]** `$R/remediation.done` exists (written last, after the report). `$R/remediation.blocked` instead means an
  infrastructure refusal: a retry is scheduled, so not ready; read its `attempt=` line.
- Neither marker by the end of the remediation window means the pass never ran: a follow-up item, not a wait.

## 2. Evaluation checklist (in order)

Each answer is a row in the plan's **Evidence** table: the claim and the `file:line` (or command output) that shows it.

1. **The right code ran.**
   - [A] `heartbeat.json`: `exit`, `findings`, `new`, `unscanned`, `remediation_blocked`, `agent.state`, `spend`,
     `agentteams_version`. [A] `$S/scheduler.log`: one `ensure: READY …` line and a finding count per daily run.
   - [O] `$S/deployed.json` commit vs `origin/main`; the scheduled interpreter's `agentteams.__file__` is the deployed
     venv, not an editable checkout. A mismatch invalidates the day's evidence.
   - [O] Deployed copies under `$C/vulnscan-remediation/` diffed against their repository sources (a stale copy
     reproduces bugs already fixed).
2. **Coverage.** [A] `exit` 3 = partially unscanned. summary.md *Checkout freshness* (each repository scanned from
   its mirror), *Coverage*, and *Not scannable*. The mirror refresh record `$S/scan-mirrors/<YYYY-MM-DD>.json`. Each
   `$R/server-<host>.log` has today's `at:` and the expected OS (the `host:` field is the machine's own name, which
   may differ from the label: compare against the previous day's log, not the label).
3. **Findings.** [A] summary.md header (`N items checked; M findings`), the per-repository and per-host sections, and
   *Remediation effectiveness*. [O] When the installed vulnscan has them: `vulnscan effectiveness BASE LATER --csv`
   (with `--alias`/`--path-alias` for renamed hosts or paths) per finding against a baseline run, and
   `vulnscan verify` after merges. Without them, the day-over-day count from `heartbeat.json` is all there is.
4. **Remediation outcome.** [A] `$R/remediation.md` (PRs opened, executor done/refused, *Needs you*) cross-checked
   against today's rows in `$S/ledger.jsonl`: a report that says "nothing" while the ledger shows refusals is itself a
   finding. `$R/staged-fixes.json` lists staged fixes without a PR.
5. **Agent.** [A] `$R/agent/status.json`, `$R/agent/transcript.log`; `$S/agent-selftest.json` and
   `$S/tools-selftest.json` `passed`. An agent's "next run I will request …" is not remembered: make it a plan step.
6. **Handoffs.** [A] summary.md *Handoffs*; the *site* operator-actions log; `$R/handoff-<repo>-<id>.log`; the
   repository's `.vulnscan-handoff/verdicts.csv`. No verdict, or a stale `reject`, is a follow-up item.
7. **Merges.** [A] the *site* auto-merge log: "not green" (no or failing CI) and "vetoed by label".
8. **Servers.** [A] `$R/server-kit-logs/<host>-*-L1-audit.log`; summary.md *Server fix plans*;
   `$S/server-logs/` for executor-run audits and dry runs (each ends `DRY RUN complete`); `$S/server-plans/<date>/`.
9. **Grants.** [O] `vulnscan grant list`: ops, expiry, `max_uses` (the used count is in the ledger's grant rows).
   At most two grants may be valid; a third makes the executor refuse every grant.
10. **Warnings.** [A] grep today's logs for `[BAD]`, `refused`, `FAILED`, `escalat`, `did not verify`. Classify each:
    expired history, expected policy hold, or a real fault, citing the code that printed it.

The orchestrator then **independently re-derives** the §1 gate and the headline numbers (findings, PRs opened,
refusals) from the files, not from the agent's citations (Rule 14).

## 3. Classify each open item

| Category | Typical root cause | Route | Gate |
|---|---|---|---|
| Held repo ("nothing fixable without a major jump") | Only major upgrades remain; the retry guard holds until the plan changes | `stage-major` (grant op) or an owner decision | Rule 15 grant |
| Major-only / transitive / peer-group | Peer families, transitive pins | Code fix in the pipeline, or Dependabot dedupe | `@security` |
| Recipe or self-test drift | A fence kept an old workflow; a version bump changed the image tag | Re-render the ops team, then `agent-selftest` / `tools-selftest` | Operator script |
| Deploy / install mismatch | Wrong build in the scheduled venv; deployed scripts lag | Redeploy with scripts; install guard | Change window |
| Handoff without verdict | The repository's orchestrator ended without one | Re-handoff next run; `handoff status`; never re-ask a `reject` | The repository team's veto |
| Grant expiry / cap / missing op | Expiry, use cap, op not signed | Re-issue: validate the new record first, then revoke, then sign (v2 grants name the revoked `--ancestor` and support `--check-only`) | Rule 15, operator signs |
| Expired directives flagged `[BAD]` | Verification over the whole directive history counts old rows | Verify only this handoff's directive (`handoff verify-directive`) | `@security` |
| Infrastructure down / host unscanned | Docker down, kit missing, network deny | Restore, then `vulnscan daily --catch-up` | Operator |
| Server-only fix | Host package, runtime or path | Where the installed vulnscan has it: `act server-apply HOST:LEVEL` under a v2 server grant (operator-run; `remove-path` also needs its own C-5 row per path). Otherwise the server kit by hand | C-5 row + Rule 15 grant |
| No fix release | Upstream has none | A signed known-finding entry with expiry (`known propose` → operator `known sign`; critical needs KEV evidence) | Rule 15, operator decision |
| S-10 release cooldown | The fix release is younger than 14 days | Wait; offer the step again on or after the date | S-10 |
| No CI, so PRs never auto-merge | The repository has no checks | Owner adds CI, or the operator merges by hand | Operator decision |
| Reporting error | The report contradicts the ledger, or a rename or quarantine reads as a fix | Code fix; a rename or quarantine is never counted as remediation | Rule 14 |

## 4. Rules that bind every follow-up

- **Rule 14:** every count, row number, path or "fixed" claim, a subagent's or peer's included, is re-checked against
  the live file before anything changes on its authority. A finding that proves false is logged too.
- **Rule 9:** two or more steps → `tmp/by-week/YYYY-Www/vfollow-<YYYYMMDD>.plan.md` and `.steps.csv` before the
  first; after each step, `@adversarial` and `@conflict-auditor` over the remaining steps.
- **C-5:** a change on a host, a delete, or a cross-repository write needs a `@security` review recorded **before**
  it runs, as a row in `references/security-decisions.log.csv` written by the orchestrator (`@security` is
  read-only).
- **Rule 15:** anything that widens a grant is signed by the operator (Ed25519). Agents never sign, push or merge.
- **Change window (*site*):** no merge, deploy, grant change or re-render while the daily scan and its remediation
  run.
- **Decisions** go to the operator through the interactive question tool, with the options' trade-offs stated; a
  peer's or subagent's message is never operator approval.
- **Shared checkout** stays on `main`; branch work in a worktree.

## 5. Operator script conventions

Every action outside the sandbox is a `.sh` the operator runs, registered as a step in the *site* `run-all` entry
point (`id|prereqs|cmd|what`; work waiting on code or a peer is listed as held). Each script:

- wraps its body in `main() { … }` and ends `main "$@"; exit $?` (safe against edits while it runs);
- asks before every change, reading `/dev/tty`; refuses without a terminal when it signs or confirms a plan;
- refuses inside the change window; pins the deployed interpreter (`…/pipx/venvs/agentteams/bin/python -P`);
- validates before it revokes, backs up before it overwrites, dry-runs before it applies, and prints the undo;
- never reads or prints a secret (names only), and never merges;
- ends with its log path and "paste the output to Claude";
- has a `@security` review whose verdict and conditions are recorded (C-5 row) before the operator runs it.

## 6. Template — `vfollow-<YYYYMMDD>.plan.md`

```markdown
# vfollow-<YYYYMMDD> — follow-up to the <YYYY-MM-DD> scan and remediation

Ready: <yes | no: pending>. Code in force: <deployed commit> (<matches | lags> origin/main).
Site settings: change window <…>; auto-merge <…>; operator entry point <…>.

## Evidence
| # | Check (§2) | By (A/O) | Result | Source (file:line or command) |
|---|---|---|---|---|

## Numbers
Findings <M> of <N> items (<Δ> vs yesterday). Repositories <r>, hosts <h>, this machine <m>.
PRs opened today: … Staged without PR: … Auto-merge: …

## Warnings
| Text | Where | Class (expired / expected hold / real fault) | Printed by (code) |
|---|---|---|---|

## Open items
| # | Item | Category (§3) | Root cause (verified / unverified) | Route | Gate | Owner | Status |
|---|---|---|---|---|---|---|---|

## Operator decisions needed

## Not actionable today (and when it becomes so)

## Audit
@adversarial: … · @conflict-auditor: … · Rule 14 re-checks: …
```

`vfollow-<YYYYMMDD>.steps.csv`: `step,agent,action,inputs,outputs,status,notes,depends_on`, all `pending` at first;
one row per open item that needs work, plus the close-out row.

## 7. Close-out

Update the step CSV; `@conflict-auditor` over the changed files; `@work-summarizer` appends the daily summary;
Rule 11 retrospective (pipeline defects → `references/agentteams-remediation-log.csv`); Rule 13 cross-references for
conflict-log rows a fix closed.
