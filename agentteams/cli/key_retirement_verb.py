"""key_retirement_verb.py — ``--retire-verify-key``: the operator verb that writes a retirement record.

The record (:mod:`agentteams.cli.key_retirement`) lists the sha256 of every signature the retiring key made. This
module finds those signatures and checks each one before the snapshot keeps it: a row of the team's decisions log
can be written by an agent, so a hex string sitting in a row that names the old key is not evidence that the key
signed that row. Each candidate is verified under the old key against the values its own document signs (a
decision row, a capability grant, the carve-out registry, a toolchain group or a dev-merge authorization). One that
does not verify, or a file of any other kind, refuses the retirement and is named.

It runs only in a generated team dir (``.claude/agents``, ``.github/agents``, ...), whose record every other store of
the project reads (:func:`agentteams.cli.key_retirement.scan_dirs`); a gates-only team dir is refused. When the key
being retired signed other keys' retirement records, the same run re-signs each of them with the new key, snapshot
unchanged, so retiring a signer never silently revokes what those records kept. Integrity-pinned and in the signing closure: it runs in the signing process.
Stdlib only (``cryptography`` is the lazily imported ``signing`` extra).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import date
from pathlib import Path

from agentteams.cli import key_retirement as kr
from agentteams.cli.clearance_binding import lstat_or_none


def _decision_candidates(rows: list[dict[str, str]], name: str, lines: list[int]) -> list[tuple]:
    """Decision rows: label, signature and ``decision_log._decision_signature_values``."""
    from agentteams.cli import decision_log as dl

    return [(f"decision {(r.get('action_reviewed') or '').strip()} {(r.get('verdict') or '').strip()} "
             f"({name} line {n})", r["signature"].strip(), dl._decision_signature_values(r))
            for r, n in zip(rows, lines)]


def _grant_candidates(rows: list[dict[str, str]], name: str, lines: list[int]) -> list[tuple]:
    """Grant rows: label, signature and the grant's Ed25519 payload values."""
    from agentteams.cli import grants

    return [(f"grant {(r.get('grant_id') or '').strip()} ({name} line {n})", r["signature"].strip(),
             grants._ed25519_signed_values(r)) for r, n in zip(rows, lines)]


def _csv_candidates(path: Path, text: str, key_id: str) -> list[tuple[str, str, list[str]]]:
    """Candidates from a decisions log or a capability-grants log."""
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or ())
    if "grant_id" in fields:
        build = _grant_candidates
    elif "action_reviewed" in fields:
        build = _decision_candidates
    else:
        raise RuntimeError(f"{path} is not a document type the retirement can verify (a decisions log or a "
                           "capability-grants log)")
    rows: list[dict[str, str]] = []
    lines: list[int] = []
    for row in reader:
        if ((row.get("key_id") or "").strip() == key_id and (row.get("sig_scheme") or "").strip().lower()
                == "ed25519" and (row.get("signature") or "").strip()):
            rows.append(row)
            lines.append(reader.line_num)
    return build(rows, path.name, lines)


def _json_candidates(path: Path, doc: object, key_id: str) -> list[tuple[str, str, list[str]]]:
    """Candidates from a carve-out registry, a toolchain group or a dev-merge authorization."""
    from agentteams import carveouts
    from agentteams.cli import dev_merge
    from agentteams.cli import toolchain_group as tg

    purpose = doc.get("purpose") if isinstance(doc, dict) else None
    if purpose == tg.PURPOSE:
        tg.validate(doc)
        return ([(f"toolchain group sequence {doc['sequence']} ({path.name})", doc["signature"],
                  tg.signed_values(doc))] if doc["key_id"] == key_id else [])
    if purpose == dev_merge.PURPOSE:
        dev_merge.validate(doc)
        return ([(f"dev-merge authorization {doc['branch']} sequence {doc['sequence']} ({path.name})",
                  doc["signature"], dev_merge.signed_values(doc))] if doc["key_id"] == key_id else [])
    sig = doc.get("signature") if isinstance(doc, dict) else None
    if isinstance(doc, dict) and "carveouts" in doc and isinstance(sig, dict):
        if sig.get("key_id") != key_id or not isinstance(sig.get("value"), str):
            return []
        body = {k: v for k, v in doc.items() if k != "signature"}
        return [(f"carve-out registry sequence {doc.get('sequence')} ({path.name})", sig["value"],
                 carveouts.signed_fields(body, key_id))]
    raise RuntimeError(f"{path} is not a document type the retirement can verify (a carve-out registry, a "
                       "toolchain group or a dev-merge authorization)")


