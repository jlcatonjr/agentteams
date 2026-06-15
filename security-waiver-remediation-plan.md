# Plan: Next steps from the bridge-offline investigation (W1 + W2)

**Date:** 2026-06-15 · **Branch:** `refactor/code-hygiene` (== main) · **Protocol:** commit-and-push (no PRs); behavior-preserving where possible; security-sensitive changes pass independent clearance before merge-to-main.
**Status:** DRAFT → audited (§Audit).

## 1. Scope — the actionable findings from `bridge-offline-investigation.md`
The investigation concluded the bridge `offline=False` is intentional (live-fetch policy), with three weaknesses. Two are actionable now; one is a maintainer policy call we will NOT decide unilaterally.

| Finding | Action |
|---|---|
| **W1** — bridge live-fetch policy undocumented; the freshness gate's `RuntimeError` doesn't mention the waiver escape or that `--security-offline` doesn't apply to cross-framework ops | **FIX** — improve the gate error message + add code comments at the 3 cross-framework `offline=False` sites. |
| **W2** — the air-gapped waiver escape is real but its docs/tooling are **fictional/inaccurate**: `security-hardening-guide.md` advertises `--create-waiver`/`--verify-waivers` (do not exist) and a **5-column** CSV + comma-joined HMAC that **do not match** the code's **11-column** schema + **pipe-joined 9-field** HMAC | **FIX** — correct the docs to the real schema + an accurate manual mint procedure; (optionally) build a read-only `--verify-waivers`. |
| **W3** — whether forcing a live fetch (vs accepting a fresh cache) is too strict | **DEFER** — maintainer policy call; do NOT change behavior. |

**The ground truth (verified in code):**
- Waiver CSV columns (`security_gate._SECURITY_WAIVER_REQUIRED_COLUMNS`): `timestamp, waiver_id, action_reviewed, expires_at, max_uses, uses, approver, ticket_id, reason_code, conditions_verified, signature`.
- HMAC-SHA256 payload (`_validate_security_waiver`), pipe-joined, **9 fields, excluding `timestamp` and `signature`**: `waiver_id|action_reviewed|expires_at|max_uses|uses|approver|ticket_id|reason_code|conditions_verified`.
- Additional validity rules: `conditions_verified == "verified"`; `approver`/`ticket_id`/`reason_code` non-empty; `expires_at` in the future; `max_uses > 0` and `uses < max_uses`; signing key from `AGENTTEAMS_WAIVER_SIGNING_KEY`; stored signature compared lowercased; `action_reviewed` must match the action (`security-intel-freshness`).

## 2. Phased plan

### Phase 1 — W1: gate error message + code comments (low-risk; test-first)
- **P1a (error message):** In `agentteams/cli/security_gate._assert_security_intelligence_fresh`, when it raises for stale-with-no-waiver, extend the message to point to the escape: e.g. append "— add a signed `security-intel-freshness` waiver for air-gapped/offline use (see docs); note `--security-offline` does not apply to bridge/convert/interop." This is additive to the message string only — no control-flow change.
- **P1b (test):** assert the new message text appears (a focused unit test of the raise path). The existing freshness deny-tests already cover the raise.
- **P1c (comments):** add a one-line comment at the 3 `offline=False` sites (`commands.py:52/136/238`) — "cross-framework external write: live security intel enforced; air-gapped uses a `security-intel-freshness` waiver, not `--security-offline`."

### Phase 2 — W2: correct the broken docs (low-risk; doc-only)
- **P2a:** Rewrite the "Creating and Verifying Waivers" section of `docs_src/security-hardening-guide.md` to the **real** 11-column schema and the **real** pipe-joined 9-field HMAC payload, with an accurate manual mint example (openssl/python over the real payload) producing a lowercase hex signature, plus the validity rules (conditions_verified=verified, future expiry, max_uses/uses, action_reviewed match).
- **P2b:** Remove or clearly relabel the **fictional** `--create-waiver`/`--verify-waivers` CLI examples. If we build `--verify-waivers` (Phase 3), keep that one and correct its description; otherwise mark both as "not implemented (manual process below)."
- **P2c:** Add a short note that bridge/convert/interop require live-or-waived intel (the policy from W1), so docs and code agree.

### Phase 3 — W2 tooling (OPTIONAL; decide via audit): read-only `--verify-waivers`
- Build a **read-only** `--verify-waivers` that lists each waiver in `references/security-waivers.log.csv` with validity (schema, signature, expiry, uses) — reusing the existing `_validate_security_waiver` logic. Read-only → no new security surface; makes part of the doc promise real.
- **`--create-waiver` (mints a signed waiver) is NOT in this plan.** It generates the security gate's escape credential; that is a security-boundary tool the maintainer should own (consistent with deferring W3). The accurate manual procedure (P2a) already makes the air-gapped path usable for a key holder. Propose `--create-waiver` as a separate maintainer-owned feature.

