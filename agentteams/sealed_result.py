"""sealed_result.py — seal a runner result so only the requesting process can read it.

Under ``write_policy: "orchestrator-only"`` on Claude Code, the session's ``Read(/.agentteams/**)`` and
``Read(/.agentteams-queue/**)`` denies (@security C4) also bind the orchestrator's sandboxed subprocesses, so its
CLI can't read results from the runner's control plane. The runner therefore publishes orchestrator-channel results
in ``.agentteams-status/``, which the session may read but never write. Every agent in the session can read that
tree, so each result is sealed with a key only the requesting process holds: the CLI puts a fresh ``reply_key`` in
its request (which sits in the read-denied queue) and keeps it in memory; the runner seals the result with it.

Construction (stdlib only; @security conditions, 2026-10-11):

* ``reply_key``: 32 bytes from :func:`secrets.token_bytes`.
* Separate keys: ``enc = HMAC(reply_key, "agentteams-seal-enc-v1")``, ``mac = HMAC(reply_key,
  "agentteams-seal-mac-v1")``.
* Encryption: HMAC-SHA256 in counter mode over a random 16-byte IV (block ``i`` = ``HMAC(enc, IV || i)``).
* Integrity: a full 32-byte tag ``HMAC(mac, MAGIC || request_id || IV || ciphertext)`` (encrypt-then-MAC),
  checked with :func:`hmac.compare_digest` before anything is decrypted.
* Padding: the plaintext (a 4-byte length, then the data) is zero-padded to a fixed bucket, so the blob's size
  reveals only the bucket, not the exact result size. The result kind is inside the ciphertext.

The runner seals once per ``reply_key`` (one result per request). Integrity-pinned.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

MAGIC = b"ATSEAL1\n"
KEY_BYTES = 32
_IV_BYTES = 16
_TAG_BYTES = 32
_LEN_BYTES = 4
#: Plaintext buckets. The largest holds a full-size runner result (proposal_runner.RESULT_MAX_BYTES, 2 MiB).
BUCKETS = (4 * 1024, 64 * 1024, 2 * 1024 * 1024 + 64 * 1024)


class SealError(ValueError):
    """A sealed result is malformed, forged, or for a different request or key."""


def new_reply_key() -> bytes:
    """A fresh reply key.

    Returns:
        32 random bytes.
    """
    return secrets.token_bytes(KEY_BYTES)


def _subkey(reply_key: bytes, label: bytes) -> bytes:
    return hmac.new(reply_key, label, hashlib.sha256).digest()


def _keystream(enc_key: bytes, iv: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(enc_key, iv + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def _tag(mac_key: bytes, request_id: str, iv: bytes, ciphertext: bytes) -> bytes:
    return hmac.new(mac_key, MAGIC + request_id.encode("ascii") + iv + ciphertext, hashlib.sha256).digest()


def _check_key(reply_key: bytes) -> None:
    if not isinstance(reply_key, bytes) or len(reply_key) != KEY_BYTES:
        raise SealError("a reply key is 32 bytes")


def seal(reply_key: bytes, request_id: str, data: bytes) -> bytes:
    """Encrypt and authenticate *data* for the holder of *reply_key*, bound to *request_id*.

    Args:
        reply_key: The requesting process's 32-byte key.
        request_id: The request id (ASCII); a blob for one request can't be passed off as another's.
        data: The serialized result.

    Returns:
        ``MAGIC || IV || ciphertext || tag``, where the ciphertext length is a fixed bucket.

    Raises:
        SealError: On a bad key, or *data* larger than the largest bucket.
    """
    _check_key(reply_key)
    plain_len = _LEN_BYTES + len(data)
    bucket = next((b for b in BUCKETS if plain_len <= b), None)
    if bucket is None:
        raise SealError("result too large to seal")
    plaintext = len(data).to_bytes(_LEN_BYTES, "big") + data + b"\0" * (bucket - plain_len)
    iv = secrets.token_bytes(_IV_BYTES)
    stream = _keystream(_subkey(reply_key, b"agentteams-seal-enc-v1"), iv, bucket)
    ciphertext = _xor(plaintext, stream)
    return MAGIC + iv + ciphertext + _tag(_subkey(reply_key, b"agentteams-seal-mac-v1"), request_id, iv, ciphertext)


def open_sealed(reply_key: bytes, request_id: str, blob: bytes) -> bytes:
    """Verify and decrypt a blob from :func:`seal`.

    Args:
        reply_key: The key the request was sent with.
        request_id: The request the blob must belong to.
        blob: The sealed bytes.

    Returns:
        The original data.

    Raises:
        SealError: When the blob is malformed, its tag doesn't verify (forged, tampered, another request's or
            another key's), or its length field is inconsistent.
    """
    _check_key(reply_key)
    head = len(MAGIC) + _IV_BYTES
    if not blob.startswith(MAGIC) or len(blob) < head + _LEN_BYTES + _TAG_BYTES:
        raise SealError("not a sealed result")
    iv, ciphertext, tag = blob[len(MAGIC):head], blob[head:-_TAG_BYTES], blob[-_TAG_BYTES:]
    if len(ciphertext) not in BUCKETS:
        raise SealError("not a sealed result")
    expected = _tag(_subkey(reply_key, b"agentteams-seal-mac-v1"), request_id, iv, ciphertext)
    if not hmac.compare_digest(tag, expected):
        raise SealError("sealed result failed verification (forged, or not this request's)")
    stream = _keystream(_subkey(reply_key, b"agentteams-seal-enc-v1"), iv, len(ciphertext))
    plaintext = _xor(ciphertext, stream)
    length = int.from_bytes(plaintext[:_LEN_BYTES], "big")
    if length > len(plaintext) - _LEN_BYTES:
        raise SealError("sealed result failed verification")
    return plaintext[_LEN_BYTES:_LEN_BYTES + length]
