"""Standing remediation grants: the operator's signed, bounded pre-clearance for unattended work.

An autonomous agent team may *request* remediation actions. The out-of-sandbox executor
(:mod:`agentteams.vulnscan.executor`) performs one only when a grant covers it. A grant is the
operator's **recorded clearance before execution** (C-5) for a constraint-relaxing class of
action (Rule 15). Each grant is:

- **Ed25519-signed by the operator**, outside every agent session, through the same operator
  flow as MCP direct grants (display, then a review digest to confirm, then the key read once).
  It carries its own purpose tag, so the signature can never be replayed as another artifact.
- **Scoped** to named operations (:data:`OPS_V1`), repository roots and exact server host names.
- **Bounded**: it expires within :data:`MAX_DAYS` and allows at most ``max_uses`` actions,
  counted from the executor's MAC-chained ledger.
- **Capped**: more than :data:`MAX_ACTIVE` valid grants refuses them all, so relaxations cannot
  silently proliferate.

``stage-major`` (``stage-fix`` with semver-major upgrades allowed) is a constraint-relaxing op of its own
(Rule 15): it relaxes the "no major jump" bound of ``stage-fix``, so only a grant that lists it explicitly covers it;
a grant listing ``stage-fix`` never implies it.

``merge`` and ``server-apply`` are part of the schema for the planned full-automation stage, but
version 1 refuses to sign or honour them. Every use also re-checks for an unretracted @security
HALT (C-2), which no grant overrides.

Records live outside every project, in ``<config>/vulnscan-grants/<grant_id>.json``. The verify
keys live in the operator verify-key store shared with MCP direct grants.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths
from agentteams.vulnscan.ops import REPO_OPS

PURPOSE_TAG = "agentteams-vulnscan-remediation-grant-v1"
OPS_V1: frozenset[str] = frozenset({"stage-fix", "stage-major", "open-pr", "close-superseded", "server-audit",
                                    "server-dryrun"})
OPS_RESERVED: frozenset[str] = frozenset({"merge", "server-apply"})   # full-automation stage; refused in v1
MAX_DAYS = 30
MAX_USES = 500
MAX_ACTIVE = 2
FIELDS = ("grant_id", "issued", "expires", "ops", "repo_roots", "hosts", "max_uses", "key_id", "governance_dir")
_ID_RE = re.compile(r"^vg-[0-9a-f]{16}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class GrantError(ValueError):
    """A grant could not be built, verified or used."""


def grants_dir() -> Path:
    """Return the operator-owned grant directory.

    Returns:
        ``<config>/vulnscan-grants``.
    """
    return paths.config_dir() / "vulnscan-grants"


def signed_values(record: dict[str, Any]) -> list[str]:
    """Return the exact Ed25519 payload: purpose tag + canonical JSON of every bound field.

    Args:
        record: Grant record.

    Returns:
        Field list for ``signed_ledger.ed25519_sign``/``ed25519_verify``.

    Raises:
        KeyError: A bound field is missing.
    """
    body = {name: record[name] for name in FIELDS}
    return [PURPOSE_TAG, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def build(*, ops: list[str], repo_roots: list[str], hosts: list[str], days: int, max_uses: int,
          key_id: str, governance_dir: str, now: datetime | None = None) -> dict[str, Any]:
    """Build an unsigned grant, enforcing every v1 bound.

    Args:
        ops: Operations to allow (subset of :data:`OPS_V1`).
        repo_roots: Absolute directories whose repositories may be changed.
        hosts: Exact server-kit host names.
        days: Lifetime (1..MAX_DAYS).
        max_uses: Action cap (1..MAX_USES).
        key_id: Operator verify-key id.
        governance_dir: Team directory whose ``references/security-decisions.log.csv`` the executor
            checks for HALTs. It is signed into the grant, so unsigned config cannot redirect the check.
        now: Clock override (tests).

    Returns:
        The unsigned record.

    Raises:
        GrantError: Any bound is violated.
    """
    now = now or datetime.now(UTC)
    bad = sorted(set(ops) - OPS_V1)
    if bad:
        raise GrantError(f"operations not grantable in v1: {bad}"
                         + (" (reserved for the full-automation stage)" if set(bad) & OPS_RESERVED else ""))
    if not ops:
        raise GrantError("a grant needs at least one operation")
    if not 1 <= days <= MAX_DAYS:
        raise GrantError(f"days must be 1..{MAX_DAYS}")
    if not 1 <= max_uses <= MAX_USES:
        raise GrantError(f"max_uses must be 1..{MAX_USES}")
    if not _KEY_ID_RE.match(key_id):
        raise GrantError(f"malformed key_id {key_id!r}")
    roots = sorted({str(Path(r).expanduser().resolve()) for r in repo_roots})
    if any(r == str(Path.home().resolve()) or r == "/" for r in roots):
        raise GrantError("a repo root may not be / or the home directory itself")
    if any(op in ops for op in REPO_OPS) and not roots:
        raise GrantError(f"{'/'.join(REPO_OPS)} need at least one repo root")
    gov = Path(governance_dir).expanduser().resolve() if governance_dir else None
    if gov is None or not (gov / "references" / "security-decisions.log.csv").is_file():
        raise GrantError("governance_dir must be a team directory containing references/security-decisions.log.csv")
    hs = sorted(set(hosts))
    if any(not _HOST_RE.match(h) or h == "all" for h in hs):
        raise GrantError("hosts must be exact hosts.conf names (never 'all')")
    if any(op.startswith("server-") for op in ops) and not hs:
        raise GrantError("server operations need at least one host")
    return {"grant_id": "vg-" + secrets.token_hex(8),
            "issued": now.replace(microsecond=0).isoformat(),
            "expires": (now + timedelta(days=days)).replace(microsecond=0).isoformat(),
            "ops": sorted(ops), "repo_roots": roots, "hosts": hs, "max_uses": max_uses, "key_id": key_id,
            "governance_dir": str(gov)}


def operator_sign(record: dict[str, Any], *, digest_kind: str, banner: str, fields: tuple[str, ...],
                  values: Callable[[dict[str, Any]], list[str]], confirm_sha256: str | None = None,
                  allow_checkout: bool = False) -> dict[str, Any] | None:
    """Sign ``record`` with the operator's Ed25519 key through the standard operator flow.

    The one copy of the flow every vulnscan signature uses (grants, publish grants, escalation
    resolutions): environment and key-file preflight, the display (control characters stripped),
    the review-digest confirmation (terminal y/N or ``--confirm-review-sha256``), then a single
    read of the key. Each caller supplies its own purpose-tagged ``values``, so a signature for one
    kind of record never verifies as another.

    Args:
        record: Unsigned record (must carry ``key_id``).
        digest_kind: Review-digest kind label.
        banner: What the operator is about to authorise.
        fields: Field names to display.
        values: The record's signed payload (purpose tag first).
        confirm_sha256: Exact review digest, for non-terminal confirmation.
        allow_checkout: ``--allow-checkout-signing`` (as for other operator signatures).

    Returns:
        The signed record, or None after printing why nothing was signed.
    """
    from agentteams.cli import operator_signing as ops_
    from agentteams.cli.signed_ledger import ed25519_sign

    preflight = ops_._signing_preflight([Path.cwd().resolve()], allow_checkout)
    if preflight is None:
        return None
    print(banner)
    width = max(len(n) for n in fields)
    for name in fields:
        shown = "".join(ch if ch.isprintable() else "?" for ch in repr(record[name]))
        minted = "   (minted for this run; not in the review digest)" if name in ops_.MINTED_FIELDS else ""
        print(f"  {name:<{width}}: {shown}{minted}")
    # The digest covers what you chose (and the lifetime), not the per-run id and times, and is
    # independent of the working directory: a digest printed without a terminal confirms the next run.
    digest = ops_.review_digest(digest_kind, ops_.review_choices(record), paths.config_dir(), record["key_id"])
    if not ops_._confirm(digest, confirm_sha256):
        return None
    private_pem = ops_._read_operator_private_key(preflight[0])
    if private_pem is None:
        return None
    try:
        return {**record, "signature": ed25519_sign(private_pem, values(record))}
    except RuntimeError as exc:
        print(f"Error: cannot sign with the operator key: {exc}")
        return None


def sign(record: dict[str, Any], *, confirm_sha256: str | None = None,
         allow_checkout: bool = False) -> dict[str, Any] | None:
    """Sign a remediation grant through :func:`operator_sign`.

    Args:
        record: Unsigned grant from :func:`build`.
        confirm_sha256: Exact review digest, for non-terminal confirmation.
        allow_checkout: ``--allow-checkout-signing`` (as for other operator signatures).

    Returns:
        The signed record, or None after printing why nothing was signed.
    """
    return operator_sign(record, digest_kind="vulnscan-remediation-grant", fields=FIELDS, values=signed_values,
                         banner="About to sign a STANDING vulnerability-remediation grant. Within these bounds, "
                                "unattended agents'\nrequests are executed without asking you; every use is "
                                "ledgered for your review:",
                         confirm_sha256=confirm_sha256, allow_checkout=allow_checkout)


def install(record: dict[str, Any]) -> Path:
    """Write a signed grant into the operator-owned grant directory (mode 600).

    Args:
        record: Signed grant.

    Returns:
        The file path.

    Raises:
        GrantError: Unsigned or malformed record.
    """
    if not _ID_RE.match(str(record.get("grant_id", ""))) or not record.get("signature"):
        raise GrantError("refusing to install an unsigned or malformed grant")
    d = grants_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"{record['grant_id']}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    return path


def revoke(grant_id: str) -> bool:
    """Delete a grant (a narrowing; no signature needed).

    Args:
        grant_id: Grant to remove.

    Returns:
        Whether a file was removed.
    """
    if not _ID_RE.match(grant_id):
        return False
    path = grants_dir() / f"{grant_id}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def verify_key(key_id: str) -> str:
    """Return the operator's public verify key ``<key_id>.pub.pem`` (owner-checked).

    Args:
        key_id: Operator verify-key id.

    Returns:
        The PEM text.

    Raises:
        GrantError: Malformed id, missing file, or a file not owned by you / group-world-writable.
    """
    from agentteams import mcp_direct_grants as G

    directory = Path(os.path.expanduser(G.VERIFY_KEYS_DIR))
    path = directory / f"{key_id}.pub.pem"
    if not _KEY_ID_RE.match(key_id):
        raise GrantError(f"no operator verify key {path}")
    # X7: whoever can write the directory can swap the key, so the directory is judged too. Each check looks at
    # the final component only (lstat / O_NOFOLLOW), never resolve() against a full path: /var → /private/var.
    unsafe = dir_problem(directory)
    if unsafe:
        raise GrantError(f"verify-keys directory {directory}: {unsafe}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise GrantError(f"no operator verify key {path}") from exc
    except OSError as exc:   # ELOOP: the key file is a symlink
        raise GrantError(f"verify key {path} could not be opened safely (symlink?): {exc}") from exc
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise GrantError(f"verify key {path} is not a regular file")
        if st.st_uid != os.getuid() or st.st_mode & 0o022:
            raise GrantError(f"verify key {path} is not owned by you or is group/world-writable")
        data = fh.read(65536)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GrantError(f"verify key {path} is not UTF-8 text") from exc


def dir_problem(directory: Path) -> str | None:
    """Return why an operator directory (grants, verify keys) may not be trusted, or ``None``.

    Judges the directory's own final component with ``lstat``: a symlink is refused even when its
    target is a 0700 directory you own, because the link's target can be changed by whoever owns the
    link's parent. Group/world-*readable* is fine; writable is not.

    Args:
        directory: The directory (it must exist; callers treat a missing one separately).

    Returns:
        A short reason, or ``None``.
    """
    try:
        st = os.lstat(directory)
    except OSError as exc:
        return f"cannot stat ({exc})"
    if stat.S_ISLNK(st.st_mode):
        return "is a symlink — refused"
    if not stat.S_ISDIR(st.st_mode):
        return "is not a directory — refused"
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        return "is not owned by you or is group/world-writable — refused"
    return None


def verify(record: dict[str, Any], *, now: datetime | None = None) -> None:
    """Verify one grant's shape, bounds, expiry and signature.

    Args:
        record: Grant as read from disk.
        now: Clock override (tests).

    Raises:
        GrantError: Anything is wrong (fail closed).
    """
    from agentteams.cli.signed_ledger import ed25519_verify

    now = now or datetime.now(UTC)
    if set(record) != {*FIELDS, "signature"}:
        raise GrantError("unexpected or missing fields")
    if not _ID_RE.match(str(record["grant_id"])):
        raise GrantError("malformed grant_id")
    if set(record["ops"]) - OPS_V1 or not record["ops"]:
        raise GrantError(f"operations not honoured in v1: {sorted(set(record['ops']) - OPS_V1)}")
    issued = datetime.fromisoformat(record["issued"])
    expires = datetime.fromisoformat(record["expires"])
    if expires - issued > timedelta(days=MAX_DAYS, minutes=1) or not 1 <= int(record["max_uses"]) <= MAX_USES:
        raise GrantError("bounds exceed the v1 limits")
    if not issued <= now < expires:
        raise GrantError(f"not valid now (valid {record['issued']} .. {record['expires']})")
    try:
        ok = ed25519_verify(verify_key(record["key_id"]), signed_values(record), str(record["signature"]))
    except RuntimeError as exc:
        raise GrantError(f"cannot verify: {exc}") from exc
    if not ok:
        raise GrantError("signature does not verify")


def load_active(*, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (valid grants, problems). Over the aggregate cap, every grant is refused.

    Args:
        now: Clock override (tests).

    Returns:
        Valid records and one message per rejected file.
    """
    valid, problems = _scan(now=now)
    if len(valid) > MAX_ACTIVE:
        return [], problems + [f"{len(valid)} valid grants exceed the aggregate cap of {MAX_ACTIVE} — refusing all"]
    return valid, problems


