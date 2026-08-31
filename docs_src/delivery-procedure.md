# Delivery Procedure

Generated agent teams ship with a **delivery receipt** under
`.github/agents/references/delivery-receipt.json`. The receipt is a small,
schema-validated attestation that an `agentteams --update` run
completed successfully against a known team identity. It is **not** a baseline
— it does not influence drift detection or subsequent updates.

> **Note.** The receipt path is framework-relative. For `copilot-vscode` the
> agents dir is `.github/agents/`, so the receipt lives at
> `.github/agents/references/delivery-receipt.json` (used in the examples
> below). For `claude`, `goose`, and `agents-md` the agents dir differs, so the
> receipt generalizes to `<agents-dir>/references/delivery-receipt.json`.

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
`agentteams --update` once to migrate the baseline (the "heal"
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

For full drift verification, follow with `agentteams --check`.

---

## Dry-run redelivery to a downstream repo

When delivering an update to a downstream consumer (e.g. a corpus repo), the
recommended sequence — applied unchanged by the P5 generator-side close-out —
is:

1. **Snapshot first.** Take a read-only snapshot of the downstream repo's
   current `.github/agents/` (or `.claude/`) tree. Do not edit yet.
2. **Throwaway dry-run.** In a scratch clone of the downstream repo, run
   `agentteams --update --dry-run --project <path>`. Inspect the
   stdout diff and the *unwritten* receipt path. No file is mutated.
3. **Classify the diff.** Distinguish two failure modes:
   - **Real drift** — generator output changed (template fix, schema bump).
     This is the expected outcome of a delivery.
   - **Reorg overlap** — downstream has moved files; the diff reflects a
     downstream-only change. Resolve on the downstream's schedule before
     redelivery.
4. **Cross-repo gate.** Any actual write to the downstream repo must route
   through `@repo-liaison` (Workflow 9) and require `@security` clearance.
   The generator side never writes outside its own repository.
5. **Real delivery.** Once the dry-run diff is classified and the downstream
   reorg is settled, run `--update` without `--dry-run`. The receipt is
   written iff the run succeeds.
6. **Verify.** Re-run the CI verification block above against the downstream
   tree to confirm fingerprints agree.

> **Why this lives here.** The dry-run is a property of the delivery
> procedure, not of a specific downstream repo. Downstream-specific
> sequencing (e.g. P5 hayekAI reorg) is owned by `@repo-liaison` and recorded
> in the cross-repository registry, not in this generator-side document.
