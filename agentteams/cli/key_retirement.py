"""key_retirement.py — retire an Ed25519 verify key without revoking what it already signed.

Removing ``<key_id>.pub.pem`` from a team's ``references/authorized-verify-keys`` store revokes the key and every
signature under it. Keeping the file lets the key keep signing. A **retirement record**,
``<key_id>.retired.json`` beside the public key, is the third option: the signatures the key made before it was
retired keep verifying, and no other signature under it does.

* **The control is a signature snapshot.** The record lists the sha256 of the decoded bytes of every signature the
  old key made (``signature_sha256``). A signature under a retired key verifies only when its digest is listed; a
  new one never is. ``not_after`` (the retirement date) is recorded and reported, never compared: many signed
  documents carry no date, and an unsigned date can be backdated.
* **Several records narrow, never widen.** When more than one record retires the same key, a signature must be
  listed in every one, and each record is bound (``binding``) to its project and team dir, so a planted, older or
  replayed record can only refuse more.
* **Keys are compared by their bytes.** A record retires the public key its ``.pub.pem`` holds, so the same key
  stored under a second file name is retired too, and it binds in every team dir store :func:`scan_dirs` names
  (the generated team dirs of the project and a declared ``parent_team``), so a document that names a sibling
  store cannot escape it. Finding those stores runs on every verify: a symbolic link at a generated team dir path
  refuses every Ed25519 verify of the project, record or not, because the stores behind it cannot be checked.
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
from agentteams.cli.decision_log import _VERIFY_KEY_STORE_REL as STORE_REL

#: The signing-domain tag: the first signed value of every retirement payload.
PURPOSE = "agentteams-key-retirement-v1"
#: The record's file name suffix, beside ``<key_id>.pub.pem``.
RECORD_SUFFIX = ".retired.json"
#: A record's fields, in the order the verb writes them.
RECORD_FIELDS = ("key_id", "not_after", "signed_by", "binding", "signature_sha256", "signature")
#: A key id: a safe bare file stem (as ``decision_log._KEY_ID_RE``).
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
#: The largest record or signed source file read.
MAX_BYTES = 8 * 1024 * 1024

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_BINDING_RE = re.compile(r"^[a-z0-9][a-z0-9-]*:[A-Za-z0-9._/-]+$")


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
        ``[PURPOSE, key_id, not_after, signed_by, binding, <digests joined with ",">]``.
    """
    return [PURPOSE, record["key_id"], record["not_after"], record["signed_by"], record["binding"],
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


def validate(doc: object, key_id: str) -> dict:
    """Check a signed record's shape: exact fields, ids, date, binding, sorted unique digests and the signature.

    Args:
        doc: The parsed record.
        key_id: The retired key id the record must name (its file stem).

    Returns:
        ``doc``, unchanged.

    Raises:
        RuntimeError: Any field is missing, extra or malformed.
    """
    if not isinstance(doc, dict) or set(doc) != set(RECORD_FIELDS):
        raise RuntimeError(f"a retirement record must have exactly the fields {sorted(RECORD_FIELDS)}")
    if doc["key_id"] != key_id or not KEY_ID_RE.match(key_id):
        raise RuntimeError(f"the record names key {doc['key_id']!r}, not {key_id!r}")
    signer = doc["signed_by"]
    if not isinstance(signer, str) or not KEY_ID_RE.match(signer) or signer == key_id:
        raise RuntimeError("signed_by must name a different key id (a key cannot sign its own retirement)")
    not_after = doc["not_after"]
    try:
        if not isinstance(not_after, str) or not _DATE_RE.match(not_after):
            raise ValueError(not_after)
        date.fromisoformat(not_after)
    except ValueError as exc:
        raise RuntimeError(f"not_after must be a YYYY-MM-DD date, not {not_after!r}") from exc
    if not (isinstance(doc["binding"], str) and _BINDING_RE.match(doc["binding"])):
        raise RuntimeError("binding must be '<project>:<team dir>'")
    digests = doc["signature_sha256"]
    if (not isinstance(digests, list) or not all(isinstance(d, str) and _DIGEST_RE.match(d) for d in digests)
            or digests != sorted(set(digests))):
        raise RuntimeError("signature_sha256 must be a sorted list of distinct lowercase sha256 hex digests")
    if not (isinstance(doc["signature"], str) and _SIGNATURE_RE.match(doc["signature"])):
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
        return json.loads(read_regular_nofollow(path, MAX_BYTES).decode("utf-8"))
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


def project_root(team_dir: Path) -> Path | None:
    """The project root when ``team_dir`` is one of the generated team dirs under it, else None.

    Args:
        team_dir: A team dir.

    Returns:
        The root, or None for any other dir (a gates-only team dir, a bare directory).
    """
    # Unlike signing_switch._project_root, never "the parent" for a non-standard dir: a record is bound to a
    # generated team dir of a known project, so an unknown layout has no root at all.
    from agentteams.carveouts import TEAM_DIRS

    resolved = Path(team_dir).resolve()
    for rel in TEAM_DIRS:
        if resolved.as_posix().endswith("/" + rel):
            return resolved.parents[len(Path(rel).parts) - 1]
    return None


def binding(team_dir: Path) -> str:
    """What a record in ``team_dir``'s store is bound to: ``<project>:<team dir relative to the project root>``.

    ``<project>`` is the slug of ``project_name`` in the team dir's ``references/build-log.json`` (written by every
    generate and kept by a clone or a copy). A record copied to another team dir, or from another project, then
    names a binding that is not its store's, and is refused. This catches mistakes, not an adversary who can edit
    the build log; records intersect, so a record let in that way can still only narrow what verifies.

    Args:
        team_dir: A generated team dir.

    Returns:
        The binding string.

    Raises:
        RuntimeError: ``team_dir`` is not a generated team dir, or its build log has no usable ``project_name``.
    """
    from agentteams._utils import _slugify

    root = project_root(team_dir)
    if root is None:
        raise RuntimeError(f"{team_dir} is not a generated team dir (one of .claude/agents, .github/agents, ...), so "
                           "a retirement record there cannot be bound to its project")
    log = _read_json(Path(team_dir) / "references" / "build-log.json", "build log")
    project = _slugify(str(log.get("project_name", "") if isinstance(log, dict) else "")).strip("-")
    value = f"{project}:{Path(team_dir).resolve().relative_to(root).as_posix()}"
    if not _BINDING_RE.match(value):
        raise RuntimeError(f"{team_dir}'s build log has no usable project_name to bind a retirement record to")
    return value


def scan_dirs(team_dir: Path) -> list[Path]:
    """Every team dir whose retirement records bind a key verified in ``team_dir``.

    ``clearance_binding.halt_scan_dirs`` (``team_dir``, every generated team dir under each enclosing git root, and
    a declared ``parent_team``), plus the generated team dirs beside ``team_dir`` when it is one of them (outside
    git these are the only siblings, and a symbolic link in any of their paths refuses, as inside git). A record for a key in any of these stores binds the same key everywhere in
    the set, so a document cannot escape it by naming a sibling store.

    Args:
        team_dir: The team dir a signature is verified in.

    Returns:
        The dirs, ``team_dir`` first, without duplicates.

    Raises:
        RuntimeError: ``halt_scan_dirs`` refuses (a symbolic link in a team dir path, a bad ``parent_team``), or a
            sibling team dir path holds a symbolic link.
    """
    from agentteams.carveouts import TEAM_DIRS
    from agentteams.cli.clearance_binding import halt_scan_dirs

    dirs = list(halt_scan_dirs(Path(team_dir)))
    seen = {d.resolve() for d in dirs}
    root = project_root(team_dir)
    for rel in TEAM_DIRS if root is not None else ():
        candidate, st = root, None
        for part in Path(rel).parts:
            candidate = candidate / part
            st = lstat_or_none(candidate, "team dir")
            if st is None:
                break
            if stat.S_ISLNK(st.st_mode):
                raise RuntimeError(f"generated team dir path {candidate} is a symbolic link, so its retirement "
                                   "records cannot be checked; refusing (fail-closed)")
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
            if KEY_ID_RE.match(rid) and lstat_or_none(pem, "verify key") is not None and material(pem) == key:
                found.append((d, rid))
    return found


def verify_signed_record(team_dir: Path, doc: dict) -> None:
    """Verify a validated record in ``team_dir``'s store: its binding and its signature under its signer.

    The binding must be ``team_dir``'s (:func:`binding`). The signer must be a different key by its bytes, not
    only by its id, and no record anywhere in :func:`scan_dirs` may retire it.

    Args:
        team_dir: The team dir whose store holds (or will hold) the record.
        doc: A record that passed :func:`validate`.

    Raises:
        RuntimeError: The binding differs, the signer is absent, the same key or retired, or the signature fails.
    """
    from agentteams.cli.decision_log import _load_verify_key
    from agentteams.cli.signed_ledger import ed25519_verify

    here = binding(team_dir)
    if doc["binding"] != here:
        raise RuntimeError(f"it is bound to {doc['binding']!r}, not to this store ({here!r}): a record moved or "
                           "replayed from another team dir or project")
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
    verify_signed_record(team_dir, doc)
    return doc


def assert_signature_permitted(team_dir: Path, key_id: str, signature: str) -> None:
    """Refuse a signature under a retired key unless a verified snapshot for that key lists it.

    Called by ``decision_log._load_verify_key`` before it returns a public key, so every Ed25519 verify applies it.
    The key is matched by its bytes, in every store :func:`scan_dirs` names; a key no record retires is unaffected.
    Every applicable record must verify, and the signature must be listed in every one of them, so a second record
    (planted, older or replayed) can only narrow what verifies.

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
    kept: set[str] | None = None
    for d, rid in records:
        try:
            doc = verified_record(d, rid)
        except RuntimeError as exc:
            raise RuntimeError(f"verify key {key_id!r} has a retirement record ({rid!r} in {d}) that cannot be "
                               f"trusted: {exc}; every signature under it is refused (fail-closed)") from exc
        listed = set(doc["signature_sha256"]) if doc else set()
        kept = listed if kept is None else kept & listed
    if records and signature_digest(signature) not in (kept or set()):
        first = records[0][1]
        alias = "" if first == key_id else f", retired as {first!r}"
        raise RuntimeError(f"verify key {key_id!r} is retired{alias}; this signature is not in its operator-signed "
                           "retirement snapshot (fail-closed)")


# ---------------------------------------------------------------------------------------------------- the report


def committed_digests(path: Path) -> set[str] | None:
    """The digests of a record's committed (HEAD) copy, read with hardened git.

    Args:
        path: The record's path on disk.

    Returns:
        The committed copy's digests, or None when git has no committed copy (untracked, or no repository).
    """
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


def records_signed_by(team_dir: Path, key: bytes) -> list[tuple[Path, dict]]:
    """``(team dir, record)`` for every verifying record in :func:`scan_dirs` whose signer holds the key ``key``.

    Retiring that key would make each of them fail its "signer not retired" check, refusing everything it kept, so
    the verb re-signs them with the new key in the same run.

    Args:
        team_dir: The team dir the verb runs in.
        key: The raw public key bytes of the key being retired.

    Returns:
        The records, each verified.

    Raises:
        RuntimeError: A store or key file cannot be read, or any record in those stores does not verify.
    """
    found: list[tuple[Path, dict]] = []
    for d in scan_dirs(team_dir):
        for path in _record_paths(d):
            rid = path.name[: -len(RECORD_SUFFIX)]
            try:
                doc = verified_record(d, rid)
            except RuntimeError as exc:
                raise RuntimeError(f"the retirement record of {rid!r} in {d} does not verify ({exc}); repair or "
                                   "remove it (an operator act) before retiring another key") from exc
            if doc is not None and material(d / STORE_REL / f"{doc['signed_by']}.pub.pem") == key:
                found.append((d, doc))
    return found


def _store_lines(team_dir: Path, label: str) -> list[str]:
    """The report lines for one store (``label`` names it when it is not the ``--output`` one)."""
    lines = [f"  WARNING: {name}{label} is committed but missing on disk, so the key it retired is no longer retired "
             "here. Removing a record is a governance event for the operator; restore it with git checkout HEAD -- "
             f"{STORE_REL}/{name} unless the operator removed it." for name in _committed_missing(team_dir)]
    for path in _record_paths(team_dir):
        key_id = path.name[: -len(RECORD_SUFFIX)]
        try:
            doc = validate(_read_json(path, "retirement record"), key_id)
            verify_signed_record(Path(team_dir), doc)
            verdict = "verifies"
        except RuntimeError as exc:
            doc, verdict = None, f"DOES NOT VERIFY ({exc}): every signature under this key is refused"
        if doc is None:
            lines.append(f"Retired verify key {key_id}{label}: {verdict}")
            continue
        lines.append(f"Retired verify key {key_id}{label}: not after {doc['not_after']}, "
                     f"{len(doc['signature_sha256'])} signature(s) kept, signed by {doc['signed_by']}, {verdict}")
        committed = committed_digests(path)
        if committed is None:
            lines[-1] += " (no committed copy, so the shrink-only check did not run)"
        added = set(doc["signature_sha256"]) - committed if committed is not None else set()
        if added:
            lines.append(f"  WARNING: {len(added)} digest(s) added since the committed copy; a retirement snapshot "
                         "should only shrink. Check who re-signed it.")
        if lstat_or_none(Path(team_dir) / STORE_REL / f"{key_id}.pub.pem", "verify key") is None:
            lines.append(f"  WARNING: {key_id}.pub.pem is missing beside this record, so it retires nothing: the same "
                         "key under another name or in another store is no longer retired. Restore the file.")
    return lines


def report_lines(team_dir: Path) -> list[str]:
    """``--verify-integrity``'s lines: one per retirement record in every store :func:`scan_dirs` names (advisory).

    Args:
        team_dir: The team dir (``--output``).

    Returns:
        Each record's key (and its store, when it is not ``team_dir``'s), ``not_after``, snapshot size, signer and
        whether it verifies; a warning when a record lists digests its committed copy does not (a snapshot should
        only shrink), for each committed record missing on disk (deleting a record un-retires its key), and for a
        record whose own ``.pub.pem`` is missing (it then retires nothing).
    """
    try:
        dirs = scan_dirs(team_dir)
    except RuntimeError as exc:
        return [f"Retired verify keys: the team dir stores cannot be listed ({exc}); every Ed25519 verify here "
                "refuses until this is fixed"]
    root = project_root(team_dir)
    lines: list[str] = []
    for d in dirs:
        label = ""
        if d.resolve() != Path(team_dir).resolve():
            rel = d.resolve().relative_to(root).as_posix() if root and root in d.resolve().parents else str(d)
            label = f" ({rel})"
        try:
            lines += _store_lines(d, label)
        except RuntimeError as exc:
            lines.append(f"Retired verify keys{label}: the store cannot be read ({exc})")
    return lines


__all__ = ["KEY_ID_RE", "MAX_BYTES", "PURPOSE", "RECORD_FIELDS", "RECORD_SUFFIX", "STORE_REL", "applicable_records",
           "assert_signature_permitted", "binding", "committed_digests", "material", "payload_claims_retirement_purpose", "project_root",
           "record_path", "records_signed_by", "report_lines", "scan_dirs", "signature_digest", "signed_values",
           "validate", "verified_record", "verify_signed_record"]
