# `carveouts` — operator-signed, workspace-scoped carve-outs

> *Source: `agentteams/carveouts.py`, `agentteams/carveout_checks.py`, `agentteams/cli/clearance_binding.py`, `agentteams/cli/carveout_commands.py`*

A *carve-out* names constitutional-rule clauses that do not refuse one bounded activity, in one workspace or a listed
set of workspaces, until a date. The constraint-relaxing-authorizations rule (Rule 15 in this repository) ships the
guardrail machinery to every generated team and never a specific exception. This module is that machinery. Each
carve-out is project data that an operator writes and signs.

Before this module, an operator who needed one (the manager-roster rollout, 2026-11-08) hand-wrote a new clause into
`.claude/CLAUDE.md`, pasted it into `AGENTS.md` and `.github/copilot-instructions.md`, and had every script grep for
it. The copies drifted, and one sat inside a fence that a sync would overwrite. Nothing checked the expiry or the cap,
and binding a script to its @security review took twelve review rounds to get right.

## The registry

One file, `.agentteams/carveouts.json`, at the project root. It is the one source for every provider surface.

```json
{
  "version": 1,
  "sequence": 1,
  "cap": {"max_active": 1},
  "decision_log": ".github/agents",
  "carveouts": [{
    "id": "ledger-rollout-2026-11",
    "status": "active",
    "workspaces": ["repo:example-owner/example-repo"],
    "relaxes": ["rule-15(b): an operator signature for each relaxing row",
                "rule-9(a): a plan file per request before the first step"],
    "effect": {"targets": ["fleet/runs/rollout/batch.md"], "cross_repo": true},
    "conditions": ["operator-run only", "exact commit file list", "merge pinned to the head sha"],
    "text": "An operator-run script may commit, push and open PRs for the rollout ledger in each listed repository.",
    "added": "2026-11-08",
    "expires": "2026-12-31",
    "closes_when": "the rollout ledger closes",
    "plan": "tmp/by-week/2026-W45/rollout.plan.md",
    "ledger": "fleet/runs/rollout/batch.md",
    "lineage": ["approve-ledger-rollout"],
    "clearance": {"scope": "ledger-batch", "file": "scripts/operator/ledger-batch.sh"}
  }],
  "signature": {"scheme": "ed25519", "key_id": "operator-2026", "value": "…"}
}
```

