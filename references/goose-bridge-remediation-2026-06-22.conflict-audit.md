# Conflict Audit — Goose Bridge Remediation (2026-06-22)

Scope: `references/plans/goose-bridge-remediation-2026-06-22.plan.md` (R1–R5 as drafted),
against `CLAUDE.md`, `references/bridge-refresh-safety.md`, `references/filing-conventions.md`,
`STABILITY.md`, `agentteams/bridge.py`, and the bridge/goose/hygiene test suites.

## Consistency Checks

1. Filing conventions — plan + audit-record placement.
- Status: PASS.
- Evidence: The plan lives at `references/plans/<slug>-2026-06-22.plan.md`, the retained-plan home mandated by `filing-conventions.md` and `CLAUDE.md`, using the `<slug>-YYYY-MM-DD.plan.md` form. No plan/report doc is written to the repo root by the plan itself; the `test_no_stray_plan_docs_at_repo_root` allowlist is untouched by the plan files. (A separate root-doc conflict from R4's runtime artifact is Check 5.)

2. Bridge-refresh-safety — R4 mode choice and pre-flight.
- Status: PASS.
- Evidence: R4 uses `--bridge-merge`, not `--bridge-refresh`, exactly as `bridge-refresh-safety.md:25-27,109-115` mandates for the SHARED `AGENTS.md`. Pre-flight records the working tree is all-untracked and that merge skips unfenced files / re-renders only fenced regions — consistent with the Mode table (`:18`) and "no default bridge mode" rule (`:21`), and with `run_bridge` (`bridge.py:287-316`). The root `AGENTS.md` carries the `goose-bridge-entry` fence, so merge re-renders it (does not skip) — R4's expectation holds.

3. STABILITY.md — "warn, not error" (R1) and the new notice.
- Status: PASS.
- Evidence: R1 keeps the empty-inventory signal a `result.notices` entry, not a hard error. Correct on two grounds: goose is an explicit **beta** framework whose emitted-artifact shapes are outside the SemVer contract (`STABILITY.md:30-36`), and `run_bridge`/`_bridge`/`BridgeResult.notices` are underscore-internal, non-`api-reference` surfaces (`STABILITY.md:38-40`), with human-readable notice wording explicitly uncovered (`:78-79`). A hard error would change the success/exit contract of a previously-passing input — correctly avoided.

4. Test consistency — R1 empty-inventory notice vs exact-empty-`notices` assertions.
- Status: PASS with note.
- Evidence: `tests/test_bridge.py:214` and `:257` assert `notices == []` exactly. R1 fires only when `len(inventory) == 0`; both tests build their source via `_build_source(...)` (one orchestrator agent), so `inventory_count >= 1` and the notice will not fire. Latent fragility: any broadening of the trigger (low-count, or unconditional) breaks these equality assertions. R3's new tests are additive. No existing test asserts `.DS_Store` presence/absence in `source_hashes`, so R2 breaks none.

5. Behavioral consistency — R2 hashing change + the root-doc-hygiene guard.
- Status: FAIL (two problems; the second is blocking).
- Evidence:
  - (a) Manifest path-key mismatch: changing what `_collect_source_files` hashes makes a standalone `--bridge-check` against an OLD manifest report removed paths as Missing → FAIL (`bridge.py:530,540-542`). Mitigated by ordering: `scripts/run_daily_bridge_maintenance.sh` always runs `--bridge-merge` (line 122) then `--bridge-check` (line 126) per target, so each run regenerates the manifest with the new rule before checking — self-consistent. No standalone `--bridge-check` exists in CI/scripts. The scoped-out claude (34) / copilot-cli (30) manifests still record `_build-description.json` and will reconcile on next daily merge. Plan must state this ordering dependency.
  - (b) **Blocking:** `tests/test_root_doc_hygiene.py` FAILS now — the bridge planted `AGENTS.md` at the repo root (untracked, fenced), and `AGENTS.md` is not in `ALLOWED_ROOT_MD` (`:27-46`). R4 (`--output .`) writes `AGENTS.md` to the output root (`bridge.py:643,708`), so the guard stays red and R5's "full suite green" claim is false. The plan must allowlist `AGENTS.md` (a legitimate bridge-owned, fenced, shared entry file — `bridge-offline-investigation.md` precedent) with a `filing-conventions.md` note.

6. Scope-out claims — daily maintenance and claude bridge.
- Status: PASS.
- Evidence: `run_daily_bridge_maintenance.sh:112` sets `targets=("copilot-cli" "claude")` — goose is not in the daily loop, so leaving it manual is consistent. The script already uses `--bridge-merge` with a rationale matching `bridge-refresh-safety.md`. Excluding the claude 34→35 inventory refresh is a defensible separate-staleness scope-out. The interaction risk this creates for R2 is captured in 5(a).

## Conflict Verdict

PASS with conditions. Design aligns with the bridge-refresh-safety, filing, and stability authorities (Checks 1–3, 6 PASS; Check 4 PASS with note). Check 5 must be resolved:

- **Blocking (5b):** allowlist the bridge-owned root `AGENTS.md` in `test_root_doc_hygiene.py` (+ filing-conventions note), or R5's green-suite criterion is false.
- **Condition (5a):** state the R2→daily-merge ordering and confirm no scoped-out manifest is checked standalone (verified: none is).
- **Condition (4):** implement R1's trigger as strictly `len(inventory) == 0`.

---

*Conditions resolved in the revised plan: R6 (allowlist + filing note), R2 blast-radius note, R1a strict trigger. See plan §5 trace.*
