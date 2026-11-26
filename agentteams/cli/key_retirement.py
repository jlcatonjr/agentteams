"""key_retirement.py — retire an Ed25519 verify key without revoking what it already signed.

Removing ``<key_id>.pub.pem`` from a team's ``references/authorized-verify-keys`` store revokes the key and every
signature under it. Keeping the file lets the key keep signing. A **retirement record**,
``<key_id>.retired.json`` beside the public key, is the third option: the signatures the key made before it was
retired keep verifying, and no other signature under it does.

* **The control is a signature snapshot.** The record lists the sha256 of the decoded bytes of every signature the
  old key made (``signature_sha256``). A signature under a retired key verifies only when its digest is listed; a
  new one never is. ``not_after`` (the retirement date) is recorded and reported, never compared: many signed
  documents carry no date, and an unsigned date can be backdated.
* **Keys are compared by their bytes.** A record retires the public key its ``.pub.pem`` holds, so the same key
  stored under a second file name is retired too, and it binds in every team dir store :func:`scan_dirs` names
  (the generated team dirs of the project and a declared ``parent_team``), so a document that names a sibling
  store cannot escape it.
* **The record is signed by another key.** ``signed_by`` must name a key in the same store whose bytes differ from
  the retired key's and that no record retires. A record signed by the retired key itself, under any name, would let
  whoever holds that key extend its own snapshot, so it is refused. Adding a digest, removing one or changing any
  field breaks the signature.
* **Fail-closed.** A record that is malformed, a symbolic link, unreadable, signed by an absent or retired key, or
  whose signature does not verify refuses every signature under the retired key: today's revocation.
* **Domain separation.** The payload starts with :data:`PURPOSE`. The decision minter and verifier refuse a
  payload starting with it, and every other signed document starts with its own tag, so no retirement signature
  verifies as a decision, grant, carve-out registry, toolchain group or dev-merge authorization, and none of
  theirs verifies as a retirement record.
* **Shrink-only, reported.** ``--verify-integrity`` prints one line per record, warns when the record lists a
  digest its committed copy does not, and warns when a committed record is missing on disk (deleting a record
  un-retires the key). Neither is enforced at verify time; whoever holds the signing key can widen a snapshot, and
  the warnings are how the operator sees it.
* **Not covered:** the user-level store ``~/.config/agentteams/verify-keys/`` (MCP-direct and vulnscan grants) has no
  retirement records; retiring a key there means removing its ``.pub.pem``.

``assert_signature_permitted`` runs inside ``decision_log._load_verify_key``, so every Ed25519 verify path (and
the verify-before-write of every signer) applies it. The operator verb is :mod:`agentteams.cli.key_retirement_verb`.
Both modules are integrity-pinned and in the signing closure.
Stdlib only (``cryptography`` is the lazily imported ``signing`` extra).
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from datetime import date
from pathlib import Path

from agentteams.cli.clearance_binding import lstat_or_none

#: The signing-domain tag: the first signed value of every retirement payload.
PURPOSE = "agentteams-key-retirement-v1"
#: The record's file name suffix, beside ``<key_id>.pub.pem``.
RECORD_SUFFIX = ".retired.json"
#: The verify-key store, relative to the team dir (as ``decision_log._VERIFY_KEY_STORE_REL``).
STORE_REL = "references/authorized-verify-keys"

_FIELDS = ("key_id", "not_after", "signed_by", "signature_sha256", "signature")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_MAX_BYTES = 8 * 1024 * 1024


def record_path(team_dir: Path, key_id: str) -> Path:
    """The retirement record's path for ``key_id`` in ``team_dir``'s verify-key store.

    Args:
        team_dir: The team dir.
        key_id: A key id (a safe bare file stem).

    Returns:
        ``<team_dir>/references/authorized-verify-keys/<key_id>.retired.json``.
    """
    return Path(team_dir) / STORE_REL / f"{key_id}{RECORD_SUFFIX}"


def signature_digest(signature: str) -> str | None:
    """The sha256 of a hex signature's decoded bytes, or None when it is not hex.

    Args:
        signature: A hex signature as stored.

    Returns:
        The lowercase hex digest, or None for an empty or non-hex value.
    """
    try:
        raw = bytes.fromhex((signature or "").strip())
    except ValueError:
        return None
    return hashlib.sha256(raw).hexdigest() if raw else None


def signed_values(record: dict) -> list[str]:
    """The ordered values a retirement record's signature covers.

    Args:
        record: A structurally valid record (:func:`validate`).

    Returns:
        ``[PURPOSE, key_id, not_after, signed_by, <digests joined with ",">]``.
    """
    return [PURPOSE, record["key_id"], record["not_after"], record["signed_by"],
            ",".join(record["signature_sha256"])]


def payload_claims_retirement_purpose(values: list[str]) -> bool:
    """True iff the canonical payload of ``values`` begins with the retirement purpose tag.

    Args:
        values: Ordered signed field values of some other document.

    Returns:
        Whether that payload lies in the key-retirement signing domain.
    """
    from agentteams.cli.signed_ledger import canonical_payload

    payload = canonical_payload(values)
    return payload == PURPOSE or payload.startswith(PURPOSE + "|")


def validate(doc: object, key_id: str, *, signed: bool = True) -> dict:
    """Check a record's shape: exact fields, ids, date, sorted unique digests and (``signed``) the signature.

    Args:
        doc: The parsed record.
        key_id: The retired key id the record must name (its file stem).
        signed: Whether the record must carry its signature.

    Returns:
        ``doc``, unchanged.

    Raises:
        RuntimeError: Any field is missing, extra or malformed.
    """
    fields = set(_FIELDS) if signed else set(_FIELDS) - {"signature"}
    if not isinstance(doc, dict) or set(doc) != fields:
        raise RuntimeError(f"a retirement record must have exactly the fields {sorted(fields)}")
    if doc["key_id"] != key_id or not _KEY_ID_RE.match(key_id):
        raise RuntimeError(f"the record names key {doc['key_id']!r}, not {key_id!r}")
    signer = doc["signed_by"]
    if not isinstance(signer, str) or not _KEY_ID_RE.match(signer) or signer == key_id:
        raise RuntimeError("signed_by must name a different key id (a key cannot sign its own retirement)")
    not_after = doc["not_after"]
    try:
        if not isinstance(not_after, str) or not _DATE_RE.match(not_after):
            raise ValueError(not_after)
        date.fromisoformat(not_after)
    except ValueError as exc:
        raise RuntimeError(f"not_after must be a YYYY-MM-DD date, not {not_after!r}") from exc
    digests = doc["signature_sha256"]
    if (not isinstance(digests, list) or not all(isinstance(d, str) and _DIGEST_RE.match(d) for d in digests)
            or digests != sorted(set(digests))):
        raise RuntimeError("signature_sha256 must be a sorted list of distinct lowercase sha256 hex digests")
    if signed and not (isinstance(doc["signature"], str) and _SIGNATURE_RE.match(doc["signature"])):
        raise RuntimeError("signature must be a lowercase hex Ed25519 signature")
    return doc


def _read_json(path: Path, what: str) -> object:
    """Parse a regular, non-link JSON file (raises RuntimeError)."""
    from agentteams.atomicio import read_regular_nofollow

    st = lstat_or_none(path, what)
    if st is None:
        raise RuntimeError(f"{what} {path} does not exist")
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"{what} {path} is a symbolic link")
    try:
        return json.loads(read_regular_nofollow(path, _MAX_BYTES).decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{what} {path} cannot be read: {exc}") from exc


def material(pem_path: Path) -> bytes:
    """The raw 32-byte Ed25519 public key in a ``.pub.pem`` file: what identifies a key, whatever its file name.

    Args:
        pem_path: A public key file.

    Returns:
        The raw public key bytes.

    Raises:
        RuntimeError: The file cannot be read, is not an Ed25519 public key, or the ``signing`` extra is absent.
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError("the Ed25519 backend (the 'signing' extra) is not installed, so a retired key cannot be "
                           "compared") from exc
    try:
        key = serialization.load_pem_public_key(Path(pem_path).read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"verify key {pem_path} cannot be read as a public key: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise RuntimeError(f"verify key {pem_path} is not an Ed25519 public key")
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _project_root(team_dir: Path) -> Path | None:
    """The project root when ``team_dir`` is one of the known team dirs under it, else None."""
    from agentteams.carveouts import TEAM_DIRS

    resolved = Path(team_dir).resolve()
    for rel in TEAM_DIRS:
        if resolved.as_posix().endswith("/" + rel):
            return resolved.parents[len(Path(rel).parts) - 1]
    return None


def scan_dirs(team_dir: Path) -> list[Path]:
    """Every team dir whose retirement records bind a key verified in ``team_dir``.

    ``clearance_binding.halt_scan_dirs`` (``team_dir``, every generated team dir under each enclosing git root, and
    a declared ``parent_team``), plus the generated team dirs beside ``team_dir`` when it is one of them and the
    project is not a git repository. A record for a key in any of these stores binds the same key everywhere in
    the set, so a document cannot escape it by naming a sibling store.

    Args:
        team_dir: The team dir a signature is verified in.

    Returns:
        The dirs, ``team_dir`` first, without duplicates.

    Raises:
        RuntimeError: ``halt_scan_dirs`` refuses (a symbolic link in a team dir path, a bad ``parent_team``).
    """
    from agentteams.carveouts import TEAM_DIRS
    from agentteams.cli.clearance_binding import halt_scan_dirs

    dirs = list(halt_scan_dirs(Path(team_dir)))
    seen = {d.resolve() for d in dirs}
    root = _project_root(team_dir)
    for rel in TEAM_DIRS if root is not None else ():
        candidate = root / rel
        st = lstat_or_none(candidate, "team dir")
        if st is not None and stat.S_ISDIR(st.st_mode) and candidate.resolve() not in seen:
            seen.add(candidate.resolve())
            dirs.append(candidate)
    return dirs


def _record_paths(team_dir: Path) -> list[Path]:
    """The retirement records in ``team_dir``'s store (none when the store is absent)."""
    store = Path(team_dir) / STORE_REL
    if lstat_or_none(store, "verify-key store") is None:
        return []
    try:
        return sorted(store.glob(f"*{RECORD_SUFFIX}"))
    except OSError as exc:
        raise RuntimeError(f"the verify-key store {store} cannot be listed: {exc}") from exc


def applicable_records(team_dir: Path, key: bytes) -> list[tuple[Path, str]]:
    """``(team dir, retired key id)`` for every record, in :func:`scan_dirs`, that retires the key ``key``.

    A record applies when the ``.pub.pem`` it names in its own store holds the same key bytes, so a second file
    name for a retired key is retired too.

    Args:
        team_dir: The team dir a signature is verified in.
        key: The raw public key bytes (:func:`material`).

    Returns:
        The applicable records.

    Raises:
        RuntimeError: A store or key file cannot be read.
    """
    found: list[tuple[Path, str]] = []
    for d in scan_dirs(team_dir):
        for path in _record_paths(d):
            rid = path.name[: -len(RECORD_SUFFIX)]
            pem = d / STORE_REL / f"{rid}.pub.pem"
            if _KEY_ID_RE.match(rid) and lstat_or_none(pem, "verify key") is not None and material(pem) == key:
                found.append((d, rid))
    return found


def _verify_doc(team_dir: Path, doc: dict) -> None:
    """Verify a validated record's signature under its signer (raises RuntimeError).

    The signer must be a different key by its bytes, not only by its id, and no record anywhere in
    :func:`scan_dirs` may retire it.
    """
    from agentteams.cli.decision_log import _load_verify_key
    from agentteams.cli.signed_ledger import ed25519_verify

    store = Path(team_dir) / STORE_REL
    signer = doc["signed_by"]
    signer_pem = store / f"{signer}.pub.pem"
    if lstat_or_none(signer_pem, "verify key") is None:
        raise RuntimeError(f"its signer {signer!r} has no {STORE_REL}/{signer}.pub.pem in {team_dir}")
    signer_key = material(signer_pem)
    retired_pem = store / f"{doc['key_id']}.pub.pem"
    if lstat_or_none(retired_pem, "verify key") is not None and material(retired_pem) == signer_key:
        raise RuntimeError(f"it is signed by {signer!r}, which is the same key as {doc['key_id']!r} under another "
                           "name; a key cannot sign its own retirement")
    if applicable_records(team_dir, signer_key):
        raise RuntimeError(f"it is signed by {signer!r}, which is itself retired; a retirement record must be "
                           "signed by a key that is not retired")
    public_pem = _load_verify_key(team_dir, signer, signature=doc["signature"])
    try:
        ok = ed25519_verify(public_pem, signed_values(doc), doc["signature"])
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f"its signature cannot be verified: {exc}") from exc
    if not ok:
        raise RuntimeError(f"its signature does not verify under {signer!r} (the record changed after signing, "
                           "or another key signed it)")


