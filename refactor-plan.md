# agentteams Refactor Plan — Security & Code-Hygiene Remediation

**Date:** 2026-06-15 · **Branch:** `refactor/code-hygiene` (off `main`, independent of `goose-integration`)
**Stance (decided):** phased / worst-offenders-first · behavior-preserving + fail-fast · full test suite green after every phase.
**Status:** DRAFT → audited (§9 appended after adversarial + conflict audits).

---

## 0. Standards basis — the repo's OWN rules (dogfooding)

agentteams ships a code-hygiene agent whose rule set is the authority for this refactor (`agentteams/templates/universal/code-hygiene.template.md`, `.../domain/code-hygiene-rules-reference.template.md`). The directly-relevant rules, quoted:

- **CH-24 — Exception Handling Is a Last Resort (Critical).** "`try`/`except`/`finally` is the *last* resort, not the first." Preferred order: (1) **encode expected conditions in data** (dict/lookup/dispatch); (2) **guard with explicit checks, then fail hard** (`raise`/`assert`); (3) **`try`/`except` only for genuinely unavoidable external failures** (I/O, network, subprocess, third-party). Prohibited: `except Exception` that swallows or logs-and-continues; try/except as flow control; `except` returning a fallback that masks a broken state. When warranted: catch the **narrowest** type; **re-raise (optionally wrapped)** or handle the specific recoverable case.
- **CH-23 — Fail Fast on Invalid Inputs (Critical).** Invalid inputs raise explicit errors; no silent pass; no implicit fallback masking bad inputs.
- **CH-22 — Type Check Function/Class Inputs (High).**
- **CH-07 — Standard Module Structure (Medium)** and **CH-10 — Dead Code Removal (Medium).**
- **CH-05 — Single Source of Truth for Mappings (Critical)** and **CH-13 — No Circular Imports (High).**

The refactor's success criterion is **measurable conformance to CH-05/07/10/22/23/24**, with **zero behavioral change** verified by the existing 1294-test suite.

---

## 1. Current state (measured 2026-06-15 via AST over `git ls-files '*.py'`, excluding `src/` + `tmp/` only)

