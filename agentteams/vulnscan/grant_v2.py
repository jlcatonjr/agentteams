"""Version-2 remediation grants: the signed fields that bound ``server-apply`` and ``runtime-patch`` (vfr-s, vfr-d.D4).

A v1 grant (:data:`grant.PURPOSE_TAG`) names operations, repository roots and hosts. The two host-changing operations
need more than that signed, so a grant that names either is a **v2 grant**: purpose tag :data:`PURPOSE_TAG_V2` over
every v1 field plus :data:`EXTRA_FIELDS` (@security Phase 2 design row, 2026-11-02, conditions S1-S3 and D4):

| Field | Meaning |
|---|---|
| ``version`` | ``2`` |
| ``server_levels`` | signed ``host:level`` pairs; ``server-apply`` runs one level on one host only when its pair is here |
| ``restart_units`` | ``host:unit`` pairs ``l2-security`` may restart (OD4). ``l4-pins``/``py-venv`` restart only their own |
|                   | apps.conf unit, which ``apps_conf_sha256`` covers |
| ``restart_window`` | ``HH:MM-HH:MM`` (UTC) inside which a level that may restart a service runs (OD4: off-hours only) |
| ``apps_conf_sha256`` | the exact apps.conf the pin and venv levels read; any other apps.conf refuses |
| ``major_allowed`` | ``host:unit`` pairs whose pins may cross a major version (never decided by apps.conf alone) |
| ``remove_paths`` | ``host:/exact/path`` pairs ``remove-path`` may quarantine (exact path, never a prefix) |
| ``runtimes`` | Homebrew formulae ``runtime-patch`` may upgrade on this machine (same major, bottles only) |
| ``ancestor`` | the grant this one re-issues; it must be revoked (archived) before this one is signed (Rule 15 lineage) |

A grant naming ``server-apply`` holds server operations only, at most :data:`MAX_USES_SERVER_APPLY` uses. A v1 record
naming a v2 operation is refused: the signature would not cover the bounds those operations need.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time
from pathlib import Path, PurePosixPath
from typing import Any

PURPOSE_TAG_V2 = "agentteams-vulnscan-remediation-grant-v2"
#: Operations only a v2 grant can carry.
V2_OPS: frozenset[str] = frozenset({"server-apply", "runtime-patch"})
#: The operations a grant naming ``server-apply`` may hold ("server ops only", S3).
SERVER_OPS: frozenset[str] = frozenset({"server-audit", "server-dryrun", "server-apply"})
EXTRA_FIELDS: tuple[str, ...] = ("version", "server_levels", "restart_units", "restart_window", "apps_conf_sha256",
                                 "major_allowed", "remove_paths", "runtimes", "ancestor")
LIST_FIELDS: tuple[str, ...] = ("server_levels", "restart_units", "major_allowed", "remove_paths", "runtimes")
#: The server-apply levels (S2). Each is a separate signed level; there is no "all".
LEVELS: tuple[str, ...] = ("l2-security", "l4-pins", "py-venv", "remove-path")
#: Levels that restart a service (their apps.conf unit) and so run only inside the restart window.
RESTARTING_LEVELS: frozenset[str] = frozenset({"l4-pins", "py-venv"})
#: Levels that read apps.conf (its sha256 must be signed).
APPS_CONF_LEVELS: frozenset[str] = frozenset({"l4-pins", "py-venv"})
MAX_USES_SERVER_APPLY = 20
#: Formulae runtime-patch never upgrades: the executor's own interpreter and credentialed tools (D4).
REFUSED_FORMULAE: tuple[str, ...] = ("python", "git", "gh", "openssh", "gnupg")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:-]{0,120}\.service$")
_FORMULA_RE = re.compile(r"^[a-z0-9][a-z0-9+_.@-]{0,63}$")
_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
_ID_RE = re.compile(r"^vg-[0-9a-f]{16}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: Path characters remove-path accepts: no spaces, quotes, globs or control characters.
_PATH_RE = re.compile(r"^/[A-Za-z0-9._@+/-]{1,4095}$")
#: Trees remove-path never quarantines, nor anything inside them, nor any ancestor of them (S2: trust roots).
TRUST_ROOTS: tuple[str, ...] = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/proc", "/run", "/sbin", "/sys",
    "/usr/bin", "/usr/lib", "/usr/lib32", "/usr/lib64", "/usr/libexec", "/usr/sbin", "/usr/share", "/usr/include",
    "/var/lib", "/var/log", "/var/spool", "/var/backups/agentteams", "/var/backups/vuln-fix",
    "/root/.ssh", "/root/.gnupg", "/root/.config/agentteams",
)
#: Path components that mark a trust root anywhere (a user's ssh/gpg material, the agentteams config).
TRUST_COMPONENTS: tuple[str, ...] = (".ssh", ".gnupg", "authorized_keys", "sudoers", "sudoers.d")


def is_v2(record: dict[str, Any]) -> bool:
    """Return whether ``record`` claims to be a v2 grant (its signature then uses :data:`PURPOSE_TAG_V2`).

    Args:
        record: Grant record.

    Returns:
        True when ``record["version"] == 2``.
    """
    return record.get("version") == 2


def signed_values(record: dict[str, Any], v1_fields: tuple[str, ...]) -> list[str]:
    """Return the v2 Ed25519 payload: the v2 purpose tag + canonical JSON of every v1 and v2 field.

    Args:
        record: v2 grant record.
        v1_fields: :data:`grant.FIELDS`.

    Returns:
        Field list for ``signed_ledger.ed25519_sign``/``ed25519_verify``.

    Raises:
        KeyError: A bound field is missing.
    """
    body = {name: record[name] for name in (*v1_fields, *EXTRA_FIELDS)}
    return [PURPOSE_TAG_V2, json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]


def trust_root_problem(path: str) -> str:
    """Return why ``path`` may not be quarantined by ``remove-path``, or "" when it may.

    The same rule runs again on the host (``L7-remove-path.sh``), which also refuses symlinks (realpath must equal
    the path) and mount points.

    Args:
        path: Absolute path as signed into a grant.

    Returns:
        A short reason, or "".
    """
    if not _PATH_RE.match(path) or "\n" in path:
        return "must be an absolute path of letters, digits and ._@+-/ only"
    if str(PurePosixPath(path)) != path or "/./" in path or "/../" in path or path.endswith(("/.", "/..")):
        return "must be normalised (no //, ., .. or trailing /)"
    parts = PurePosixPath(path).parts[1:]
    if len(parts) < 2:
        return "a top-level directory is never quarantined"
    if parts[0] == "home" and len(parts) < 3:
        return "a home directory itself is never quarantined"
    if parts[0] == "usr" and (len(parts) < 3 or parts[1] != "local"):
        return "only /usr/local/<name>/... may be quarantined under /usr"
    if any(c in TRUST_COMPONENTS for c in parts):
        return "names ssh/gpg/sudo material (a trust root)"
    for root in TRUST_ROOTS:
        if path == root or path.startswith(root + "/") or root.startswith(path + "/"):
            return f"is, lies in, or contains the trust root {root}"
    return ""


def pair(value: str, *, what: str) -> tuple[str, str]:
    """Split a ``host:rest`` grant entry and check the host grammar.

    Args:
        value: ``host:rest``.
        what: Field name for the error.

    Returns:
        ``(host, rest)``.

    Raises:
        ValueError: Malformed entry.
    """
    host, sep, rest = str(value).partition(":")
    if not sep or not _HOST_RE.match(host) or host == "all" or not rest:
        raise ValueError(f"{what} entries are host:value with an exact hosts.conf name, got {value!r}")
    return host, rest


def in_window(window: str, now: datetime) -> bool:
    """Return whether ``now`` (UTC) lies inside ``HH:MM-HH:MM`` (the window may wrap midnight).

    Args:
        window: Signed ``restart_window``.
        now: Aware UTC time.

    Returns:
        True inside the window; False for an empty or malformed window.
    """
    m = _WINDOW_RE.match(window or "")
    if not m:
        return False
    start, end = time(int(m[1]), int(m[2])), time(int(m[3]), int(m[4]))
    t = now.time().replace(second=0, microsecond=0, tzinfo=None)
    return start <= t < end if start < end else (t >= start or t < end)


def refused_formula(name: str) -> str:
    """Return why ``runtime-patch`` refuses Homebrew formula ``name`` by name, or "".

    Args:
        name: Formula name.

    Returns:
        A short reason, or "".
    """
    if not _FORMULA_RE.match(name):
        return "is not a Homebrew formula name"
    base = name.split("@", 1)[0]
    if base in REFUSED_FORMULAE or base.startswith("python"):
        return "is the executor's interpreter or a credentialed tool (python, git, gh, openssh, gnupg) — refused"
    return ""


def check_shape(record: dict[str, Any], *, ops: set[str], hosts: list[str], max_uses: int) -> None:
    """Check every v2 bound that the record alone decides (shared by :func:`extend` and verification).

    Args:
        record: Record carrying :data:`EXTRA_FIELDS`.
        ops: The grant's operations.
        hosts: The grant's hosts.
        max_uses: The grant's use cap.

    Raises:
        ValueError: A bound is violated (``grant.GrantError`` is a ValueError; callers re-raise as it).
    """
    if record.get("version") != 2:
        raise ValueError("a v2 grant has version 2")
    for name in LIST_FIELDS:
        value = record.get(name)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value) or value != sorted(set(value)):
            raise ValueError(f"{name} must be a sorted list of distinct strings")
    for name in ("restart_window", "apps_conf_sha256", "ancestor"):
        if not isinstance(record.get(name), str):
            raise ValueError(f"{name} must be a string")
    apply_ = "server-apply" in ops
    if apply_:
        if ops - SERVER_OPS:
            raise ValueError(f"a grant naming server-apply holds server operations only, not {sorted(ops - SERVER_OPS)}")
        if max_uses > MAX_USES_SERVER_APPLY:
            raise ValueError(f"a grant naming server-apply allows at most {MAX_USES_SERVER_APPLY} uses")
        _check_server_fields(record, set(hosts))
    elif any(record[n] for n in ("server_levels", "restart_units", "restart_window", "apps_conf_sha256",
                                  "major_allowed", "remove_paths")):
        raise ValueError("server-apply fields are set but the grant does not name server-apply")
    if "runtime-patch" in ops:
        if not record["runtimes"]:
            raise ValueError("runtime-patch needs at least one signed runtime (Homebrew formula)")
        for f in record["runtimes"]:
            if why := refused_formula(f):
                raise ValueError(f"runtime {f!r} {why}")
    elif record["runtimes"]:
        raise ValueError("runtimes are set but the grant does not name runtime-patch")
    if ops & V2_OPS and not _ID_RE.match(record["ancestor"]):
        raise ValueError("a grant naming server-apply or runtime-patch names the grant it re-issues (ancestor)")
    if record["ancestor"] and not _ID_RE.match(record["ancestor"]):
        raise ValueError("malformed ancestor grant id")


def _check_server_fields(record: dict[str, Any], hosts: set[str]) -> None:
    levels: dict[str, set[str]] = {}
    for entry in record["server_levels"]:
        host, level = pair(entry, what="server_levels")
        if host not in hosts or level not in LEVELS:
            raise ValueError(f"server_levels entry {entry!r}: host must be a grant host, level one of {LEVELS}")
        levels.setdefault(host, set()).add(level)
    if not levels:
        raise ValueError("server-apply needs at least one signed host:level pair")
    removal_hosts = set()
    for entry in record["remove_paths"]:
        host, path = pair(entry, what="remove_paths")
        if "remove-path" not in levels.get(host, set()):
            raise ValueError(f"remove_paths entry {entry!r}: {host} has no signed remove-path level")
        if why := trust_root_problem(path):
            raise ValueError(f"remove_paths entry {entry!r} {why}")
        removal_hosts.add(host)
    if any("remove-path" in lv and h not in removal_hosts for h, lv in levels.items()):
        raise ValueError("a signed remove-path level names the exact paths it may quarantine (remove_paths)")
    for entry in record["restart_units"]:
        host, unit = pair(entry, what="restart_units")
        if "l2-security" not in levels.get(host, set()) or not _UNIT_RE.match(unit):
            raise ValueError(f"restart_units entry {entry!r}: needs l2-security on {host} and a <name>.service unit")
    for entry in record["major_allowed"]:
        host, unit = pair(entry, what="major_allowed")
        if not levels.get(host, set()) & APPS_CONF_LEVELS or not _UNIT_RE.match(unit):
            raise ValueError(f"major_allowed entry {entry!r}: needs l4-pins or py-venv on {host} and a .service unit")
    reads_apps = any(lv & APPS_CONF_LEVELS for lv in levels.values())
    if reads_apps and not _HEX64.match(record["apps_conf_sha256"]):
        raise ValueError("l4-pins and py-venv need the signed apps.conf sha256 (64 lower-case hex)")
    if not reads_apps and record["apps_conf_sha256"]:
        raise ValueError("apps_conf_sha256 is set but no signed level reads apps.conf")
    restarts = reads_apps or bool(record["restart_units"])
    if restarts and not _WINDOW_RE.match(record["restart_window"]):
        raise ValueError("a level that may restart a service needs a signed restart_window HH:MM-HH:MM (UTC)")
    if record["restart_window"] and not _WINDOW_RE.match(record["restart_window"]):
        raise ValueError("restart_window must be HH:MM-HH:MM (UTC)")


def extend(record: dict[str, Any], *, server_levels: list[str], restart_units: list[str], restart_window: str,
           apps_conf_sha256: str, major_allowed: list[str], remove_paths: list[str], runtimes: list[str],
           ancestor: str, grants_dir: Path, revoked_dir: Path,
           ancestor_ok: Any = None) -> dict[str, Any]:
    """Return ``record`` (an unsigned v1 build) extended to a v2 grant, enforcing every v2 bound.

    Args:
        record: Unsigned v1 record from ``grant.build``'s v1 checks.
        server_levels: ``host:level`` pairs.
        restart_units: ``host:unit`` pairs.
        restart_window: ``HH:MM-HH:MM`` UTC, or "".
        apps_conf_sha256: Hex digest, or "".
        major_allowed: ``host:unit`` pairs.
        remove_paths: ``host:/path`` pairs.
        runtimes: Homebrew formulae.
        ancestor: Grant id this one re-issues.
        grants_dir: Active grants directory (the ancestor must no longer be in it: revoke first, S3).
        revoked_dir: Where ``grant revoke`` archives revoked grants (the ancestor must be there).
        ancestor_ok: Callable ``(path) -> bool`` verifying the archived ancestor's signature (``grant``'s check).

    Returns:
        The v2 record (still unsigned).

    Raises:
        ValueError: A bound is violated.
    """
    out = {**record, "version": 2, "restart_window": restart_window.strip(),
           "apps_conf_sha256": apps_conf_sha256.strip().lower(), "ancestor": ancestor.strip()}
    for name, values in (("server_levels", server_levels), ("restart_units", restart_units),
                         ("major_allowed", major_allowed), ("remove_paths", remove_paths), ("runtimes", runtimes)):
        out[name] = sorted({str(v).strip() for v in values})
    check_shape(out, ops=set(out["ops"]), hosts=list(out["hosts"]), max_uses=int(out["max_uses"]))
    if out["ancestor"]:
        anc = out["ancestor"]
        if (grants_dir / f"{anc}.json").exists():
            raise ValueError(f"ancestor {anc} is still installed: revoke it first (grant revoke --grant-id {anc}), "
                             "then sign its re-issue (S3: never a third grant)")
        archived = revoked_dir / f"{anc}.json"
        if not archived.is_file() or (ancestor_ok is not None and not ancestor_ok(archived)):
            raise ValueError(f"ancestor {anc} is not a revoked, validly signed grant in {revoked_dir}")
    return out