def cap_problem(*, now: datetime | None = None) -> str:
    """Return why signing one more grant would exceed :data:`MAX_ACTIVE`, or "" when it would not.

    Over the cap :func:`load_active` refuses every grant, so ``grant sign`` checks first instead of silently
    disabling the grants already in force (2026-10-25: a third grant stopped all remediation).

    Args:
        now: Clock override (tests).

    Returns:
        A message naming the valid grants and how to revoke one, or "".
    """
    valid, problems = _scan(now=now)
    unsafe = next((p for p in problems if p.endswith("refusing all grants")), "")
    if unsafe:   # an unsafe grants dir refuses every grant anyway: say so instead of signing into it
        return unsafe
    if len(valid) < MAX_ACTIVE:
        return ""
    held = ", ".join(f"{g['grant_id']} ({'/'.join(g['ops'])}, expires {g['expires']})" for g in valid)
    return (f"{len(valid)} valid grant(s) already ({held}); one more exceeds the aggregate cap of {MAX_ACTIVE}, "
            "which refuses every grant. Revoke one first: grant revoke --grant-id <id>")


def _scan(*, now: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (verified grants, problems) without applying the aggregate cap."""
    d = grants_dir()
    valid: list[dict[str, Any]] = []
    problems: list[str] = []
    if not os.path.lexists(d):
        return [], []
    unsafe = dir_problem(d)
    if unsafe:
        return [], [f"{d} {unsafe}: refusing all grants"]
    for f in sorted(d.glob("vg-*.json")):
        unsafe = file_problem(f)
        if unsafe:
            problems.append(f"{f.name}: {unsafe}")
            continue
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            verify(rec, now=now)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        valid.append(rec)
    return valid, problems


def file_problem(path: Path) -> str | None:
    """Return why one grant file may not be trusted, or ``None``.

    The directory check alone is not enough: a group/world-writable file inside a 0700 directory could be
    overwritten with another validly signed grant (for example one the operator revoked). A symlink is refused
    because its target's permissions are not the grants directory's.

    Args:
        path: A grant file.

    Returns:
        A short reason, or ``None`` when the file is a regular file owned by you and not group/world-writable.
    """
    try:
        st = path.lstat()
    except OSError as exc:
        return f"cannot stat ({exc})"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file (symlink or special) — refused"
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        return "not owned by you or group/world-writable — refused"
    return None


def unverified_values(directory: Path, key: str) -> list[str]:
    """Return one string field from every ``*.json`` grant file in ``directory``, read **unverified**.

    The one tolerant reader of grant files: only ever used to *refuse* (narrowing), so an unsigned,
    expired or malformed file may add a value but can never widen what the executor does. Every
    ``*.json`` file counts, whatever its name, so a misnamed file still narrows.

    Args:
        directory: A grants directory (remediation or publish).
        key: The field to read (e.g. ``governance_dir``, ``ops_repo``).

    Returns:
        The non-empty string values, in file-name order.

    Raises:
        GrantError: A grant file exists but cannot be read (callers refuse rather than skip it).
    """
    out: list[str] = []
    for f in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        if not f.is_file():   # a directory or dangling link holds no value
            continue
        value = _json_field(f, key)
        if value:
            out.append(value)
    return out


def _json_field(path: Path, key: str) -> str:
    """Return ``key`` from one grant file read unverified; malformed JSON has no value to read.

    Raises:
        GrantError: The file cannot be read. Skipping it would shorten a refusal list (fail open).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GrantError(f"cannot read grant file {path.name}: {exc}") from exc
    try:
        rec = json.loads(text)
    except ValueError:
        return ""   # not JSON: it names nothing to refuse by
    value = rec.get(key) if isinstance(rec, dict) else None
    return value if isinstance(value, str) else ""


def covers(record: dict[str, Any], op: str, target: str) -> bool:
    """Return whether ``record`` grants ``op`` on ``target`` (scope only; use counts are the ledger's).

    Args:
        record: A verified grant.
        op: Operation.
        target: Absolute repo path (repo ops) or exact host name (server ops).

    Returns:
        True when the operation and target are both within the grant.
    """
    from agentteams.cli.signed_ledger import path_within

    if op not in record["ops"]:
        return False
    if op.startswith("server-"):
        return target in record["hosts"]
    return any(path_within(target, root) for root in record["repo_roots"])


# ---------------------------------------------------------------- grant sign --from-registry

def previous_grant() -> dict[str, Any] | None:
    """Return the most recently issued grant in :func:`grants_dir` that carries a valid operator signature.

    Expired grants count (the previous grant is usually expired or about to be); unsigned, forged or
    unreadable files do not, so a planted file cannot make additions look unchanged. With no baseline every
    proposed root is an addition, which asks for more confirmation, never less.

    Returns:
        The grant record, or None.
    """
    d = grants_dir()
    if not d.is_dir() or dir_problem(d):
        return None
    best: dict[str, Any] | None = None
    for f in sorted(d.glob("vg-*.json")):
        rec = _signed_record(f)
        if rec is not None and (best is None or rec["issued"] > best["issued"]):
            best = rec
    return best


def _signed_record(path: Path) -> dict[str, Any] | None:
    """Return one grant file's record when it verifies as of its own issue time, else None."""
    if file_problem(path):
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        verify(rec, now=datetime.fromisoformat(rec["issued"]))
    except (OSError, ValueError, TypeError, KeyError):   # GrantError is a ValueError
        return None
    return rec


def registry_proposal(entries: list[Any], protected: dict[str, Path]) -> tuple[dict[str, str], list[str]]:
    """Return the roots the registry proposes and the ones left out because they are protected.

    A proposal is the fix checkout of every entry at level ``remediate`` (each once). An entry whose
    enrolled identity, or the identity its fix checkout has now, is a protected origin is left out.
    Each root is grouped under the owner of its **current** origin (what a PR would reach); when that
    differs from the registry's, the owner label says so (the executor refuses it until it is re-enrolled).

    Args:
        entries: :func:`registry.load` entries.
        protected: :func:`executor_guard.protected_identities`.

    Returns:
        ``({resolved root: owner}, [left-out descriptions])``.
    """
    from agentteams.vulnscan import registry

    roots: dict[str, str] = {}
    left_out: list[str] = []
    for e in entries:
        if e.level != "remediate":
            continue
        root = str(Path(e.fix_checkout).expanduser().resolve())
        now = registry.identity(Path(root))
        hit = next((i for i in (e.identity, now) if i in protected), None)
        if hit is not None:
            left_out.append(f"{root} ({hit} is protected: {protected[hit]})")
            continue
        owner = registry.owner_of(now)
        if now != e.identity:
            owner += f" (ORIGIN CHANGED: enrolled as {e.identity}, owner {e.owner or registry.owner_of(e.identity)})"
        roots.setdefault(root, owner)
    return roots, left_out


def _owner_of_root(root: str, proposed: dict[str, str]) -> str:
    from agentteams.vulnscan import registry

    return proposed.get(root) or registry.owner_of(registry.identity(Path(root)))


def review_registry_roots(proposed: dict[str, str], explicit: list[str], previous: list[str], *,
                          ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> list[str]:
    """Show the change versus the previous grant and ask, per owner, for the registry's additions.

    Additions and removals are listed grouped by owner; unchanged roots only as a count. Each owner's
    registry additions need an explicit ``y``; anything else drops them. ``--repo-root`` additions were
    typed by the operator and are shown, not asked. The caller then signs the full list through the
    usual review (its digest covers every root).

    Args:
        proposed: ``{root: owner}`` from :func:`registry_proposal`.
        explicit: Roots given with ``--repo-root``.
        previous: The previous grant's ``repo_roots`` ([] when there is none).
        ask: Prompt function (terminal ``input``).
        say: Output function.

    Returns:
        The roots to sign: unchanged, accepted additions and explicit roots (sorted, each once).
    """
    explicit_r = {str(Path(r).expanduser().resolve()) for r in explicit}
    prev = set(previous)
    wanted = set(proposed) | explicit_r
    added = sorted(set(proposed) - prev - explicit_r)
    removed = sorted(prev - wanted)
    unchanged = sorted(wanted & prev)
    say(f"Remediation roots versus the previous grant: {len(added) + len(explicit_r - prev)} to add, "
        f"{len(removed)} to remove, {len(unchanged)} unchanged.")
    by_owner: dict[str, list[str]] = {}
    for root in added:
        by_owner.setdefault(proposed[root], []).append(root)
    for label, roots in (("given with --repo-root", sorted(explicit_r - prev)), ("removed", removed)):
        if roots:
            say(f"\n{label}:")
            groups: dict[str, list[str]] = {}
            for root in roots:
                groups.setdefault(_owner_of_root(root, proposed), []).append(root)
            for owner in sorted(groups):
                say(f"  {owner}: " + ", ".join(groups[owner]))
    accepted: set[str] = set()
    for owner in sorted(by_owner):
        say(f"\nadd ({owner}):")
        for root in by_owner[owner]:
            say(f"  + {root}")
        answer = ask(f"Add these {len(by_owner[owner])} repositories of {owner} to the grant? [y/N] ")
        if answer.strip().lower() in ("y", "yes"):
            accepted.update(by_owner[owner])
        else:
            say(f"  dropped {len(by_owner[owner])} additions of {owner}")
    return sorted(set(unchanged) | accepted | explicit_r)


def roots_from_registry(cfg: dict[str, Any], explicit: list[str], governance_dir: str, *,
                        ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                        tty: Callable[[], bool] | None = None) -> list[str] | None:
    """Return the repo roots for ``grant sign --from-registry`` after the operator's per-owner review.

    The registry only proposes: nothing it says reaches a grant without the per-owner answer here and the
    signing review after it. Refused on CI and without a terminal (the answers must be the operator's).

    Args:
        cfg: Daily config (``agent.team_dir`` is protected).
        explicit: ``--repo-root`` values.
        governance_dir: The governance dir about to be signed (protected too).
        ask: Prompt function.
        say: Output function.
        tty: Terminal check (default: stdin and stdout are terminals).

    Returns:
        The roots, or None after saying why nothing can be proposed.
    """
    import sys

    from agentteams.vulnscan import executor_guard, registry

    if registry.is_ci():
        say("refused: grant sign --from-registry never runs on CI (CI is set)")
        return None
    if not (tty() if tty else sys.stdin.isatty() and sys.stdout.isatty()):
        say("refused: grant sign --from-registry asks per owner on a terminal; run it interactively")
        return None
    try:
        entries = registry.load()
        protected = executor_guard.protected_identities(cfg, extra=(governance_dir,))
    except (OSError, ValueError) as exc:   # GrantError is a ValueError
        say(f"refused: {exc}")
        return None
    proposed, left_out = registry_proposal(entries, protected)
    for line in left_out:
        say(f"not proposed (protected): {line}")
    prev = previous_grant()
    if prev is None:
        say("no previous signed grant: every root is an addition")
    return review_registry_roots(proposed, explicit, list(prev["repo_roots"]) if prev else [], ask=ask, say=say)
