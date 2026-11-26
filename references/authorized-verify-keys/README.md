# Authorized Ed25519 verify keys (the constraint-relaxing signing trust anchor)

**Where the keys live (F-2).** The security gate reads the **public** Ed25519 verify keys, one per
`key-id` named `<key-id>.pub.pem`, from `<team dir>/references/authorized-verify-keys/` — the
team's agents dir (e.g. `.claude/agents/references/authorized-verify-keys/`), never this
repository-root directory. This directory holds only the operator helper and this README; it
holds no key. A constraint-relaxing authorization (Constitutional Rule on the
exception-governance guardrail) is cleared in a governed workspace only when its `sig_scheme`
is `ed25519` and its signature verifies against the public key named by its signed `key_id`
in the team store (`agentteams/cli/decision_log.py` → `_load_verify_key`).

The helper is **not emitted** into generated projects; run it from an agentteams checkout.

## The security model — read before adding a key

- **Agents hold only the public keys in this directory.** They can *verify* a relaxing
  authorization; they can never *mint* one. Minting requires the operator's **private** key.
- **The private key must NEVER enter an agent session, a repo, CI, or a sandbox.** It lives on
  the operator's machine only, outside the repository tree, mode `600`, and is read only by the
  operator when running `agentteams --sign-decision`. The launcher's default-deny env allowlist
  (`sandbox/confine-run.sh`) drops the key-file environment variable from any confined agent.
- **This directory is itself a trust root.** An actor who could drop its own public key here
  would be able to self-sign. It is therefore:
  - **non-eligible** — no decision-log or directive exception may authorize a write to it, even
    with a valid operator signature (`effect_classifier.non_eligibility_reason`); and
  - a governance target in the shared trust-root vocabulary (`governance_targets.py`).
  Adding a key is a deliberate operator act performed directly (as below), never through an agent
  authorization.

## Provisioning a key (operator, on your own machine)

Run the helper in your **interactive shell** — never inside an agent/sandbox session:

```
references/authorized-verify-keys/provision-operator-signing-key.sh --team-dir .claude/agents op-2026
```

It generates the private key **outside** the repo in `~/.config/agentteams/keys/` (directory
mode 700, file mode 600), writes the public key into each `--team-dir`'s
`references/authorized-verify-keys/<key-id>.pub.pem` (repeat `--team-dir` for several teams), and
prints the signing commands, which name no key: the signers find
`decision-signing-<key-id>.pem` there by the spec's `key_id`. `--team-dir` is required: without it
the helper refuses (exit 2) and lists the team dirs it finds, because the gate never reads a
repository-root store. A `--team-dir` must hold `references/agent-privilege.json` or
`references/build-log.json`. Commit the `.pub.pem` if the team dir is tracked; in this repository
`.claude/` is gitignored, so the key there stays local (an operator action). Never commit the
private key. Then, to sign a relaxing decision:

```
agentteams --sign-decision path/to/decision-spec.json --output .claude/agents
```

With `AGENTTEAMS_DECISION_ED25519_KEYFILE` unset, the signer uses
`~/.config/agentteams/keys/decision-signing-<key_id>.pem` for the spec's `key_id` and prints that path.
Set the variable only for a key kept elsewhere, and only on the signing command itself.

`--sign-decision` refuses an `--output` that is not a team dir (no `agent-privilege.json` or
`build-log.json` under `references/`), and verifies the new signature against that team's
`<key-id>.pub.pem` before appending, so a key provisioned into the wrong frame fails at mint time.

