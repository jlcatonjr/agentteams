# Working with Your Generated Team

## When to Use This Guide

Read this guide if you:

- Have just generated an agent team with AgentTeams and want to know how to use it effectively day-to-day
- Want to understand how plan documentation (`tmp/by-week/`) integrates with your team's orchestrator
- Need to understand when to invoke `@adversarial` vs `@conflict-auditor` vs other governance agents
- Want to use work summary reporting to track session output across days and weeks

---

## The Generated Team Structure

When AgentTeams generates a team, every team includes a four-tier hierarchy:

1. **Orchestrator** — routes all work, enforces constitutional rules, closes sessions
2. **Governance agents** — cross-cutting concerns (security, hygiene, adversarial review, conflict audit, etc.)
3. **Domain agents** — production workflows (producing, auditing, converting, compiling)
4. **Workstream experts** — one per project component

See [How It Works](how-it-works.md) for architecture details. This guide focuses on day-to-day operation.

---

## How Sessions Work

A session begins when you invoke the orchestrator (or a domain agent directly) with a task. For multi-step tasks, the orchestrator:

1. Generates a plan before executing any step
2. Routes each step to the appropriate domain agent
3. After each step, audits remaining steps with governance agents
4. Closes the session with a conflict audit and Final Check

You do not need to manage this flow manually — the orchestrator handles it. What you should know is when and why the plan artifacts end up in `tmp/by-week/`.

---

## Plan Documentation

### Where Plans Live

For every task involving two or more implementation steps, the orchestrator creates:

- `tmp/by-week/YYYY-Www/<plan-slug>.plan.md` — summary: goal, agent sequence, success criteria, rollback notes
- `tmp/by-week/YYYY-Www/<plan-slug>.steps.csv` — step-by-step record

The ISO week folder (`YYYY-Www`) is created automatically if it does not exist.

### Steps CSV Format

Each row in the steps file has these required columns, plus an optional `depends_on`:

```
step,agent,action,inputs,outputs,status,notes[,depends_on]
```

| Column | Values |
|---|---|
| `status` | `pending`, `in_progress`, `done`, `blocked` |
| `notes` | Additional context, blockers, or deviation notes |
| `depends_on` *(optional)* | Space- or comma-separated `step` ids this row depends on. Empty = no prerequisite. Enables parallelization analysis. |

After each step completes, the orchestrator updates `status` to `done` and runs the remaining `pending` rows through `@adversarial` and `@conflict-auditor` before proceeding to the next step (or wave).

### Parallel Execution of Independent Steps

If you add the optional `depends_on` column and fill `inputs`/`outputs` with concrete repo-relative paths, the orchestrator can identify steps whose domains are **independent** and dispatch them together as a **wave** instead of strictly one at a time (Workflow 0A — Parallelization Analysis). Compute the schedule yourself with:

```
python -m agentteams.parallel_plan tmp/by-week/YYYY-Www/<plan-slug>.steps.csv
```

