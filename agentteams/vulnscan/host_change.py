"""The operator's side of the host-changing ops: ``vulnscan act server-apply|runtime-patch`` and v2 ``grant sign`` fields.

``act`` for :data:`ops.OPERATOR_ONLY_OPS` is two steps in one command: a **preview** (the dry-run plan, its digest;
nothing changes, no grant use is reserved), then the operator's confirmation of exactly that digest — ``y`` on a
terminal, or ``--confirm-plan-sha256 <digest>`` — and only then ``executor.act``, which recomputes the plan and
refuses unless it still has that digest. "Each lever still asks the operator at run time."
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from typing import Any

from agentteams.vulnscan import paths, runtime_patch, server_apply, subproc_confine
from agentteams.vulnscan.executor_env import ExecError

#: ``grant sign`` options (dest, help) for the v2 fields; each is repeatable unless it is a single value.
V2_OPTIONS: tuple[tuple[str, str, str, bool], ...] = (
    ("--level", "server_levels", "HOST:LEVEL a server-apply grant signs (l2-security|l4-pins|py-venv|remove-path)", True),
    ("--restart-unit", "restart_units", "HOST:UNIT.service l2-security may restart (OD4)", True),
    ("--restart-window", "restart_window", "HH:MM-HH:MM UTC inside which a restarting level runs (OD4)", False),
    ("--apps-conf-sha256", "apps_conf_sha256", "the reviewed apps.conf sha256, or 'auto' for the current file", False),
    ("--major-allowed", "major_allowed", "HOST:UNIT.service whose pins may cross a major version", True),
    ("--remove-path", "remove_paths", "HOST:/exact/path remove-path may quarantine", True),
    ("--runtime", "runtimes", "Homebrew formula runtime-patch may upgrade (same major)", True),
    ("--ancestor", "ancestor", "the revoked grant this one re-issues (Rule 15 lineage)", False),
)


def add_grant_options(sp: Any) -> None:
    """Add the v2 ``grant sign`` options to an argparse sub-parser.

    Args:
        sp: The ``grant`` sub-parser.
    """
    for flag, dest, text, many in V2_OPTIONS:
        if many:
            sp.add_argument(flag, dest=dest, action="append", default=[], help=text)
        else:
            sp.add_argument(flag, dest=dest, default="", help=text)


def grant_fields(args: Any) -> dict[str, Any]:
    """Return the v2 keyword arguments for ``grant.build`` from parsed ``grant sign`` options.

    Args:
        args: Parsed namespace.

    Returns:
        ``{field: value}`` (``apps_conf_sha256 auto`` resolved to the current apps.conf's sha256).
    """
    out = {dest: getattr(args, dest) for _, dest, _, _ in V2_OPTIONS}
    if out["apps_conf_sha256"] == "auto":
        apps = paths.server_kit_dir() / "apps.conf"
        out["apps_conf_sha256"] = hashlib.sha256(apps.read_bytes()).hexdigest() if apps.is_file() else ""
    return out


def operator_act(op: str, target: str, cfg: dict[str, Any], *, confirm_sha256: str | None,
                 act: Callable[..., dict[str, Any]], ask: Callable[[str], str] = input,
                 say: Callable[[str], None] = print, tty: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Preview, confirm, then run one host-changing op.

    Args:
        op: ``server-apply`` or ``runtime-patch``.
        target: Its target.
        cfg: Daily config (the confinement settings for the preview's ssh).
        confirm_sha256: ``--confirm-plan-sha256`` (non-terminal confirmation of an earlier preview), or None.
        act: ``executor.act``.
        ask: Prompt function.
        say: Output function.
        tty: Terminal check (default: stdin and stdout are terminals).

    Returns:
        ``executor.act``'s result, or ``{"status": "refused", "detail": ...}``.
    """
    try:
        if op == "server-apply":
            with subproc_confine.scope(cfg, scratch=[]):
                shown = server_apply.preview(target)
        else:
            shown = runtime_patch.preview(target)
    except (ExecError, OSError, ValueError, subproc_confine.Unavailable) as exc:
        return {"status": "refused", "detail": f"preview refused: {exc}"}
    digest = shown["plan_sha256"]
    say(f"{op} {target}: the plan ({len(shown['plan'])} line(s)):")
    for line in shown["plan"] or ["(empty: nothing would change)"]:
        say("  " + "".join(ch if ch.isprintable() or ch == "\t" else "?" for ch in line).replace("\t", "  "))
    say(f"plan sha256: {digest}")
    if confirm_sha256 is not None:
        if confirm_sha256 != digest:
            return {"status": "refused", "detail": f"the plan's digest is {digest}, not the confirmed {confirm_sha256}"}
    elif tty() if tty else (sys.stdin.isatty() and sys.stdout.isatty()):
        if ask(f"Apply exactly this plan ({op} {target})? [y/N] ").strip().lower() not in ("y", "yes"):
            return {"status": "refused", "detail": "not confirmed by the operator"}
    else:
        return {"status": "refused", "detail": f"confirm on a terminal, or re-run with --confirm-plan-sha256 {digest}"}
    return act(op, target, cfg, rationale="operator-run (confirmed plan)", payload={server_apply.CONFIRM_KEY: digest})