**Why `keys/`.** Every sandbox agentteams emits — the Claude `sandbox` block (plus its
`permissions.deny` `Read(...)` rules for the built-in tools), the goose Seatbelt profile and
`sandbox/confine-run.sh` — read-denies `~/.config/agentteams/keys`, in every privilege profile.
No other directory is denied, so the script **refuses** a custom `KEY_DIR` unless you pass
`--allow-unprotected-keydir` (and then warns loudly). It also refuses a symlinked or
group/world-accessible `keys/`. Only the key **file** is protected: environment variables
(`AGENTTEAMS_DECISION_ED25519_KEYFILE`, `AGENTTEAMS_*_SIGNING_KEY`) are inherited by Claude-sandboxed
commands, so do not export them into the shell that launches an agent. Claude-arm enforcement is
unverified end-to-end on Linux.

**Migrating from the old location.** Keys provisioned before this change sit directly in
`~/.config/agentteams/`. The script warns about them; `--migrate` moves each one into `keys/`
with `mv -n` (never overwrites, never deletes — a skipped file is reported and the run exits 1),
then reminds you to update any `AGENTTEAMS_DECISION_ED25519_KEYFILE` exported in shell rc files or
CI. Until then the sandboxes also deny the legacy files transitionally.

```
references/authorized-verify-keys/provision-operator-signing-key.sh --migrate
```

## Rotation, retirement and revocation

There are three ways to stop using a key. All three are git-visible acts.

- **Rotate only.** Provision a new `key-id` and sign new rows under it. The old `.pub.pem` stays, so
  old rows keep verifying, and so would anything signed with the old key from now on.
- **Revoke.** Remove the `.pub.pem`. Every row and document under that `key_id` then fails closed,
  including the ones you still rely on (relaxing decisions, grants, the carve-out registry, a
  toolchain group, a dev-merge authorization).
- **Retire.** Keep the `.pub.pem` and add a signed retirement record, `<key-id>.retired.json`, beside it.
  In the team dir stores of the project, signatures the old key made before the retirement keep
  verifying; any other signature under it is refused, and so is new signing with it. Use this to rotate a
  key you still have history under. It does not cover the user-level store (below).

### The retirement record

`<key-id>.retired.json` holds `key_id`, `not_after` (the retirement date), `signed_by`,
`signature_sha256` (the sorted sha256 of the decoded bytes of every signature the old key made) and
`signature`. Every Ed25519 verify path of a team dir loads keys through `decision_log._load_verify_key`,
which applies the record (`agentteams/cli/key_retirement.py`):

- a signature under a retired key verifies only when its digest is in `signature_sha256`. `not_after` is
  recorded and reported but never compared, because many signed documents carry no date;
- keys are compared by their **bytes**, not their file names: the same public key stored under a second
  `key-id` is retired too;
