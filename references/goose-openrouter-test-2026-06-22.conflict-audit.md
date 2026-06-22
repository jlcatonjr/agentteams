# Conflict Audit — Goose ⇄ OpenRouter Preflight Test Plan (2026-06-22)

Scope: `references/plans/goose-openrouter-test-2026-06-22.plan.md` (proposes
`scripts/goose-openrouter-preflight.py`, `tests/test_goose_openrouter_preflight.py`, and
edits to `docs_src/goose-system-prep.md`), against `CLAUDE.md`, `filing-conventions.md`,
`STABILITY.md`, `SECURITY.md`, `bridge-refresh-safety.md`, and the test/CI guards.

## Consistency Checks

1. Filing conventions — plan + audit record placement.
- Status: PASS.
- Evidence: Plan at `references/plans/<slug>-2026-06-22.plan.md` matches the retained-local home and `<slug>-YYYY-MM-DD.plan.md` naming (`filing-conventions.md:17,25`). The root-stray guard checks only the top-level glob `REPO_ROOT.glob("*.md")` (`tests/test_root_doc_hygiene.py:57`) and does not recurse into `references/`, so neither the plan nor an audit record under `references/` trips it (currently 1 passed). No script/doc is placed where a guard forbids.

2. SECURITY.md + key-by-reference design.
- Status: PASS.
- Evidence: The plan resolves `OPENROUTER_API_KEY` "the same way `goose-backend.sh` does (env-file by reference)… never reads secrets into files," live probe opt-in `--live` only. Matches the doc rule "never paste an API key into a recipe, `config.yaml`, or any committed file" (`goose-system-prep.md:11-13`) and `goose-backend.sh:13`. SECURITY.md forbids inputs that "exfiltrate environment variables" (`SECURITY.md:40-41`); a key passed into a subprocess env for one run does not violate this. **Carry to implementation:** the `--json` report and `--fix` backup MUST NOT serialize the key; report presence only (`key=set/unset`).

3. Network egress in the offline (CI) suite.
- Status: PASS.
- Evidence: No socket blocker (no `tests/conftest.py`; `pyproject.toml` pytest config has only `testpaths`), and CI runs `pytest tests/` unguarded (`.github/workflows/ci.yml`). Egress avoidance is per-test discipline, as existing network-named tests do it (`tests/test_framework_research.py:3` "all network calls are stubbed"). The plan conforms: R2 exercises pure logic against a fixture catalog; fetch + live probe stay out of the offline suite; preflight excluded from CI as a gate. Implementation must ensure the test imports the script via `importlib` without any module-level network call (I/O at edges).

4. Script/doc conventions — test, docstring/exit-codes, mkdocs, api-parity.
- Status: PASS.
- Evidence: New-script-needs-a-test precedent honored (R2 mirrors `test_verify_env.py:16-21`); docstring with Usage+Exit codes mirrors `verify-env.py:9-20`. `check_api_doc_parity.py` scans only `docs_src/api-reference/` vs `agentteams/` modules (`:48-53`) — never `scripts/` — so no STALE_PAGE/COVERAGE_GAP. mkdocs is **not** `--strict` and the four `docs_src/goose-*.md` are already unregistered in nav; docs.yml runs only on push to main. R4 edits an already-unregistered page → no new mkdocs obligation. (Nav registration of the goose docs is a pre-existing gap, out of scope.)

5. STABILITY.md — new surface vs breaking change.
- Status: PASS.
- Evidence: `scripts/*.py` is not SemVer-covered surface (the contract is the `agentteams` console entry point + documented `agentteams.1` flags, `STABILITY.md:13-15`; repo-internal layout explicitly uncovered, `:79-81`). A standalone local diagnostic with its own flags is additive/new-surface, no contract touched.

6. Consistency with `goose-system-prep.md`.
- Status: PASS with note.
- Evidence: The doc is internally consistent and already on the correct hyphen form — §2b checks `qwen/qwen3.6-35b-a3b` (`:51`), §3 default `_GOOSE_OR_MODEL_DEFAULT="qwen/qwen3.6-35b-a3b"` (`:67`), matching live `goose-backend.sh:33`. The colon form appears only in the §2a **Ollama** block (`:42`), where `model:tag` is correct. The real defect is the user's `~/.config/goose/config.yaml:2` (colon), exactly as F1 localizes — not the doc. **Note:** R4 must keep the Ollama `:tag` example intact while adding OpenRouter-hyphen guidance, so the two providers' syntaxes are not conflated.

7. `--fix` editing a file outside the repo.
- Status: PASS.
- Evidence: No CLAUDE.md/safety doc forbids a user-opted, backed-up edit to the user's own config; the governing philosophy is `bridge-refresh-safety.md` (non-destructive default, opt-in + recoverable for overwrites). `--fix` is off by default, backs up first, rewrites only the `GOOSE_MODEL` line, applies only when the hyphen slug is catalog-confirmed — consistent with that posture.

## Conflict Verdict

PASS with conditions. No contradiction with any authority file or prior decision; each
condition is an existing guard the implementation must clear:

1. **CH-07/CH-24 (hard CI guards over `scripts/`):** keep the script < 1000 lines and add **no** broad/bare `except` — exit-2 path catches specific exceptions only (`OSError`, `urllib.error.URLError`, `json.JSONDecodeError`, `subprocess.*`, `ValueError`); no `pass`-only handler bodies (`tests/test_code_hygiene.py`, baselines 11 / 29).
2. **Secret never serialized (SECURITY.md + design):** resolved key absent from `--json`, `--fix` backup, and logs — presence only.
3. **CI stays offline:** test imports via `importlib` and exercises only fixture-catalog pure logic; `fetch_catalog`/`live_probe` not invoked under `pytest tests/`.
4. **R4 wording:** preserve the Ollama `:tag` example (`goose-system-prep.md:42`) while adding OpenRouter-hyphen guidance.

---

*Conditions resolved in the revised plan (R1 code-hygiene + secret-hygiene, R2 offline, R4 wording; §5 trace).*
