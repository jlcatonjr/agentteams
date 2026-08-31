# Production-readiness report — agentteams Security Infrastructure handbook

**Status: ready to publish.** Four editions authored from a single source, every claim sourced and
✅/⚙-marked, conformance green, write-scan clean, and every honest ceiling carried into every edition
including the plain-language one.

## What was built

A single-source, multi-projection handbook at `docs_src/agentteams-security-guide/`:

- **`SKELETON.md`** — 27 sections across 9 parts; each with numbered canonical facts, a `Source`, a
  ✅/⚙ marker, and per-edition depth dials. The single place facts/structure live.
- **Four editions** projected from it:
  - `reference/` — 1,628 lines, full mechanism + `file:line` + every ceiling (the dense elaboration).
  - `for-developers/` — 1,224 lines, task-oriented, runnable commands.
  - `for-researchers/` — 1,366 lines, threat-model-first, guarantees-and-ceilings.
  - `for-everyone/` — 378 lines, plain-language analogy, no code, S27 skipped.
- **Scaffolding** — `audience-profiles.md`, `SOURCES.md` (per-section provenance), `README.md`, and
  `_meta/` (`scaffold.py`, `check-skeleton.py`, `projection-guide.md`).

## Coverage (the section map)

Part I what-it-is/why (threat model, two surfaces) · II constitution & authority (C-1..C-5,
instruction-authority ordering, `@security`, HALT, capability limits) · III clearance/waivers/grants ·
IV the gates (destructive, intel-freshness, shrink-policy, bridge-refresh) · V content safety (scanner,
redaction) · VI OS confinement (L0–L7 model, sandbox profiles, the PreToolUse hook) · VII threat
intelligence & red team · VIII integrity/provenance/recovery · IX synthesis & reference matter.

## Verification trail

- **Provenance (`@technical-validator`): no flags.** Every `Source` resolves on disk; every ✅ is a real
  implemented control; every ⚙ is correctly design/procedural. High-risk claims verified directly
  (no formula-injection detector; 24h intel TTL + payload-digest bind; HMAC fail-closed; integrity
  manifest self-inclusion; macOS-only confinement; 9-column decisions-log schema).
- **Adversarial (skeleton, pre-authoring): four ceiling gaps caught and folded in** before any edition
  was written — default posture is opt-in (S1.5); the hook is fail-open by default (S19 marker); the
  sentinel is a fallible LLM (S5.6); two ceilings marked mandatory-survive for the plain edition.
- **Consistency (`@conflict-auditor`): clean** — no cross-edition contradictions; SOURCES↔SKELETON
  agree; anchors/IDs intact; one glossary gap fixed.
- **Adversarial (projections): cleared** — no dropped/softened ceiling, no plain-language overclaim; the
  four mandatory-survive ceilings present as plain sentences in `for-everyone`. Three minor revises
  applied (E landing-page glyph/code cleanup; two accurate facts legitimized in the skeleton).
- **Conformance:** `python3 _meta/check-skeleton.py` → all editions conform (reference/developers/
  researchers 27; for-everyone 26, S27 Skip).
- **Content safety:** every guide file passes `agentteams.scan` (no HALT). The guide tripped the
  injection scanner twice during authoring on quoted attack examples — a live demonstration of the
  C-4 / scanner-shape-blindness point it documents (S15).

## Honest ceilings the guide commits to (carried into all editions)

None of this runs inside the produced app (design-time only) · runtime OS-confinement is opt-in
(cooperative default = sandbox off, hook fail-open) · confinement is empirically verified on macOS
only (Linux D-3 open, Windows design-only) · symmetric-HMAC signing stops a keyless forger, not a
key-holder · the integrity manifest is a speed bump, not a boundary (E4 residual) · the sentinel is a
fallible LLM except the S-1/S-8 deterministic scanner backstop · no formula/CSV-injection detector
exists · emitted sandbox blocks are inert until the operator wires them.

## Known limitations / follow-ups

- Line numbers in `Source` lines are point-in-time; they will drift as the code evolves. The concept
  anchors and file paths are stable; re-verify via `@technical-validator` on a refresh.
- The `_meta` tooling checks structure, not facts; a fact change must go through the skeleton-first
  workflow (`_meta/projection-guide.md`) and a re-verification pass.
- Runtime-created ledgers (`security-waivers.log.csv`, `security-approvers.txt`,
  `capability-grants.log.csv`) are created on demand, not shipped — noted in S9/S10.

## Maintainer note

Edit the skeleton first, then re-project (`_meta/projection-guide.md`); keep `check-skeleton.py` green.