def _candidates(path: Path, key_id: str) -> list[tuple[str, str, list[str]]]:
    """``(label, signature, signed values)`` for each signature under ``key_id`` in one signed file."""
    from agentteams.atomicio import read_regular_nofollow

    try:
        text = read_regular_nofollow(path, kr.MAX_BYTES).decode("utf-8")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{path} cannot be read: {exc}") from exc
    if path.suffix == ".csv":
        return _csv_candidates(path, text, key_id)
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"{path} is not JSON: {exc}") from exc
    return _json_candidates(path, doc, key_id)


def collect_signatures(team_dir: Path, key_id: str, includes: list[str]) -> list[tuple[str, str]]:
    """Every signature under ``key_id`` the snapshot should keep, each verified: ``(label, digest)`` pairs.

    Sources: the team's decisions log; at the project root (when ``team_dir`` is a known team dir), the capability
    grants log, the carve-out registry, the toolchain group and the dev-merge authorization; then each
    ``includes`` file. Absent default sources are skipped. Each candidate must verify under ``key_id`` (through
    ``decision_log._load_verify_key``, so a key already retired keeps only what its snapshot lists) against the
    values its own document signs.

    Args:
        team_dir: The team dir.
        key_id: The key being retired.
        includes: Further signed files (``--retire-include``).

    Returns:
        The labelled digests (a signature found twice appears twice).

    Raises:
        RuntimeError: A source cannot be read or is of an unknown type, an include holds no signature under the
            key, or a signature does not verify against its document (the source is named).
    """
    from agentteams.carveouts import REGISTRY_REL
    from agentteams.cli import decision_log as dl
    from agentteams.cli import signed_ledger as sl
    from agentteams.cli.dev_merge import AUTH_FILE_REL
    from agentteams.cli.grants import GRANT_LOG_REL
    from agentteams.cli.toolchain_group import GROUP_FILE_REL

    defaults = [Path(team_dir) / "references" / "security-decisions.log.csv"]
    root = kr.project_root(team_dir)
    if root is not None:
        defaults += [root / rel for rel in (GRANT_LOG_REL, REGISTRY_REL, GROUP_FILE_REL, AUTH_FILE_REL)]
    found: list[tuple[str, str, list[str]]] = []
    for path in defaults:
        if lstat_or_none(path, "signed source") is not None:
            found += _candidates(path, key_id)
    for name in includes:
        more = _candidates(Path(name), key_id)
        if not more:
            raise RuntimeError(f"--retire-include {name} holds no Ed25519 signature under key {key_id!r}")
        found += more
    labelled: list[tuple[str, str]] = []
    for label, sig, values in found:
        try:
            ok = sl.ed25519_verify(dl._load_verify_key(Path(team_dir), key_id, signature=sig), values, sig)
        except (RuntimeError, ValueError) as exc:
            raise RuntimeError(f"{label}: the signature cannot be verified under {key_id!r}: {exc}") from exc
        digest = kr.signature_digest(sig)
        if not ok or digest is None:
            raise RuntimeError(f"{label}: the signature under {key_id!r} does not verify against this document, so "
                               "it is not kept; remove or repair it, then re-run")
        labelled.append((label, digest))
    return labelled


