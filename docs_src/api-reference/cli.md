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
| `action_ids.py` | How a decision row's `action_reviewed` id is matched: `action_matches` (strict, decides which rows can clear an action) and `halt_names_action` (a HALT also blocks every action named as a token run in its id). A HALT whose id has no letter or digit blocks every action (`halt_blocks`) and is retracted by its row handle (`halt_row_handle`). Integrity-pinned. |
| `decision_chain.py` | The security-decisions log's hash chain in one place: `verify_chain` (including the pre-1.0 legacy prefix), `chain_tip` for every writer, and `describe_log_chain` for `--verify-integrity`. Integrity-pinned. |
| `decision_uses.py` | A decision row may appear only once (`assert_no_duplicate_clearing_rows`, run with the chain check), and each use of a clearance is recorded as its own `USED` row (`append_use_row`); `is_spent` reads either record. Integrity-pinned. |
| `open_halts.py` | **Beta.** `open_halts(team_dir, action=None)`: a read-only listing of the decisions log's HALT rows, judged by the gate's own HALT scan. Integrity-pinned. See [below](#beta-api-open_haltsopen_halts). |
| `decision_log_migrate.py` | `--migrate-decision-log`: appends the missing signing columns to a team's decisions-log header (never `prev_digest`), padding short legacy rows with empty cells (signed payloads unchanged), and lists the unsigned authorizing rows that signing revokes. A separate module because `decision_log.py` is integrity-pinned. See [below](#decision-log-migration). |
| `adopt_only.py` | `--adopt-only`: adopts a named subset of orphan agents by rewriting only the orchestrator's adopted routing rows (and, gated, its `agents:` list). `tools_grant` reports each slug's `tools:` line, wider than read-only unless every tool is. |
| `reconcile_apply_gate.py` | What clears a capability widening by `--reconcile-apply` (action `reconcile-apply-widen`): in a governed workspace a signed, non-waiver decision with `scope` exactly the action, `effect_grants` exactly this run's `frontmatter:<file>#<key>@sha256:<hex>` tokens (a digest of the value written) and room under the aggregate cap; in an ungoverned one an ordinary PASS row from an approved author. Both end in the destructive-action gate. Integrity-pinned. |
| `operator_signing.py` | The operator Ed25519 signing path for `--sign-decision` and `--sign-grant` (and the `--issue-grant` spec helpers): key read, payload construction, the pre-sign display, the pre-sign refusals, signing, verify-before-append and the append. Integrity-pinned (`integrity.ENFORCEMENT_MODULES`); the runners in `commands.py` / `grant_commands.py` are one-line delegators. See [below](#the-pinned-operator-signing-path). |
| `schema_cache.py` | Shared JSON-Schema validation plus a content-hash cache, so re-validating unchanged bytes is free. |
| `goose_switch.py` | Glue for `--goose-source` / `--goose-model` / `--goose-show`. |
| `backup_switch.py` | Glue for `--stale-check` / `--stale-remediate` / `--prune-backups` / `--backup-mirror`. |
| `fleet_switch.py` | Glue for `--fleet` / `--fleet-frameworks` / `--fleet-report` / `--fleet-exclude` / `--fleet-skip-parent` / `--fleet-ignore-locks`. |
| `package_switch.py` | Glue + dispatch for `--package-team` / `--package-source-framework`. |
| `sync_switch.py` | Glue + dispatch for `--sync-init` / `--sync` / `--sync-since` / `--pin` / `--sync-refresh-instructions`. |
| `recipe_check.py` | Standalone structural validator for Goose recipe YAML. |
| `standalone_modes.py` | The "do one thing and exit" modes carved out of `generate.py`: restore-backup, scan-security, check-budget, template pinning, and the retrieval utilities. They were never part of the generate pipeline. |
| `output_target.py` | Resolves and validates the output directory, including the foreign-output refusal behind `--allow-foreign-output`. |
| `post_emit_checks.py` | Checks that run after the write phase and cannot change its outcome. |
| `code_index_artifacts.py` | Writers for the code & API index cache (`references/code-index/`, gitignored). |
| `json_mode.py` | `--json` output shaping. |
| `exit_codes.py` | The named exit-code constants, so a status is set in one place and read everywhere. |
| `os_sandbox_switch.py` | `--os-sandbox` and the brief's `os_sandbox`: sets the effective OS sandbox host features, prints the ENABLED/OFF notice, decides when the sandbox-relaxation gate engages (`gate_engaged`), runs it and records `sandbox_state`. Integrity-pinned. See [below](#sandbox-and-wasm-flags). |
| `wasm_sandbox_switch.py` | `--wasm-sandbox` and `--wasm-substrate`: validates the WASM half of the state before anything is emitted, prints the WASM notices and the D6 composition advisories (`wasm-without-os-sandbox`, `wasm-only-host`, `wasm-windows-unsupported`), and reports the per-provider `wasm_enforcement` verdict after the emit. It never claims enforcement itself. |
| `sandbox_effects.py` | The seven sandbox-relaxation effect kinds (`sandbox-os-off`, `sandbox-wasm-off`, `wasm-grant-widen`, `native-exec-keep`, `wasm-to-os-switch`, `mcp-approval-auto`, `gate-policy-weaken`) and their action-id / grant-token spelling, shared by the effect classifier, the directive denylist and the gate. |
| `sandbox_relaxation_gate.py` | The render-time gate for those seven effects (constraint-relaxing-authorizations rule, D7): in a governed workspace each needs an exactly scoped, operator Ed25519-signed decision row; standing ones are registered with a 90-day sunset under the aggregate cap; ungoverned workspaces get a `SANDBOX RELAXATION` notice. Integrity-pinned. |
| `wasm_commands.py` | `--wasm-install`, `--wasm-status`, `--wasm-pin-update` (with `--remediation`, `--tofu`, `--pins-out`): the explicit provisioning commands (D4). The only caller of `wasm_runtime.install` / `pin_update`. |
| `wasm_agent_commands.py` | The `--framework wasm` launcher: `--run-wasm-agent` (with `--wasm-task` / `--wasm-task-file`, `--wasm-provider`, `--wasm-base-url`), `--revoke-wasm-run` (`--revoke-reason`) and `--verify-wasm-run` (`--tree`). |

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

`--sign-decision`, `--sign-grant`, `--sign-mcp-direct-grant` and `vulnscan grant sign` are the only code
paths that read the operator's Ed25519 private key (the file named by `AGENTTEAMS_DECISION_ED25519_KEYFILE`,
else `~/.config/agentteams/keys/decision-signing-<key_id>.pem` for the spec's `key_id`). Everything between that
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
2. spec load and shape check: a JSON object with a non-empty `action_reviewed`;
3. the display-spoofing refusal;
4. the row is built: the chain link (`prev_digest`, from the log's chain tip, whose bytes are kept for step 13)
   and, when the log has the column (or is new), `text_digest` (binding `conditions` and `evidence`) are computed,
   never taken from the spec;
5. eligibility and effect classification;
6. the `text_digest` downgrade refusal (a constraint-relaxing row into a log without the column; migrate it with
   `--migrate-decision-log`), or, for a non-relaxing row, a notice that its text is not bound;
7. the grant-purpose (PR-E), toolchain-group and dev-merge-authorization domain-separation refusals; then, for
   an authorizing row, the author-roster refusal (the author the log will store is off the approver roster),
   and for a PASS or CONDITIONAL PASS the open-HALT refusal (an id the row names, or a `derives_from` parent,
   is under an open HALT, #616);
8. preflight: env, integrity, install location, key-file existence. With `allow_checkout`, an overridden
   location refusal is recorded in the signed `conditions_verified`;
9. the display, with the review digest;
10. the confirm gate: a y/N prompt on a terminal, or an exact `confirm_sha256` match;
11. the key read, then the signature over the same in-memory row;
12. verify-before-append against the team's verify-key store;
13. the append, refused if the log changed since step 4 read it (#470).

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

> *Source: `agentteams/cli/sign_decision_refusals.py` (re-exported by `operator_signing`)*

F-2: the refusal sentence when `output_dir` holds neither `references/agent-privilege.json` nor
`references/build-log.json`, else `None`. The same module holds the signer's other pre-key refusals:
`author_refusal(output_dir, row, fieldnames)` (the row's author, read from the cells the log keeps
(`stored_view(row, fieldnames)`), is off the approver roster) and `open_halt_refusal(output_dir, row, fieldnames)` (a clearance
on an id, or a `derives_from` parent, under an open HALT, #616).

### `retire_verify_key(old, new, team_dir, includes, *, confirm_sha256=None, allow_checkout=False)` / `assert_signature_permitted(team_dir, key_id, signature)`

> *Source: `agentteams/cli/key_retirement_verb.py` (the verb) and `agentteams/cli/key_retirement.py` (the check)*

`--retire-verify-key`: writes `<team_dir>/references/authorized-verify-keys/<old>.retired.json`, the sorted sha256
of the decoded bytes of every signature under `old` (`collect_signatures`, each verified under `old` against its own
document's signed values), signed with key `new` over `[agentteams-key-retirement-v1, key_id, not_after, signed_by,
binding, digests]`, where `binding(team_dir)` is `<project_name slug>:<team dir from the project root>`. Returns 0, or
1 on any error. Before the key is read it refuses inside an agent session, two equal or malformed key ids or the same
key bytes under two ids, a non-team or gates-only dir, a missing `.pub.pem` for either key, a retired `new`, a store
whose record `old` signed but which lacks `new`, an include of an unknown kind or with no signature under `old`, a
signature that does not verify, and display-spoofing characters. Then the preflight, the display, confirm, the key
read, signing of the new record and of each record `old` signed (re-signed by `new` with its snapshot,
`records_signed_by`, after a comparison with its committed copy that refuses a widened one), and verification of each (`verify_signed_record`) before anything is written.
`assert_signature_permitted` is called by `decision_log._load_verify_key(output_dir, key_id, *, signature)`: it
matches the key by its bytes against every record in `scan_dirs(team_dir)` (`clearance_binding.halt_scan_dirs` plus,
outside git, the generated team dirs beside it, a symbolic link in their paths refusing); with none, nothing changes;
otherwise every applicable record must verify (binding, and a signer that differs by its bytes and is not retired),
and the signature's digest must be listed in every one, else `RuntimeError`. `report_lines(team_dir)` gives
`--verify-integrity` its lines for every store in `scan_dirs`.

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

`sha256:` plus the hex digest of the length-prefixed encoding `f"{len(c)}:{c}{len(e)}:{e}"` over the stripped texts, with lengths in UTF-8 bytes. A length prefix
keeps the boundary unambiguous: a plain `\x1f` join let `("A", "B\x1fC")` and `("A\x1fB", "C")` collide. A text
holding `\x1f` is refused (`RuntimeError`). A legacy row has no `evidence`; it reads as empty.

### `bind_text_digest(row) -> dict`

Sets `row["text_digest"]` from `text_digest_for(row)` and returns the row. **Bind, then sign:** every minting path
calls it before computing the signature. `decision_log.sign_decision_row` does so itself when the row has a
`text_digest` key; the gate verifies with the non-binding `_hmac_decision_row`.

### `assert_text_bound(row, *, action)`

Raises `RuntimeError` ("failed signature verification") when the row's non-empty `text_digest` no longer matches
its text. `decision_log` calls it right after a row's HMAC or Ed25519 signature verifies, so an edited row stops
clearing. A row without `text_digest` passes. `text_is_bound(row)` and `text_digest_mismatch(row)` are the
underlying predicates.

### `describe_text_binding(team_dir) -> list[str]`

The `--verify-integrity` report lines: signed rows that bind their text, those that don't, a missing column, and
any mismatched row. Empty when the log is absent, unreadable or has no signed row. Advisory only.

## Sandbox and WASM flags

The switches and commands added by the WASM sandbox program, and where each is handled. Option semantics are in the
[CLI Reference](../cli-reference.md); the concepts are in the
[Sandboxing Guide, Part IX](../agentteams-sandboxing-guide/reference/part-ix-switching-sandboxes-and-wasm.md).

| Flag | Module | Kind |
|---|---|---|
| `--framework wasm` | `frameworks/wasm.py` ([`frameworks`](frameworks.md)) | render target (beta, generate-only); turns WASM on |
| `--os-sandbox {on,off}` | `os_sandbox_switch.py` | render switch; `off` is `sandbox-os-off` (signed decision when governed) |
| `--wasm-sandbox {on,off}` | `wasm_sandbox_switch.py` | render switch; `off` after on is `sandbox-wasm-off` |
| `--wasm-substrate {on,off}` | `wasm_sandbox_switch.py` → [`sandbox_projection`](sandbox-projection.md) | render switch; `off` after on is `sandbox-wasm-off:model-substrate` |
| `--wasm-install [COMPONENTS]` | `wasm_commands.py` → [`wasm_runtime`](wasm-runtime.md) | standalone operator command (network) |
| `--wasm-status [COMPONENTS]` | `wasm_commands.py` | standalone, read-only, offline |
| `--wasm-pin-update COMPONENT VERSION` | `wasm_commands.py` → `_wasm_pin_update.py` | standalone operator tool (network) |
| `--remediation GHSA-IDS`, `--tofu`, `--pins-out PATH` | `wasm_commands.py` | modifiers of `--wasm-pin-update` only |
| `--run-wasm-agent SLUG` | `wasm_agent_commands.py` → [`wasm_agent`](wasm-agent.md) | standalone launcher |
| `--wasm-task TEXT`, `--wasm-task-file PATH` | `wasm_agent_commands.py` | exactly one, with `--run-wasm-agent` |
| `--wasm-provider NAME`, `--wasm-base-url URL` | `wasm_agent_commands.py` → [`model_providers`](model-providers.md) | with `--run-wasm-agent`; never a key |
| `--revoke-wasm-run RUN_ID`, `--revoke-reason TEXT` | `wasm_agent_commands.py` | standalone |
| `--verify-wasm-run RUN_ID`, `--tree` | `wasm_agent_commands.py` | standalone, read-only |

`parser_validate.py` keeps the standalone ones standalone: the provisioning flags are refused alongside
`--description`, `--self`, `--update` or `--output`, so a render never downloads anything, and each modifier is refused
without the flag it modifies.

## Supported API: `sandbox_relaxation_gate.spent_off_decision`

**New in 1.3.0 (#574).** It and the `SandboxRelaxationError` it raises are the only `agentteams.cli` surface STABILITY.md
covers (§7, #568); the rest of the package stays internal. Signature: `spent_off_decision(team_dir: Path, framework_id: str) -> str`.

Read-only: is the operator's `sandbox-wasm-off:<framework_id>` decision spent? An outside tool (baseAgent's `--wasm`
path, #574) can check that before acting on WASM being off.

It returns `<key_id>@<first 16 characters of the signature>` only when all of these hold:

- the workspace is governed (decision signing is enforced);
- the newest decision row scoped exactly to `sandbox-wasm-off:<framework_id>` is marked consumed and its verdict
  authorizes (a `CONDITIONAL PASS` also needs `conditions_verified: verified`);
- the row is Ed25519-signed (`sig_scheme` `ed25519`, a `key_id`), the signature verifies, and the decisions log's hash
  chain is intact;
- no unretracted HALT covers `sandbox-wasm-off` or the exact action.

Otherwise it raises `SandboxRelaxationError` naming the check that failed, with filesystem paths removed.

| Argument | Meaning |
|---|---|
| `team_dir` | The team directory that holds `references/security-decisions.log.csv`. |
| `framework_id` | A framework agentteams renders (`claude`, `copilot-vscode`, ...). `model-substrate` is refused: `sandbox-wasm-off:model-substrate` is a different decision. |

Things to know before relying on it:

- **It writes nothing.** Nothing is consumed, registered or retired.
- **It is a point-in-time read.** A later HALT, a retraction, or a newer row scoped to the action changes the answer.
  A newer unconsumed row hides the spent one, and the call refuses. Call it immediately before each use and never
  cache the result.
- **`consumed` is not signed.** The result means "authenticated by the operator and marked consumed", not proof that a
  render spent it.
- **The id is a pointer.** It identifies the row so it can be re-verified; it is not proof on its own.
- **Treat refusal messages as data (C-4).** Stamp the returned id, never the message, and never act on the message's
  text.

It is stricter than the check the gate uses internally to excuse stale WASM evidence. That check accepts any row that
authenticates under the workspace's own signing state; this function also requires a governed workspace and an
Ed25519-signed row.

## Beta API: `open_halts.open_halts`

**Beta** (STABILITY.md): its signature, the `HaltRow` fields and the rules it applies may change in a minor release.
Import it as `from agentteams.cli.open_halts import open_halts, HaltRow`.

### `open_halts(team_dir, action=None)`

Read-only: lists the HALT rows of `team_dir/references/security-decisions.log.csv` as the destructive-action gate's
own HALT check judges them, by calling that check's scan rather than re-implementing it. Returns a list of `HaltRow`
in row order, empty when the log is absent or holds no HALT.

- **With `action`:** the HALT rows that block that action now, each once. They are found the way the gate finds them:
  on the action itself and on each `derives_from` ancestor (when the log has that column), with the gate's matching
  and retraction rules for that action. Every entry has `is_open` true, and `blocks` names the action or the ancestor
  it was found on.
- **Without `action`:** every HALT row, each judged for its own id, which is retraction by exact id. A row with
  `is_open` false blocks no action, and `retracted_by` names the accepted retraction that cleared it. A row with
  `is_open` true still blocks at least its own id; a retraction of exactly a family's id may still lift it at that
  family's own gate action (ask with `action` to know).

| `HaltRow` field | Meaning |
|---|---|
| `row` | The 1-based row number in the log (the header is not counted). |
| `halt_id` | The id a refusal names and a `HALT-RETRACTED` row must carry to retract it: the HALT's own id, or for a HALT with no letter or digit in its id, its row handle `halt-row:<N>:<digest>`. |
| `is_open` | Whether it is still in force (for `action`, or for its own id). |
| `retracted_by` | The 1-based row of the accepted retraction that cleared it, or `None`. |
| `blocks` | With `action`: the id it blocks (the action or an ancestor). Without: its own retraction id. |

Which actions a HALT blocks (the gate's rules, `action_ids.halt_blocks`):

- **Strict match:** the HALT's id equals the action's id, or extends it with `-`, `_`, `.` or `:` and more. A HALT on
  `prune-001` blocks `prune`.
- **Family, across a colon only:** the action's id extends the HALT's id with `:` and more. A HALT on `server-apply`
  blocks `server-apply:remove-path:<host>:<path>`.
- **Tokens:** the action's tokens appear as a run in the HALT's id. Tokens split on every character that is not a
  letter or digit (so on `/` too), and on camelCase and letter-digit boundaries.
- **`/` makes no family.** For the action `family/gate/study:sha256-...`, a HALT on `family` or on `family/gate` does
  not block it, and a HALT on `family/gate/study` does (across the colon). In the other direction, the token rule
  makes a HALT on `family/gate/study` also block `family/gate` and `gate`.
- **No usable id:** a HALT whose id has no letter or digit blocks every action and is retracted only by its row handle.

Things to know before relying on it:

- **It writes nothing**, and it is a point-in-time read: call it immediately before acting on the answer.
- **It refuses what the gate refuses.** An unreadable or malformed log, a broken hash chain, a signed decision row that
  appears twice, or (with `action`) a `derives_from` lineage that cannot be walked raises `RuntimeError`.
- **One id, one log.** With `action` it answers the destructive-action gate's HALT check for that one id in this one
  team directory. A gate that also checks a family id (a sandbox relaxation checks `sandbox-os-off` as well as
  `sandbox-os-off:claude`, so a HALT on a sibling instance blocks it) or reads HALTs from other directories
  (`--merge-pr`, the S-10 gates) is answered by asking for each id, in each directory, that gate checks.
- **No open HALT is not a clearance.** The action still needs a PASS or a waiver, and a retracted HALT that is still
  the newest row matching the action refuses until a new clearance is recorded.

## Related pages

- [CLI Reference](../cli-reference.md) — flags and option semantics
- [`update_report`](update-report.md) — the `update.report.md` record
- [`memory_index`](memory-index.md) — index construction and path storage
- [`output_plan`](output-plan.md) — which files a manifest produces
