# Decision Required — should the security-intelligence freshness gate be scoped?

**Status:** Open. Awaiting an operator decision.
**Raised:** 2026-07-30, during the Medium-complexity P1/P2 remediation round.
**Decision owner:** the operator. This memo deliberately **recommends nothing** — the question is
how much security margin to trade for convenience, and that is not a call the implementing agent
should make on its own.

---

## The situation

`agentteams/cli/security_gate.py::_assert_security_intelligence_fresh` raises when the cached
security-intelligence snapshot is older than its 24-hour TTL and no signed waiver is present.
It is called once per run, before any file is written, so it fails the **entire** run.

Observed consequence, recorded in the remediation log: a run regenerating files with no
relationship to security content was blocked, and the workaround adopted at the time was to
bypass the CLI and drive `ingest`/`analyze`/`render`/`emit` directly — which skips **every** gate,
not just this one. That is the sharpest fact in this memo: the current design produced a
workaround strictly worse than any scoping proposal below.

A diagnostic shipped on 2026-07-30: the refusal now names its blast radius, stating how many
intel-bearing placeholders would actually have been interpolated. That improves the operator's
information. It does not change what is blocked.

---

## Option A — leave the gate as it is

**For.** A generated team is a single artifact. Its security agent embeds threat intelligence
with a stated freshness contract; shipping *any* part of a team while that contract is violated
means the team as a whole may carry expired advisories. The gate's all-or-nothing behaviour is
not an accident of implementation — it reflects the team being the unit, not the file.

Waivers already exist for the legitimate air-gapped case, they are signed, and
`--verify-waivers` audits them. The escape hatch is present and auditable.

**Against.** The failure mode is disproportionate and, as observed, drives operators to bypass
the CLI entirely — trading one stale-intel risk for the loss of every other control.

## Option B — scope the gate to files that carry intel

Refuse only the files whose rendered content interpolates an intel-bearing placeholder; generate
the rest, and report what was withheld.

**For.** Proportionate. Keeps operators inside the CLI, so the other gates keep applying. The
machinery already exists — the diagnostic added on 2026-07-30 computes exactly the set that would
be withheld.

**Against.** It changes what "the team is fresh" means. A team can now be half-regenerated, with
files from two different intel epochs, and nothing in the output records which. It also concedes
the principle: the next convenience argument starts from a gate that has already been narrowed
once.

## Option C — keep the gate, add a scoped waiver

A waiver that covers *only* non-intel files, signed and audited like existing waivers.

**For.** Preserves the all-or-nothing default while making the proportionate case expressible.
Every relaxation stays recorded and auditable, consistent with how this project already handles
`--security-offline` and destructive-action clearances.

**Against.** More machinery for a case a plain waiver already covers. Risks becoming the default
path, at which point it is Option B with extra ceremony.

---

## What is not in dispute

- The diagnostic is an improvement under every option and has already shipped.
- Bypassing the CLI is the worst available outcome and is what the status quo currently
  encourages.
- Whatever is chosen should be recorded here rather than inferred from the code.

## To decide

State the option in this file with a date, and log the outcome in
`references/agentteams-remediation-log.csv`. If Option B or C is chosen, the implementing work
should re-enter through a plan with `@security` in the audit set, not as an incidental change.
