# Conflict Panel Audit — Five Goose Forward-Implementation Plans (2026-06-22)

Plans P1–P5 audited against CLAUDE.md, filing-conventions, bridge-refresh-safety, STABILITY.md,
SECURITY.md, and the CH-07/CH-24/man/root-doc/api-parity guards.

## P1 — Live `goose run` delegation validation
- CI no-key/no-egress: `ci.yml` has no secrets/network; plan keeps live calls out of CI. **PASS.**
- Skip-by-default credential gate matches precedent (`test_goose_bridge_mcp.py:167` skipif goose absent;
  env/path skips elsewhere). **PASS.**
- SECURITY.md key handling: resolves key by env-file reference, never logs/serializes. **PASS.**
- Doc note lands in `docs_src/goose-system-prep.md` (nav-registered, tracked — currently untracked in WT). **PASS.**
- **Verdict: PASS** — binding: the test MUST be `skipif`-on-missing-key (never `xfail`/unconditional);
  key never logged/serialized.

## P2 — Goose-as-SOURCE bridging
- **CH-07 1000-line ceiling — FAIL (binding):** `bridge.py` is **970 lines**, 30 under the ceiling, NOT on
  `LENGTH_ALLOWLIST` (`test_code_hygiene.py:38-48`). P2 adds detect branch + recipe inventory + framework-aware
  hashing + allow-set branch → breaches 1000, trips `test_no_new_oversized_modules`. Plan does not budget a carve.
- STABILITY.md: goose is beta; "interop-to-Goose not yet supported" is a documented gap, not a contract — adding
  source extends beta. **PASS.**
- `_collect_source_files` reopening task-2 work: collision correctly identified; framework-extension selection is
  additive; binding control = task-2 regression tests still pass. **PASS w/ note.**
- CH-24 broad-except: regex reuse (no YAML dep, no blanket catch) keeps the baseline (11/29). **PASS w/ note.**
- **Verdict: FAIL** — binding: P2 cannot land without a CH-07 carve of `bridge.py` (extract source-collection/
  hashing helpers to a sibling module) so the post-change file stays ≤1000 lines.

## P3 — Subagent stubs for the copilot-vscode → goose bridge
- The claude analogue is NOT an argparse flag — it's a `--target-host-features` TOKEN
  (`bridge:copilot-vscode-to-claude:subagents`, `bridge.py:362-368`); mirroring it needs NO man-page regen
  (`--target-host-features` already in `agentteams.1:178`). "Add a flag" wording is misleading. **PASS w/ note.**
- bridge-refresh-safety: `.goose/recipes/bridge-orchestrator.yaml` is bridge-OWNED (`:45-48`); P3 writes additional
  `<slug>.yaml`, skips reserved/owned slugs, never overwrites. **PASS w/ note.**
- STABILITY beta output shape: opt-in default-off emitter keeps the plain bridge byte-identical. **PASS.**
- CH-07/CH-24: a parallel goose stub module (~claude's 333 lines) is well under 1000; no broad-except if mirrored. **PASS.**
- **Verdict: PASS with conditions** — gate via a `--target-host-features` token (NOT a new argparse flag); if a
  real flag is added, the plan MUST regenerate `agentteams.1` (else `ci.yml:41-45` man-diff fails).

## P4 — Add Goose to the daily bridge-maintenance loop
- bridge-refresh-safety on the SHARED `AGENTS.md` written every run: loop uses `--bridge-merge` (fenced-only,
  skips unfenced); repo's `AGENTS.md` already fenced + allowlisted (`test_root_doc_hygiene.py:38-44`). Consistent
  ONLY because merge is fenced-only and the file is fenced. **PASS w/ note.**
- `bridge-maintenance.yml`/watchdog: workflow just runs the script; watchdog tracks workflow-run age, not per-target
  freshness — no edit needed (P4's out-of-scope claim correct). **PASS.**
- Editing the scoped script in-convention (hard scope guard + `run_noncritical` isolation). **PASS.**
- **Verdict: PASS with conditions** — extend the script's safety comment (currently CLAUDE.md-only) to cover the
  goose shared `AGENTS.md`/`.goosehints` fenced-merge; keep the goose target on `--bridge-merge`, never `--bridge-refresh`.

## P5 — API-reference page for `agentteams/goose_config.py`
- COVERAGE_GAP is real; a stem-matched page (`goose-config` → `goose_config`) clears it. **PASS.**
- **`test_api_doc_parity.py` gates only on STALE_PAGE, not coverage gaps** — the suite is ALREADY green with the
  gap present (`test_api_doc_parity.py:41-47`); `--strict` (the only gating mode) is not run in CI. P5's "test goes
  green" success criterion is vacuously already-true and overstates the gate. **FAIL of the stated Verify.**
- mkdocs nav + index registration points correctly identified. **PASS.**
- No man-page/other registration (api-reference is doc-only; `cli` is `_EXEMPT`). **PASS.**
- **Verdict: PASS with conditions** — restate success as "`check_api_doc_parity.py` no longer lists
  `goose_config.py` under COVERAGE_GAP"; stem must be exactly `goose-config.md`.

## Cross-plan conflicts
1. **P2 ↔ P3/P4 contend for `bridge.py` CH-07 headroom (most serious):** `bridge.py` at 970/1000. P2 adds lines
   directly; if P3's emitter is wired with an in-`bridge.py` dispatch hook it consumes the same ~30 lines. P2 must
   carve `bridge.py` BEFORE/together with any plan adding lines there; P3 should land its emitter in a NEW sibling
   module (mirroring `bridge_subagents.py`) with only a minimal dispatch line.
2. **P2 ↔ P4 share the `_collect_source_files` hashing contract:** P4's daily copilot/claude checks rely on the
   current md-only behavior; P2's framework-aware rewrite must preserve it. Validate together or land P4 first.
3. **P2/P3/P4 all assume the just-committed task-2 hardening (md-only hashing + 0-inventory FAIL guard)** as
   load-bearing — review them as a coupled set; none may weaken it.
4. **Filing — all five PASS:** plans at `references/plans/` (gitignored) + `.steps.csv` siblings; `test_root_doc_hygiene`
   only scans repo-root `*.md`; no plan writes a root stray (P4's root `AGENTS.md` is allowlisted).
