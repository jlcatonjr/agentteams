# Delivery Procedure

Generated agent teams ship with a **delivery receipt** under
`.github/agents/references/delivery-receipt.json`. The receipt is a small,
schema-validated attestation that an `agentteams build_team --update` run
completed successfully against a known team identity. It is **not** a baseline
— it does not influence drift detection or subsequent updates.

This guide explains how the receipt is written, how to read it, and how to use
it to verify a delivery.

---

## What the receipt is — and is not

| | Receipt (`delivery-receipt.json`) | Build log (`build-log.json`) |
|---|---|---|
| Purpose | Attestation that `--update` succeeded | Baseline used by drift detection |
| Top-level discriminator | `"artifact_type": "delivery-receipt"` | `"schema_version": "1.2"` |
| Read by `--check` / `--update` | No | Yes |
| Required for drift detection | No | Yes |
| Safe to delete | Yes (re-emitted on next `--update`) | No (next `--update` would treat the team as new) |

> **Baseline vs attestation.** The build log is the *baseline*: drift detection
> compares the current rendered team against it. The receipt is the
> *attestation*: it states "an operator successfully delivered this team at
> this time." Confusing the two leads to false drift trust.

---

## When the receipt is written

The receipt is written:

- **After** `build-log.json` is written (the "heal first, attest second"
  order: the baseline is fresh before the attestation is recorded).
- **Inside** the `not args.dry_run and result.success` block in
  `build_team.py`, so:
  - `--update --dry-run` does **not** write a receipt;
  - a failed `--update` does **not** write a receipt;
  - a security-gate-blocked `--update` does **not** write a receipt.

Initial generation (no `--update`) currently does not write a receipt — the
receipt is `--update`-scoped because it is intended to attest delivery into an
existing operating environment.

The `--update` path also no-ops (and writes no receipt) when there is
literally nothing to update — only deliveries that actually rewrite files
attest. CI patterns that gate on receipt presence should therefore expect a
receipt only when an `--update` had work to do.

If the receipt write fails after the build-log was written, the heal still
happened: the next `--update` converges to zero drift and re-emits the
receipt. The error is surfaced on **stderr** (not stdout) and does not abort
the run — when scripting against the output of `--update`, capture both
streams (`2>&1` or equivalent) so the receipt-failure warning is not
swallowed.

---

## Receipt schema (summary)

The full schema lives at `schemas/delivery-receipt.schema.json`. Required
fields:

- `artifact_type` — always the literal string `"delivery-receipt"`.
- `receipt_schema_version` — receipt contract version (currently `"1.0"`).
- `delivered_at` — ISO 8601 UTC timestamp.
- `project_name` — project name from the manifest at delivery time.
- `framework` — `copilot-vscode`, `copilot-cli`, `claude`, etc.
- `manifest_fingerprint` — equals the build-log `manifest_fingerprint` just
  written.
- `fingerprint_algo_version` — equals
  `agentteams.drift.FINGERPRINT_ALGO_VERSION` at delivery time.

Optional fields: `output_dir`, `agentteams_version`, `delivered_by`.

---

## How to verify a delivery

```bash
# 1. Read the receipt.
RECEIPT=.github/agents/references/delivery-receipt.json
LOG=.github/agents/references/build-log.json

# 2. Compare fingerprints. If they match, no `--update` has been run since
#    the receipt was issued.
jq -r .manifest_fingerprint "$RECEIPT"
jq -r .manifest_fingerprint "$LOG"

# 3. Compare algo versions. Fingerprints are only comparable when these match.
jq -r .fingerprint_algo_version "$RECEIPT"
jq -r .fingerprint_algo_version "$LOG"
```

If the algo versions differ, the fingerprints are not comparable — run
`agentteams build_team --update` once to migrate the baseline (the "heal"
behavior), then re-verify.

---

## What the receipt does **not** prove

- It does **not** prove the on-disk team is byte-identical to what the
  generator would produce now. That is what `--check` and `--update --dry-run`
  are for.
- It does **not** prove no manual edits have been made. Manual edits to
  `{MANUAL:*}` placeholders are preserved by `--update` and do not invalidate
  the receipt; other manual edits are detected by drift, not by the receipt.
- It does **not** influence drift detection. An absent, stale, or malformed
  receipt has no effect on `--check` or `--update`.

---

## Exclusion from drift

The receipt path (`references/delivery-receipt.json`) is excluded from drift
artifacts by construction:

- It is not in the rendered set.
- It is not added to `output_files_map`, `template_hashes`, or
  `file_hashes` in the build log.
- The drift detector compares files listed in the build log; the receipt is
  not listed.

This means deleting, editing, or moving the receipt **never** appears as drift
and never affects what `--update` writes.

---

## CI usage

A typical CI verification step:

```yaml
- name: Verify agent-team delivery
  run: |
    test -f .github/agents/references/delivery-receipt.json
    RECEIPT_FP=$(jq -r .manifest_fingerprint .github/agents/references/delivery-receipt.json)
    LOG_FP=$(jq -r .manifest_fingerprint .github/agents/references/build-log.json)
    test "$RECEIPT_FP" = "$LOG_FP"
```

This guarantees:

1. A delivery was made (receipt exists).
2. The build-log has not been rewritten since delivery (fingerprints match).

For full drift verification, follow with `agentteams build_team --check`.
