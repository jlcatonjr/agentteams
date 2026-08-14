# `feature_audit`

Verifies that features documented in [Feature Inventory](feature-inventory.md) still
function, via the machine-readable registry at `references/feature-registry.csv`.

Procedure: `references/feature-audit.procedure.md` ·
Driver: `scripts/run_feature_audit.sh` ·
Workflow: `.github/workflows/feature-audit.yml` (daily, 04:37 UTC)

## Why the registry is derived, not authored

The inventory body is already machine-parseable — each feature is `<n>. **Name**` under a
`## Category`. The registry is generated from it, so the prose stays the single authored
source. A second hand-maintained list would rot exactly as the summary table did: it read
`125 features across 12 areas` against a body of `146 across 14`, omitting two whole
capability areas, because the only check compared the total to *its own column*.

## Three rules that keep it honest

1. **`proven` is self-enforcing** — a row needs both a `proof_test` and a **distinct**
   `negative_control`; `load_registry` downgrades anything else. A proof with no negative
   control shows a test runs, not that it can fail.
2. **A tier that executes zero proofs is a finding** — otherwise an all-`UNPROVEN` registry
   passes every structural check and exits clean.
3. **Unreachable is a value, not an exception** — `agentteams/research` is
   degrade-don't-raise, so a live probe asserting only "no error" passes on an empty
   response. Live proofs assert positive content; an outage reports `UNREACHABLE` and never
   gates.

## Outcomes

| Code | Meaning |
|---|---|
| 0 | clean — every executed proof passed; registry matches the inventory |
| 1 | findings — a proof failed, the registry drifted, or a tier proved nothing |
| 2 | harness broken — malformed/missing registry, engine death, unclassified exit |

Indeterminate is not a pass; code 2 outranks code 1.

## Usage

```bash
python -m agentteams.feature_audit --tiers unit          # structural + unit proofs
python -m agentteams.feature_audit --structural-only     # parity and resolution only
bash scripts/run_feature_audit.sh                        # driver, with outcome classification
FEATURE_REGISTRY=/path/to/fixture.csv python -m agentteams.feature_audit   # test override
```

## Current coverage

**6 of 151 proven.** `UNPROVEN` means nothing specifically demonstrates the feature — not
that it is broken. See the procedure doc for how to close a row correctly.
