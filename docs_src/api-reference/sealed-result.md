# sealed_result

> *Source: `agentteams/sealed_result.py`* (integrity-pinned, stdlib only)

Seals a runner result so that only the process that queued the request can read it.

Under `write_policy: "orchestrator-only"`, Claude Code applies the session's `Read(/.agentteams/**)` and
`Read(/.agentteams-queue/**)` denies to sandboxed commands too (measured on 2.1.246, 2026-10-11). The runner
therefore publishes the orchestrator's results in the session-readable `.agentteams-status/` tree
([proposal-runner](proposal-runner.md)). Every agent in the session can read that tree, so each result is sealed
with the `reply_key` its request carried. The CLI generates that key and keeps it in memory only.

## Construction

- **Key:** `reply_key` is 32 bytes from `secrets.token_bytes`.
- **Separate keys:** encryption and MAC keys are derived as `HMAC(reply_key, "agentteams-seal-enc-v1")` and
  `HMAC(reply_key, "agentteams-seal-mac-v1")`.
- **Encryption:** HMAC-SHA256 in counter mode over a random 16-byte IV.
- **Integrity:** encrypt-then-MAC. A full 32-byte tag over `MAGIC ‖ request id ‖ IV ‖ ciphertext` is checked with
  `hmac.compare_digest` before anything is decrypted, so a forged or tampered blob, or one for another request or
  key, is refused.
- **Padding:** a 4-byte length plus the data, zero-padded to a fixed bucket (`BUCKETS`: 4 KiB, 64 KiB, 2 MiB + 64
  KiB). A blob's size reveals only its bucket.

## Public surface

| Name | Purpose |
|---|---|
| `new_reply_key() -> bytes` | A fresh 32-byte key. |
| `seal(reply_key, request_id, data) -> bytes` | `MAGIC ‖ IV ‖ ciphertext ‖ tag`. Raises `SealError` on a bad key or data larger than the largest bucket. |
| `open_sealed(reply_key, request_id, blob) -> bytes` | Verify, then decrypt. Raises `SealError` when the blob is malformed or fails verification. |
| `SealError` | A `ValueError`. |
| `BUCKETS`, `MAGIC`, `KEY_BYTES` | Format constants. |
