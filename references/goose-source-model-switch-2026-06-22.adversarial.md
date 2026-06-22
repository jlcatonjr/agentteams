# Adversarial Audit — Goose Source/Model Switch (2026-06-22)

Scope: `references/plans/goose-source-model-switch-2026-06-22.plan.md` (D1–D6).

## Presupposition Review

1. Presupposition (D1): parsing `goose info` for the `Config yaml:` line is a stable, authoritative protocol, with an XDG/platform fallback that matches goose.
- Verdict: Accepted with mitigation.
- Basis: `goose info` (1.37.0) is real, read-only, exits 0 even with `XDG_CONFIG_HOME=/tmp/fake-xdg` and missing files, and emits `Config yaml: /…/config.yaml` — but with heavy fixed-column trailing whitespace and, when absent, a trailing status token (`… missing (can create)`). A greedy `(.+)$` captures the pad and the token (verified); `\S+` breaks on a path with spaces (Windows `%APPDATA%\Block\goose\config`). The XDG fallback is correct — goose honors `XDG_CONFIG_HOME` (verified live). Mitigation: case-insensitive label, capture-then-strip the pad and ` ... missing (can create)` suffix, do not split on internal whitespace.

2. Presupposition (D3): a top-level line-rewrite/insert preserves the nested `extensions:` block and comments.
- Verdict: Accepted with mitigation.
- Basis: The proven `apply_fix` (`scripts/goose-openrouter-preflight.py:275`) uses a column-0 `^(GOOSE_MODEL:\s*).*$` anchor — verified it does NOT match an indented `    GOOSE_MODEL:` under `extensions:` and rewrites a quoted value cleanly. Corruption is real only with a sloppy `^\s*GOOSE_MODEL:` anchor (which DID match the nested line in testing). Mitigation: inherit the exact column-0 anchor; round-trip test asserting `extensions:` is byte-preserved.

3. Presupposition (D2): `qwen/qwen3.6-35b-a3b` is a valid tool-capable default; the ollama default is sensible; two sources + override file suffice.
- Verdict: Accepted.
- Basis: Live catalog: `qwen/qwen3.6-35b-a3b` exists+tools; colon form absent. `qwen/qwen3-30b-a3b` valid+tools. `ollama list` shows `qwen3.6:35b-a3b` present locally, matching config.yaml and goose-backend.sh. The override file reads only model/key-env names (no secrets) — minimal surface.

4. Presupposition (D4): the CLI semantics — `--goose-model X` alone, provider-set-without-key as a warning — leave goose coherent and functional.
- Verdict: Rejected.
- Basis: Central flaw, twofold. **(a) Architectural inversion.** `docs_src/goose-system-prep.md:29-32` and `goose-backend.sh` establish that goose reads provider/model from the **environment first, then config.yaml**, and that switching is done via *env overrides*, with config.yaml as a baseline. D3/D4 mutate config.yaml — the opposite. A shell with `goose-backend openrouter` active then running `agentteams --goose-source openrouter` mutates config.yaml but the env override **masks** it (env wins) — the switch "succeeds" yet is invisible; and overwriting the baseline breaks a later `goose-backend local`. **(b) Model-only incoherence.** `--goose-model qwen/qwen3.6-35b-a3b` with provider unchanged at ollama writes an OpenRouter slash slug goose hands to ollama (which has `qwen3.6:35b-a3b`, colon) → silent breakage at next run; goose validates nothing at write time. "Provider set without key → warning" yields a config `goose info --check` would fail while the switch reports success.

5. Presupposition (D6): carving `_validate_option_combinations` + `_BRIDGE_USAGE_HINT` and re-importing is behavior-preserving and sufficient; parser.py drops under CH-07.
- Verdict: Accepted with mitigation.
- Basis: The function references no module-level state beyond `_BRIDGE_USAGE_HINT` (scanned 707-999). parser.py is exactly 1000 by the CH-07 metric (`count("\n")+1`). Importers: `build_team.py:124-128` (re-exports), `app.py:30`, `tests/test_goose_convert_interop.py:116` — all resolve from `agentteams.cli.parser`, preserved by re-import. Two corrections: (i) `man.py:256` imports only `_build_parser` from `build_team`; the man-page change is driven by *adding the flags*, not the carve. (ii) Keep the goose-arg *definitions* out of parser.py (in `cli/goose_switch.py`) so the freed headroom is not re-consumed.

## Risk Notes

- **Wrong moment to edit the right file (HIGH):** `goose info` locates the file correctly, but env-first precedence means a config edit can be silently overridden in any `goose-backend.sh` shell. Detect an active `GOOSE_PROVIDER`/`GOOSE_MODEL` env and warn; reconcile with the baseline doctrine in docs.
- **Model/provider mismatch (HIGH):** model-only and source-only edits cross provider namespaces (ollama `name:tag` vs OpenRouter `vendor/slug`). Reject/warn on `/`-under-ollama and `:`-variant-under-openrouter, reusing `offline_syntax_suspect` (`goose-openrouter-preflight.py:186`).
- **Regex fragility (MEDIUM):** pin the `goose info` parse (strip pad + status token) and the column-0 mutation anchor.
- **Action isolation (MEDIUM):** enforce mutual-exclusion of the goose flags with generate/bridge/convert in the validator (the new block lands in `parser_validate.py`, shipping with the carve).
- Low risk: the OpenRouter + ollama defaults are currently valid; the carve has no hidden state; baseline tests + man diff green.

## Adversarial Verdict

FAIL.

Do not implement D3/D4 as written: switching by *mutating config.yaml* inverts the user's documented, security-motivated design (env-first; config.yaml as baseline; switch via env overrides). The result can "succeed" while masked by an env override, destroys the baseline, and (model-only) writes provider-incompatible slugs goose fails on at run time (verified).

Revise before re-audit: (1) reconcile with env-first precedence — keep the config edit but detect+warn on an active env override and document the baseline repurposing (or emit env-export instructions); (2) make model selection provider-aware; (3) pin D1's parse and D3's column-0 anchor. The D6 carve (Accepted w/ mitigation) and D2 registry (Accepted) may proceed; correct the "man.py re-export" wording and keep goose-arg defs in `cli/goose_switch.py`.

---

*Resolution: a CLI subprocess cannot mutate the parent shell's env, so config-editing is the only persistent-default mechanism; revised plan keeps it and adds the three guards (env-override-masking warning, provider-aware model validation, missing-key warning) + the parse/anchor fixes + doc reconciliation. See plan §5 + D1/D3/D4/D7.*
