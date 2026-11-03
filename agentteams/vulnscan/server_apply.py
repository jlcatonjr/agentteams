"""``server-apply``: one signed level on one signed host, under the @security Phase 2 design conditions (vfr-s S1/S2).

The executor's first operation that changes a production host. It runs only from the operator's ``vulnscan act``
(never a spooled agent request, :data:`ops.OPERATOR_ONLY_OPS`) and only under a v2 grant (:mod:`.grant_v2`) whose
signature names the host×level pair. The target is ``HOST:LEVEL`` or, for ``remove-path``, ``HOST:remove-path:/path``.

One op, in order (the ``begun`` row, C-5's recorded clearance and use reservation, is written by
``executor.authorize`` before any of it):

1. **Pre-checks** (refuse, nothing contacted): the target grammar; the host is a hosts.conf name and no open
   escalation blocks it; the operator's confirmed plan digest is present; for a level that may restart a service,
   the time is inside the signed ``restart_window`` (OD4: off-hours); for ``l4-pins``/``py-venv`` the apps.conf the
   run will read has the signed sha256 (the run reads a snapshot of it, not the live file); for ``remove-path`` the
   path passes the trust-root rule, has not been quarantined before, and the per-path C-5 decision row
   ``server-apply:remove-path:<host>:<path>`` is cleared **and verified** (:func:`removal_clearance`).
2. **Pre-audit**: the kit's L1 audit. Compromise indicators (exit 4) open a global escalation: everything stops.
   ``remove-path`` of a ``<name>.disabled-YYYYMMDD`` tree also needs this audit's ``SCOPE <path> disabled`` line
   (vfr-e.E1's inertness check, OD6b).
3. **Plan**: ``run-host.sh HOST --level L`` (a dry run). Its normalised plan digest (:func:`plan_digest`) must equal the
   digest the operator confirmed. The pre-state it prints (``dpkg -l``/``rpm -qa`` or ``pip freeze``, or the path's
   listing) is saved and recorded in a ledger ``note`` row with its sha256.
4. **Apply**: ``run-host.sh HOST --level L --apply --expect-digest D``. ``run-host`` and each level script recompute
   the plan on the host just before changing anything and abort on a mismatch (exit 5). The executor's argv never
   carries ``--reboot``, ``--all-updates``, ``--node-major``, ``--allow-fresh`` or ``--restart-services``, and level
   mode never runs L3 or L5 (:func:`check_argv`).
5. **Post-audit**: a fresh L1 audit. A post-audit worse than the pre-audit, a failed health check (exit 6: reverted,
   7: not reverted) or a failed step escalates and stops the host; exit 4 (anywhere) escalates and refuses everything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from agentteams.vulnscan import escalations, grant, grant_v2, ledger, paths, subproc_confine, subproc_confine_ssh
from agentteams.vulnscan.executor_env import ExecError

#: Flags the executor never passes to run-host.sh for a level (S1); :func:`check_argv` refuses them.
FORBIDDEN_FLAGS: tuple[str, ...] = ("--reboot", "--all-updates", "--node-major", "--allow-fresh", "--restart-services")
#: Kit scripts a level never runs (S1: never L5; never the Node runtime either).
FORBIDDEN_SCRIPTS: tuple[str, ...] = ("L3-node-runtime.sh", "L5-containers.sh")
CONFIRM_KEY = "confirm_sha256"
#: run-host.sh level exits: what the executor does with each after the change started.
EXIT_MISMATCH, EXIT_REVERTED, EXIT_NOT_REVERTED = 5, 6, 7
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DISABLED = re.compile(r"\.disabled-20\d\d(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_RESULT = re.compile(r"^RESULT (clean|action-needed|COMPROMISE)", re.M)


class Target(NamedTuple):
    """A parsed ``server-apply`` target."""

    host: str
    level: str
    path: str


def parse_target(target: str) -> Target:
    """Parse ``HOST:LEVEL`` or ``HOST:remove-path:/exact/path``.

    Args:
        target: The op target.

    Returns:
        The parsed target.

    Raises:
        ExecError: Malformed host, unknown level, or a path where none belongs / none where one does.
    """
    host, _, rest = str(target).partition(":")
    level, _, path = rest.partition(":")
    try:
        grant_v2.pair(f"{host}:{level}", what="server-apply target")
    except ValueError as exc:
        raise ExecError(f"server-apply targets are HOST:LEVEL or HOST:remove-path:/path ({exc})") from exc
    if level not in grant_v2.LEVELS:
        raise ExecError(f"unknown level {level!r} (one of {', '.join(grant_v2.LEVELS)})")
    if (level == "remove-path") != bool(path):
        raise ExecError("remove-path names exactly one path (HOST:remove-path:/path); other levels take none")
    if path and (why := grant_v2.trust_root_problem(path)):
        raise ExecError(f"remove-path refuses {path}: {why}")
    return Target(host, level, path)


def plan_digest(text: str) -> str:
    """Return the sha256 of the normalised plan in ``text`` (the kit's ``plan_digest`` shell function, in Python).

    PLAN lines only (``PLAN<TAB>...``), CR dropped, whitespace runs squeezed to one space, trailing space dropped,
    sorted bytewise and de-duplicated, each newline-terminated.

    Args:
        text: Kit output.

    Returns:
        Hex sha256 (of the empty string when there is no PLAN line).
    """
    lines = {re.sub(r"[ \t\v\f]+", " ", ln).rstrip(" ")
             for ln in text.replace("\r", "").split("\n") if ln.startswith("PLAN\t")}
    body = "".join(ln + "\n" for ln in sorted(lines, key=lambda s: s.encode()))
    return hashlib.sha256(body.encode()).hexdigest()


def plan_lines(text: str) -> list[str]:
    """Return the PLAN lines of kit output (for display).

    Args:
        text: Kit output.

    Returns:
        The lines, in order.
    """
    return [ln for ln in text.replace("\r", "").split("\n") if ln.startswith("PLAN\t")]


def level_args(t: Target, g: dict[str, Any]) -> list[str]:
    """Build run-host.sh's level arguments from the parsed target and the signed grant only.

    Args:
        t: Target.
        g: The covering v2 grant.

    Returns:
        ``["--level", L, ...]``: signed restart units (l2-security), signed major-allowed units (l4-pins/py-venv),
        the signed path (remove-path).
    """
    args = ["--level", t.level]
    for entry in g.get("restart_units", []) if t.level == "l2-security" else []:
        host, unit = entry.split(":", 1)
        args += ["--restart-unit", unit] if host == t.host else []
    for entry in g.get("major_allowed", []) if t.level in grant_v2.APPS_CONF_LEVELS else []:
        host, unit = entry.split(":", 1)
        args += ["--major-allowed", unit] if host == t.host else []
    if t.level == "remove-path":
        args += ["--remove-path", t.path]
    return args


def check_argv(args: list[str], *, apply: bool) -> None:
    """Refuse a run-host.sh argument list that is not exactly one level and nothing that widens it (S1).

    Args:
        args: Arguments after the host.
        apply: Whether this is the apply run.

    Raises:
        ExecError: Not exactly one ``--level``; a forbidden flag or script; ``--apply`` without ``--expect-digest``.
    """
    if args.count("--level") != 1:
        raise ExecError("server-apply runs exactly one --level")
    bad = [a for a in args if a in FORBIDDEN_FLAGS or a in FORBIDDEN_SCRIPTS]
    if bad:
        raise ExecError(f"server-apply never passes {', '.join(bad)}")
    if apply != ("--apply" in args) or (apply and "--expect-digest" not in args):
        raise ExecError("--apply goes with --expect-digest, and only on the apply run")


def removal_clearance(gov: Path, host: str, path: str) -> None:
    """Require the per-path C-5 decision row ``server-apply:remove-path:<host>:<path>``, cleared and verified.

    The row is read through the same gate the other destructive actions use (``security_gate.check_clearance``:
    an unretracted HALT on the id refuses, the row must be an approved author's, signed when signing is active,
    PASS or CONDITIONAL PASS, not consumed), read-only. On top of that the newest matching row must name **exactly**
    this id (a decorated id such as ``...:/path.old`` never clears ``/path``: Rule 12) and carry
    ``conditions_verified=verified``.

    Args:
        gov: The grant's signed governance team directory.
        host: Host.
        path: Exact path.

    Raises:
        ExecError: No such clearance.
    """
    from agentteams.cli import security_gate

    action = removal_action(host, path)
    ok, why = security_gate.check_clearance(gov, action=action)
    if not ok:
        raise ExecError(f"C-5: no usable clearance {action!r} in {gov}/references/security-decisions.log.csv: {why}")
    row = security_gate._latest_security_decision(gov, action=action) or {}
    if row.get("action_reviewed", "").strip() != action:
        raise ExecError(f"C-5: the newest matching row is {row.get('action_reviewed')!r}, not exactly {action!r}")
    if row.get("conditions_verified", "").strip().lower() != "verified":
        raise ExecError(f"C-5: clearance {action!r} is not conditions_verified=verified")


def removal_action(host: str, path: str) -> str:
    """Return the decision-log action id that clears one quarantine.

    Args:
        host: Host.
        path: Exact path.

    Returns:
        ``server-apply:remove-path:<host>:<path>``.
    """
    return f"server-apply:remove-path:{host}:{path}"


def precheck(target: str, payload: dict[str, Any] | None) -> Target:
    """Refuse before ``authorize`` (no use reserved): target grammar, host, host escalation, confirmed digest.

    Args:
        target: Op target.
        payload: ``{"confirm_sha256": hex}`` from the operator's ``vulnscan act``.

    Returns:
        The parsed target.

    Raises:
        ExecError: Any check fails.
    """
    from agentteams.vulnscan import executor

    t = parse_target(target)
    if t.host not in executor._kit_hosts():
        raise ExecError(f"host {t.host!r} is not a hosts.conf entry")
    esc = escalations.blocking(t.host)
    if esc is not None:
        raise ExecError(f"blocked by open escalation {esc.id} ({esc.kind}, scope {esc.scope}); see: vulnscan halts")
    if not _HEX64.match(str((payload or {}).get(CONFIRM_KEY, ""))):
        raise ExecError("server-apply runs only the plan the operator confirmed: vulnscan act server-apply "
                        f"{target} (it shows the plan and its digest, then asks)")
    return t


def gate(t: Target, g: dict[str, Any], conf: Path, *, now: datetime | None = None) -> None:
    """The grant-dependent pre-checks: restart window, signed apps.conf, remove-path clearance and replay.

    Args:
        t: Target.
        g: The covering v2 grant.
        conf: The config snapshot the run will read.
        now: Clock override (tests).

    Raises:
        ExecError: Any check fails.
    """
    restarts = t.level in grant_v2.RESTARTING_LEVELS or any(
        e.split(":", 1)[0] == t.host for e in g.get("restart_units", []) if t.level == "l2-security")
    if restarts and not grant_v2.in_window(g.get("restart_window", ""), now or datetime.now(UTC)):
        raise ExecError(f"{t.level} on {t.host} may restart a service: it runs only inside the signed restart window "
                        f"{g.get('restart_window') or '(none)'} UTC")
    if t.level in grant_v2.APPS_CONF_LEVELS:
        apps = conf / "apps.conf"
        have = hashlib.sha256(apps.read_bytes()).hexdigest() if apps.is_file() else "(no apps.conf)"
        if have != g.get("apps_conf_sha256"):
            raise ExecError(f"apps.conf sha256 {have} is not the signed {g.get('apps_conf_sha256')}: re-sign the grant "
                            "after reviewing apps.conf")
    if t.level == "remove-path":
        if any(r.get("op") == "server-apply" and r.get("kind") == "done" and r.get("target") == f"{t.host}:remove-path:"
               f"{t.path}" for r in ledger.read()):
            raise ExecError(f"{t.path} on {t.host} was already quarantined (ledger); restoring it is manual")
        removal_clearance(Path(str(g["governance_dir"])), t.host, t.path)


def snapshot_conf() -> Path:
    """Copy hosts.conf, apps.conf and ioc-*.txt (regular files, no symlinks) into a private run directory.

    The apps.conf digest check and the run then read the same bytes.

    Returns:
        The snapshot directory (0700); the caller removes it.

    Raises:
        ExecError: No hosts.conf, or a config file that is a symlink.
    """
    src = paths.server_kit_dir()
    dest = paths.state_dir() / "server-apply" / f"conf-{os.urandom(6).hex()}"
    dest.mkdir(parents=True, mode=0o700)
    for f in [src / "hosts.conf", src / "apps.conf", *sorted(src.glob("ioc-*.txt"))]:
        if f.is_symlink():
            raise ExecError(f"{f} is a symlink: refusing to read server-kit config through it")
        if f.is_file():
            fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as fh:
                (dest / f.name).write_bytes(fh.read(4_000_000))
    if not (dest / "hosts.conf").is_file():
        raise ExecError(f"no {src}/hosts.conf")
    return dest


def run_kit(script: str, host: str, args: list[str], conf: Path) -> subprocess.CompletedProcess:
    """Run one packaged kit script for ``host`` with config snapshot ``conf`` (confined when a scope is active).

    Args:
        script: ``remote.sh`` or ``run-host.sh``.
        host: Exact host.
        args: Script arguments after the host.
        conf: Config snapshot.

    Returns:
        The completed process.
    """
    from agentteams.vulnscan import executor

    logs = paths.state_dir() / "server-logs"
    logs.mkdir(parents=True, exist_ok=True)
    if subproc_confine.active():
        return subproc_confine_ssh.run_kit(executor.kit_dir() / script, host, args, conf_dir=conf, logs=logs,
                                           timeout=3600)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTTEAMS_SERVER_KIT")}
    env.update({"AGENTTEAMS_SERVER_KIT_CONFIG": str(conf), "AGENTTEAMS_SERVER_KIT_LOGS": str(logs),
                "AGENTTEAMS_VULNSCAN_NO_REPORT": "1"})
    return subprocess.run(["bash", str(executor.kit_dir() / script), host, *args], capture_output=True, text=True,
                          env=env, timeout=3600, check=False)


def _log(host: str, what: str, proc: subprocess.CompletedProcess) -> Path:
    logs = paths.state_dir() / "server-logs"
    logs.mkdir(parents=True, exist_ok=True)
    out = logs / f"{host}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-server-apply-{what}.log"
    out.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    return out


def audit(host: str, conf: Path) -> tuple[str, int, int]:
    """Run the L1 audit and judge it.

    Args:
        host: Host.
        conf: Config snapshot.

    Returns:
        ``(stdout, report exit code, number of findings)``.

    Raises:
        ExecError: The audit did not complete.
    """
    from agentteams.vulnscan import engine

    proc = run_kit("remote.sh", host, ["L1-audit.sh"], conf)
    log = _log(host, "audit", proc)
    if not _RESULT.search(proc.stdout or ""):
        raise ExecError(f"L1 audit of {host} did not complete (exit {proc.returncode}); log {log}")
    rep = engine.scan_inventory(proc.stdout, host)
    return proc.stdout, rep.exit_code(), len(rep.findings)


def escalate(kind: str, host: str, evidence: str, request_id: str) -> str:
    """Open an executor escalation (caller holds the ledger lock): ``ioc`` blocks everything, ``other`` the host.

    Args:
        kind: ``ioc`` or ``other``.
        host: Host.
        evidence: Executor-written text.
        request_id: The op's request id.

    Returns:
        The escalation id.
    """
    from agentteams.vulnscan import executor

    esc = escalations.record(kind, host, evidence, request_id=request_id, run_id=f"server-apply-{request_id}",
                             canonical=executor.canonical_target)
    return esc.id


def disabled_scope_problem(audit_text: str, path: str) -> str:
    """Return why a ``<name>.disabled-YYYYMMDD`` tree may not be quarantined yet, or "" (OD6b: E1's check first).

    Args:
        audit_text: The pre-audit's output.
        path: Exact path.

    Returns:
        A reason, or "" (also for a path that is not a disabled-tree name: its gate is the C-5 row).
    """
    if not _DISABLED.search(path):
        return ""
    lines = audit_text.replace("\r", "").split("\n")
    if any(ln.startswith(f"SCOPE\t{path}\tdisabled\t") for ln in lines):
        return ""
    return f"the latest L1 audit does not label {path} as a disabled, inert tree (no 'SCOPE {path} disabled' line)"


def run(target: str, g: dict[str, Any], *, request_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Perform one ``server-apply`` op (call inside ``ledger.locked()``, after ``authorize``).

    Args:
        target: ``HOST:LEVEL[:path]``.
        g: The covering grant (authorize's).
        request_id: The op's request id.
        payload: ``{"confirm_sha256": hex}``.

    Returns:
        Result fields for the ``done`` row.

    Raises:
        ExecError: Refused or failed; escalations are opened first where the conditions say so.
    """
    t = precheck(target, payload)
    confirmed = str((payload or {})[CONFIRM_KEY])
    if not grant_v2.is_v2(g) or not grant.covers(g, "server-apply", target):
        raise ExecError("the covering grant does not sign this host:level")
    conf = snapshot_conf()
    try:
        gate(t, g, conf)
        return _run(t, g, conf, confirmed=confirmed, request_id=request_id)
    finally:
        shutil.rmtree(conf, ignore_errors=True)


def _run(t: Target, g: dict[str, Any], conf: Path, *, confirmed: str, request_id: str) -> dict[str, Any]:
    pre_text, pre_exit, pre_n = audit(t.host, conf)
    if pre_exit == 4 or "RESULT COMPROMISE" in pre_text:
        eid = escalate("ioc", t.host, f"server-apply pre-audit of {t.host}: compromise indicators", request_id)
        raise ExecError(f"compromise indicators on {t.host} before {t.level}: escalation {eid} stops everything")
    if t.level == "remove-path" and (why := disabled_scope_problem(pre_text, t.path)):
        raise ExecError(f"remove-path refused: {why}")
    args = level_args(t, g)
    check_argv(args, apply=False)
    dry = run_kit("run-host.sh", t.host, args, conf)
    dry_log = _log(t.host, "plan", dry)
    if dry.returncode == 4:
        eid = escalate("ioc", t.host, f"server-apply plan on {t.host}: compromise indicators", request_id)
        raise ExecError(f"compromise indicators on {t.host}: escalation {eid} stops everything; log {dry_log}")
    if dry.returncode not in (0, 1):
        raise ExecError(f"the {t.level} plan on {t.host} did not complete (exit {dry.returncode}); log {dry_log}")
    digest = plan_digest(dry.stdout)
    printed = re.search(rf"^PLANDIGEST\t{re.escape(t.level)}\t([0-9a-f]{{64}})$", dry.stdout, re.M)
    if not printed or printed[1] != digest:
        raise ExecError(f"the kit's plan digest does not match the executor's own ({digest}); log {dry_log}")
    if digest != confirmed:
        raise ExecError(f"the {t.level} plan on {t.host} changed since you confirmed it ({digest} != {confirmed}): "
                        "nothing changed; review it again")
    pre = "".join(ln + "\n" for ln in dry.stdout.split("\n") if ln.startswith("PRESTATE\t"))
    pre_file = paths.state_dir() / "server-apply" / f"{t.host}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{t.level}.prestate"
    pre_file.parent.mkdir(parents=True, exist_ok=True)
    pre_file.write_text(pre, encoding="utf-8")
    os.chmod(pre_file, 0o600)
    ledger.append("note", grant_id=g["grant_id"], op="server-apply", target=":".join(filter(None, t)),
                  request_id=request_id, detail=json.dumps({
                      "prestate": str(pre_file), "prestate_sha256": hashlib.sha256(pre.encode()).hexdigest(),
                      "prestate_lines": pre.count("\n"), "plan_sha256": digest, "plan_lines": len(plan_lines(dry.stdout)),
                      "pre_audit": {"exit": pre_exit, "findings": pre_n}}))
    args = [*args, "--apply", "--expect-digest", digest]
    check_argv(args, apply=True)
    applied = run_kit("run-host.sh", t.host, args, conf)
    log = _log(t.host, "apply", applied)
    rc = applied.returncode
    if rc == 4:
        eid = escalate("ioc", t.host, f"server-apply {t.level} on {t.host}: compromise indicators", request_id)
        raise ExecError(f"compromise indicators on {t.host}: escalation {eid} stops everything; log {log}")
    if rc in (2, EXIT_MISMATCH):   # refused or the plan changed on the host: nothing was changed
        raise ExecError(f"{t.level} on {t.host} changed nothing (exit {rc}: "
                        f"{'the plan changed on the host' if rc == EXIT_MISMATCH else 'refused'}); log {log}")
    post_text, post_exit, post_n = audit(t.host, conf)
    if post_exit == 4 or "RESULT COMPROMISE" in post_text:
        eid = escalate("ioc", t.host, f"server-apply post-audit of {t.host}: compromise indicators", request_id)
        raise ExecError(f"compromise indicators on {t.host} after {t.level}: escalation {eid} stops everything")
    problem = ""
    if rc not in (0, 1):
        problem = {EXIT_REVERTED: "a health check failed; the pre-state was restored",
                   EXIT_NOT_REVERTED: "a health check failed and the pre-state was NOT restored"}.get(
                       rc, f"a step failed (exit {rc})")
    elif post_exit > pre_exit or post_n > pre_n:
        problem = f"the post-audit is worse (exit {pre_exit}->{post_exit}, findings {pre_n}->{post_n})"
    if problem:
        eid = escalate("other", t.host, f"server-apply {t.level} on {t.host}: {problem}", request_id)
        raise ExecError(f"{t.level} on {t.host}: {problem}; escalation {eid} stops {t.host}; log {log}")
    quarantined = re.findall(r"^QUARANTINED\t(\S+)\t(\S+)$", applied.stdout, re.M)
    return {"host": t.host, "level": t.level, **({"path": t.path} if t.path else {}), "plan_sha256": digest,
            "exit": rc, "log": str(log), "plan_log": str(dry_log), "prestate": str(pre_file),
            "pre_audit": {"exit": pre_exit, "findings": pre_n}, "post_audit": {"exit": post_exit, "findings": post_n},
            **({"quarantine": quarantined[0][1]} if quarantined else {})}


def preview(target: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Show the plan an apply would run, without changing anything or reserving a use (``vulnscan act`` step 1).

    Needs a covering grant with uses left, and passes the same gates an apply does, so the operator is never asked
    to confirm a plan the executor would refuse. Call inside the executor's confinement scope.

    Args:
        target: ``HOST:LEVEL[:path]``.
        now: Clock override (tests).

    Returns:
        ``{"plan": [lines], "plan_sha256": hex, "log": path}``.

    Raises:
        ExecError: Refused.
    """
    t = parse_target(target)
    pre = precheck(target, {CONFIRM_KEY: "0" * 64})
    grants, problems = grant.load_active()
    rows = ledger.read()
    usable = [g for g in grants if grant.covers(g, "server-apply", target)
              and ledger.uses(g["grant_id"], rows) < int(g["max_uses"])]
    if not usable:
        raise ExecError(f"not authorized: server-apply {target}: " + ("; ".join(problems) or "no v2 grant signs it"))
    g = min(usable, key=lambda g: g["expires"])
    conf = snapshot_conf()
    try:
        gate(pre, g, conf, now=now)
        args = level_args(t, g)
        check_argv(args, apply=False)
        dry = run_kit("run-host.sh", t.host, args, conf)
    finally:
        shutil.rmtree(conf, ignore_errors=True)
    log = _log(t.host, "preview", dry)
    if dry.returncode not in (0, 1):
        raise ExecError(f"the {t.level} plan on {t.host} did not complete (exit {dry.returncode}); log {log}")
    return {"plan": plan_lines(dry.stdout), "plan_sha256": plan_digest(dry.stdout), "log": str(log)}