| Field | Rule |
|---|---|
| `id` | 3–64 characters of `a-z 0-9 . -`; unique. |
| `status` | `active`, `closed`, `expired` or `revoked`. Only `active` entries bind. |
| `workspaces` | Required. `repo:<owner>/<name>` (the GitHub origin, case-folded) or `team:<slug>` (the brief's `project_name` slug). **Exact match only**: no prefix, suffix or substring widening, the same rule as management directives' task scopes. |
| `relaxes` | Required. Each is `rule-<n>(<letter>)`, then `: ` and what the clause says, so the limits can read it. A whole rule (no letter) is never lifted. |
| `effect` | The structured effect: `targets`, `relaxes`, `cross_repo`, `bulk` (and `grants`, `destructive`, which are always refused). The effect class is **derived** from it by `effect_classifier`; a declared `effect_class` must agree. Every valid entry derives as relaxing, so the class is shown and compared with a declared one, nothing more. |
| `conditions`, `text` | Required. What the activity must still satisfy, and the wording every constitution copy shows. No HTML comment markers. |
| `added`, `expires` | Required dates. `added` is not in the future, and `expires` is at most 90 days after it. An `active` entry past `expires` stops binding: its text leaves every copy on the next sync, and `--verify-carveouts` reports it (`lapsed`) until it is set to `expired`. |
| `closes_when`, `plan`, `ledger` | Optional. Paths are relative to the project root, without `..`. |
| `lineage` | Decision-log action ids this entry derives from. Each must be logged and carry no unretracted HALT, ancestors included (C-2). |
| `clearance` | Optional `{scope, file}`: a verified @security PASS must exist for `scope` bound to the file's current sha256. |
| `cap.max_active` | Active entries allowed; default 1, never above 3. They also count against the team's Rule 15 exception-registry cap, with its active relaxing exceptions. |
| `decision_log` | Required: one of the generated team directories (it must hold `references/build-log.json`, since the sandbox protects only a team's key store that exists when it starts) — `.claude/agents`, `.github/agents`, `.goose/recipes`, `.codex/agents`, `.grok/agents` — whose verify-key store, approvers and rosters the emitted sandboxes write-protect. Its `references/` must hold the decisions log and the exception registry. No other directory is accepted, so a registry can't choose where its own verify key lives. |
| `sequence` | Required positive integer, raised on every re-sign. The registry is refused (`rollback`) when any of its last 500 commits (`--full-history`, replace refs ignored) holds a different version with an equal or higher sequence, when the clone is shallow, or when the team dir's `references/carveout-high-water.json` records a higher sequence already rendered. |
| `signature` | Written by `--sign-carveouts`: the operator's Ed25519 signature over the domain tag `agentteams-carveouts-v1`, the key id and the canonical registry. Any later edit breaks it. |

Unknown fields are refused, so a misspelt `expires` can't silently drop a limit.

**What this can't express.** No carve-out may target or name a roster, the directive ledger or another trust root
(the @security review's condition, 2026-11-09). So the activity that motivated this module, a script committing
`authorized-managers.txt` across repositories, is out of reach: a carve-out can't lift trust-root protection for
the files the trust model rests on. That still takes a reviewed, per-repository, operator-run procedure.

## Hard limits

No entry can lift these. Strings are checked after NFKC folding, with every dash-like character read as `-`:

- no rendered string (text, conditions, paths, ids) names the Constitutional Core (C-1..C-5) or a HALT;
- no relaxed clause names the carve-out mechanism or Rule 15's own bounds: lineage, the cap, sunset, expiry,
  precedence, the constitution;
- it never grants a capability (C-3) and is never destructive (C-5);
- no target and no rendered string names a trust root: the verify keys, signing keys, integrity manifest, decisions
  log, approvers, agent-privilege and signing-governed markers, rosters, management-authority, directive ledger,
  waivers, constitution files, exception registry or this registry;
- a whole rule is never lifted (every clause names its letter), and of the constraint-relaxing-authorizations rule
  only (b), the operator-signature clause, can be, by number as this project's constitution numbers it. When no
  entry file numbers that rule, every carve-out is refused. Clause (c) is not liftable: every trust root it protects
  is already refused as a target here, so lifting it would let nothing through;
- strings other than `text` are ASCII, and no string holds an invisible format character (zero-width space, soft
  hyphen), so lookalikes can't hide a limit;
- no field holds an HTML comment marker or (except `text`) a line break, and `text` holds no heading, so nothing can
  close the block or start a section of its own.

## Residual risks

These are accepted and bounded by the operator signature, the 90-day expiry and C-2 (a HALT on `carveout:<id>`):

- **An unsandboxed agent** can write the team dir's key store, as it can for `--sign-decision`. The protection is the
  sandbox's write-protection of the generated teams' key stores.
- **A team directory created by an agent.** The sandbox protects only the key stores of teams that exist when it
  starts. A sandboxed agent can create a new team directory (for example `.codex/agents` in a project with no Codex
  team), put a build log and its own verify key there, point `decision_log` at it and sign with its own key.
  agentteams can't tell how old that directory is. So every verify and render prints the signing team, key id and
  the public key's sha256 fingerprint: check that it is your key.
- **History.** A reset or rewritten history can lower what git shows. The high-water mark in the team dir catches a
  sequence lower than one already rendered. Since 2026-11-12 the mark is a governed control-plane file: stubbed at
  sequence 0 (which reads as absent) and protected wherever it exists, by Claude's `permissions.deny`, the Goose
  Seatbelt profile and `confine-run.sh`. A team rendered before then has no stub until its next `--update`; until
  then a sandboxed agent that can write the team dir can create or lower the mark.
- **Wording.** A restatement that names nothing on the lists (for example "the reviewer's stop verdict") can't be
  caught by word lists.
- **A forged block.** The copy hash is unkeyed, so a block rewritten with a recomputed hash is caught only by
  `--verify-carveouts`, which compares every copy with the signed registry. The post-build hook runs the same
  comparison after every generate and `--update`.

## Trust root

The registry is a trust root. `governance_targets.GOVERNANCE_TARGET_STEMS` names it (`carveouts-json`, `carve-outs-json`, so
`CarveOuts.json` and `carve_outs.json` match too), which makes a decision row targeting it non-eligible.
`sandbox_handoff.EXTRA_TRUST_ROOT_TOKENS` names it as well, so a handoff that touches it is refused. The module is
integrity-pinned.

Every carve-out relaxes a clause, so Rule 15(b) applies: the registry renders nothing until the **operator** signs it
with `--sign-carveouts`. That command runs the same preflight as `--sign-decision`: the key env, the signing code's
integrity, the install location, the review digest and its confirmation. The signature is verified against
`<decision_log>/references/authorized-verify-keys/<key_id>.pub.pem`. An agent that edits the file breaks the signature.
Rendering also requires the registry's bytes to equal the last commit's (`git show HEAD:…`, so
`--assume-unchanged` can't hide an edit).

## Rendering

`project()` writes a hash-stamped block into the unfenced `## Project-Specific Rules` region of every enabled entry
file. It runs after every generate and `--update`, and on `--sync-carveouts`. Lineage, clearance and aggregate-cap
problems refuse it, as does a HALT on `carveout:<id>`. A refused registry binds nothing: the blocks it rendered
earlier are removed (when their copy hash still matches), then the refusal is reported. The targets are the same as [`rules_projection`](rules-projection.md)'s: `.github/copilot-instructions.md`,
`.claude/CLAUDE.md` (else `CLAUDE.md`), `AGENTS.override.md` (else `AGENTS.md`) and `.goosehints`. Frozen surfaces are
skipped.

```
<!-- CARVEOUTS:BEGIN source=.agentteams/carveouts.json source-sha256=… copy-sha256=… -->
…
<!-- CARVEOUTS:END -->
```

That region survives `--update --merge` and `--overwrite`. A block edited by hand is refused, not overwritten. The
copy hash detects edits; it does not authenticate them, which is the signed registry's job. A block that would take a
Codex `AGENTS.md` past its 32 KiB budget is refused. When no
carve-out binds the workspace any more, the block is removed. `rules_projection` strips the block from the operator's
own rules, so it is never re-projected as a project rule.

## API

| Name | Purpose |
|---|---|
| `load_registry(root)` | The parsed registry, or `None`. Raises `CarveoutError` on a symlink, unreadable file or wrong shape. |
| `validate_entry(entry, today=)` / `validate_registry(registry, today=)` | The problems (`Problem(entry, code, detail)`), each with a stable code such as `unrelaxable`, `lapsed`, `over-cap`, `bad-workspace` or `effect-class-mismatch`. |
| `workspace_ids(root)` / `in_scope(entry, ids)` | This project's ids, and exact matching against them. |
| `derived_effect_class(entry)` | `relaxing`, `non-relaxing` or `indeterminate`, from `effect_classifier.derive_effect_class`. |
| `active_entries(registry, ids, today=)` | The entries that bind now. |
| `signed_fields(registry, key_id)` / `signature_problem(root, registry)` / `signer(root, registry)` | What the operator signs, why a signature does not verify, and who signed it (team, key id, public-key fingerprint). |
| `carveout_checks.team_dir_problem(root, registry)` | The pinned team directory must hold `references/build-log.json` (a generated team), and be one of `TEAM_DIRS`: `.claude/agents`, `.github/agents`, `.goose/recipes`, `.codex/agents`, `.grok/agents`, whose key stores the sandbox protects; it must not be reached through a symlink, and must hold its decisions log and exception registry. |
| `carveout_checks.rollback_problem(root, registry)` | Every earlier commit of the registry must have a lower `sequence`, so an older signed registry can't be restored. |
| `carveout_checks.clause_problems(root, registry)` | By number, read from this project's constitution: only clause (b) of the constraint-relaxing-authorizations rule can be lifted, and nothing can when no entry file numbers that rule. |
| `carveout_checks.check_own_halts(root, registry, entries)` | A HALT on `carveout:<id>` stops that carve-out, lineage or not (C-2). |
| `carveout_checks.check_lineage(root, registry, entries)` | Missing lineage ids and lineage HALTs. Every HALT along the lineage passes down, whatever its basis. |
| `carveout_checks.check_aggregate_cap(root, registry, entries)` | Every active carve-out (in scope or not) plus the team's active relaxing exceptions, against the Rule 15 cap. |
| `binding_entries(root, today=)` | `(registry, entries, refusals)`: what binds now, and what refuses rendering. |
| `clearance_binding.clearance_action(scope, path)` | `<scope>:sha256-<hex>`. `action_matches` lets a row's id carry a suffix but never lets a shorter id match, so a bare-scope PASS, or one for another file's bytes, can't satisfy it. |
| `clearance_binding.check_file_clearance(team_dir, scope, path)` | `(cleared, reason)`. A HALT on the bare `scope` blocks it, then the non-consuming `security_gate.check_clearance` runs: HALTs, author, signature and chain checks all apply, and nothing is spent. The caller must then use the same bytes it checked. |
| `project(root, frozen=, write=, today=)` | Render. Returns a `rules_projection.Report`. |
| `verify(root, frozen=, today=)` | Every problem above, plus the committed state and every copy that has drifted. Writes nothing. |

## CLI

- `agentteams --sign-carveouts --key-id ID [--confirm-review-sha256 SHA]` (operator only) signs the registry. Commit it
  afterwards.
- `agentteams --sync-carveouts [--dry-run]` renders the blocks in the project it runs in (another `--project` is
  refused: a write into another repository needs that repository's own run, C-5).
- `agentteams --verify-carveouts [--project P]` is read-only and exits 1 on any problem.
- `agentteams --check-clearance SCOPE --clearance-file PATH [--output TEAM_DIR]` exits 0 only when the decisions log
  holds a verified PASS for `SCOPE:sha256-<hex of PATH>`. An operator script calls it first, instead of hand-rolling
  its own gate.

To clear a script, @security records a row whose `action_reviewed` is the action id the failed check prints.
