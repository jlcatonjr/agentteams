# Authorized Ed25519 verify keys (the constraint-relaxing signing trust anchor)

This directory holds the **public** Ed25519 verify keys, one per `key-id`, named
`<key-id>.pub.pem`. A constraint-relaxing authorization (Constitutional Rule on the
exception-governance guardrail) is cleared in a governed workspace only when its `sig_scheme`
is `ed25519` and its signature verifies against the public key named by its signed `key_id`
here (`agentteams/cli/decision_log.py` → `_load_verify_key`).

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
references/authorized-verify-keys/provision-operator-signing-key.sh op-2026
```

It generates the private key **outside** the repo (`~/.config/agentteams/`, mode 600), writes
the public key here as `<key-id>.pub.pem`, and prints the `AGENTTEAMS_DECISION_ED25519_KEYFILE`
export line. Commit **only** the `.pub.pem`; never the private key. Then, to sign a relaxing
decision:

```
export AGENTTEAMS_DECISION_ED25519_KEYFILE="$HOME/.config/agentteams/decision-signing-op-2026.pem"
agentteams --sign-decision path/to/decision-spec.json
```

## Rotation / revocation

New key = run the helper with a new `key-id` and sign new rows under it; old rows keep verifying
against their retained `.pub.pem`. Revoke by removing the `.pub.pem` (rows under that `key_id`
then fail closed). Both are git-visible acts.
