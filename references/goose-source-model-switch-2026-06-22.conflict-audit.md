# Conflict Audit — Goose Source/Model Switch CLI Feature (2026-06-22)

Scope: new `--goose-source/--goose-model/--goose-show/--goose-config` flags;
`agentteams/cli/parser_validate.py`, `agentteams/goose_config.py`,
`agentteams/cli/goose_switch.py`; the `_validate_option_combinations` carve.

## Consistency Checks

1. STABILITY.md — new flags additive; man-page regen mandatory + CI-enforced.
- Status: PASS.
- Evidence: `STABILITY.md:101-105` classifies "new flags" as minor (non-breaking); `:12-14` makes documented `agentteams.1`/`--help` flags the contract. No existing flag removed/renamed. `man.py:68-73` walks `parser._actions`, so new flags appear automatically; `ci.yml:41-45` regenerates and `diff`s `agentteams.1` — committed man page is clean today, so a missed regen is a guaranteed CI failure (R6 covers it). Goose-adjacent CLI is additionally beta (`STABILITY.md:29-36`).

2. CH-07 — carve drops parser.py below 1000; new modules under; re-export keeps imports green.
- Status: PASS.
- Evidence: parser.py `count("\n")+1 == 1000` (matches `test_code_hygiene.py:37,107`), zero headroom. The block to extract (`_BRIDGE_USAGE_HINT` + `_validate_option_combinations`, `parser.py:699-999`) is 301 lines; removing it → ~699. The guard scans tracked + untracked-non-ignored `*.py` (`:66-70`), so new files are checked immediately. Symbols must stay importable from `agentteams.cli.parser` (sites: `app.py:30`, `tests/test_goose_convert_interop.py:116`, `build_team.py:124-128`). Api-doc-parity: `cli/` is `_EXEMPT_MODULES` (`check_api_doc_parity.py:60`) so `parser_validate.py` needs no page; `goose_config.py` (top-level) registers a COVERAGE_GAP — advisory only (`test_api_doc_parity.py` asserts only `stale_pages==[]`; `ci.yml` never runs the script; `--strict` unused; 9 such gaps already exist).

3. CH-24 — no new broad/bare except; narrow catches for subprocess + I/O.
- Status: PASS with note.
- Evidence: `BROAD_EXCEPT_BASELINE=11`, `SWALLOW_BASELINE=29`, AST-measured, ratchet-down only (`test_code_hygiene.py:49-54,133-150`). The precedent `goose-openrouter-preflight.py` already models the required narrow set: subprocess → `FileNotFoundError`, `subprocess.TimeoutExpired` (`:260-262`); config I/O → `FileNotFoundError`, `(OSError, UnicodeDecodeError)` (`:205-241`); user JSON → `(json.JSONDecodeError, ValueError)` (`:218`). Binding: the verbatim carve must add no broad handler; the `goose info` fallback must **report** the resolution method, not `pass`-swallow (would hit the 29 ratchet).

4. SECURITY.md — config.yaml edit (provider/model only) compatible with secrets-never-in-files + backup posture.
- Status: PASS.
- Evidence: Plan sets only `GOOSE_PROVIDER`/`GOOSE_MODEL`, reasons about `key_env` *names* to emit a warning, delegates key custody to goose (`SECURITY.md:41`). Timestamped backup before write mirrors the `--migrate`/snapshot pattern (`SECURITY.md:44-49`). Binding: warning prints the env-var name never a value; backup precedes the `re.subn` (no partial-write window).

5. Filing conventions — plan/audit placement match; one new user-doc needs nav.
- Status: PASS with note.
- Evidence: Plan at `references/plans/<slug>-2026-06-22.plan.md` (`filing-conventions.md:17,25`); audit records at `references/` root (`:18`); `test_root_doc_hygiene.py:27-44` allowlists only root `*.md` — neither tripped. Note: R7 edits `docs_src/goose-system-prep.md`, which is **not** in `mkdocs.yml` today; `filing-conventions.md:19` says add docs to nav. No CI gate, but R7 should wire the nav entry.

6. Test/CI — man tests green; man diff must regen in same change; carved validator's tests stay green.
- Status: PASS.
- Evidence: `tests/test_man.py:15-28` builds its own minimal parsers — new flags can't break it. The man diff (`ci.yml:44-45`) forces `agentteams.1` regen in the same commit. The carve must keep green: `tests/test_goose_convert_interop.py:116-118`, `tests/test_build_team_option_matrix.py` (via `build_team._build_parser`), `tests/test_backup_retention.py` (CP-1 mutual-exclusion at `parser.py:718-733`). Ran the at-risk set (`test_code_hygiene test_man test_root_doc_hygiene test_api_doc_parity test_goose_convert_interop test_build_team_option_matrix test_backup_retention`) — **160 passed**.

7. CLI consistency — goose flags follow the action-flag convention.
- Status: PASS.
- Evidence: `--recipe-check` short-circuits in `app.py:291-301` before `run_generate`; validators live in `_validate_option_combinations` (e.g. `:842-843`); actions return `int` (`:301`). The plan mirrors all three (`run_goose_switch(args)->int`, dispatch in `app.py`, exclusion in the validator, `add_goose_arguments(parser)` helper). The man page is the canonical flag enumeration (auto-regenerated); no README flag-list guard.

## Conflict Verdict

PASS with conditions. No contradiction with STABILITY.md, SECURITY.md, filing conventions, or the CH-07/CH-24/man/root-doc/api-parity guards; 160 at-risk tests green; carve math checks out (1000 → ~699). Binding conditions (all named in the plan):

1. Regenerate `agentteams.1` in the same commit (R6) — else `ci.yml:44-45` fails.
2. Carve verbatim + re-export from `agentteams.cli.parser`; re-run the full incompatibility set (R1/R5).
3. No broad/bare `except`, no swallow-only handler in the carve / `goose_config.py` / `goose_switch.py` / the `goose info` subprocess — narrow set only; fallback reports the resolution method.
4. Never write/log/serialize a provider key — provider/model only; env-var names in warnings; timestamped backup before the `re.subn`.
5. Wire `docs_src/goose-system-prep.md` into `mkdocs.yml` nav (R7).

Advisory (non-blocking): `goose_config.py` registers as a COVERAGE_GAP (consistent with 9 existing); a `docs_src/api-reference/goose-config.md` would close it but is not required.

---

*Conditions folded into the revised plan (R1, R6, R7, D3, code-hygiene step; §5 trace).*
