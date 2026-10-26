"""``python -m agentteams.vulnscan`` — scan and remediate known vulnerabilities.

| Command | What it does |
|---|---|
| ``scan PATH`` | Lockfiles and pins under PATH → OSV report |
| ``inventory LOG --host H`` | Server-kit audit log → report |
| ``runtimes`` | Node/PHP/Python on this machine vs live release data |
| ``fix PATH [--apply]`` | Plan, or make, dependency fixes in a working tree you control |
| ``init`` | Write a starter ``vulnscan.json`` (user config dir) |
| ``daily [--catch-up]`` | One full scheduled run (repos, runtimes, servers); ``--catch-up`` only when the last run is stale |
| ``schedule install\\|remove\\|status [--apply]`` | Manage the daily job |
| ``server-kit [DEST]`` | Export the Linux server scripts (keeps an existing ``hosts.conf`` / ``apps.conf``) |
| ``grant sign\\|list\\|revoke`` | Operator-signed standing remediation grants (sign: operator only, outside agent sessions; ``--from-registry`` proposes enrolled ``remediate`` projects, reviewed per owner) |
| ``act OP TARGET`` | Run one grant-covered action through the executor (ledgered) |
| ``serve-requests`` | Execute the agents' spooled requests that a grant covers |
| ``review [--ack]`` | After-action review of what unattended agents did; ``--ack`` records your review |
| ``agent-image build`` | Build the credential-free agent container image (goose pinned by version + sha256) |
| ``agent-selftest`` | Prove the container's isolation; agent mode is refused until this passes for the image |
| ``agent-run`` | Run the agent team once on today's scan (normally done by ``daily`` when ``agent.enabled``) |
| ``tools-image build`` / ``tools-selftest`` | Build the tools image git, npm and the server kit run in, and prove its isolation (:mod:`.tools_image`) |
| ``enroll`` / ``roots`` / ``status --project DIR`` | Enrol projects (operator only), effective scan roots, one project's status (:mod:`.project_cli`) |
| ``halts`` / ``resolve`` / ``say`` / ``publish-grant`` / ``publish`` / ``agent-chat`` | Operator loop (:mod:`.operator_cli`) |

Exit codes for ``scan``, ``inventory``, ``runtimes``, ``fix`` and ``daily``: 0 clean, 1 findings at
or above ``--fail-on`` (``fix``: something held back or refused), 3 partially unscanned (a feed or host
failed), 4 malicious package or compromise indicator. Every command uses 2 for usage or setup
errors (no config, refused signing). ``act`` gives 0 done, 1 refused, 3 failed. ``agent-run`` gives 0
completed, 3 not run or agent error. ``agent-selftest`` and ``tools-selftest`` give 0 pass, 1 fail.

Kept separate from the ``agentteams`` console script, like ``python -m agentteams.research``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from importlib import resources
from pathlib import Path

from agentteams.vulnscan import (
    agent_run,
    daily,
    engine,
    executor,
    fix,
    grant,
    operator_cli,
    paths,
    project_cli,
    review,
    schedule,
    tools_image,
)
from agentteams.vulnscan.report import Report

KIT_CONFIGS = ("hosts.conf", "apps.conf")


def _emit(report: Report, args: argparse.Namespace) -> int:
    report.apply_unscored(args.unscored_as)
    text = json.dumps(report.to_dict(), indent=2) if args.format == "json" else report.markdown()
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"report: {args.out}")
    else:
        print(text)
    return report.exit_code(args.fail_on, args.include_dev)


def export_server_kit(dest: Path) -> list[str]:
    """Copy the bundled server kit to ``dest``.

    Scripts are refreshed; the user's ``hosts.conf`` / ``apps.conf`` are never overwritten.
    They are created from the ``.example`` files only when missing.

    Args:
        dest: Target directory (created).

    Returns:
        Relative names written.
    """
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    kit = resources.files("agentteams") / "data" / "server_kit"
    for item in kit.iterdir():
        name = item.name
        if not item.is_file() or name.startswith("__"):
            continue
        target = dest / name
        target.write_bytes(item.read_bytes())
        if name.endswith(".sh"):
            target.chmod(0o755)
        written.append(name)
        if name.endswith(".conf.example"):
            real = dest / name[: -len(".example")]
            if not real.exists():   # examples arrive commented out: no example host is ever live
                text = item.read_text(encoding="utf-8")
                real.write_text("".join(ln if ln.startswith("#") or not ln.strip() else "# " + ln
                                        for ln in text.splitlines(keepends=True)), encoding="utf-8")
                written.append(real.name)
    return sorted(written)


def load_cfg() -> dict | None:
    """Load the daily config for a command, printing why when it cannot be used.

    Returns:
        The config, or None (the caller exits 2) when there is none or it is malformed.
    """
    try:
        return daily.load_config()
    except FileNotFoundError:
        print(f"no config at {paths.config_file()} — run `python -m agentteams.vulnscan init`", file=sys.stderr)
    except (OSError, ValueError) as exc:   # JSONDecodeError and a non-object file are ValueErrors
        print(f"config {paths.config_file()} is unusable: {exc}", file=sys.stderr)
    return None


def build_parser() -> argparse.ArgumentParser:
    """Build the vulnscan argument parser (every command, operator commands included).

    Returns:
        The parser; parsing does not run anything.
    """
    p = argparse.ArgumentParser(prog="python -m agentteams.vulnscan",
                                description="Find and remediate known-vulnerable dependencies and runtimes.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--format", choices=("md", "json"), default="md")
        sp.add_argument("--out", help="write the report here instead of stdout")
        sp.add_argument("--fail-on", default="high", choices=("critical", "high", "moderate", "low", "any", "none"))
        sp.add_argument("--include-dev", action="store_true", help="dev-only dependencies count toward --fail-on")
        sp.add_argument("--unscored-as", default="high", choices=("critical", "high", "moderate", "low"),
                        help="what an advisory with no CVSS vector or severity label counts as (default high)")

    sp = sub.add_parser("scan", help="scan manifests under a repo or a folder of repos")
    sp.add_argument("path", type=Path)
    sp.add_argument("--include-worktrees", action="store_true")
    common(sp)
    sp = sub.add_parser("inventory", help="analyze a server-kit audit log")
    sp.add_argument("log", type=Path)
    sp.add_argument("--host", default="")
    common(sp)
    sp = sub.add_parser("runtimes", help="check Node/PHP/Python on this machine")
    common(sp)
    sp = sub.add_parser("fix", help="plan (or --apply) dependency fixes in a working tree you control")
    sp.add_argument("path", type=Path)
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--allow-major", action="store_true")
    sp.add_argument("--allow-dirty", action="store_true")
    sp.add_argument("--cooldown-days", type=int, default=14)
    sp.add_argument("--include-worktrees", action="store_true")
    sp.add_argument("--no-npm", action="store_true")
    sp = sub.add_parser("init", help="write a starter config (never overwrites)")
    sp.add_argument("--repo-root", action="append", default=[])
    sp.add_argument("--server", action="append", default=[])
    sp = sub.add_parser("daily", help="one scheduled run over the configured repos and servers")
    sp.add_argument("--catch-up", action="store_true",
                    help="run only when the last completed run is older than stale_after_hours (or there is none)")
    sp = sub.add_parser("schedule", help="install/remove/status of the daily job")
    sp.add_argument("action", choices=("install", "remove", "status"))
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--hour", type=int)
    sp.add_argument("--minute", type=int)
    sp = sub.add_parser("server-kit", help="export the Linux server scripts")
    sp.add_argument("dest", nargs="?", type=Path, default=None)
    sp = sub.add_parser("grant", help="standing remediation grants (sign: operator only)")
    sp.add_argument("action", choices=("sign", "list", "revoke"))
    sp.add_argument("--op", action="append", default=[], choices=sorted(grant.OPS_V1 | grant.OPS_RESERVED))
    sp.add_argument("--repo-root", action="append", default=[])
    sp.add_argument("--from-registry", action="store_true",
                    help="propose the fix checkouts of registry entries at remediate (reviewed per owner)")
    sp.add_argument("--host", action="append", default=[])
    sp.add_argument("--days", type=int, default=14)
    sp.add_argument("--max-uses", type=int, default=50)
    sp.add_argument("--key-id", default="")
    sp.add_argument("--governance-dir", default="", help="team dir whose decision log the executor checks for HALTs")
    sp.add_argument("--grant-id", default="")
    sp.add_argument("--confirm-review-sha256", default=None)
    sp.add_argument("--allow-checkout-signing", action="store_true")
    sp = sub.add_parser("act", help="run one grant-covered action through the executor")
    sp.add_argument("op", choices=sorted(grant.OPS_V1))
    sp.add_argument("target")
    sp.add_argument("--rationale", default="operator-run")
    sub.add_parser("serve-requests", help="execute spooled agent requests that a grant covers")
    sp = sub.add_parser("review", help="after-action review of unattended remediation")
    sp.add_argument("--since", default=None, type=operator_cli.iso_date,
                    help="YYYY-MM-DD (default: since the last --ack)")
    sp.add_argument("--ack", action="store_true", help="record that you reviewed everything up to now")
    sp.add_argument("--note", default="")
    sp.add_argument("--gh-replies", action="store_true", help="also list replies on the agents' PRs (uses gh)")
    sp = sub.add_parser("agent-image", help="build the agent container image for one framework")
    sp.add_argument("action", choices=("build",))
    sp.add_argument("--framework", default=None, help="the agent CLI to install (default: agent.framework)")
    sp.add_argument("--cli-version", "--goose-version", dest="cli_version", required=True,
                    help="the CLI's release version (--goose-version: alias)")
    sp.add_argument("--cli-sha256", "--goose-sha256", dest="cli_sha256", required=True,
                    help="checksum of the linux release asset, from the release metadata (--goose-sha256: alias)")
    sp.add_argument("--python-image", default="python:3.12-slim", help="pin by digest: python:3.12-slim@sha256:...")
    sp.add_argument("--source", type=Path, default=None)
    sub.add_parser("agent-selftest", help="prove the agent container's isolation")
    sub.add_parser("agent-run", help="run the agent team once on the latest scan")
    operator_cli.register(sub)
    project_cli.register(sub)
    tools_image.register(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    """Run the vulnscan CLI.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)
    if args.cmd != "schedule" or args.action == "status":   # install/remove print their own plan only
        warn = daily.stale_warning()
        if warn:
            print(warn, file=sys.stderr)

    if args.cmd in ("scan", "fix") and not args.path.exists():
        print(f"no such path: {args.path}", file=sys.stderr)
        return 2
    if args.cmd == "scan":
        return _emit(engine.scan_path(args.path, include_worktrees=args.include_worktrees), args)
    if args.cmd == "inventory":
        try:
            text = args.log.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"cannot read the audit log: {exc}", file=sys.stderr)
            return 2
        return _emit(engine.scan_inventory(text, args.host or args.log.stem), args)
    if args.cmd == "runtimes":
        return _emit(engine.local_runtimes(), args)
    if args.cmd == "fix":
        res = fix.fix_path(args.path, apply=args.apply, allow_major=args.allow_major,
                           cooldown_days=args.cooldown_days, include_worktrees=args.include_worktrees,
                           allow_dirty=args.allow_dirty, npm=not args.no_npm)
        print(res.markdown())
        return res.exit_code()
    if args.cmd == "init":
        path = daily.init_config(repo_roots=args.repo_root, servers=args.server)
        print(f"config: {path}\nEdit it, then: python -m agentteams.vulnscan schedule install --apply")
        return 0
    if args.cmd == "daily":
        cfg = load_cfg()
        if cfg is None:
            return 2
        if args.catch_up:
            result = daily.catch_up(cfg)
            if result is None:
                return 0   # the last run is recent: nothing to catch up, quietly
            report, code, run_dir = result
        else:
            report, code, run_dir = daily.run_daily(cfg)
        print(f"{len(report.findings)} findings; exit {code}; summary: {run_dir / 'summary.md'}")
        return code
    if args.cmd == "schedule":
        if args.action == "status":
            print(schedule.status())
            return 0
        when: dict = {}
        if paths.config_file().exists():
            cfg = load_cfg()
            if cfg is None:
                return 2
            when = cfg.get("schedule") if isinstance(cfg.get("schedule"), dict) else {}
        try:
            hour = args.hour if args.hour is not None else int(when.get("hour", 7))
            minute = args.minute if args.minute is not None else int(when.get("minute", 30))
        except (TypeError, ValueError) as exc:
            print(f"config schedule is unusable: {exc}", file=sys.stderr)
            return 2
        plan = (schedule.install(hour, minute, apply=args.apply) if args.action == "install"
                else schedule.remove(apply=args.apply))
        for path, content in plan.files.items():
            print(f"{'DELETE' if not content else 'WRITE'} {path}" + (f"\n{content}" if content else ""))
        for cmd in plan.commands:
            print(f"{'RUN' if args.apply else 'WOULD RUN'} {' '.join(cmd)}")
        for note in plan.notes:
            print(f"NOTE {note}")
        for err in plan.errors:
            print(f"ERROR {err}", file=sys.stderr)
        if not args.apply:
            print("dry run — re-run with --apply")
        return 1 if plan.errors else 0
    if args.cmd == "grant":
        if args.action == "list":
            valid, problems = grant.load_active()
            for g in valid:
                print(json.dumps({k: g[k] for k in grant.FIELDS}))
            for pr in problems:
                print(f"INVALID {pr}", file=sys.stderr)
            return 0
        if args.action == "revoke":
            ok = grant.revoke(args.grant_id)
            print("revoked" if ok else "no such grant")
            return 0 if ok else 1
        capped = grant.cap_problem()
        if capped:
            print(f"refused: {capped}", file=sys.stderr)
            return 2
        roots = args.repo_root
        if args.from_registry:
            cfg = load_cfg() if paths.config_file().exists() else {}
            roots = None if cfg is None else grant.roots_from_registry(cfg, args.repo_root, args.governance_dir)
            if roots is None:
                return 2
        try:
            rec = grant.build(ops=args.op, repo_roots=roots, hosts=args.host, days=args.days,
                              max_uses=args.max_uses, key_id=args.key_id, governance_dir=args.governance_dir)
        except grant.GrantError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        signed = grant.sign(rec, confirm_sha256=args.confirm_review_sha256, allow_checkout=args.allow_checkout_signing)
        if signed is None:
            return 1
        try:
            grant.verify(signed)
        except grant.GrantError as exc:
            print(f"refused: the signed grant does not verify: {exc}", file=sys.stderr)
            return 2
        print(f"installed {grant.install(signed)}")
        return 0
    if args.cmd in ("act", "serve-requests"):
        cfg = load_cfg()
        if cfg is None:
            return 2
        if args.cmd == "act":
            res = executor.act(args.op, args.target, cfg, rationale=args.rationale)
            print(json.dumps(res, indent=2))
            return {"done": 0, "refused": 1}.get(res["status"], 3)
        import fcntl

        agent_run.run_lock_path().parent.mkdir(parents=True, exist_ok=True)
        with open(agent_run.run_lock_path(), "a+") as lock:
            try:   # never alongside a live run: it would take that run's report and escalations
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("an agent run or debrief holds the run lock; its requests are served by it", file=sys.stderr)
                return 2
            results = executor.serve_requests(cfg)
        print(json.dumps(results, indent=2))
        return 0
    if args.cmd in ("agent-image", "agent-selftest", "agent-run"):
        cfg = load_cfg()
        if cfg is None:
            return 2
        if args.cmd == "agent-image":
            try:
                print(agent_run.build_image(cfg, cli_version=args.cli_version, cli_sha256=args.cli_sha256,
                                            python_image=args.python_image, source=args.source,
                                            framework=args.framework))
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                print(f"agent image build failed: {exc}", file=sys.stderr)
                return 1
            return 0
        if args.cmd == "agent-selftest":
            try:
                res = agent_run.selftest(cfg)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:   # docker failed mid-test
                print(f"agent-selftest failed: {exc}", file=sys.stderr)
                return 1
            if res.get("error"):
                print(f"agent-selftest: {res['error']}", file=sys.stderr)
            print(json.dumps(res, indent=2))
            return 0 if res["passed"] else 1
        runs = paths.run_dirs()
        if not runs:
            print("no scan to work on — run `python -m agentteams.vulnscan daily` first", file=sys.stderr)
            return 2
        status = agent_run.run_agent(cfg, runs[-1])
        print(json.dumps(status, indent=2))
        return 0 if status.get("state") == "completed" else 3
    if args.cmd == "review":
        if args.ack:
            row = review.acknowledge(args.note)
            print(f"acknowledged (ledger row {row['seq']})")
            return 0
        print(review.build(since=args.since, gh_replies=args.gh_replies))
        return 0
    if args.cmd in project_cli.COMMANDS:
        return project_cli.dispatch(args, daily.load_config)
    if args.cmd in operator_cli.COMMANDS:
        return operator_cli.dispatch(args, daily.load_config)
    if args.cmd in tools_image.COMMANDS:
        return tools_image.dispatch(args, daily.load_config)
    if args.cmd == "server-kit":
        dest = args.dest or paths.server_kit_dir()
        names = export_server_kit(dest)
        print(f"server kit in {dest}: {', '.join(names)}")
        print(f"Edit {dest / 'hosts.conf'} and {dest / 'apps.conf'}, then: {dest / 'remote.sh'} all L1-audit.sh")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

