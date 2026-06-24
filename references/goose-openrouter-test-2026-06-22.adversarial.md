# Adversarial Audit — Goose ⇄ OpenRouter Preflight Test Plan (2026-06-22)

Scope: `references/plans/goose-openrouter-test-2026-06-22.plan.md` (proposes
`scripts/goose-openrouter-preflight.py`, `tests/test_goose_openrouter_preflight.py`,
doc/config edits).

## Presupposition Review

1. Presupposition (F1): the early-stop is caused by the invalid colon slug `qwen/qwen3.6:35b-a3b`; the hyphen form is the valid fix.
- Verdict: Accepted.
- Basis: Live catalog (`/api/v1/models`, 340 models) confirms `qwen/qwen3.6:35b-a3b` and base `qwen/qwen3.6` are both absent, while `qwen/qwen3.6-35b-a3b` is present with `tools=True`. A live run with a real key and the colon slug returns `Bad request (400): qwen/qwen3.6:35b-a3b is not a valid model ID` and stops immediately; the hyphen slug drives the shell tool and prints the sentinel. Root-cause claim sound (`config.yaml:2`).

2. Presupposition (F2): only the non-`goose-or` path (plain `goose run`, incl. the VS Code task) hits the bug.
- Verdict: Accepted.
- Basis: `goose-backend.sh` sets `_GOOSE_OPENROUTER_MODEL_DEFAULT="qwen/qwen3.6-35b-a3b"` and exports `GOOSE_MODEL` only in the goose child (`goose-or`) or interactive shell. `.vscode/tasks.json:286` runs `goose run --recipe …` with no prefix and no `--model`, so it falls back to `config.yaml`'s colon slug. Mechanism confirmed.

3. Presupposition (F3/R1): a preflight validating the effective model against the catalog is the right diagnostic; exit codes can mirror `verify-env.py` (0/1/2).
- Verdict: Accepted with mitigation.
- Basis: The offline half is correct and well-precedented (`importlib` import, pure-core/IO-edge split, `--quiet`/`--json`). **But `goose run` exits 0 even on hard error** — the invalid colon slug (400), a bad-key 401, and a clean success all return exit 0. The plan's "FAIL on immediate error → exit 1" cannot read goose's exit code; the live probe must parse stdout/stderr for an error sentinel. The offline catalog path keeps clean exit semantics; the live path must not rely on exit codes.

4. Presupposition (R1 live-probe): `goose run --no-session --quiet --max-turns N -t "<probe>"`, PASS iff a sentinel appears, reliably distinguishes healthy from early-stop.
- Verdict: Rejected (as sole PASS criterion).
- Basis: Three empirically-demonstrated false verdicts: (a) **false FAIL from `--max-turns` too small** — with the valid model and `--max-turns 1`, goose ran the tool, printed the sentinel, then emitted "reached the maximum number of actions"; (b) **false FAIL from reasoning models** — `qwen/qwen3.6-35b-a3b` is reasoning-capable (170/257 tool-capable catalog models are), and hidden reasoning can miss the literal sentinel within `N`; (c) **`GOOSE_MODE` interaction** — config sets `GOOSE_MODE: auto`; the doc's own probe forced `GOOSE_MODE=chat`. `-t`+`--no-session` is the right non-interactive form for goose 1.37, but the surrounding contract is underspecified.

5. Presupposition (R1 precedence): "effective model = env else config.yaml" is complete, and the preflight reads the same model the failing invocation used.
- Verdict: Accepted with mitigation.
- Basis: env→config precedence matches goose. **But the preflight inherits its parent shell:** if run from a shell that already did `goose-backend openrouter` (hyphen exported), it sees the valid model and PASSes while the `.vscode` task (separate process, no override) uses the colon slug — a false PASS. The preflight must read `config.yaml` directly as authoritative for plain `goose run`, report both env- and config-effective models, warn on divergence, and ideally pin the probe with explicit `--model/--provider`. Per-recipe `model:` is correctly out of scope (recipes forbid a `model:` key).

6. Presupposition (R3): the colon→hyphen fix heuristic is safe.
- Verdict: Accepted with mitigation.
- Basis: Zero catalog slugs have a real `:free`/`:thinking` variant that ALSO has an existing hyphen-joined twin, so the heuristic won't mis-rewrite a legitimate `…instruct:free`. Mitigations: suggest only when the pre-colon base is itself absent; never auto-suggest for a known-variant suffix (`free`/`nitro`/`thinking`/…) unless catalog-confirmed; split on the first colon only; keep `--fix`'s catalog-confirm + timestamped backup.

## Risk Notes

- **goose validates the API key before the model** (bogus model + bad key surfaced a 401, not a model error). So a missing `OPENROUTER_API_KEY` makes the live probe early-stop with 401, which a naive "FAIL on immediate error" would wrongly blame on the slug. The probe must classify error text (`401` / `not a valid model ID` / `maximum … actions` / no-sentinel) into distinct causes.
- **`supported_parameters` containing `tools` is necessary-not-sufficient** — a gate, not a PASS. Reasoning models can list `tools` yet miss the sentinel within `N`.
- **`GOOSE_MODE: auto` + recipe `prompt:` one-shot is an independent early-stop vector** (`bridge-orchestrator.yaml:4`). A correct one-shot reply could be misread as "broken." The probe text must require a tool turn; the doc row should distinguish "short by design" from "model-not-found."
- **Low `N` is a self-inflicted early-stop** — pin `N≥3` for a one-tool-call probe.
- The offline unit half (R2) is the safe, valuable core and should ship regardless of the live-probe outcome.

## Adversarial Verdict

PASS with conditions.

Root cause (F1) and path analysis (F2) are empirically correct, and the offline
catalog-validation + colon→hyphen-suggestion core is sound and worth building. The
live-probe contract as drafted is unsound (false PASS/FAIL). Blocking conditions:

1. Drop `goose run` exit code as a signal; classify stdout/stderr: `not a valid model ID` → invalid-slug FAIL(1); `401`/auth → setup error(2); `maximum number of actions` → raise N / inconclusive(2); sentinel → PASS(0); else early-stop FAIL(1).
2. Resolve and report BOTH effective models (env + `config.yaml`); warn on divergence; pin the probe with explicit `--provider/--model`.
3. Pin the probe: `--max-turns ≥3`, force `GOOSE_MODE=chat`, reasoning sentinel-miss = inconclusive.
4. Tighten the fix heuristic (base absent + not known-variant unless catalog-confirmed; first colon only; `--fix` catalog-confirm + backup).
5. Treat catalog `tools=True` as a gate, not a PASS.

Ship R2 + the offline validation/suggestion path as-is; gate `--live` behind conditions 1–3.

---

*Conditions resolved in the revised plan (R1a/R1b/R2/R3, §5 trace).*
