"""The daily run: scan the user's repos, local runtimes and servers; plan or stage fixes; notify.

Configuration is ``vulnscan.json`` (see :data:`DEFAULT_CONFIG`) in the user's config
dir. That file names the user's own repos and hosts, so it is never inside a repository.

One run:

1. Scans each ``repo_roots`` entry, writes a fix plan per root, and in
   ``fix_mode = "apply-worktree"`` stages non-major, cooldown-clean fixes in a fresh git
   worktree per repository. It never commits, pushes or merges.
2. Checks the runtimes on this machine (``local_runtimes``).
3. Runs the *packaged* server kit's read-only ``L1-audit.sh`` (with the user's kit dir as its
   config) on each ``servers`` host and analyzes
   its inventory. Server *changes* are never automatic; they remain a person running
   ``run-host.sh --apply``.
4. Writes ``<state>/<date>/summary.{md,json}``, marking findings new since the last run.
5. Updates ``<state>/heartbeat.json`` and sends a notification.

A host that cannot be reached, or a feed that fails, makes the run *unscanned* (exit 3).
It is never reported as clean.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import re
import subprocess
from pathlib import Path

from agentteams.vulnscan import engine, executor, fix, notify, paths
from agentteams.vulnscan.report import EXIT_COMPROMISE, Report, new_finding_keys

#: The audit's final verdict line; its presence proves the script ran to completion.
_AUDIT_DONE = re.compile(r"^RESULT (clean|action-needed|COMPROMISE)", re.M)

DEFAULT_CONFIG: dict = {
    "repo_roots": [],            # e.g. ["~/code"] — a repo or a folder of repos
    "include_worktrees": False,
    "servers": [],               # host names from <server kit>/hosts.conf
    "fail_on": "high",           # critical | high | moderate | low | any | none
    "include_dev": False,
    "cooldown_days": 14,         # Rule S-10 release cooldown
    "local_runtimes": True,
    "fix_mode": "plan",          # plan | apply-worktree
    "allow_major": False,
    "notify": "desktop",         # none | desktop | command:<program and args>
    "schedule": {"hour": 7, "minute": 30},
    "stale_after_hours": 26,
    "governance_dir": "",        # team dir whose decision log the executor checks for HALTs (C-2)
    # Agent mode (off by default): after the scan, the team runs in an isolated container and
    # requests actions that the executor performs only under a signed grant. See agent_run.DEFAULT_AGENT.
    "agent": {"enabled": False, "team_dir": "", "key_file": "", "budget_usd": 2.0},
}


def load_config(path: Path | None = None) -> dict:
    """Read the config, filling defaults for missing keys.

    Args:
        path: Config file; defaults to :func:`paths.config_file`.

    Returns:
        The merged config.

    Raises:
        FileNotFoundError: No config yet (run ``vulnscan init``).
        ValueError: The file is not a JSON object.
    """
    path = path or paths.config_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return {**DEFAULT_CONFIG, **data}


def init_config(path: Path | None = None, *, repo_roots: list[str] | None = None,
                servers: list[str] | None = None) -> Path:
    """Write a starter config if none exists. Never overwrites.

    Args:
        path: Target file; defaults to :func:`paths.config_file`.
        repo_roots: Initial repo roots.
        servers: Initial server host names.

    Returns:
        The config path.
    """
    path = path or paths.config_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {**DEFAULT_CONFIG, "repo_roots": repo_roots or [], "servers": servers or []}
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path


def heartbeat_path() -> Path:
    """Return the heartbeat file path.

    Returns:
        ``<state>/heartbeat.json``.
    """
    return paths.state_dir() / "heartbeat.json"


def stale_warning(max_hours: float | None = None) -> str:
    """Return a warning when a configured daily run has not completed recently.

    Args:
        max_hours: Threshold; defaults to the config's ``stale_after_hours``.

    Returns:
        Warning text, or "" when fresh or when no daily run is configured.
    """
    if not paths.config_file().exists():
        return ""
    if max_hours is None:
        try:
            max_hours = float(load_config().get("stale_after_hours", 26))
        except (OSError, ValueError):
            max_hours = 26.0
    hb = heartbeat_path()
    if not hb.exists():
        return "vulnscan: no daily run has completed yet — run `python -m agentteams.vulnscan daily` or `schedule install`"
    try:
        last = _dt.datetime.fromisoformat(json.loads(hb.read_text(encoding="utf-8"))["last_run"])
    except (OSError, ValueError, KeyError) as exc:
        return f"vulnscan: heartbeat unreadable ({exc}) — the daily run may be broken"
    age = (_dt.datetime.now(_dt.UTC) - last).total_seconds() / 3600
    if age > max_hours:
        return f"vulnscan: last daily run was {age:.0f}h ago (> {max_hours:.0f}h) — check `vulnscan schedule status`"
    try:
        last_exit = int(json.loads(hb.read_text(encoding="utf-8")).get("exit", 0))
    except (OSError, ValueError):
        last_exit = 0
    if last_exit == 3:
        return "vulnscan: the last daily run was only partially scanned — see its summary.md"
    if last_exit == 4:
        return "vulnscan: the last daily run found MALICIOUS packages or compromise indicators — see its summary.md"
    return ""


def _previous_summary(state: Path, today: str) -> Report | None:
    runs = [d for d in paths.run_dirs(state) if d.name < today]
    if not runs:
        return None
    try:
        return Report.from_dict(json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in ``repo`` with the repository's own hooks disabled (unattended, scheduled use)."""
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def stage_worktree_fixes(repo: Path, cfg: dict, date: str) -> tuple[str, str]:
    """Create ``<repo>.worktrees/vulnscan-<date>`` off the default branch and apply fixes there.

    Args:
        repo: Repository root.
        cfg: Daily config.
        date: Run date (YYYY-MM-DD).

    Returns:
        (worktree path or "", markdown fix result or error text).
    """
    if not (repo / ".git").exists():
        return "", f"{repo}: not a git repository"
    _git(repo, "fetch", "-q", "origin")
    head = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() or "HEAD"
    wt = repo.parent / f"{repo.name}.worktrees" / f"vulnscan-{date}"
    branch = f"vulnscan/{date}"
    if not wt.exists():
        made = _git(repo, "worktree", "add", "-q", "-b", branch, str(wt), head)
        if made.returncode:
            return "", f"{repo}: worktree add failed: {made.stderr.strip()}"
    res = fix.fix_path(wt, apply=True, allow_major=False,   # unattended: never a major jump
                       cooldown_days=int(cfg["cooldown_days"]))
    return str(wt), res.markdown()