def verified_record(team_dir: Path, key_id: str) -> dict | None:
    """Load and verify ``key_id``'s retirement record in ``team_dir``'s store, or None when there is none.

    Args:
        team_dir: The team dir whose store holds the record.
        key_id: The key id.

    Returns:
        The verified record, or None.

    Raises:
        RuntimeError: A record exists but is a link, unreadable, malformed or does not verify.
    """
    path = record_path(team_dir, key_id)
    if lstat_or_none(path, "retirement record") is None:
        return None
    doc = validate(_read_json(path, "retirement record"), key_id)
    _verify_doc(team_dir, doc)
    return doc


def assert_signature_permitted(team_dir: Path, key_id: str, signature: str) -> None:
    """Refuse a signature under a retired key unless a verified snapshot for that key lists it.

    Called by ``decision_log._load_verify_key`` before it returns a public key, so every Ed25519 verify applies it.
    The key is matched by its bytes, in every store :func:`scan_dirs` names; a key no record retires is unaffected.
    Every applicable record must verify; the signature must be listed in one of them.

    Args:
        team_dir: The team dir whose store holds the key.
        key_id: The (already validated) key id the signature claims.
        signature: The hex signature about to be verified.

    Raises:
        RuntimeError: The key is retired and a record does not verify, or the signature is in no snapshot.
    """
    if not any(_record_paths(d) for d in scan_dirs(team_dir)):
        return
    records = applicable_records(team_dir, material(Path(team_dir) / STORE_REL / f"{key_id}.pub.pem"))
    kept: set[str] = set()
    for d, rid in records:
        try:
            doc = verified_record(d, rid)
        except RuntimeError as exc:
            raise RuntimeError(f"verify key {key_id!r} has a retirement record ({rid!r} in {d}) that cannot be "
                               f"trusted: {exc}; every signature under it is refused (fail-closed)") from exc
        kept.update(doc["signature_sha256"] if doc else ())
    if records and signature_digest(signature) not in kept:
        first = records[0][1]
        alias = "" if first == key_id else f", retired as {first!r}"
        raise RuntimeError(f"verify key {key_id!r} is retired{alias}; this signature is not in its operator-signed "
                           "retirement snapshot (fail-closed)")