def _fingerprint(team_dir: Path, key_id: str) -> str:
    """The sha256 of ``key_id``'s public key file, for the operator to recognise."""
    try:
        return hashlib.sha256((Path(team_dir) / kr.STORE_REL / f"{key_id}.pub.pem").read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _resign_plan(team_dir: Path, old: str, new: str) -> list[tuple[Path, dict]]:
    """``(store, unsigned record)`` for each live record ``old`` signed, re-pointed at ``new`` (raises RuntimeError).

    Every such store must hold ``new`` (the same key bytes as in ``team_dir``), or the run is refused naming it.
    """
    store = Path(team_dir) / kr.STORE_REL
    new_key = kr.material(store / f"{new}.pub.pem")
    plan = []
    for d, doc in kr.records_signed_by(team_dir, kr.material(store / f"{old}.pub.pem")):
        there = d / kr.STORE_REL / f"{new}.pub.pem"
        if lstat_or_none(there, "verify key") is None or kr.material(there) != new_key:
            raise RuntimeError(f"{old!r} signed the retirement record of {doc['key_id']!r} in {d}, which would stop "
                               f"verifying once {old!r} is retired, and that store does not hold {new!r} to re-sign "
                               f"it; provision {new!r} there first")
        plan.append((d, {k: doc[k] for k in kr.RECORD_FIELDS if k != "signature"} | {"signed_by": new}))
    return plan


def _pre_key_refusal(old: str, new: str, team_dir: Path) -> str | None:
    """Why the verb refuses before anything is collected or the key is located, or None."""
    from agentteams.cli.dev_merge import agent_session
    from agentteams.cli.sign_decision_refusals import sign_decision_team_refusal

    host = agent_session()
    if host:
        return f"refusing to sign inside an agent session ({host}); run this from your own terminal"
    if not (kr.KEY_ID_RE.match(old or "") and kr.KEY_ID_RE.match(new or "")) or old == new:
        return "--retire-verify-key and --key-id must be two different key ids (letters, digits, '.', '_', '-')"
    team = sign_decision_team_refusal(team_dir)
    if team:
        return team
    kr.binding(team_dir)  # a generated team dir of a named project, or RuntimeError
    for key_id in (old, new):
        if lstat_or_none(Path(team_dir) / kr.STORE_REL / f"{key_id}.pub.pem", "verify key") is None:
            return f"there is no {kr.STORE_REL}/{key_id}.pub.pem in {team_dir}"
    store = Path(team_dir) / kr.STORE_REL
    if kr.material(store / f"{old}.pub.pem") == kr.material(store / f"{new}.pub.pem"):
        return f"{old!r} and {new!r} are the same key under two names; sign with a different key"
    if kr.applicable_records(team_dir, kr.material(store / f"{new}.pub.pem")):
        return f"key {new!r} is itself retired, so it cannot sign a retirement record"
    return None


def retire_verify_key(old: str, new: str, team_dir: Path, includes: list[str], *,
                      confirm_sha256: str | None = None, allow_checkout: bool = False) -> int:
    """Operator-only: write ``<old>.retired.json``, the verified snapshot of ``old``'s signatures, signed by ``new``.

    Uses ``--sign-decision``'s path, in this order: pre-key refusals (including every collected signature verified),
    preflight (integrity, key file, install location), display, review digest and confirmation, key read, signing
    (the new record, and each record ``old`` signed, re-signed by ``new``), then verification of each against its
    store before anything is written.

    Args:
        old: The key being retired.
        new: The key that signs the record (a different key, not retired, present in the store).
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
    labelled: list[tuple[str, str]] = []
    resign: list[tuple[Path, dict]] = []
    try:
        refusal = _pre_key_refusal(old, new, team_dir)
        labelled = [] if refusal else collect_signatures(team_dir, old, includes)
        resign = [] if refusal else _resign_plan(team_dir, old, new)
    except RuntimeError as exc:
        refusal = str(exc)
    spoof = None if refusal else ops._spoofing_problem([label for label, _ in labelled], "source labels")
    if refusal or spoof:
        print(f"Error: {refusal or spoof}. The key was not read.", file=sys.stderr)
        return 1
    record = {"key_id": old, "not_after": date.today().isoformat(), "signed_by": new, "binding": kr.binding(team_dir),
              "signature_sha256": sorted({digest for _, digest in labelled})}
    preflight = ops._signing_preflight(ops._project_roots(team_dir), allow_checkout, new)
    if preflight is None:
        return 1
    keyfile, _overridden = preflight
    print(f"About to retire verify key {old!r}: from now on only the {len(record['signature_sha256'])} signature(s) "
          "below verify under it, in this store and every team dir store of this project that holds the same key.")
    print(f"  retired key : {old}  (public key sha256 {_fingerprint(team_dir, old)})")
    print(f"  signing key : {new}  (public key sha256 {_fingerprint(team_dir, new)})")
    print(f"  team dir    : {team_dir.resolve()}")
    print(f"  binding     : {record['binding']}")
    print(f"  not_after   : {record['not_after']}")
    for label, digest in labelled:
        print(f"  keeps       : {digest[:16]}  {label}")
    for d, doc in resign:
        print(f"  re-signs    : the retirement record of {doc['key_id']!r} in {d}: snapshot unchanged "
              f"({len(doc['signature_sha256'])} signature(s)), signer {old} -> {new}")
    print(f"  values      : {json.dumps(kr.signed_values(record), ensure_ascii=True)}")
    spec = {"record": record, "re_signs": [[str(d.resolve()), doc] for d, doc in resign]}
    if not ops._confirm(ops.review_digest("key-retirement", spec, team_dir, new), confirm_sha256):
        return 1
    private_pem = ops._read_operator_private_key(keyfile)
    if private_pem is None:
        return 1
    try:
        writes = [(d, dict(doc)) for d, doc in resign] + [(team_dir, record)]
        for d, doc in writes:
            doc["signature"] = sl.ed25519_sign(private_pem, kr.signed_values(doc))
            kr.verify_signed_record(d, kr.validate(doc, doc["key_id"]))
        for d, doc in writes:  # the re-signed records first: they verify whether or not the new record lands
            replace_no_follow(kr.record_path(d, doc["key_id"]),
                              json.dumps({k: doc[k] for k in kr.RECORD_FIELDS}, indent=2, ensure_ascii=True) + "\n")
    except (OSError, RuntimeError) as exc:
        print(f"Error: {exc}. Nothing written.", file=sys.stderr)
        return 1
    print(f"\nWrote {kr.record_path(team_dir, old)}. Commit it where the team dir is tracked; keep {old}.pub.pem. "
          "Also remove the old key from ~/.config/agentteams/verify-keys/ if it is there (that store has no "
          "retirement records).")
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--retire-verify-key`` and ``--retire-include`` (``--key-id``, ``--team-dir`` are shared).

    Args:
        parser: The agentteams argument parser.

    Returns:
        None.
    """
    parser.add_argument(
        "--retire-verify-key", dest="retire_verify_key", default=None, metavar="OLD",
        help=("Operator-only: retire verify key OLD without revoking what it already signed. Writes "
              "<team-dir>/references/authorized-verify-keys/OLD.retired.json, the sha256 of every signature under OLD "
              "(each verified against its own document) in the team's decisions log, the project's grants log, "
              "carve-out registry, toolchain group and dev-merge authorization, and each --retire-include file, "
              "signed with --key-id NEW (a different key that is not retired), after the same preflight, review "
              "digest and confirmation as --sign-decision. Afterwards, in every team dir store of the project, a "
              "signature under OLD verifies only if it is listed, and new signing under OLD is refused. It does not "
              "cover ~/.config/agentteams/verify-keys/."))
    parser.add_argument(
        "--retire-include", dest="retire_include", action="append", default=None, metavar="FILE",
        help=("With --retire-verify-key (repeatable): another signed decisions log, capability-grants log, carve-out "
              "registry, toolchain group or dev-merge authorization whose signatures under OLD the retirement "
              "keeps; it must hold at least one, and each must verify."))


def run_retire_verify_key(args: argparse.Namespace) -> int:
    """``--retire-verify-key OLD --key-id NEW --team-dir DIR [--retire-include FILE ...]``: the CLI runner.

    Args:
        args: The parsed CLI namespace.

    Returns:
        2 when ``--key-id`` or ``--team-dir`` is missing, else :func:`retire_verify_key`'s exit code.
    """
    if not getattr(args, "key_id", None) or not getattr(args, "team_dir", None):
        print("Error: --retire-verify-key needs --key-id NEW (the signing key) and --team-dir DIR", file=sys.stderr)
        return 2
    return retire_verify_key(args.retire_verify_key, args.key_id, Path(args.team_dir),
                             list(args.retire_include or []),
                             confirm_sha256=getattr(args, "confirm_review_sha256", None),
                             allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))


__all__ = ["add_arguments", "collect_signatures", "retire_verify_key", "run_retire_verify_key"]
