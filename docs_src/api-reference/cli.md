# `agentteams.cli` — CLI Package Decomposition

> *Source: `agentteams/cli/`*

**This page documents the module layout, not the command-line surface.** For flags,
option combinations and their interactions, see
[CLI Reference](../cli-reference.md) — restating them here would duplicate rather than
reference (CH-14).

## Why the package exists

`build_team.py` was a single module holding argument parsing, the generate pipeline,
artifact writers and the security gate. CH-07 caps a module at 1000 lines, so it was
carved into this package. `build_team.py` **re-exports** what it moved, so
`build_team.main`, `build_team._write_memory_index` and friends resolve unchanged — the
carve was behaviour-preserving by construction, and the re-exports are what let the
existing test suite pin that.

The `LENGTH_ALLOWLIST` in `tests/test_code_hygiene.py` is now **empty** — every module in
this package is under the 1000-line CH-07 ceiling on its own merits. `app.py` came down from
1174 to 465 when the pipeline moved out, and `generate.py` from 979 to 974 when the
standalone modes moved to `standalone_modes.py`.

Line counts are deliberately not restated per module here: they change on every carve, and a
doc that hard-codes them goes stale the way this paragraph did. Run
`wc -l agentteams/cli/*.py` for current figures.

## Module map

| Module | Owns |
|---|---|
| `app.py` | Entry point. Dispatches on parsed arguments to the right runner; holds no pipeline logic itself. |
| `parser.py` | Argument parser definition — every flag lives here. |
| `parser_validate.py` | Option-combination validation, carved from `parser.py`. Rejects mutually exclusive pairs (e.g. two bridge modes) *before* any work begins. |
| `generate.py` | The generate / update / check pipeline: analyse → render → merge → emit → attest. |
| `render_pipeline.py` | Template rendering and content-merge helpers used by the pipeline. |
| `artifacts.py` | Writers for the generator-owned artifacts: delivery receipt, eval suite, model routing, memory index, code index. Also owns the memory index's source-scope rules. |
| `commands.py` | Sub-command runners for `--convert`, `--interop-*` and `--bridge-*`. |
| `security_gate.py` | The destructive-action gate: requires a recorded PASS decision, or an explicit waiver, before a destructive operation proceeds. |
| `decision_log.py` | Authenticates rows in the security-decisions log (HMAC signing/verification, calls `decision_chain` for the chain check); carved from `security_gate.py` at its own CH-07 ceiling. |
| `action_ids.py` | How a decision row's `action_reviewed` id is matched: `action_matches` (strict, decides which rows can clear an action) and `halt_names_action` (a HALT also blocks every action named as a token run in its id). Integrity-pinned. |
| `decision_chain.py` | The security-decisions log's hash chain in one place: `verify_chain` (including the pre-1.0 legacy prefix), `chain_tip` for every writer, and `describe_log_chain` for `--verify-integrity`. Integrity-pinned. |
| `decision_log_migrate.py` | `--migrate-decision-log`: appends the missing signing columns to a team's decisions-log header (never `prev_digest`), padding short legacy rows with empty cells (signed payloads unchanged), and lists the unsigned authorizing rows that signing revokes. A separate module because `decision_log.py` is integrity-pinned. See [below](#decision-log-migration). |
| `adopt_only.py` | `--adopt-only`: adopts a named subset of orphan agents by rewriting only the orchestrator's adopted routing rows (and, gated, its `agents:` list). `tools_grant` reports each slug's `tools:` line, wider than read-only unless every tool is. |
| `operator_signing.py` | The operator Ed25519 signing path for `--sign-decision` and `--sign-grant` (and the `--issue-grant` spec helpers): key read, payload construction, the pre-sign display, the pre-sign refusals, signing, verify-before-append and the append. Integrity-pinned (`integrity.ENFORCEMENT_MODULES`); the runners in `commands.py` / `grant_commands.py` are one-line delegators. See [below](#the-pinned-operator-signing-path). |
| `schema_cache.py` | Shared JSON-Schema validation plus a content-hash cache, so re-validating unchanged bytes is free. |
| `goose_switch.py` | Glue for `--goose-source` / `--goose-model` / `--goose-show`. |
| `backup_switch.py` | Glue for `--stale-check` / `--stale-remediate` / `--prune-backups` / `--backup-mirror`. |
| `fleet_switch.py` | Glue for `--fleet` / `--fleet-frameworks` / `--fleet-report`. |
| `package_switch.py` | Glue + dispatch for `--package-team` / `--package-source-framework`. |
| `sync_switch.py` | Glue + dispatch for `--sync-init` / `--sync` / `--sync-since` / `--pin`. |
| `recipe_check.py` | Standalone structural validator for Goose recipe YAML. |
| `standalone_modes.py` | The "do one thing and exit" modes carved out of `generate.py`: restore-backup, scan-security, check-budget, template pinning, and the retrieval utilities. They were never part of the generate pipeline. |
| `output_target.py` | Resolves and validates the output directory, including the foreign-output refusal behind `--allow-foreign-output`. |
| `post_emit_checks.py` | Checks that run after the write phase and cannot change its outcome. |
| `code_index_artifacts.py` | Writers for the code & API index cache (`references/code-index/`, gitignored). |
| `json_mode.py` | `--json` output shaping. |
| `exit_codes.py` | The named exit-code constants, so a status is set in one place and read everywhere. |

## Two behaviours worth knowing when reading this package

**The memory index's scope lives in `artifacts.py`, not `memory_index.py`.**
`_memory_index_sources` decides *what* to index and `_memory_index_root` decides what
relative paths are relative *to*; `memory_index.py` only builds an index over whatever
it is handed. `_SCRATCH_DIR_NAMES` / `_is_durable_source` exclude backup and cache
directories from every recursive scan — without that filter the index reached 2120
documents, 1488 of them backup snapshots, in a 51 MB committed artifact.

**Reporting never changes an outcome.** `generate.py` calls
[`update_report.report_run`](update-report.md) after the write phase and only when
`--dry-run` was not passed. A failure to write the report must not turn a successful
update into a failed one.

## The pinned operator signing path

`--sign-decision` and `--sign-grant` are the only code paths that read the operator's Ed25519
private key (the file named by `AGENTTEAMS_DECISION_ED25519_KEYFILE`). Everything between that
env read and the appended row lives in `operator_signing.py`, which is listed in
[`integrity.ENFORCEMENT_MODULES`](integrity.md), so an in-place edit to it shows up in
`references/enforcement-integrity.json`, `--verify-integrity` and red-team phase 6. The
`commands.py` and `grant_commands.py` runners only choose the spec path and the output/team dir.

**What the pin does not cover.** It closes in-place edits and unnoticed drift. It does not defend
against unpinned code in the same process: the dispatch chain (`app.py`, `parser.py`,
`agentteams/__init__.py`, `.pth` files) can patch `Path.read_text` or this module's primitives,
or call `signed_ledger` directly. Since #9 the display is a gate: a y/N confirm on a terminal, or
an exact `--confirm-review-sha256` match without one, before the key is read. Sign from a trusted
install (`SECURITY.md`, "Signing from a trusted install"), and run `--verify-integrity` first.

**The pre-sign self-check** (`presign_integrity_check`, run before the key is read). Both minters
verify the signing closure (`SIGNING_CLOSURE`: this module, `signed_ledger.py`, `decision_log.py`,
`grants.py`, `effect_classifier.py`, `governance_targets.py`, `management_directives.py`,
`atomicio.py`, `signer_location.py`, `_sandbox_emit.py`, `integrity.py`) against the integrity
manifest. They **refuse** on drift in that closure, on a missing manifest the repository should
carry, or on an unreadable manifest, and the key is never read. They **warn** (and still sign) on
drift in other pinned modules, and on a closure that differs from git `HEAD` or from the nearest
local tag. A local tag is **unverified**: anyone who can write the repository can create one, so
an agent that can commit and tag silences both comparisons. Git runs with repo config neutralised
and never through a content filter (`ls-tree` object ids against Python-computed blob ids, and
`cat-file blob`, because `git diff`/`show` would run a planted `.gitattributes` clean filter as the
operator). Since #10 a package, prefix or interpreter inside the current directory, a git work
tree or a sandbox `allowWrite` root is **refused** (`signer_location.py`) unless
`--allow-checkout-signing`. There is no environment override. The check lives in the module it
protects, so deleting it is itself recorded by the manifest: a speed bump with a trail, not a
boundary. An agent that edits the closure AND regenerates the manifest passes it, which is what
the `HEAD`/tag warnings are for.

### `sign_decision(output_dir, spec_path, *, confirm_sha256=None, allow_checkout=False)`

> *Source: `agentteams/cli/operator_signing.py`*

Mints and appends one Ed25519-signed constraint-relaxing decision row under the team dir
`output_dir`. Returns 0, or 1 on any error (fail-closed). The steps run in this order (#9/#10):

1. team-dir refusal;
2. spec load and shape check, then the display-spoofing refusal; the chain link (`prev_digest`) and, when the
   log has the column, `text_digest` (binding `conditions` and `evidence`) are computed, never taken from the spec;
3. eligibility and effect classification, then the PR-E grant-purpose refusal;
4. preflight: env, integrity, install location, key-file existence. With `allow_checkout`, an overridden
   location refusal is recorded in the signed `conditions_verified`;
5. the display, with the review digest;
6. the confirm gate: a y/N prompt on a terminal, or an exact `confirm_sha256` match;
7. the key read, then the signature over the same in-memory row;
8. verify-before-append, then the append.

### `sign_grant(spec_path, resolve_dirs, *, confirm_sha256=None, allow_checkout=False)`

> *Source: `agentteams/cli/operator_signing.py`*

Mints and appends one Ed25519-signed capability grant. Returns 0, or 1 on any error. `resolve_dirs` is a
zero-argument callable returning `(ledger_root, team_dir)`; it runs unpinned adapter code, so it is always
called before the key read. The error precedence is (amended by #9):

1. spec;
2. `key_id`;
3. display-spoofing refusal;
4. preflight;
5. directories and `max_uses`;
6. an install-location re-check against the holder project;
7. the display (every field, grant id and timestamp minted once, review digest);
8. confirm;
9. the key read, then the signature.

### `sign_decision_team_refusal(output_dir)`

> *Source: `agentteams/cli/operator_signing.py`*

F-2: the refusal sentence when `output_dir` holds neither `references/agent-privilege.json` nor
`references/build-log.json`, else `None`.

### `load_grant_spec(path, flag)` / `grant_spec_kwargs(spec)` / `report_issued(record, ledger_root, team_dir)`

> *Source: `agentteams/cli/operator_signing.py`*

The grant spec helpers shared with the HMAC `--issue-grant` runner: read and shape-check a spec
against `GRANT_SPEC_REQUIRED`, map it to minter keyword arguments (fresh `grant_id` and
timestamp), and print the post-append summary.

## Decision-log migration

> *Source: `agentteams/cli/decision_log_migrate.py`*

`decision_log.append_signed_decision_row` refuses a log whose header lacks a signing column, so a team
generated before signing could not record a signed decision. This module upgrades the header and nothing
else. It imports `decision_log` and never changes it (that module is pinned). Flag semantics:
[CLI Reference](../cli-reference.md#--migrate-decision-log).

### `missing_signing_columns(columns) -> list[str]`

The `SIGNED_DECISION_COLUMNS` entries a header lacks, in that order, never `prev_digest`
(`EXCLUDED_COLUMNS`): chaining is opt-in on that column, and the legacy rows were never chained.

### `plan_migration(log_path) -> MigrationPlan`

Works out the migration and writes nothing. The new bytes are the old header line, the appended columns, the
old line ending, and every data line with its bytes kept as a prefix and empty cells appended to rows narrower
than the new header (`plan.padded_lines`). They are re-read as the gate reads them, must still classify
(`_security_decision_schema_kind`) with every added cell empty, and must leave every row's signed payload
unchanged. A header-current log with short rows yields a pad-only plan. `plan.revoked` lists the live unsigned
authorizing rows (unconsumed `PASS` / `CONDITIONAL PASS`, and every `HALT-RETRACTED`) by first line and action,
when the migration adds `signature`. Raises `DecisionLogMigrationError` for a missing or symlinked log, a
malformed or unknown header, malformed data rows, a row with a value past the old header (no laundering), or a
row too short to be a legacy row (never padded).

### `apply_migration(plan, team_dir, *, framework="") -> Path`

Backs the log up (`backup.backup_output_dir`, reason `migrate-decision-log`), refuses if the log changed since
it was planned, then replaces it atomically with the file's own mode. Returns the backup directory.

### `run_migrate_decision_log(args) -> int`

The CLI runner (re-exported by `commands.py` as `_run_migrate_decision_log`, dispatched by `app.py` after
`--self` resolves the team dir). `resolve_team_dir(args)` resolves the agents dir as the generate pipeline does.
Returns 0 for a migration, a no-op or a dry run, and 1 for a refusal or a real run without `--yes`.

## Decision text binding

> *Source: `agentteams/cli/decision_text.py`*

Binds a signed decision row's free-text `conditions` and `evidence` into its signature. `text_digest` is the last
entry of `decision_log._OPTIONAL_SIGNED_AXES`, so it is signed and chained only when non-empty and every older
signature still verifies. The module is integrity-pinned and in the signing closure.

### `text_digest_for(row) -> str`

`sha256:` plus the hex digest of the UTF-8 of `conditions.strip() + "\x1f" + evidence.strip()`. A legacy row
has no `evidence`; it reads as empty. `--sign-decision` uses it to fill the column; verification uses it to check.

### `assert_text_bound(row, *, action)`

Raises `RuntimeError` ("failed signature verification") when the row's non-empty `text_digest` no longer matches
its text. `decision_log` calls it right after a row's HMAC or Ed25519 signature verifies, so an edited row stops
clearing. A row without `text_digest` passes. `text_is_bound(row)` and `text_digest_mismatch(row)` are the
underlying predicates.

### `describe_text_binding(team_dir) -> list[str]`

The `--verify-integrity` report lines: signed rows that bind their text, those that don't, a missing column, and
any mismatched row. Empty when the log is absent, unreadable or has no signed row. Advisory only.

## Related pages

- [CLI Reference](../cli-reference.md) — flags and option semantics
- [`update_report`](update-report.md) — the `update.report.md` record
- [`memory_index`](memory-index.md) — index construction and path storage
- [`output_plan`](output-plan.md) — which files a manifest produces