Independence is conservative and fail-safe: steps share a wave only when their read/write footprints are disjoint and neither touches shared mutable state (git, databases, locks, the network, migrations). Destructive, cross-repository, and `--bridge-refresh` steps always run alone. Actual concurrency happens only where the host runtime supports concurrent subagents (e.g. Claude's `agent` tool); elsewhere the independent set is surfaced as an "any-order" recommendation. The per-step `@conflict-auditor` cadence is preserved (run per member at wave join). Full contract: `references/parallelization.reference.md`.

### Complex Requests: Item Plans and a Metaplan

When a request bundles two or more separable items (several features, defects, or audit findings), the orchestrator runs Workflow 0C. It writes one development or remediation plan per item, then a metaplan, `tmp/by-week/YYYY-Www/<meta-slug>.plan.md` + `.steps.csv`, with one row per item plan, and schedules them with `agentteams.parallel_plan`. `@adversarial` and `@conflict-auditor` audit the metaplan and every item plan, and the plans are revised on their findings before any implementation starts. Implementation then runs in rounds: independent items advance together, and a destructive, cross-repository, shared-state or operator step runs alone.

### Keeping the API Current After Each Change

The `pre-commit` hook that `agentteams --update` installs also checks each commit for API impact: Python modules or public functions, classes and methods added, removed, renamed or re-signed; command-line options or subcommands added or removed; schemas changed; and other files added, removed or renamed (outside docs, tests and work records). It prints a one-line summary, plus a line for each changed module whose API documentation page was not updated, is missing for a new public module, or misses added names or still mentions removed ones. It does not block the commit unless you opt into strict mode with `git config agentteams.apiDocsStrict true`; then a commit that changes a module's API without updating its page (or adds a public module with new public names and no page) fails until you stage the page or bypass it with `git commit --no-verify`. Before the session closes, Workflow 11 runs `python -m agentteams.api_impact status` over the branch's commits. For each commit not yet reviewed, the orchestrator's API Impact Review updates the API documentation, audits every file that mentions what changed (plus the agent docs, references, templates and tests around it), revises them on the findings, and records the review in `references/api-impact-reviews/`. That ledger is committed, so the review shows in the pull request. Full contract: `references/api-impact.reference.md`.

### Decisions You Are Asked to Make

When the orchestrator reaches a decision only you can make (a preference, a trade-off, an approval, a fork that would be costly to undo), it asks through your host's interactive selection interface: `AskUserQuestion` in Claude Code, `vscode/askQuestions` in VS Code Copilot, `ask_user` in Copilot CLI, `request_user_input` in Codex Plan mode. You get its 2–4 best options with the recommended one first, each with a line on what it leads to; you can always choose "Other" and type an answer instead. It does not ask about things it can look up, choices with a conventional default, or whether to proceed with a plan it has already audited. Subagents route their decisions to the agent you are talking to, so the questions come from one place. Where no picker is available, the same question arrives as a numbered list. In a non-interactive run the question, its options and the recommendation go to `references/orchestrator-escalation.log.csv` for your review; approvals are never assumed, and other decisions take the recommendation only when it is reversible. Full contract: `references/operator-decision.reference.md`.

### Reviewing Plan Status

You can review plans at any time by asking:

> "Show plan status" or "Review plan progress"

The orchestrator runs Workflow 10, which reads all `.plan.md` and `.steps.csv` files, summarizes status, and surfaces any blocked steps.

### When Plans Complete

When all rows reach `done`, the orchestrator invokes `@work-summarizer` to append the plan's outcomes to `workSummaries/daily/YYYY-MM-DD.md` before closing out the session.

---

## Work Summary Reporting

### Daily Summaries

`workSummaries/daily/YYYY-MM-DD.md` accumulates plan outcomes for each day. You can generate or update a summary at any time:

> "Summarize today's work" or "Daily summary for 2026-05-11"

The orchestrator invokes `@work-summarizer`, which reads:
- `tmp/by-week/` plan artifacts for the current week
- Legacy undated plans in `tmp/` (fallback)
- Git history and diffs for the day

The summary is then passed through `@technical-validator` (verify commit hashes and paths), `@adversarial` (presupposition audit), and `@conflict-auditor` (consistency check) before being written.

### Weekly and Monthly Summaries

> "Weekly summary for 2026-W20" or "Monthly summary for 2026-05"

Weekly summaries aggregate daily summaries. Monthly summaries aggregate weekly ones. Both pass through the same audit chain.

---

## When to Invoke Governance Agents Directly

For most work, you should let the orchestrator route to governance agents automatically. However, you can invoke them directly when needed:

| Situation | Agent to invoke |
|---|---|
| You added a new agent file and want to check for duplicated scope | `@conflict-auditor` |
| You are about to delete files or make bulk edits to 3+ files | `@security` (required by Constitutional Rule #1) |
| A task plan has hidden assumptions you want challenged before execution | `@adversarial` |
| Agent documentation seems out of date after recent code changes | `@agent-updater` |
| There are duplicate or orphaned files that should be cleaned up | `@code-hygiene` |
| You need to commit, push, or merge changes | `@git-operations` |

### When to Use `@adversarial`

The adversarial agent challenges presuppositions in plans and technical claims — not prose quality or style. Invoke it when:

- A plan involves irreversible or cross-cutting changes
- You are about to execute based on a claim that hasn't been verified against source files
- A team member (or another agent) is confident about something that hasn't been checked

Do not invoke `@adversarial` as a substitute for factual verification — use `@technical-validator` for that.

### When to Use `@conflict-auditor`

The conflict auditor detects contradictions across agent files and documentation. Invoke it after:

- Any session modifying two or more files (required by Constitutional Rule #3)
- Adding new agents, routing rows, or workflow extensions
- Updating the authority hierarchy or constitutional rules

---

## The Append-Only Conflict Log

Conflict-audit findings are typically maintained under `references/conflict-log.csv` in generated teams. Keep this log append-only in your operating process so resolved findings remain visible in the audit trail.

### Reading the Conflict Log

Each row contains:

```
id,session_date,file,finding_type,description,resolution,status
```

Rows with `status = open` require action. Rows with `status = resolved` are historical record.

To review open conflicts:

> "What are the open conflict audit findings?"

The orchestrator will read the latest conflict-log CSV entries and surface any `open` rows.

---

## Final Check (Workflow 11)

At the close of every multi-step session, the orchestrator runs a Final Check that:

1. Reads the current plan's steps file and lists any `pending` or `blocked` rows
2. Creates sub-plans for unresolved items
3. Scans `CHANGELOG.md` for `Known Issues` headings
4. Checks `git status` for untracked files in `tmp/` or modified files outside the plan's scope
5. Invokes `@adversarial` and `@conflict-auditor` on the findings
6. Presents audited summaries as a numbered list

You do not need to run Final Check manually — the orchestrator invokes it automatically at the close of every workflow execution. If you want to trigger it explicitly:

> "Run a final check" or "Close out this session"

---

## Best Practices

- **Let the orchestrator route work.** Direct agent invocations bypass the constitutional rules checks and audit chains. Only invoke agents directly when you have a specific reason to.
- **Use "show plan status" frequently.** Reviewing `pending` steps before starting a session saves you from re-doing work that was already planned but not started.
- **Keep `references/adjacent-repos.md` current.** Out-of-date registry entries cause Protocol 1 assessments to miss affected repos. Run a registry maintenance pass after any fleet update.
- **Review AI-enriched values in `references/defaults-audit.csv` before committing.** Values with `ai_filled` status are suggestions — verify them against your actual project before treating them as final.
- **Commit before migration; do not pre-create the migration tag.** If you run `--migrate`, ensure your working tree is clean and let the command create `pre-fencing-snapshot` automatically.