# ---------------------------------------------------------------------------------------------------- the report


def _committed_digests(path: Path) -> set[str] | None:
    """The digests of the record's committed (HEAD) copy, or None when git has none."""
    from agentteams.git_exec import run_git

    try:
        res = run_git(path.parent, "show", f"HEAD:./{path.name}", hardened=True, timeout=10)
        doc = json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, ValueError):
        return None
    digests = doc.get("signature_sha256") if isinstance(doc, dict) else None
    return {d for d in digests if isinstance(d, str)} if isinstance(digests, list) else None


def _committed_missing(team_dir: Path) -> list[str]:
    """Record file names the committed (HEAD) tree holds in ``team_dir``'s store but the disk does not."""
    from agentteams.git_exec import run_git

    try:
        res = run_git(Path(team_dir), "ls-tree", "--name-only", "HEAD", "--", f"{STORE_REL}/", hardened=True,
                      timeout=10)
    except OSError:
        return []
    names = [Path(line).name for line in res.stdout.splitlines()] if res.returncode == 0 else []
    return [n for n in names if n.endswith(RECORD_SUFFIX)
            and lstat_or_none(Path(team_dir) / STORE_REL / n, "retirement record") is None]


def report_lines(team_dir: Path) -> list[str]:
    """``--verify-integrity``'s lines: one per retirement record in ``team_dir``'s store (advisory).

    Args:
        team_dir: The team dir.

    Returns:
        Each record's key, ``not_after``, snapshot size, signer and whether it verifies, a warning when the record
        lists digests its committed copy does not (the snapshot should only shrink), and a warning for each committed
        record missing on disk (deleting a record un-retires its key).
    """
    lines = [f"  WARNING: {name} is committed but missing on disk, so the key it retired is no longer retired "
             "here. Removing a record is a governance event for the operator; restore it with git checkout HEAD -- "
             f"{STORE_REL}/{name} unless the operator removed it." for name in _committed_missing(team_dir)]
    try:
        paths = _record_paths(team_dir)
    except RuntimeError as exc:
        return [*lines, f"Retired verify keys: the store cannot be listed ({exc})"]
    for path in paths:
        key_id = path.name[: -len(RECORD_SUFFIX)]
        try:
            doc = validate(_read_json(path, "retirement record"), key_id)
            _verify_doc(Path(team_dir), doc)
            verdict = "verifies"
        except RuntimeError as exc:
            doc, verdict = None, f"DOES NOT VERIFY ({exc}): every signature under this key is refused"
        if doc is None:
            lines.append(f"Retired verify key {key_id}: {verdict}")
            continue
        lines.append(f"Retired verify key {key_id}: not after {doc['not_after']}, "
                     f"{len(doc['signature_sha256'])} signature(s) kept, signed by {doc['signed_by']}, {verdict}")
        committed = _committed_digests(path)
        if committed is None:
            lines[-1] += " (no committed copy, so the shrink-only check did not run)"
        added = set(doc["signature_sha256"]) - committed if committed is not None else set()
        if added:
            lines.append(f"  WARNING: {len(added)} digest(s) added since the committed copy; a retirement snapshot "
                         "should only shrink. Check who re-signed it.")
    return lines


__all__ = ["PURPOSE", "RECORD_SUFFIX", "STORE_REL", "applicable_records", "assert_signature_permitted", "material",
           "payload_claims_retirement_purpose", "record_path", "report_lines", "scan_dirs", "signature_digest",
           "signed_values", "validate", "verified_record"]