def _scan_server(host: str, kit: Path, out_dir: Path) -> Report:
    """Run the *packaged* kit's read-only L1 audit on one host and analyze its inventory (P12).

    The scripts always come from :func:`executor.kit_dir` (never the user-writable copy); ``kit``
    is only the config dir (``hosts.conf``, ``apps.conf``, optional extra ``ioc-*.txt`` data),
    passed as ``AGENTTEAMS_SERVER_KIT_CONFIG`` exactly as :func:`executor.server_op` does.

    Args:
        host: hosts.conf name.
        kit: The user's server-kit config dir (:func:`paths.server_kit_dir`).
        out_dir: Run directory (logs go here).

    Returns:
        The host's report; a missing ``hosts.conf``, a timeout or an incomplete audit is a feed error.
    """
    if not (kit / "hosts.conf").is_file():
        rep = Report(target=host)
        rep.feed_errors.append(f"{host}: no hosts.conf in {kit} (run `vulnscan server-kit` and add the host)")
        return rep
    remote = executor.kit_dir() / "remote.sh"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTTEAMS_SERVER_KIT")}
    env.update({"AGENTTEAMS_SERVER_KIT_CONFIG": str(kit), "AGENTTEAMS_SERVER_KIT_LOGS": str(out_dir),
                "AGENTTEAMS_VULNSCAN_NO_REPORT": "1"})
    try:
        proc = subprocess.run(["bash", str(remote), host, "L1-audit.sh"], capture_output=True, text=True,
                              env=env, timeout=1800, check=False)
    except subprocess.TimeoutExpired:
        rep = Report(target=host)
        rep.feed_errors.append(f"{host}: audit timed out")
        return rep
    (out_dir / f"server-{host}.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    rep = engine.scan_inventory(proc.stdout, host)
    if proc.returncode == 255 or not _AUDIT_DONE.search(proc.stdout):
        rep.feed_errors.append(f"{host}: audit did not complete (ssh exit {proc.returncode}) — see server-{host}.log")
    return rep


def run_daily(cfg: dict, *, today: str | None = None) -> tuple[Report, int, Path]:
    """Execute one daily run.

    Args:
        cfg: Config from :func:`load_config`.
        today: Override the run date (tests).

    Returns:
        (aggregate report, exit code, run directory).
    """
    today = today or _dt.date.today().isoformat()
    state = paths.state_dir()
    run_dir = state / today
    run_dir.mkdir(parents=True, exist_ok=True)
    total = Report(target="daily")
    staged: list[str] = []
    try:
        _collect(cfg, today, run_dir, total, staged)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        total.feed_errors.append(f"daily run stopped early: {type(exc).__name__}: {exc}")
    report, code, run_dir = _finish(cfg, state, today, run_dir, total, staged)
    agent = cfg.get("agent") or {}
    if agent.get("enabled"):
        from agentteams.vulnscan import agent_run

        try:
            status = agent_run.run_agent(cfg, run_dir)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:   # docker / launcher failure
            status = {"state": "agent-error", "run_state": "agent-error",
                      "reason": f"launcher failed: {type(exc).__name__}: {exc}"[:300]}
        with open(run_dir / "summary.md", "a", encoding="utf-8") as fh:   # before publishing, so it is included
            fh.write(f"\n## Agent run\n\n- state: **{status.get('state')}**"
                     f"{' — ' + str(status.get('reason')) if status.get('reason') else ''}\n"
                     "- review what it did: `python -m agentteams.vulnscan review`\n")
        published = publish_run(cfg, run_dir, str(status.get("run_id") or agent_run.executor.new_run_id(run_dir)))
        hb = json.loads(heartbeat_path().read_text(encoding="utf-8"))
        hb["agent"] = {k: status.get(k) for k in ("state", "reason", "exit", "timed_out", "spend", "run_id")}
        hb["publish"] = published
        heartbeat_path().write_text(json.dumps(hb, indent=2), encoding="utf-8")
        if status.get("state") != "completed":
            notify.send(cfg, "agent-not-completed", state=str(status.get("state")))
    return report, code, run_dir


def publish_run(cfg: dict, run_dir: Path, run_id: str) -> dict | None:
    """Publish the run's report, review and summary to the ops repository when a publish grant is active.

    Publishing is not blocked by escalations (the operator needs the report most then), only by a
    ``vulnscan-publish`` HALT, an invalid grant or a failed preflight / content scan. Every agent
    text in the files is fenced: the report by :mod:`.report_store`, the review by
    ``review.build(for_publish=True)``.

    Args:
        cfg: Daily config.
        run_dir: The run directory.
        run_id: The run id.

    Returns:
        The publish result, or None when no publish grant is active.
    """
    from agentteams.vulnscan import publish, report_store, review   # lazy: publish imports executor

    grants, _problems = publish.load_active()
    if not grants:
        return None
    summary = run_dir / "summary.md"
    try:
        files = {"report.md": report_store.render_for_review(run_dir), "review.md": review.build(for_publish=True),
                 "summary.md": summary.read_text(encoding="utf-8") if summary.is_file() else "_no summary_\n"}
    except (OSError, ValueError) as exc:
        notify.send(cfg, "publish-failed")
        return {"status": "failed", "detail": f"could not assemble the files: {exc}"[:300]}
    result = publish.publish(grants[0], run_dir, run_id, files)
    notify.send(cfg, "publish-done" if result.get("status") == "done" else "publish-failed")
    return {k: result.get(k) for k in ("status", "commit", "path", "detail")}


def _collect(cfg: dict, today: str, run_dir: Path, total: Report, staged: list[str]) -> None:
    """Scan repos, local runtimes and servers into ``total``."""
    for root_s in cfg.get("repo_roots", []):
        root = Path(root_s).expanduser()
        if not root.is_dir():
            total.feed_errors.append(f"repo root missing: {root_s}")
            continue
        rep = engine.scan_path(root, include_worktrees=bool(cfg["include_worktrees"]))
        slug = root.name or "root"
        (run_dir / f"repos-{slug}.json").write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
        total.merge(rep)
        for f in rep.findings:
            f.target = f"{root_s}/{f.target}" if f.target != "." else root_s
        fixable = sorted({f.target for f in rep.findings if f.kind == "package" and f.fix and not f.malicious})
        if cfg.get("fix_mode") == "apply-worktree":
            for t in fixable:
                repo = Path(t).expanduser()
                wt, md = stage_worktree_fixes(repo, cfg, today)
                staged.append(f"- `{t}` → `{wt or 'not staged'}`")
                (run_dir / f"fix-{repo.name}.md").write_text(md, encoding="utf-8")
        elif fixable:
            plan = fix.fix_path(root, apply=False, allow_major=bool(cfg["allow_major"]),
                                cooldown_days=int(cfg["cooldown_days"]),
                                include_worktrees=bool(cfg["include_worktrees"]), npm=False)
            (run_dir / f"fixplan-{slug}.md").write_text(plan.markdown(), encoding="utf-8")
            total.feed_errors += [f"fix plan: {e}" for e in plan.feed_errors]

    if cfg.get("local_runtimes", True):
        total.merge(engine.local_runtimes(platform.node() or "localhost"))

    kit = paths.server_kit_dir()
    for host in cfg.get("servers", []):
        total.merge(_scan_server(host, kit, run_dir))


def _finish(cfg: dict, state: Path, today: str, run_dir: Path, total: Report,
            staged: list[str]) -> tuple[Report, int, Path]:
    """Write summary + heartbeat, notify, and return (report, exit code, run dir)."""
    previous = _previous_summary(state, today)
    new = new_finding_keys(total, previous)
    code = total.exit_code(cfg.get("fail_on", "high"), bool(cfg.get("include_dev")))
    md = total.markdown(new_keys=new)
    if staged:
        md += "\n## Fixes staged in worktrees (review, test, commit, open a PR)\n\n" + "\n".join(staged) + "\n"
    (run_dir / "summary.md").write_text(md, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(total.to_dict(), indent=2), encoding="utf-8")
    heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path().write_text(json.dumps({
        "last_run": _dt.datetime.now(_dt.UTC).isoformat(), "exit": code, "findings": len(total.findings),
        "new": len(new), "unscanned": len(total.feed_errors), "summary": str(run_dir / "summary.md")}, indent=2),
        encoding="utf-8")
    if code or new:
        label = {EXIT_COMPROMISE: "COMPROMISE INDICATORS", 3: "partially unscanned", 1: "action needed"}.get(code, "new findings")
        notify.send(cfg, "run-done", state=label, findings=len(total.findings), new=len(new))
    return total, code, run_dir
