# Refactor — Next Phases Plan (Phase 1 completion → Phase 2/4)

**Branch:** `refactor/code-hygiene` · **Protocol:** commit-and-push per milestone (no PRs) · behavior-preserving moves, full suite green per commit; exception sweep is intentional-change-with-new-tests.
**Status:** DRAFT → audited (§Audit appended after adversarial + conflict audits).

## 0. Where we are (pushed)
build_team.py 4086→2621. Extracted: `cli/security_gate.py`, `cli/parser.py`, `cli/render_pipeline.py`; centralized `frameworks/registry.py` (CH-05); deleted dead `src/analyze.py` (CH-10); added `tests/test_code_hygiene.py` ratchets. Suite 1283 green.

## 1. Verified coupling facts (load-bearing for this plan)
- **Re-export pattern** keeps tests green for leaf moves *as long as the caller stays in build_team* (bare-name calls resolve in build_team's namespace; `monkeypatch.setattr(build_team, …)` still hits them).
- **The one blocker:** `_assert_security_intelligence_fresh` is called by `main`, `_run_convert`, `_run_interop`, `_run_bridge`; **4 tests** `monkeypatch.setattr(build_team, "_assert_security_intelligence_fresh", …)` around `main(--bridge-from/--self/--update)`. Moving any of those callers out of build_team makes their bare-name call resolve elsewhere → the patch silently no-ops → gate runs unpatched.
- **`__file__`-anchored, must stay in build_team:** `_persist_orphan_events`, `_persist_shrink_events`, `_check_dual_descriptor` (write to `<build_team dir>/tmp/...`; 2 tests `monkeypatch.setattr(build_team, "__file__", …)` redirect them).
- **Artifacts** (`_write_delivery_receipt/_eval_suite/_model_routing`, memory-index fns, `_compute_file_hashes`, `_require_jsonschema` + error classes) use `Path(__file__).resolve().parent / "schemas"`; resolved dir = repo-root/schemas.
- **`_run_migrate` calls `main`** (self-re-invocation) and `_git`; **must stay** in build_team or use a lazy `import build_team`.
- **`_require_jsonschema` / version block / `inspect_ai` YAML catch** are load-bearing optional-dependency/degradation handlers — **never narrow** (CH-24 "unavoidable external boundary").

## 2. Steps (each = one commit, suite green, then push)

### Step A — Route gate calls through the `security_gate` module (security-sensitive unblock; TEST-FIRST)
*Why first:* it removes the only obstacle to moving `commands` and `main`, and it is the most safety-critical change, so it gets isolated review.
- In `build_team`, add `from agentteams.cli import security_gate`; change every `_assert_security_intelligence_fresh(...)` call (in `main`, `_run_convert`, `_run_interop`, `_run_bridge`) to `security_gate._assert_security_intelligence_fresh(...)`. *(Also route `_assert_destructive_action_allowed` through the module for consistency, even though it is not currently patched.)* This is behavior-identical (same function object); only the patch target moves.
- Repoint the **4** `monkeypatch.setattr(build_team, "_assert_security_intelligence_fresh", …)` → `monkeypatch.setattr(security_gate, …)` (import the module in `test_build_team_option_matrix.py`).
- **New tests (CH-21):** (a) the routed call IS patchable via `security_gate` (bypass still works); (b) a **positive deny test** — with the gate *unpatched*, a stale-intel `main` run still raises/blocks (proves routing didn't neuter the gate).
- Gate: full suite green.

### Step B — Extract `cli/commands.py`
- Move `_run_convert`, `_run_interop`, `_run_bridge`, `_normalize_bridge_output_root` + `_BRIDGE_AGENTS_DIR_SUFFIXES`. They call `security_gate._assert_security_intelligence_fresh` (routed in A) → no patch breakage. Imports: `FRAMEWORKS`, `Path`, `security_gate`, local `convert/interop/bridge` imports. Re-export from build_team. **Keep** `_run_migrate`/`_run_revert_migration`/`_git` in build_team (migrate self-invokes `main`).
- Gate: suite green.

### Step C — Extract `cli/artifacts.py` + `agentteams/errors.py`
- `errors.py`: `class AgentTeamsError(Exception)` base + move `DeliveryReceiptError`, `EvalSuiteError`, `ModelRoutingError`, `MemoryIndexError` (subclass the base; keep `RuntimeError` mixin to preserve `except RuntimeError` callers). Re-export from build_team (+ artifacts) for tests.
- `cli/artifacts.py`: move the writers + memory-index fns + `_compute_file_hashes` + `_require_jsonschema` + receipt/eval/routing rel-path constants. Re-anchor schema paths to `Path(__file__).resolve().parents[2] / "schemas"` (verify resolves identically). **Do not alter `_require_jsonschema` logic.** Re-export. `_run_refresh_index`/`_run_query_index` move too (called by main → re-export).
- Gate: suite green + assert each emitted artifact still validates against its schema.

### Step A.5 — Re-home `_MIGRATE_GATE_EXEMPTION_ACTIVE` (CRITICAL prerequisite for Step D; TEST-FIRST)
*Both audits flagged this as a latent fail-closed/fail-open regression.* The flag is a build_team module global (`:2453`), **written** by `_run_migrate` (`:2526`, stays in build_team) and **read** by `main` (`:1348`, moves in D). Splitting them silently breaks the migrate gate exemption; no current test covers it (migrate tests stub `main`).
- Move the flag into `security_gate` with `set_migrate_exemption(bool)` / `migrate_exemption_active() -> bool` accessors; route the writer (`_run_migrate`) and reader (the gate / `main`) through them. Behavior-identical while everything is still in build_team.
- **New test (blocking):** a real `agentteams --migrate` run (not stubbed) reaches overwrite with the exemption active and is NOT blocked by the destructive gate; and without the exemption it IS blocked. This is the regression test the suite currently lacks.
- Gate: suite green.

### Step D — Split `main` → `cli/app.py`; `build_team.py` becomes a thin shim (depends on A, A.5, B, C)
- Move `main` + `_finalize_exit_code` to `cli/app.py`. **Dispatch-table ONLY for Layer-1 standalone commands** that early-`return` (`--fleet`, baseline, fence-markers, `--self`, `--revert-migration`, `--migrate`, `--convert-from`, `--interop-from`, `--bridge-from`). **The generate/update/check pipeline stays a single linear handler — the inline security gates at `:816/:914/:1350` and their documented "gate-BEFORE-side-effect" ordering (`:906-911`, `:1341-1342`) are NOT reordered or hoisted.**
- **Enumerate all ~43 module-level names `main` references** (AST) and import each into `app.py`: the re-exported cli symbols (security_gate, parser, render_pipeline, commands, artifacts), `analyze/emit/ingest/render/drift/liaison_logs`, `Path/sys`, `FRAMEWORKS`, and the build_team-resident `_attempt_auto_correct`, `_heal_build_log_baseline`, `_prune_removed_files`, `_check_dual_descriptor`, events, `_git`, migrate, `_SCRIPT_DIR`/`TEMPLATES_DIR` — the build_team-resident ones via a **lazy `import build_team` inside `main`** (mirrors `fleet.py:345`); build the dispatch table **inside `main`**, never at app.py module level, to avoid a load-time `build_team ↔ app` cycle.
- `app.py` recomputes the self-update root as `Path(__file__).resolve().parents[2]`; **caveat:** this severs the `--self` link to a patched `build_team.__file__` (no test combines them today — document it).
- `__file__`-anchored events + `_git` + migrate **stay in build_team** (preserves the 2 `__file__` patches). `build_team.py` → thin shim: `from agentteams.cli.app import main, _finalize_exit_code, _deprecated_build_team_entry` + a re-export block that is the **full union** of every `build_team.<name>` the tests + `man.py` touch (error classes, `*_REL_PATH` constants, artifact writers, `_check_dual_descriptor`, `_build_parser`, submodules `render/ingest/emit/analyze`, etc.) — not a short list. Console-script + `py-modules=["build_team"]` unchanged.
- **Line budget (verify before commit): both `cli/app.py` AND the build_team residue < 1000.** If `app.py`'s `main` alone breaches it, extract the Layer-1 handlers into separate `app.py` functions (or `cli/commands.py`) until under. build_team.py then **drops from the CH-07 allowlist** (the stale-allowlist guard forces this).
- Gate: suite green; smoke-test dispatch for generate/update/check/bridge/convert/interop/migrate/dry-run; order-test that the gate fires before any filesystem mutation on each destructive path.

### Step E — Phase 2 exception sweep (CH-24/CH-23; TEST-FIRST; security_gate EXEMPT)
- Sweep the 15 broad + 29 swallow sites hottest-first. Decision tree: flow-control→dict/guard; validatable precondition→`raise` (CH-23); genuine external boundary→narrowest catch + wrap/re-raise via `errors.py`. **Preserve** `_require_jsonschema`, version block, `inspect_ai` (one-line CH-24 justification each). Write a failure-branch test **before** each narrowing. Lower `BROAD_EXCEPT_BASELINE`/`SWALLOW_BASELINE` as counts drop.

### Step F — Phase 4 type guards (CH-22) on the new `cli/*` public boundaries (scoped to touched surfaces).

### Step G — Pre-merge gates (constitutional): run the **CH-25 AI bad-habits screen** (`agentteams/ai_bad_habits.py`) over the full refactor diff; clear `@code-hygiene` + `@security`. Only then is the branch merge-ready.

## 3. Risk controls
Behavior-preserving moves verified by the 1283-suite per commit; security gate sweep-exempt with positive deny tests; load-bearing optional-dep catches preserved; commit-and-push per step; the `__file__` and gate monkeypatch semantics explicitly preserved by keeping anchored functions in build_team and routing gate calls through one module.

## Audit — findings & revised decisions (supersede the body where they conflict)

Two independent audits ran against the live code. Both confirmed Step A is sound and Step D was **not executable as written**. Corrections:

**Verified correct:** call-site counts (4 freshness callers: `816/1525/1609/1721`), routing semantics (patching `security_gate.X` reaches build_team's `security_gate.X(...)` call), broad/swallow = 15/29, the CH-07 stale-allowlist guard fires correctly, and the goose reconciliation cost is unchanged (Phase 1 already relocated all 3 goose-edited targets; Steps A–G add zero new goose conflicts).

**Must-fix, now folded in:**
1. **`_MIGRATE_GATE_EXEMPTION_ACTIVE` (CRITICAL).** New **Step A.5** re-homes it with accessors + a real migrate-through-gate test, before Step D.
2. **Gate is inline mid-pipeline, not a routable branch.** Step D dispatch-table scoped to **Layer-1 only**; generate/update/check pipeline stays linear; gate ordering untouched (+ an order-asserting test).
3. **Step A also routes `_assert_destructive_action_allowed`'s 4th caller in `_prune_removed_files:1814`** (not just main).
4. **Step C: 5 schema sites** (`2019/2066/2112/2243/2348` — memory-index anchored twice); relabel as a *structure-preserving edit* (not a "move") needing a positive (resolves to repo-root/schemas) **and** negative (wrong anchor fails) test.
5. **Step D dependency surface = ~43 names** (incl. orphaned `_attempt_auto_correct`/`_heal_build_log_baseline`/`_prune_removed_files`); lazy dispatch table to avoid the import cycle; re-export = full union; explicit <1000 line budget for **both** `app.py` and the build_team residue; D depends on C.

**Behavior-honesty (conflict #1):** Steps C and D are **structure-preserving edits**, not pure "moves" — verified by targeted new tests, not just suite-green.

**Clearance in the no-PR / commit-and-push model (conflict #3 + user directive):** pushing each step to `refactor/code-hygiene` is fine; **clearance gates the eventual merge of `refactor/code-hygiene` → `main`, not the feature-branch pushes.** This explicitly supersedes `refactor-plan.md` §9G's "separate PR for Phase 2" — per the user's commit-and-push directive, Phases 1 and 2 land sequentially on this one branch; the CH-25 screen + `@code-hygiene`/`@security` clearance is the merge-to-main gate.

**CH-25 timing (conflict #4):** the security-sensitive **Step A gets the CH-25 bad-habits screen immediately after it lands** (not deferred to Step G), and Step A.5's positive deny test is a **blocking gate** before Step B. Step G remains the full-diff backstop screen.

**Single most important constraint:** do not treat the security gate as a dispatch entry, and re-home the migrate-exemption global before moving `main`. Everything else is wording/sequencing.
