<!-- AGENTTEAMS:BEGIN content v=1 -->
# Skill Generation Reference — AgentTeamsModule

The capability-gap protocol: what to do when a request needs a resource this team cannot
already reliably access. Operative step: Workflow 0's "Capability gap check" in the
orchestrator.

## Trigger

**Attempted, not suspected.** This protocol applies after actually trying existing
capability — this team's declared tools/extensions, plus the discovery and installation
methodology in `references/cli-tool-discovery.reference.md` — and confirming it does not
reliably satisfy the request. Concluding "I don't have a tool for this" without first working
the gap is exactly the failure mode this reference exists to close.

## If direct access succeeds

Just do it. This protocol only applies once existing capability has genuinely been tried and
found insufficient — it is not a mandatory detour for requests that are already fulfillable.

## If it doesn't: two-part response, not a bare refusal

1. **Best-effort access now.** If any partial path exists — a manual workaround, a
   lower-fidelity substitute, a nearby tool that gets close — use it and say plainly what it
   does and doesn't cover.
2. **A plan to build durable infrastructure for reliable future access.** Examples: wiring in
   a needed extension, installing a missing CLI tool (per
   `references/cli-tool-discovery.reference.md`, subject to its Security Rule S-4 clearance
   requirement), or adding a small script. Surface the option to build the capability — don't
   stop at reporting that it's unavailable.

## Output and ownership — reuse this project's own plan mechanism

Part 2 is itself multi-step work. It does not get a free-floating suggestion; it follows this
team's own mandatory-plan rule exactly like any other multi-step request:

- A summary at `tmp/by-week/YYYY-Www/<plan-slug>.plan.md` and a step CSV at
  `tmp/by-week/YYYY-Www/<plan-slug>.steps.csv`, before the first infrastructure-building step
  executes.
- The agent that hit the gap opens that plan.
- If closing the gap means changing this team's own generated agent infrastructure (a
  template, a framework adapter, a routing rule) rather than a one-off local install, route
  through the orchestrator to the relevant specialist instead of acting solo — the same
  routing discipline that applies to any other domain-specific change.
- The plan gets the same audit chain as any other plan: `@adversarial` and `@conflict-auditor`
  before execution, and after each completed step.

## Why this exists

A team that only ever reports "I can't do that" for a genuinely reachable or buildable
capability under-serves every future request of the same shape. This reference exists so that
a confirmed capability gap becomes a scoped, auditable plan — not a dead end, and not an
unaudited ad hoc fix either.
<!-- AGENTTEAMS:END content -->