- **62 modules.** 167 `try` blocks, **0 bare `except:`** (good), **15 `except Exception`/`BaseException`** (broad), **29 swallowed** (`except …:` whose body is only `pass`/`continue`). *(Corrected from an earlier draft's 16/28 per the adversarial audit; `scripts/` is included in scope.)*
- **Test suite: 1278 tests** (`pytest --collect-only`). *(Corrected from "1294".)*
- broad-except by file: `build_team.py` ×7; one each in `convert.py`, `fleet.py`, `handoff_payloads.py`, `ingest.py`, `mcp_emit.py`, `memory_index_incremental.py`, `plan_steps_todo.py`, `scripts/verify-env.py`.
- **Length offenders:** `build_team.py` **4086**, `analyze.py` 1503, `emit.py` 1389, `audit.py` 978, `security_refs.py` 851, `bridge.py` 794, `ingest.py` 779.
- **`build_team.py` dominates both axes:** 76/167 `try` blocks (46%), 7 broad, 5 swallowed; 54 top-level functions; two monsters — `_build_parser` ~550 lines and **`main` ~1100 lines**.
- **CH-05 violation:** the framework registry is **triplicated** — `build_team.py:84` `FRAMEWORKS`, `interop.py:24` `_ADAPTERS`, `convert.py:45` `_ADAPTERS`. (Adding a target requires editing all three; confirmed while adding Goose.)
- **CH-10 violation:** `src/analyze.py` (1026 lines) is a tracked, **dead duplicate** of `agentteams/analyze.py` — nothing imports it (`tests/test_analyze.py` imports `from agentteams.analyze`); its docstring "Tests for src/analyze.py" is stale.
- **No linter configured** (`pyproject.toml` has only setuptools + pytest).

---

## 2. Phase 0 — Guardrails first (lowest risk; no production behavior change)

Establish executable standards so the refactor can't regress and future code can't re-offend.

1. **`agentteams/errors.py` — a small exception hierarchy.** A base `AgentTeamsError(Exception)` plus the error classes currently scattered in `build_team.py` (`DeliveryReceiptError`, `EvalSuiteError`, `ModelRoutingError`, `MemoryIndexError`, `DeliveryReceiptError`) re-homed and re-exported (keep old names as aliases for any external import). Enables CH-24 "wrap with context, re-raise" without inventing types ad hoc.
2. **`tests/test_code_hygiene.py` — executable CH guards** (no new runtime dependency; pytest already present):
   - **CH-07 file-length ceiling:** assert no tracked non-test module exceeds **1000 lines**, with an explicit, *shrinking* allowlist seeded with today's offenders (`analyze.py`, `emit.py`, `build_team.py` until split). New oversize files fail immediately.
   - **CH-24 no-new-broad-except:** assert the count of `except Exception`/bare-`except` in tracked non-test code does not exceed a **baseline that only ratchets down** (seeded at 16). Prevents new broad catches; forces the sweep to lower it.
   - **CH-05 single-registry:** assert the framework registry is defined in exactly one module.
3. **Optional `ruff` (recommended, scoped):** add `[tool.ruff]` selecting only `BLE` (blind-except) + `E722` (bare except) so linting enforces CH-24 without a repo-wide style cleanup. Added to the `[test]` extra, not runtime deps (keeps the wheel small per the existing pyproject note). *If ruff surfaces noise, the pytest guard above is the hard gate and ruff stays advisory.*

**Gate:** full suite green (the new guard tests pass against the seeded baselines).

---

## 3. Phase 1 — Decompose `build_team.py` (the dominant offender)

Behavior-preserving extraction into an `agentteams/cli/` package. **`build_team.py` stays as a thin shim** (`from agentteams.cli.app import main, _deprecated_build_team_entry`) so the `agentteams = "build_team:main"` entry point and `py-modules = ["build_team"]` packaging are unchanged. Proposed module map (seams confirmed from the function inventory):

| New module | Moved from build_team.py | ~lines |
|---|---|---|
| `cli/app.py` | `main`, top-level orchestration, `_finalize_exit_code` | ~1150 → to be further split |
| `cli/parser.py` | `_build_parser`, `_validate_option_combinations`, `_check_dual_descriptor` | ~700 |
| `cli/security_gate.py` | `_assert_destructive_action_allowed`, all `_*_security_*`, `_consume_*`, `_latest_*`, `_action_matches` | ~400 |
| `cli/commands.py` | `_run_convert`, `_run_interop`, `_run_bridge`, `_run_migrate`, `_run_revert_migration`, `_normalize_bridge_output_root` | ~500 |
| `cli/render_pipeline.py` | `_build_final_rendered`, `_make_content_matches`, `_guess_file_type`, `_preserve_manual_values`, `_extract_resolved_value`, `_apply_placeholder_policy`, `_resolve_strict_manual_mode`, `_stale_tool_agent_paths`, `_remove_stale_tool_agents` | ~400 |
| `cli/artifacts.py` | `_write_delivery_receipt`, `_write_eval_suite`, `_write_model_routing`, memory-index writers/readers, `_compute_file_hashes`, `_require_jsonschema` (error classes → `errors.py`) | ~450 |
| `cli/events.py` | `_persist_orphan_events`, `_persist_shrink_events`, `_write_run_log`, `_heal_build_log_baseline`, `_git` | ~350 |

**Method:** pure *move* (cut/paste + import fix), one module per commit, **full suite green after each move** before the next. No logic edits in this phase — splitting and exception-cleanup are kept separate so a regression bisects to one or the other. `main` is split last, after its helpers are extracted, into a command-dispatch table (CH-24 "encode conditions in data": map `args.command → handler` instead of a long if/elif).

**Gate per commit:** `pytest -q` green; `git diff` shows only moves/import-rewrites.

---

## 4. Phase 2 — Exception-handling sweep (CH-24/CH-23)

Apply the CH-24 decision tree to the **16 broad + 28 swallowed** sites, hottest first (`build_team`/`cli/*`, then `emit.py`, `security_refs.py`, `fleet.py`, `ingest.py`, `memory_index*.py`, `enrich/_tools.py`). For each `try`/`except`:

1. **Flow-control-in-disguise** → replace with dict lookup / membership test / `if-elif` (no exception).
2. **Validatable precondition** → hoist an explicit guard that `raise`s (CH-23); delete the `try`.
3. **Genuine external boundary** (file/subprocess/network/`json`/`jsonschema`) → keep `try`, **narrow** the caught type, and **re-raise wrapped** in the relevant `errors.py` type with context (never `pass`/`continue`/return-fallback).

**Preserve intentional safety nets** (do NOT "fail-fast" these into breakage): the bridge-refresh safety gate, the security-decision gate, the `--fleet` post-merge jsonschema handling (per memory: a benign post-merge crash was already fixed — keep its guard), and any `except` whose swallow is a documented degradation path. Each preserved site gets a one-line comment justifying it as a CH-24 "unavoidable external failure" boundary.

**Gate:** suite green; the CH-24 baseline guard ratchets down to the new (lower) count.

---

## 5. Phase 3 — Single-source-of-truth & dead code (CH-05/CH-10)

1. **Centralize the framework registry** into `agentteams/frameworks/registry.py` (or `frameworks/__init__.py`): one `FRAMEWORKS: dict[str, type[FrameworkAdapter]]`. `build_team`, `interop`, `convert` import it. Removes the triplication (and the 3-edit tax a future target like Goose pays). Watch CH-13 (no circular import): registry imports adapters; consumers import registry.
2. **Delete `src/analyze.py`** (dead duplicate) after a final `grep` confirms zero importers; fix the stale `tests/test_analyze.py` docstring.

**Gate:** suite green; CH-05 single-registry guard passes.

---

## 6. Phase 4 — Type-checking on public boundaries (CH-22)

Add/verify type annotations and lightweight runtime guards (`raise TypeError`/`ValueError`) on the **public** function boundaries of the newly extracted `cli/*` modules and the centralized registry — not a repo-wide retrofit. Scope limited to the surfaces touched in Phases 1–3 to keep the diff reviewable.

**Gate:** suite green.

---

## 7. Risk controls & verification

- **Behavior-preserving contract:** no CLI flags, output paths, file formats, or exit codes change. The 1294-test suite is the oracle; it must stay green after **every commit**, not just every phase.
- **Bisectable history:** one cohesive move/sweep per commit; splitting (Phase 1) never mixed with logic edits (Phase 2).
- **Manual smoke tests after Phases 1–2** (beyond unit tests): regenerate a known team (`--description examples/data-pipeline/brief.json --project /tmp/rt --no-scan`) and `diff` the output tree against a pre-refactor capture — byte-identical expected. Exercise `--update`, `--check`, `--bridge-merge`, and `--dry-run` paths.
- **Safety gates are untouchable** without explicit call-out: bridge-refresh safety, security-decision gate.
- **Rollback:** each phase is a set of small commits on `refactor/code-hygiene`; revert is per-commit.

---

## 8. Out of scope / non-goals
- No functional/behavioral changes; no new features; no test rewrites (only additive guard tests + one docstring fix).
- No interaction with `goose-integration` (separate branch; will be reconciled at merge time — overlapping files like `build_team.py`/`emit.py` will conflict and are resolved then).
- No splitting of `analyze.py`/`emit.py`/`audit.py` in this pass beyond what the file-length allowlist forces; they stay on the allowlist as tracked debt (future phase).
- No dependency upgrades; `ruff` (if added) is dev-only and advisory.

## 9. Audit Findings & Revised Decisions

Two independent audits (adversarial + conflict) read this plan against the codebase. Both surfaced correct, serious problems. Empirically verified corrections and the revised decisions follow; they **supersede** the body above where they conflict.

### 9A. Corrections to facts (adversarial M3 — verified)
- Broad-except = **15** (not 16); swallow = **29** (not 28); tests = **1278** (not 1294). The Phase-0 guard seeds these exact, AST-measured numbers with an explicitly pinned scope (`git ls-files '*.py'` minus `src/`,`tmp/`; `tests/` **included** in the file-length guard exclusion but **counted** for nothing else).

### 9B. Must-fix before/within implementation
1. **Test coupling is deep → test rewrites ARE in scope (adversarial C1/C2; deletes the §8 "no test rewrites" non-goal).** 12 `from build_team import …`, ~30 `build_team.<symbol>` refs, **14 `monkeypatch.setattr(build_team, …)`**, and `agentteams/man.py:243 from build_team import _build_parser`. A shim re-exporting only `main` would make monkeypatches **silently no-op** (worse than failing). **Resolution:** each Phase-1 move commit also (a) re-exports moved symbols from the `build_team` shim for import-compat, **and** (b) repoints `monkeypatch.setattr` targets + `man.py` to the new module, because patching the shim alias won't affect the real call site. "Suite green after each move" is kept; "no test rewrites" is dropped.
2. **Smoke test cannot be byte-identical (adversarial C3; proven).** Generation embeds non-deterministic fields (`generated_at`, `built_at`, `index_build_id`, `delivered_at`, backup `timestamp_utc`, and dependent `manifest_fingerprint`/`file_hashes`). **Resolution:** the smoke harness is a **committed, lifecycle-tagged script** (also satisfies CH-06/CH-02) that normalizes/excludes those fields before `diff`, or freezes clock+nonce. Byte-identical is asserted only on normalized output.
3. **1000-line ceiling vs `cli/app.py` (adversarial M2 / conflict 2).** `main` is 1094 lines; a `cli/app.py ~1150` would violate the Phase-0 guard on creation. **Resolution:** `main` is split into a **command-dispatch table** (CH-24 "encode conditions in data") *within* Phase 1 so no created module exceeds 1000; the line estimates in §3's table are targets to beat, not allowances. Seed allowlist = `build_team.py`, `analyze.py`, `emit.py` only.
4. **`agentteams/cli/__init__.py` is required (adversarial m1)** or `setuptools find_packages` omits `cli/` from the wheel (dev suite would pass; installed console scripts would break). Add it.
5. **Preserve deferred imports / no new cycles (adversarial m2).** `fleet.py:345` and `man.py:243` use lazy `import build_team` to avoid a cycle; `--fleet` is dispatched *from* `main`. Keep `fleet`/`man` imports deferred inside functions in `cli/app.py`.
6. **Guards are AST-based, not grep (adversarial m3).** Avoids counting `except` in strings/docstrings; ratchet fails only on *increase*.

### 9C. Behavior-change honesty (conflict 1 — the core tension)
The blanket "zero behavioral change" claim is **wrong for Phase 2**. Revised per-phase contract:
- **Phase 1 (decompose) & Phase 3 (registry/dead-code): behavior-preserving** — normalized-output-identical, old suite is a sufficient oracle.
- **Phase 2 (exception sweep) & Phase 4 (type guards): INTENTIONAL behavior change on failure/invalid-input paths.** The old green suite is **necessary but not sufficient**; each swept site requires a **new test asserting the new raise** *before* the change. Load-bearing broad catches that must **NOT** be narrowed (would break optional-dependency/degradation contracts): `_require_jsonschema` `except ImportError` (build_team.py:3419-3428; the documented "merge-complete, re-emit-next-time" path — see MEMORY note on the benign post-merge jsonschema crash), the `importlib.metadata`/`PackageNotFoundError` version block (build_team.py:72-80; source-checkout runs), and `inspect_ai.py:49` YAML-frontmatter degradation. These get a one-line CH-24 "unavoidable external boundary" justification and stay.

### 9D. Security gate is move-only, sweep-EXEMPT (conflict 4)
The gate (`build_team.py:2620-3024`) is **already CH-24-compliant and fail-CLOSED** (narrow `except (OSError, csv.Error, ValueError)` → re-raise as contextual `RuntimeError`; `raise` == *deny*). A mechanical CH-24 sweep could *weaken* it (e.g. converting an atomic "can't read log → deny" into a check-then-act guard = TOCTOU; or breaking single-use replay protection in `_consume_*`). **Resolution:** `cli/security_gate.py` is **Phase-1 move-only and exempt from the Phase-2 sweep**. A new test asserts every deny branch (no-PASS, HALT, unverified-conditional, unsupported-verdict, consumed/replay) still raises.

### 9E. The repo's own mandatory rules the plan owes (conflict 3/3b)
- **CH-25 (Critical-to-this-repo): the AI-authored diff must be screened against the bad-habits catalog** (`agentteams/ai_bad_habits.py`, BH-01..) before mainline integration. Added to §7 as a required gate.
- **CH-21:** Phase-0 new code (guards, later `errors.py`) is validated before use. **CH-06:** smoke/diff work ships as a committed script, not inline >5-line terminal blocks.
- **Self-application circularity:** the load-bearing self-checks are the **mechanical** Phase-0 guards (length/except-ratchet/single-registry), not agent judgment; the diff must still clear `@code-hygiene` + `@security` before merge to main, ideally reviewed by a pass other than the author.

### 9F. Goose reconciliation — adopted strategy (conflict 5; the top tension both audits flag)
Goose commit `ccc904a` edits all three registry dicts + `_build_final_rendered` + adds the `extra_output_files` loop — exactly what Phase 1/3 relocate. "Independent, resolve at merge" understates the cost (structural re-homing, risk of silently dropping Goose's `extra_output_files` wiring). **Adopted (consistent with the user's "refactor off main, Goose on its own branch" choice): Option D — refactor lands on `main` first; then `goose-integration` rebases onto the refactored `main`, where Goose's footprint *shrinks* (centralized registry = 1 edit instead of 3).** Reconciliation checklist for the Goose rebase: (i) re-add `"goose"` to the single `frameworks/registry.py`; (ii) re-apply the `extra_output_files` loop in `cli/render_pipeline.py`; (iii) schema enum + `goose.py` are untouched by the refactor. Goose stays unmerged-to-main until its live `goose run` test passes (honoring "tested locally first"). *(Alternative if preferred: land Goose first — but it lacks a live run; deferred.)*

### 9G. Scope honesty (conflict 6)
Re-labeled: Phase 1 = **full structural decomposition of build_team.py** (high effort, mechanical, zero-behavior) — not "a few offenders." Phase 2 = **behavior-changing remediation of 44 sites, each needing a new test.** The *process* is genuinely phased/bisectable; the *scope* on build_team.py is near-total. Phase 2 will be a **separate PR after Phase 1 merges**, so the safe structural move lands and is reviewed independently of the behavior-changing sweep.

### 9H. Verdict
Plan is **executable after these revisions**, not as originally written. Implementation order: **Phase 0 (additive guards) → Phase 1 (decompose + test repoint, behavior-preserving) → Phase 3 (registry/dead-code) → Phase 2 (exception sweep, separate PR, test-first) → Phase 4 (types)**, each gated on suite-green + the CH-25 screen + `@code-hygiene`/`@security` before any merge to main. `errors.py` moves from Phase 0 to Phase 1 (avoids a dead-code/CH-10 window — nothing would import it in Phase 0).
