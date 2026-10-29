# Coordination Request — 2026-W44 baseAgent joins vulnscan remediation

**From:** agentteams orchestrator · **Date:** 2026-10-28 · **Protocol:** 3 (orchestrator-to-orchestrator)
**Clearance:** `@security` review of this request and its operator script pending (agentteams side); the roster change
below also needs baseAgent's own `@security` clearance before it is committed there.
**Channel:** mediated — this artifact is the request. No cross-repo write happens from agentteams: baseAgent's
orchestrator (or the operator) applies it in a baseAgent worktree, never in baseAgent's shared checkout.

## Goal

baseAgent's own goose team reviews the fixes vulnscan stages for baseAgent (handoff delivery: the executor opens a PR
only after baseAgent's orchestrator writes `approve-staged` in `<baseAgent checkout>/.vulnscan-handoff/verdicts.csv`),
and baseAgent's Rust lockfiles are scanned (agentteams `feat/vulnscan-baseagent-integration`, crates.io support).

## Current state (verified 2026-10-28)

- `brief.json` → `vulnerability_scanning.level` is `scan`; the operator's registry entry follows it. Repository ops
  for baseAgent are refused until **both** say `remediate`.
- `authorized-managers.txt` in `.goose/recipes/references/`, `.claude/agents/references/` and
  `.github/agents/references/` is the comment-only stub, so every management directive is refused. Handoffs still
  run without one, but carry no signed directive.
- The team was rendered by agentteams v1.0.2: it has a goose orchestrator and `sandbox/confine-run.sh` (needed by
  the handoff launcher) but not Rule S-VM, the orchestrator's review protocol for `vulnscan/*` PRs (v1.1 and later).

## Requested change (one PR in baseAgent; apply together or not at all)

1. `brief.json` → `"vulnerability_scanning": {"level": "remediate", "delivery": "handoff"}`.
   (`agentteams --set-vulnscan remediate --project <baseAgent worktree>` writes the level and shows the diff; add
   `delivery` by hand.)
2. `brief.json` → `"authorized_managers": ["<vulnscan manager team>"]` (the operator's vulnscan team id, the name of
   its `agent.team_dir`). **Privileged:** it widens who may direct baseAgent's team (Rule 15 / C-3). baseAgent's
   `@security` clears it and records a row in its `references/security-decisions.log.csv`.
3. Re-render so the roster files carry the new manager: `agentteams --update --merge --project <baseAgent worktree>`
   for each framework baseAgent emits, with the agentteams release baseAgent pins (1.0.x writes the roster from
   `authorized_managers` too). Review the diff: only the three roster files and the brief should change.
4. Optional, operator ruling **O1**: render on agentteams v1.1 (rc.11 now, or 1.1.0 final when released) to gain Rule
   S-VM. Not needed for handoffs to work.

Not requested: the `vulnerability_remediation` capability (it adds the operator team's scan-triage agent, which
queues requests; reviewing a handoff does not need it), and any change to baseAgent's research agents.

## After the PR merges (operator, in the agentteams terminal)

1. Fast-forward the registry's fix checkout of baseAgent (`git -C <baseAgent checkout> pull --ff-only`, only when it
   is clean or its uncommitted work is unrelated and preserved); `enroll` reads the brief from there.
2. `python -m agentteams.vulnscan enroll <baseAgent checkout>`, answer `e` (enrol at the brief's level).
3. `python -m agentteams.vulnscan status --project <baseAgent checkout>` shows `level remediate`.
4. Replace the repository grant so it covers baseAgent (agentteams plan `vba-p2-grant-replace`; operator-signed).

## Acceptance

`status --project` for baseAgent: level `remediate`, grant covers it; its goose roster names the manager team;
`handoff status` lists baseAgent once a fix is staged; the next daily lists crates.io findings for baseAgent's
`serve/deploy/*` and `wasm/rust` lockfiles, if any exist.
