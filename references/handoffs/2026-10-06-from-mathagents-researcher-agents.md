<!-- Filed 2026-10-06 from baseAgent session baseagent-78 for operator Jim, as a PR (docs only). Source: mathAgents
     branch feat/researcher-agents, docs/handoffs/2026-10-05-to-agentteams-researcher-agents.md (verbatim below).
     Status against agentteams main 75fd790 at filing:
     - R1 (Goose over-grant): partly in hand. #109 (agentteams_readfs, phase 1) gives read-only Goose agents a
       read-only file server; the root fix (per-agent extensions + available_tools derived from tools:, and dropping
       dead load("orchestrator") text for agents without `agent`) is still open as far as this filer can see.
     - Orchestrator roster for adopted agents: #107 (88bed21) addresses it.
     - R2 (Rule 7 carve-out for orchestrator-written researcher artifacts) and R3 (Codex read-only vs derived
       outputs): open.
     The mathAgents side ships this as v0.2.4 (D38) on top of v0.2.3. Nothing else in this repository is changed. -->

# Handoff → agentteams: researcher agents, orchestrator-only writes (2026-10-05)

**From:** mathAgents (branch `feat/researcher-agents`) · **To:** agentteams maintainers · **Date:** 2026-10-05
**Operator directive (Jim, 2026-10-05):** "Only the orchestrator can write. These agents are researchers who report
to the orchestrator. If necessary, coordinate with agentteams concerning this issue." Scope chosen: no `edit` anywhere;
`execute` stays. Recorded in mathAgents as decision D38 (`docs/charter/03-decisions.md`).

**What changed in mathAgents.** The nine pipeline agents no longer hold `edit`. They are statement-formalizer,
fidelity-auditor, proof-strategist, logic-articulator, tactic-planner, lean-prover, counterexample-hunter,
axiom-auditor and engine-advisor. None holds `agent`; that was dropped in the earlier conformance commit.
- Their tools are now `[read, search]`, or `[read, search, execute]` for the formalizer, prover, hunter and auditor.
- They return every artifact as text, with its path, and the orchestrator writes it verbatim.
- Commands that write a committed record (`log_counterexample.py add|witness`, `snapshot_kernel.py`) are run by
  the orchestrator.
- `lean-prover` checks proof attempts with `lake env lean --stdin`, so no attempt is written.

Nothing in this handoff was changed in the agentteams repository. Its checks were read at `9f4136d`, and the
agent-contract functions were imported read-only with `PYTHONDONTWRITEBYTECODE=1`.

## Findings: the researcher model passes agentteams' checks

The checks were run on the nine agent files plus `orchestrator.agent.md`, at agentteams `9f4136d`:

| Check | Findings |
|---|---|
| `_check_invariant_core_present` (AR_MISSING_INVARIANT_CORE) | 0 |
| `_check_return_handoff_present` (AR_MISSING_RETURN_HANDOFF) | 0 |
| `_check_readonly_tool_declarations` (AR_READONLY_TOOL_VIOLATION) | 0 |
| `_check_writer_dispatch_grants` (AR_WRITER_DISPATCH) | 0 |
| `_check_instruction_authority_reachable` | 1 warning. It names `references/instruction-authority.reference.md`, which was not in the partial file map, so it is an artifact of the method, not of the agents. |

No rule requires a domain agent to hold `edit`, flags "drafts"/"writes" wording without `edit`, or flags `execute`
without `edit`. The rank ceiling for `domain`, `{read, search, execute, retrieval}`
(`agentteams/rank_conformance.py:96-100`), fits the researcher shape exactly. The previous `edit`-bearing versions
exceeded it.

## Requests

### R1. Goose over-grant: please fix it at the root (blocking for an unscoped Goose surface)

- **The problem.** `capability_map.py:180-183` maps `read`, `search`, `edit` and `execute` all to `developer`, and
  `frameworks/goose.py:184-191` always adds `developer` in the default `append` mode. So every recipe gets
  `developer`'s `write`, `edit` and `shell`, whatever the agent's `tools:` says. That includes a read-only
  researcher.
- **The workaround.** mathAgents scopes its own recipes after projection (`mathagents/_surfaces.py`,
  `scope_goose`). Each agent lists only the extensions its tools need, with `available_tools` (Goose 1.37):

  | Tool | Goose extension and tools |
  |---|---|
  | `read`, `search` | `developer.tree`, `analyze` |
  | `edit` | `developer.write`, `developer.edit` |
  | `execute` | `developer.shell` |
  | `agent` | `summon.delegate`, `summon.load` |

  Any other team's Goose projection still over-grants.
- **Dead handoff text.** The recipes of agents without `agent` still carry the line
  `load("orchestrator") ... then act on them here` (for example `.goose/recipes/lean-prover.yaml`). `summon` isn't
  loaded for those agents, so the line can't run. Please omit it, or write a plain "return to the orchestrator"
  line, when the agent has no `agent` tool.
- **The request.** Emit per-agent extensions with `available_tools` from each agent's `tools:`, or at least
  withhold `developer.write`/`edit` from agents without `edit`, and `shell` from agents without `execute`. Our map
  and the Goose 1.37 evidence are in `docs/uniform-surface-projection.md`.

### R2. The orchestrator template's Rule 7 conflicts with orchestrator-only writes

- **The conflict.** `templates/universal/orchestrator.template.md:182` says: "Domain agents own their scope — The
  orchestrator routes; it does not perform domain work directly." In a researcher team, the orchestrator writes
  what the domain agents return.
- **How we handle it now.** mathAgents states the duty in its `brief.json` orchestrator-duties style rule. That
  rule says to write each returned artifact verbatim, at the path the agent names, and to run the record-writing
  commands.
- **The request.** Add a template carve-out, or a brief field such as `write_authority: orchestrator`. It would say
  that persisting a researcher's returned content verbatim, and running record commands it names, is not domain
  work.

### R3. Codex `read-only` blocks the derived outputs of checking commands (known limit, unverified)

agentteams projects execute-only agents as `read-only` (`frameworks/codex.py:279-294`), and mathAgents now requires
that for its researchers. `lake build` writes `lean/.lake/`, and the G-KERNEL gate writes `reports/kernel/`, so the
sandbox may stop these agents from building or running the gates on Codex. Codex is not installed here, so this is
unverified.

If Codex offers a per-agent way to make only derived, gitignored paths writable, consider it. We have not checked
whether one exists.

### R4. `execute` is a write path on Claude and Goose (for awareness)

Claude's `Bash` and Goose's `developer.shell` are general shells, so for an execute-holding researcher the
"never write" rule rests on its instructions and on the orchestrator. agentteams already emits a scoped
`Bash(python -m agentteams.research:*)` for `retrieval`. An optional per-agent command allowlist that projects to
scoped `Bash(...)` patterns on Claude would let the surface enforce it there.

## Not requested

The adopted-agent roster fold-in (`--adopt-orphans --overwrite`) is unchanged by this work, and still waits on the
operator's clearance.
