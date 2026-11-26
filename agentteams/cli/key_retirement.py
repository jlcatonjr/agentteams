"""key_retirement.py — retire an Ed25519 verify key without revoking what it already signed.

Removing ``<key_id>.pub.pem`` from a team's ``references/authorized-verify-keys`` store revokes the key and every
signature under it. Keeping the file lets the key keep signing. A **retirement record**,
``<key_id>.retired.json`` beside the public key, is the third option: the signatures the key made before it was
retired keep verifying, and no other signature under it does.

* **The control is a signature snapshot.** The record lists the sha256 of the decoded bytes of every signature the
  old key made (``signature_sha256``). A signature under a retired key verifies only when its digest is listed; a
  new one never is. ``not_after`` (the retirement date) is recorded and reported, never compared: many signed
  documents carry no date, and an unsigned date can be backdated.
* **The record is signed by another key.** ``signed_by`` must name a different key in the same store that has no
  retirement record of its own. A record signed by the retired key itself would let whoever holds that key extend
  its own snapshot, so it is refused. Adding a digest, removing one or changing any field breaks the signature.
* **Fail-closed.** A record that is malformed, a symbolic link, unreadable, signed by an absent or retired key, or
  whose signature does not verify refuses every signature under the retired key: today's revocation.
* **Domain separation.** The payload starts with :data:`PURPOSE`. The decision minter and verifier refuse a
  payload starting with it, and every other signed document starts with its own tag, so no retirement signature
  verifies as a decision, grant, carve-out registry, toolchain group or dev-merge authorization, and none of
  theirs verifies as a retirement record.
* **Shrink-only, reported.** ``--verify-integrity`` prints one line per record and warns when the record lists a
  digest its committed copy does not. The check is not enforced at verify time; whoever holds the signing key can
  widen a snapshot, and the warning is how the operator sees it.

``assert_signature_permitted`` runs inside ``decision_log._load_verify_key``, so every Ed25519 verify path (and
the verify-before-write of every signer) applies it. This module is integrity-pinned and in the signing closure.
Stdlib only (``cryptography`` is the lazily imported ``signing`` extra).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import stat
import sys
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


def _verify_doc(team_dir: Path, doc: dict) -> None:
    """Verify a validated record's signature under its signer (raises RuntimeError)."""
    from agentteams.cli.decision_log import _load_verify_key
    from agentteams.cli.signed_ledger import ed25519_verify

    signer = doc["signed_by"]
    if lstat_or_none(record_path(team_dir, signer), "retirement record") is not None:
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
    """Load and verify ``key_id``'s retirement record, or None when the key has none.

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
    """Refuse a signature under a retired key unless the key's verified snapshot lists it.

    Called by ``decision_log._load_verify_key`` before it returns a public key, so every Ed25519 verify applies it.
    A key with no retirement record is unaffected.

    Args:
        team_dir: The team dir whose store holds the key.
        key_id: The (already validated) key id the signature claims.
        signature: The hex signature about to be verified.

    Raises:
        RuntimeError: The key is retired and the record does not verify, or the signature is not in its snapshot.
    """
    try:
        record = verified_record(team_dir, key_id)
    except RuntimeError as exc:
        raise RuntimeError(f"verify key {key_id!r} has a retirement record that cannot be trusted: {exc}; every "
                           "signature under it is refused (fail-closed)") from exc
    if record is not None and signature_digest(signature) not in record["signature_sha256"]:
        raise RuntimeError(f"verify key {key_id!r} is retired (not after {record['not_after']}); this signature "
                           "is not in its operator-signed retirement snapshot (fail-closed)")


# ---------------------------------------------------------------------------------- the operator verb and the report


def _project_root(team_dir: Path) -> Path | None:
    """The project root when ``team_dir`` is one of the known team dirs under it, else None."""
    from agentteams.carveouts import TEAM_DIRS

    resolved = Path(team_dir).resolve()
    for rel in TEAM_DIRS:
        if resolved.as_posix().endswith("/" + rel):
            return resolved.parents[len(Path(rel).parts) - 1]
    return None


def _signatures_in(path: Path, key_id: str) -> list[tuple[str, str]]:
    """``(label, signature)`` for each Ed25519 signature under ``key_id`` in a CSV log or a signed JSON file."""
    from agentteams.atomicio import read_regular_nofollow

    try:
        text = read_regular_nofollow(path, _MAX_BYTES).decode("utf-8")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{path} cannot be read: {exc}") from exc
    found: list[tuple[str, str]] = []
    if path.suffix == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if ((row.get("key_id") or "").strip() == key_id and (row.get("sig_scheme") or "").strip().lower()
                    == "ed25519" and (row.get("signature") or "").strip()):
                what = " ".join(filter(None, ((row.get(c) or "").strip() for c in
                                              ("date", "action_reviewed", "verdict", "grant_id", "target_path"))))
                found.append((f"{path.name} line {reader.line_num}: {what}", row["signature"].strip()))
        return found
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"{path} is not JSON: {exc}") from exc
    sig = doc.get("signature") if isinstance(doc, dict) else None
    if isinstance(sig, str) and doc.get("key_id") == key_id:
        found.append((path.name, sig))
    elif isinstance(sig, dict) and sig.get("key_id") == key_id and isinstance(sig.get("value"), str):
        found.append((path.name, sig["value"]))
    return found


def collect_signatures(team_dir: Path, key_id: str, includes: list[str]) -> list[tuple[str, str]]:
    """Every signature under ``key_id`` the snapshot should keep: ``(label, digest)`` pairs, in source order.

    Sources: the team's decisions log; at the project root (when ``team_dir`` is a known team dir), the capability
    grants log, the carve-out registry, the toolchain group and the dev-merge authorization; then each ``includes``
    file. Absent default sources are skipped; an include that is absent or holds no signature under the key refuses.

    Args:
        team_dir: The team dir.
        key_id: The key being retired.
        includes: Further signed CSV logs or JSON documents (``--include``).

    Returns:
        The labelled digests (a signature listed twice appears twice).

    Raises:
        RuntimeError: A source cannot be read, an include holds no signature under the key, or a signature is
            not hex.
    """
    from agentteams.carveouts import REGISTRY_REL
    from agentteams.cli.dev_merge import AUTH_FILE_REL
    from agentteams.cli.grants import GRANT_LOG_REL
    from agentteams.cli.toolchain_group import GROUP_FILE_REL

    defaults = [Path(team_dir) / "references" / "security-decisions.log.csv"]
    root = _project_root(team_dir)
    if root is not None:
        defaults += [root / rel for rel in (GRANT_LOG_REL, REGISTRY_REL, GROUP_FILE_REL, AUTH_FILE_REL)]
    pairs: list[tuple[str, str]] = []
    for path in defaults:
        if lstat_or_none(path, "signed source") is not None:
            pairs += _signatures_in(path, key_id)
    for name in includes:
        found = _signatures_in(Path(name), key_id)
        if not found:
            raise RuntimeError(f"--include {name} holds no Ed25519 signature under key {key_id!r}")
        pairs += found
    labelled: list[tuple[str, str]] = []
    for label, sig in pairs:
        digest = signature_digest(sig)
        if digest is None:
            raise RuntimeError(f"{label}: the signature is not hex")
        labelled.append((label, digest))
    return labelled


def _fingerprint(team_dir: Path, key_id: str) -> str:
    """The sha256 of ``key_id``'s public key file, for the operator to recognise."""
    try:
        return hashlib.sha256((Path(team_dir) / STORE_REL / f"{key_id}.pub.pem").read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _pre_key_refusal(old: str, new: str, team_dir: Path) -> str | None:
    """Why the verb refuses before anything is collected or the key is located, or None."""
    from agentteams.cli.dev_merge import agent_session
    from agentteams.cli.sign_decision_refusals import sign_decision_team_refusal

    host = agent_session()
    if host:
        return f"refusing to sign inside an agent session ({host}); run this from your own terminal"
    if not (_KEY_ID_RE.match(old or "") and _KEY_ID_RE.match(new or "")) or old == new:
        return "--retire-verify-key and --key-id must be two different key ids (letters, digits, '.', '_', '-')"
    team = sign_decision_team_refusal(team_dir)
    if team:
        return team
    for key_id in (old, new):
        if lstat_or_none(Path(team_dir) / STORE_REL / f"{key_id}.pub.pem", "verify key") is None:
            return f"there is no {STORE_REL}/{key_id}.pub.pem in {team_dir}"
    if lstat_or_none(record_path(team_dir, new), "retirement record") is not None:
        return f"key {new!r} is itself retired, so it cannot sign a retirement record"
    return None


def retire_verify_key(old: str, new: str, team_dir: Path, includes: list[str], *,
                      confirm_sha256: str | None = None, allow_checkout: bool = False) -> int:
    """Operator-only: write ``<old>.retired.json``, the snapshot of ``old``'s signatures, signed with key ``new``.

    Shares ``--sign-toolchain-group``'s path: pre-key refusals, display, review digest and confirmation, preflight
    (integrity, key file, install location), sign, then verify against ``team_dir``'s store before writing.

    Args:
        old: The key being retired.
        new: The key that signs the record (not retired, present in the store).
        team_dir: The team dir whose store holds both keys.
        includes: Further signed files whose signatures under ``old`` the snapshot keeps.
        confirm_sha256: ``--confirm-review-sha256`` (no terminal); None prompts on a terminal.
        allow_checkout: ``--allow-checkout-signing``.

    Returns:
        0 when signed and written, 1 otherwise (fail-closed).
    """
    from agentteams.atomicio import replace_no_follow
    from agentteams.cli import operator_signing as ops
    from agentteams.cli import signed_ledger as sl

    team_dir = Path(team_dir)
    refusal = _pre_key_refusal(old, new, team_dir)
    try:
        labelled = [] if refusal else collect_signatures(team_dir, old, includes)
    except RuntimeError as exc:
        refusal = str(exc)
    spoof = None if refusal else ops._spoofing_problem([label for label, _ in labelled], "source labels")
    if refusal or spoof:
        print(f"Error: {refusal or spoof}. The key was not read.", file=sys.stderr)
        return 1
    record = {"key_id": old, "not_after": date.today().isoformat(), "signed_by": new,
              "signature_sha256": sorted({digest for _, digest in labelled})}
    preflight = ops._signing_preflight(ops._project_roots(team_dir), allow_checkout, new)
    if preflight is None:
        return 1
    keyfile, _overridden = preflight
    print(f"About to retire verify key {old!r}: from now on only the {len(record['signature_sha256'])} signature(s) "
          "below verify under it, anywhere this team's store is read.")
    print(f"  retired key : {old}  (public key sha256 {_fingerprint(team_dir, old)})")
    print(f"  signing key : {new}  (public key sha256 {_fingerprint(team_dir, new)})")
    print(f"  team dir    : {team_dir.resolve()}")
    print(f"  not_after   : {record['not_after']}")
    for label, digest in labelled:
        print(f"  keeps       : {digest[:16]}  {label}")
    print(f"  values      : {json.dumps(signed_values(record), ensure_ascii=True)}")
    if not ops._confirm(ops.review_digest("key-retirement", record, team_dir, new), confirm_sha256):
        return 1
    private_pem = ops._read_operator_private_key(keyfile)
    if private_pem is None:
        return 1
    try:
        record["signature"] = sl.ed25519_sign(private_pem, signed_values(record))
        _verify_doc(team_dir, validate(record, old))
        replace_no_follow(record_path(team_dir, old),
                          json.dumps({k: record[k] for k in _FIELDS}, indent=2, ensure_ascii=True) + "\n")
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}. Nothing written.", file=sys.stderr)
        return 1
    print(f"\nWrote {record_path(team_dir, old)}. Commit it where the team dir is tracked; keep {old}.pub.pem.")
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--retire-verify-key`` and ``--include`` (``--key-id`` and ``--team-dir`` are shared with other modes).

    Args:
        parser: The agentteams argument parser.

    Returns:
        None.
    """
    parser.add_argument(
        "--retire-verify-key", dest="retire_verify_key", default=None, metavar="OLD",
        help=("Operator-only: retire verify key OLD without revoking what it already signed. Writes "
              "<team-dir>/references/authorized-verify-keys/OLD.retired.json, the sha256 of every signature under OLD "
              "in the team's decisions log, the project's grants log, carve-out registry, toolchain group and dev-merge "
              "authorization, and each --include file, signed with --key-id NEW (a different key that is not "
              "retired), after the same preflight, review digest and confirmation as --sign-decision. Afterwards a "
              "signature under OLD verifies only if it is listed; new signing under OLD is refused."))
    parser.add_argument(
        "--include", dest="include", action="append", default=None, metavar="FILE",
        help=("With --retire-verify-key (repeatable): another signed CSV log or JSON document whose signatures under "
              "OLD the retirement keeps; it must hold at least one."))


def run_retire_verify_key(args: argparse.Namespace) -> int:
    """``--retire-verify-key OLD --key-id NEW --team-dir DIR [--include FILE ...]``: the CLI runner.

    Args:
        args: The parsed CLI namespace.

    Returns:
        2 when ``--key-id`` or ``--team-dir`` is missing, else :func:`retire_verify_key`'s exit code.
    """
    if not getattr(args, "key_id", None) or not getattr(args, "team_dir", None):
        print("Error: --retire-verify-key needs --key-id NEW (the signing key) and --team-dir DIR", file=sys.stderr)
        return 2
    return retire_verify_key(args.retire_verify_key, args.key_id, Path(args.team_dir), list(args.include or []),
                             confirm_sha256=getattr(args, "confirm_review_sha256", None),
                             allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))


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


def report_lines(team_dir: Path) -> list[str]:
    """``--verify-integrity``'s lines: one per retirement record in ``team_dir``'s store (advisory).

    Args:
        team_dir: The team dir.

    Returns:
        Each record's key, ``not_after``, snapshot size, signer and whether it verifies, plus a warning when the
        record lists digests its committed copy does not (the snapshot should only shrink).
    """
    store = Path(team_dir) / STORE_REL
    try:
        paths = sorted(store.glob(f"*{RECORD_SUFFIX}"))
    except OSError:
        return []
    lines: list[str] = []
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


__all__ = ["PURPOSE", "RECORD_SUFFIX", "add_arguments", "assert_signature_permitted", "collect_signatures",
           "payload_claims_retirement_purpose", "record_path", "report_lines", "retire_verify_key",
           "run_retire_verify_key", "signature_digest", "signed_values", "validate", "verified_record"]