## 3. Risk controls
P1a/P1c and P3 are code changes → suite green + CH-25 screen + independent clearance before merge-to-main (security_gate is security-critical — P1a only touches an error-message string, no gate logic). P2 is doc-only. Each phase: commit-and-push; clearance gates the main fast-forward. No change to gate behavior or the live-fetch policy (W3 deferred).

## 4. Sequencing
P1 (error+comments+test) + P2 (docs) behind one clearance + merge. P3 only if the audit endorses building `--verify-waivers` now; otherwise defer with P2 documenting the manual path. Then, separately, review remaining Goose-integration steps.

## Audit — findings & revised plan (supersede §2-§3)

Both audits confirmed the foundation (schema/HMAC ground truth is correct, P1a additive, W2 premise true) but found four must-fixes. Revised plan:

### M1 (CRITICAL, CH-05/CH-14) — don't re-create the drift; add a single source + guard
W2 exists *because* the schema was hand-copied into docs and drifted from the code. Hand-copying the *correct* schema back reinstates the failure (the payload is already duplicated in `security_gate.py` `_validate_security_waiver` **and** `_consume_security_waiver_use`, plus the test helpers — 3-4 copies). **Fix:**
- **P2a (single source):** introduce an ordered `_WAIVER_SIGNATURE_FIELDS` tuple in `security_gate.py`; build the HMAC payload from it in BOTH `_validate_security_waiver` and `_consume_security_waiver_use` (DRY; CH-05). **Security-critical** — verify byte-identical signatures via the existing end-to-end waiver tests (`test_build_team_security_gates.py` signs+validates).
- **P2c (doc-drift guard test):** assert the doc's documented CSV header (as a set) == `_SECURITY_WAIVER_REQUIRED_COLUMNS` and the doc contains the exact `"|".join(_WAIVER_SIGNATURE_FIELDS)` payload string. Pins docs to code so W2 can't recur. Doc references `security_gate.py` as the authoritative source (CH-14); inline example minimized.

### M2 (HIGH) — regenerate the committed `docs/`
`mkdocs.yml` has `site_dir: docs`; the generated site is **tracked** (143 files; `docs/security-hardening-guide/index.html` still has the fictional content). No CI drift check catches this. **P2d:** after editing `docs_src/`, run `mkdocs build` and commit the regenerated `docs/` (if mkdocs isn't installed, install it from the docs extra or skip with an explicit note).

### M3 (HIGH) — accurate manual-mint snippet; honest UX framing
The doc's mint example must be a **Python snippet mirroring `security_gate.py`**: `hmac.new(key, "|".join([... 9 stripped fields, uses=0 ...]).encode(), sha256).hexdigest()` (lowercase). The broken comma-joined openssl one-liner is removed. Downgrade "the manual procedure makes the path usable" → "**reproducible by a careful key-holder; a brittle stopgap**."

### M4 (MEDIUM) — build read-only `--verify-waivers` now (reconciles the usability gap)
Building it (P3) is upgraded from "optional" to **in-scope**: the gate error (P1a) and docs point users to hand-mint a waiver, so they need a way to check it. **Hard constraints:** reuse `_validate_security_waiver`/`_latest_security_waiver` ONLY — **never** `_consume_security_waiver_use` (mutates `uses` + rewrites CSV) or `_assert_security_intelligence_fresh` (consumes on stale); degrade gracefully if `AGENTTEAMS_WAIVER_SIGNING_KEY` is unset (report "unverifiable", don't crash). Adds a small CLI flag + a Layer-1 dispatch branch in the just-refactored `parser.py`/`app.py` (not "zero surface"). `--create-waiver` stays **deferred** (mints the gate's escape credential — maintainer-owned, like W3).

### Other revisions
- **P1a:** must NOT touch the branch logic (the `fresh` early-return / waiver-consume path); keep the literal substring `stale`. **P1b:** one test, two asserts — the raise still fires on stale-with-no-waiver (positive deny, matching prior precedent) AND the new guidance text is present.
- Note the plan deliberately decomposes the report's bundled `--create-waiver`/`--verify-waivers` into build-verify / defer-create.

### Revised implementation order
P2a (single-source constant — verify byte-identical) → P1 (error+comments+test) → P3 (`--verify-waivers`) → P2c (drift guard) → P2b (docs) → P2d (regenerate `docs/`). One independent clearance over the whole delta (security_gate is touched) → merge. Then review Goose remaining steps.
