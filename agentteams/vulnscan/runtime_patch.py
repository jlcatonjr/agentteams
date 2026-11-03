"""``runtime-patch``: a same-major Homebrew bottle upgrade of ONE signed formula on this machine (vfr-d.D4, OD8).

For a runtime the latest scan flagged on this machine (``vulnscan runtimes`` / the daily run's ``this-machine``
findings) that Homebrew provides. Operator-run only (``vulnscan act runtime-patch FORMULA``,
:data:`ops.OPERATOR_ONLY_OPS`), under a v2 grant whose signed ``runtimes`` list names the formula (@security Phase 2
design, condition D4, and the P8 review's 8b):

- **Flagged.** The latest completed run's ``summary.json`` has a ``runtime`` finding on ``this-machine`` for the
  formula's runtime, installed by brew from this formula's Cellar.
- **One formula.** ``brew upgrade --formula --dry-run FORMULA`` must name exactly that formula and nothing else (no
  dependency installs or upgrades, no dependents: ``HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK``), at the same major.
  The normalised dry-run plan's digest must equal the one the operator confirmed, recomputed just before the change.
- **Aged.** The target version is its release line's latest patch and was released at least
  :data:`RELEASE_COOLDOWN_DAYS` days ago (S-10's cooldown, per endoflife.date). An unknown date refuses.
- **homebrew/core bottles only.** The formula's tap is ``homebrew/core``, it is installed and linked (the runtime on
  PATH is Homebrew's), not pinned, and a bottle exists for this Mac's tag (or ``all``): nothing builds from source.
  Afterwards the installed version must be linked and ``poured_from_bottle``, or the op escalates. No tap is added;
  only ``brew`` at a standard macOS prefix runs, never through a PATH lookup.
- **No sudo.** Refused as root; the Homebrew prefix must be the operator's; ``SUDO_*`` never reaches brew.
- **Never the executor's own tools.** ``python*``, ``git``, ``gh``, ``openssh`` and ``gnupg`` are refused by name, and
  so is any formula that holds the running interpreter or the git/gh/ssh/gpg the executor runs, or that one of them
  depends on (:data:`TOOL_DEPENDENCIES` plus ``brew deps --installed`` of those formulae).
- The preview may let Homebrew refresh its index; the apply never does (``HOMEBREW_NO_AUTO_UPDATE``), so it installs
  the plan that was reviewed.

brew runs on this machine, not in the tools container: the runtime being patched is this machine's.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import escalations, grant, grant_v2, ledger, paths, runtimes
from agentteams.vulnscan.executor_env import ExecError
from agentteams.vulnscan.host_plan import CONFIRM_KEY, HEX64, plan_sha256

BREW_PATHS: tuple[str, ...] = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
TAP = "homebrew/core"
#: S-10's release cooldown for the version runtime-patch installs (P8 review 8b).
RELEASE_COOLDOWN_DAYS = 14
APPLY_ENV: dict[str, str] = {"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
                             "HOMEBREW_NO_INSTALL_CLEANUP": "1", "HOMEBREW_NO_ANALYTICS": "1",
                             "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_INSTALL_UPGRADE": "1"}
#: Formulae python, git, gh, openssh and gnupg depend on (Homebrew, 2026-11); ``brew deps --installed`` adds the
#: ones this machine actually has. runtime-patch refuses all of them: they are the executor's own toolchain.
TOOL_DEPENDENCIES: frozenset[str] = frozenset({
    "openssl@3", "ca-certificates", "mpdecimal", "readline", "sqlite", "xz", "gdbm", "zstd", "lz4", "expat",
    "libffi", "zlib", "bzip2", "ncurses", "gettext", "pcre2", "libunistring", "libidn2", "ldns", "libfido2",
    "libcbor", "gnutls", "libassuan", "libgcrypt", "libgpg-error", "libksba", "libusb", "npth", "pinentry",
    "nettle", "p11-kit", "libtasn1", "unbound", "libevent", "brotli", "libnghttp2", "krb5", "libssh2", "curl",
})
#: macOS major version -> Homebrew bottle codename.
MACOS_TAGS: dict[int, str] = {26: "tahoe", 15: "sequoia", 14: "sonoma", 13: "ventura", 12: "monterey"}
_UPGRADE = re.compile(r"^(\S+) (\S+) -> (\S+)$")
_HEADER = re.compile(r"^==> Would upgrade 1 outdated package:?$")
Runner = Callable[[list[str], dict[str, str]], subprocess.CompletedProcess]


def brew_path() -> str:
    """Return the Homebrew executable at a standard macOS prefix, owned by the operator.

    Returns:
        Its path.

    Raises:
        ExecError: None found, run as root, or a prefix not owned by the operator (that would need sudo).
    """
    if os.geteuid() == 0:
        raise ExecError("runtime-patch never runs as root (no sudo)")
    for p in BREW_PATHS:
        if os.path.isfile(p):
            prefix = Path(p).resolve().parent.parent
            if prefix.stat().st_uid != os.getuid():
                raise ExecError(f"the Homebrew prefix {prefix} is not yours: runtime-patch never uses sudo")
            return p
    raise ExecError("no Homebrew at a standard prefix")


def _default_run(argv: list[str], env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SUDO_", "HOMEBREW_"))}
    env.update(env_extra)
    return subprocess.run([brew_path(), *argv], capture_output=True, text=True, env=env, timeout=3600, check=False)


def bottle_tag() -> str:
    """Return this Mac's Homebrew bottle tag (``arm64_sequoia``, ``sonoma`` ...), or "" when unknown.

    Returns:
        The tag.
    """
    if sys.platform != "darwin":
        return ""
    name = MACOS_TAGS.get(int((platform.mac_ver()[0] or "0").split(".")[0]), "")
    return "" if not name else (f"arm64_{name}" if platform.machine() == "arm64" else name)


def own_tool_formulae() -> set[str]:
    """Return the formulae whose Cellar holds the running interpreter or the executor's git/gh/ssh/gpg.

    Returns:
        Formula names.
    """
    out = set()
    for exe in (sys.executable, *(shutil.which(t) or "" for t in ("git", "gh", "ssh", "gpg"))):
        m = re.search(r"/Cellar/([^/]+)/", os.path.realpath(exe)) if exe else None
        if m:
            out.add(m[1])
    return out


def toolchain(run: Runner) -> set[str]:
    """Return every formula the executor's own tools are, or depend on (static list plus ``brew deps``).

    Args:
        run: brew runner.

    Returns:
        Formula names (the static :data:`TOOL_DEPENDENCIES` even when ``brew deps`` fails).
    """
    own = own_tool_formulae()
    found = set()
    if own:
        deps = run(["deps", "--installed", "--formula", *sorted(own)], APPLY_ENV)
        if deps.returncode == 0:
            found = {ln.strip() for ln in (deps.stdout or "").splitlines() if ln.strip()}
    return own | found | set(TOOL_DEPENDENCIES)


def major(version: str) -> str:
    """Return a Homebrew version's major component (``22.11.0_1`` -> ``22``).

    Args:
        version: Version text.

    Returns:
        Leading digits, or the whole string when there are none.
    """
    m = re.match(r"\d+", version)
    return m[0] if m else version


def runtime_kind(formula: str) -> str:
    """Return the :data:`runtimes.PRODUCTS` kind a formula provides (``node@22`` -> ``node``), or "".

    Args:
        formula: Formula name.

    Returns:
        The kind, or "" when vulnscan judges no such runtime.
    """
    base = formula.split("@", 1)[0]
    return base if base in runtimes.PRODUCTS else ""


def latest_findings() -> list[dict[str, Any]]:
    """Return the findings of the latest completed run (its ``summary.json``).

    Returns:
        The findings.

    Raises:
        ExecError: No completed run, or an unreadable summary.
    """
    runs = paths.run_dirs()
    if not runs:
        raise ExecError("no completed scan: runtime-patch needs the latest scan to flag the runtime")
    try:
        data = json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8"))
        findings = data["findings"]
        if not isinstance(findings, list):
            raise TypeError("findings is not a list")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExecError(f"the {runs[-1].name} summary.json is unreadable ({exc})") from exc
    return [f for f in findings if isinstance(f, dict)]


def flagged(formula: str, findings: list[dict[str, Any]]) -> bool:
    """Return whether ``findings`` hold a this-machine runtime finding installed by brew from ``formula``.

    Args:
        formula: Formula name.
        findings: The latest run's findings.

    Returns:
        True when one matches: kind ``runtime``, target ``this-machine``, the formula's runtime kind, ``[brew]`` in
        its detail, and a location inside the formula's Cellar.
    """
    from agentteams.vulnscan import engine

    kind = runtime_kind(formula)
    return any(f.get("kind") == "runtime" and f.get("target") == engine.LOCAL_TARGET and f.get("name") == kind
               and "[brew]" in str(f.get("detail", ""))
               and any(f"/Cellar/{formula}/" in os.path.realpath(str(loc)) for loc in f.get("locations") or [])
               for f in findings)


def check(formula: str, *, apply: bool, run: Runner = _default_run, today: date | None = None) -> tuple[list[str], str]:
    """Run every D4 check and the dry run; return the plan and its digest.

    Args:
        formula: Formula name.
        apply: Whether this is the apply's pre-change recomputation (no index refresh).
        run: brew runner (tests).
        today: Date override (tests).

    Returns:
        ``(plan lines, digest)``.

    Raises:
        ExecError: Any D4 condition fails.
    """
    if why := grant_v2.refused_formula(formula):
        raise ExecError(f"runtime-patch refuses {formula!r}: it {why}")
    kind = runtime_kind(formula)
    if not kind:
        raise ExecError(f"runtime-patch refuses {formula}: vulnscan judges no runtime it provides")
    if formula in toolchain(run):
        raise ExecError(f"runtime-patch refuses {formula}: the executor's own interpreter or git/gh/ssh/gpg is, or "
                        "depends on, it")
    if not flagged(formula, latest_findings()):
        raise ExecError(f"the latest scan has no this-machine runtime finding for {formula}: nothing to patch")
    env = APPLY_ENV if apply else {k: v for k, v in APPLY_ENV.items() if k != "HOMEBREW_NO_AUTO_UPDATE"}
    f = _info(formula, run, env)
    if f.get("name") != formula or f.get("tap") != TAP:
        raise ExecError(f"{formula} is from {f.get('tap')!r}, not {TAP}: refused (no other taps)")
    if not f.get("installed") or not f.get("linked_keg"):
        raise ExecError(f"{formula} is not installed and linked by Homebrew: the runtime on PATH is not this formula")
    if f.get("pinned"):
        raise ExecError(f"{formula} is pinned in Homebrew (brew unpin is the operator's decision)")
    tag = bottle_tag()
    files = ((f.get("bottle") or {}).get("stable") or {}).get("files") or {}
    if not (tag and tag in files) and "all" not in files:
        raise ExecError(f"no {TAP} bottle of {formula} for {tag or 'this machine'}: refusing a source build")
    dry = run(["upgrade", "--formula", "--dry-run", formula], env)
    if dry.returncode:
        raise ExecError(f"brew upgrade --dry-run {formula} failed (exit {dry.returncode})")
    lines = [ln.strip() for ln in (dry.stdout or "").splitlines() if ln.strip()]
    ups = [m for ln in lines if (m := _UPGRADE.match(ln))]
    other = [ln for ln in lines if not _UPGRADE.match(ln) and not _HEADER.match(ln)]
    if len(ups) != 1 or ups[0][1] != formula or other:
        raise ExecError(f"the dry run must upgrade exactly {formula} and nothing else; it shows: {lines[:8]}")
    old, new = ups[0][2], ups[0][3]
    if major(old) != major(new):
        raise ExecError(f"{formula} {old} -> {new} crosses a major version: runtime-patch is same-major only")
    upstream = re.sub(r"_\d+$", "", new)   # Homebrew's _N revision is not part of the upstream version
    released = runtimes.patch_release_date(kind, upstream)
    age = ((today or datetime.now(UTC).date()) - released).days if released else -1
    if age < RELEASE_COOLDOWN_DAYS:
        when = "its release date is unknown" if released is None else f"released {age} days ago"
        raise ExecError(f"{formula} {new}: {when}; S-10 needs {RELEASE_COOLDOWN_DAYS} days")
    plan = [f"PLAN\truntime\t{formula}\t{old}\t{new}\tbottle {tag if tag in files else 'all'}"]
    return plan, plan_sha256("\n".join(plan))


def _info(formula: str, run: Runner, env: dict[str, str]) -> dict[str, Any]:
    info = run(["info", "--json=v2", "--formula", formula], env)
    try:
        f = json.loads(info.stdout)["formulae"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ExecError(f"brew info {formula} gave no formula ({exc})") from exc
    if not isinstance(f, dict):
        raise ExecError(f"brew info {formula} gave no formula")
    return f


def precheck(target: str, payload: dict[str, Any] | None) -> str:
    """Refuse before ``authorize``: the formula grammar and the operator's confirmed digest.

    Args:
        target: Formula name.
        payload: ``{"confirm_sha256": hex}``.

    Returns:
        The formula.

    Raises:
        ExecError: Malformed or unconfirmed.
    """
    if why := grant_v2.refused_formula(target):
        raise ExecError(f"runtime-patch refuses {target!r}: it {why}")
    if not HEX64.match(str((payload or {}).get(CONFIRM_KEY, ""))):
        raise ExecError(f"runtime-patch runs only the plan the operator confirmed: vulnscan act runtime-patch {target}")
    return target


def run(target: str, g: dict[str, Any], *, request_id: str, payload: dict[str, Any] | None,
        runner: Runner = _default_run) -> dict[str, Any]:
    """Perform one ``runtime-patch`` (call inside ``ledger.locked()``, after ``authorize``).

    Args:
        target: Formula.
        g: Covering grant.
        request_id: Request id.
        payload: ``{"confirm_sha256": hex}``.
        runner: brew runner (tests).

    Returns:
        Result fields.

    Raises:
        ExecError: Refused or failed (a failed post-check opens an escalation first).
    """
    from agentteams.vulnscan import executor

    formula = precheck(target, payload)
    if not grant.covers(g, "runtime-patch", formula):
        raise ExecError(f"the covering grant does not sign the runtime {formula}")
    plan, digest = check(formula, apply=True, run=runner)
    if digest != str((payload or {}).get(CONFIRM_KEY)):
        raise ExecError(f"the {formula} plan changed since you confirmed it: nothing changed; review it again")
    ledger.append("note", grant_id=g["grant_id"], op="runtime-patch", target=formula, request_id=request_id,
                  detail=json.dumps({"plan": plan, "plan_sha256": digest}))
    old, new = plan[0].split("\t")[3:5]
    up = runner(["upgrade", "--formula", formula], APPLY_ENV)
    try:
        after = _info(formula, runner, APPLY_ENV)
    except ExecError:
        after = {}
    linked = after.get("linked_keg")
    poured = any(isinstance(i, dict) and i.get("version") == new and i.get("poured_from_bottle") is True
                 for i in after.get("installed") or [])
    if up.returncode or linked != new or not poured:
        esc = escalations.record("other", f"runtime:{formula}", f"runtime-patch {formula}: brew exit {up.returncode}, "
                                 f"linked {linked!r}, planned {new}, poured from a bottle: {poured}",
                                 request_id=request_id, run_id=f"runtime-patch-{request_id}",
                                 canonical=executor.canonical_target)
        raise ExecError(f"runtime-patch {formula} did not end at a poured {new} bottle (brew exit {up.returncode}, "
                        f"linked {linked!r}, poured {poured}); escalation {esc.id}")
    return {"formula": formula, "from": old, "to": new, "plan_sha256": digest}


def preview(target: str, *, runner: Runner = _default_run) -> dict[str, Any]:
    """Show the plan an apply would run (no change, no use reserved); needs a covering grant with uses left.

    Args:
        target: Formula.
        runner: brew runner (tests).

    Returns:
        ``{"plan": [lines], "plan_sha256": hex}``.

    Raises:
        ExecError: Refused.
    """
    formula = precheck(target, {CONFIRM_KEY: "0" * 64})
    grants, problems = grant.load_active()
    rows = ledger.read()
    if not any(grant.covers(g, "runtime-patch", formula) and grant_v2.uses_left(g, "runtime-patch", rows)
               for g in grants):
        raise ExecError(f"not authorized: runtime-patch {formula}: " + ("; ".join(problems) or "no grant signs it"))
    plan, digest = check(formula, apply=False, run=runner)
    return {"plan": plan, "plan_sha256": digest}