- a record binds the key in **every team dir store of the project**: the generated team dirs
  (`.claude/agents`, `.github/agents` and the other frameworks' dirs) under each enclosing git root, the
  team dir's declared `parent_team`, and, outside git, the generated team dirs beside it. A document that
  picks its own store (the carve-out registry's `decision_log`, for example) cannot escape the record by
  naming a sibling store;
- the record must be signed by a key whose bytes differ from the retired key's and that no record retires.
  A record signed by the retired key itself (under any name), or by a key that is absent or retired, is
  refused;
- a record that is a symbolic link, malformed, or edited after signing (a digest added, for example) is
  refused, and then every signature under the key is refused. That is the same as revoking it;
- the record's payload starts with `agentteams-key-retirement-v1`, which no decision, grant, carve-out
  registry, toolchain group or dev-merge authorization payload can start with.

`--verify-integrity` prints one line per record: key, `not_after`, how many signatures it keeps, the
signer, and whether it verifies. It also warns when the record lists a digest its committed copy does
not (a snapshot should only shrink), and when a committed record is missing on disk. Both warnings are
report-only: whoever holds the signing key can re-sign a wider snapshot, and **deleting a record
un-retires the key**. Removing or widening a record is a governance event: only the operator does it,
deliberately, and a warning you did not cause means someone else did.

**The user-level store is not covered.** MCP-direct grants and vulnscan grants, publish grants,
escalation resolutions and known-vulnerability acceptances verify against
`~/.config/agentteams/verify-keys/<key-id>.pub.pem`, signed with the same operator key, and that store has
no retirement records. Retiring a key there means removing its `.pub.pem` (step 7 below). Outstanding
documents of those kinds under the old key then stop verifying: MCP-direct grants last at most 30 days
(7 by default), vulnscan and publish grants at most 30 days, known-vulnerability acceptances at most 90
days, and an escalation resolution not yet applied can no longer be applied. Re-issue the ones you still
need under the new key.

### Retiring a key (operator, on your own machine)

Run these from your own terminal, never inside an agent session (the verb refuses there), using the
trusted install described in `SECURITY.md` ("Signing from a trusted install"). `<old>` is the key you
retire, `<new>` the key that signs the record, and `<team>` the team dir, such as
`<project>/.claude/agents`.

1. Provision the new key if you have not: run the helper above with `--team-dir <team> <new>`.
2. Check the signing code and the store: `agentteams --verify-integrity --output <team>`.
3. Write the record:

   ```
   agentteams --retire-verify-key <old> --key-id <new> --team-dir <team> \
       [--retire-include <signed file> ...]
   ```

   It collects every Ed25519 signature under `<old>` from the team's decisions log and, at the project
   root, the capability-grants log, `.agentteams/carveouts.json`, `.agentteams/toolchain-group.json` and
   `.agentteams/dev-merge-authorization.json`. Pass `--retire-include` for any other signed decisions log,
   capability-grants log, carve-out registry, toolchain group or dev-merge authorization under `<old>`
   (another workspace's toolchain group, for example); an include of another kind, or with no signature
   under `<old>`, is refused. Each signature is verified under `<old>` against its own document before it
   is kept: one that does not verify (a hex string pasted into a row, for example) refuses the retirement
   and names the row or file. Use a generated team dir: from a gates-only team dir the verb refuses while
   another store holding the key would not see the record, and names those stores. The private key is
   found as for `--sign-decision` (`~/.config/agentteams/keys/decision-signing-<new>.pem`, or
   `AGENTTEAMS_DECISION_ED25519_KEYFILE`).
4. Check the displayed review before you answer `y` (or before you pass `--confirm-review-sha256` with no
   terminal; the digest covers today's date, so confirm on the same day):
   - `retired key` is `<old>` and `signing key` is `<new>`, and the `sha256` shown for `<new>` matches
     `shasum -a 256 <team>/references/authorized-verify-keys/<new>.pub.pem` for the key you provisioned;
   - `team dir` is the store you mean;
   - every `keeps` line names a document you signed (`decision <action> <verdict>`, `grant <id>`,
     `carve-out registry sequence <n>`, `toolchain group sequence <n>`, `dev-merge authorization ...`,
     with its file and line). A line you do not recognise is a document you did not sign: if the old key
     may be compromised, stop and revoke it instead;
   - nothing you still need is missing. A signed document that is not listed stops verifying once the
     record exists: re-run with `--retire-include` for it.
   The record is verified against `<new>.pub.pem` before it is written.
5. Confirm: `agentteams --verify-integrity --output <team>` prints
   `Retired verify key <old>: not after <date>, <n> signature(s) kept, signed by <new>, verifies`. The
   verify verbs you use (`--verify-carveouts`, `--verify-toolchain-group`,
   `--verify-dev-merge-authorization`, `--verify-grants`) should still pass.
6. Commit the record where the team dir is tracked and keep `<old>.pub.pem`. One record covers every
   team dir store of this project; repeat in every other repository whose stores hold the old key.
7. Remove `<old>.pub.pem` from `~/.config/agentteams/verify-keys/` if it is there (see "The user-level
   store is not covered" above), then move the old private key out of `~/.config/agentteams/keys/`.

If you later retire `<new>` as well, records it signed stop verifying (their signer is now retired):
re-run step 3 for each of them with the next key.
