"""The daily run: scan the user's repos, local runtimes and servers; plan or stage fixes; notify.

Configuration is ``vulnscan.json`` (see :data:`DEFAULT_CONFIG`) in the user's config
dir. That file names the user's own repos and hosts, so it is never inside a repository.

One run:

1. Scans each ``repo_roots`` entry, writes a fix plan per git repository (``fixplan-<name>-<digest>.md``,
   naming the repository as the ``stage-fix`` target), and in
   ``fix_mode = "apply-worktree"`` stages non-major, cooldown-clean fixes in a fresh git
   worktree per repository. It never commits, pushes or merges.
2. Checks the runtimes on this machine (``local_runtimes``).
3. Runs the *packaged* server kit's read-only ``L1-audit.sh`` (with the user's kit dir as its
   config) on each ``servers`` host and analyzes
   its inventory. Server *changes* are never automatic; they remain a person running
   ``run-host.sh --apply``.
4. Writes ``<state>/<date>/summary.{md,json}``, marking findings new since the last run.
5. Updates ``<state>/heartbeat.json`` (atomically) and sends a notification.

Only one daily run works at a time: a second one finds ``<state>/daily.lock`` held and exits 3
without touching the first run's files. A host that cannot be reached, a feed that fails, or a
summary / heartbeat that cannot be written makes the run *unscanned* (exit 3). It is never
reported as clean.
"""

from __future__ import annotations

import copy
import datetime as _dt
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentteams.vulnscan import engine, executor, fix, notify, paths
from agentteams.vulnscan.manifests import discover
from agentteams.vulnscan.report import (EXIT_COMPROMISE, EXIT_FINDINGS, EXIT_UNSCANNED, Report,
                                        new_finding_keys)

#: The audit's final verdict line; its presence proves the script ran to completion.
_AUDIT_DONE = re.compile(r"^RESULT (clean|action-needed|COMPROMISE)", re.M)

#: ``run-done`` notification state per exit code; any other code with new findings is NEW_FINDINGS_LABEL.
RUN_LABELS: dict[int, str] = {EXIT_COMPROMISE: "COMPROMISE INDICATORS", EXIT_UNSCANNED: "partially unscanned",
                              EXIT_FINDINGS: "action needed"}
NEW_FINDINGS_LABEL = "new findings"

_FETCH_TIMEOUT = 300          # seconds for ``git fetch`` (an unreachable remote must not hang the daily run)
_GIT_TIMEOUT = 600            # seconds for any other git step

_FAIL_ON = ("critical", "high", "moderate", "low", "any", "none")
_FIX_MODES = ("plan", "apply-worktree")
_BOOL_KEYS = ("include_worktrees", "include_dev", "local_runtimes", "allow_major")

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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _config_problem(cfg: dict[str, Any]) -> str:
    """Return the first key whose value has the wrong type or range, as ``"<key> must be ..."``.

    Args:
        cfg: The merged config (every :data:`DEFAULT_CONFIG` key present).

    Returns:
        The problem, or "" when the config is well formed.
    """
    def got(value: Any) -> str:
        return f" (got {repr(value)[:80]})"

    for key in ("repo_roots", "servers"):
        value = cfg[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"{key} must be a list of strings{got(value)}"
    for key in _BOOL_KEYS:
        if not isinstance(cfg[key], bool):
            return f"{key} must be true or false{got(cfg[key])}"
    if not isinstance(cfg["fail_on"], str) or cfg["fail_on"] not in _FAIL_ON:
        return f"fail_on must be one of {', '.join(_FAIL_ON)}{got(cfg['fail_on'])}"
    if not _is_int(cfg["cooldown_days"]) or cfg["cooldown_days"] < 0:
        return f"cooldown_days must be a whole number of days >= 0{got(cfg['cooldown_days'])}"
    if not isinstance(cfg["fix_mode"], str) or cfg["fix_mode"] not in _FIX_MODES:
        return f"fix_mode must be one of {', '.join(_FIX_MODES)}{got(cfg['fix_mode'])}"
    how = cfg["notify"]
    if not isinstance(how, str) or not (how in ("none", "desktop") or how.startswith("command:")):
        return f"notify must be none, desktop or command:<program and args>{got(how)}"
    hours = cfg["stale_after_hours"]
    if isinstance(hours, bool) or not isinstance(hours, int | float) or not math.isfinite(hours) or hours <= 0:
        return f"stale_after_hours must be a number of hours > 0{got(hours)}"
    if not isinstance(cfg["governance_dir"], str):
        return f"governance_dir must be a path string{got(cfg['governance_dir'])}"
    agent = cfg["agent"]
    if agent is not None and not isinstance(agent, dict):
        return f"agent must be an object{got(agent)}"
    if isinstance(agent, dict) and not isinstance(agent.get("enabled", False), bool):
        return f"agent.enabled must be true or false{got(agent['enabled'])}"
    if isinstance(agent, dict):
        from agentteams.vulnscan import agent_run   # lazy, as in _agent_phase

        try:
            agent_run.validate_agent(agent)
        except ValueError as exc:
            return str(exc)
    when = cfg["schedule"]
    if not isinstance(when, dict):
        return f"schedule must be an object with hour and minute{got(when)}"
    for key, top in (("hour", 23), ("minute", 59)):
        if not _is_int(when.get(key)) or not 0 <= when[key] <= top:
            return f"schedule.{key} must be a whole number 0-{top}{got(when.get(key))}"
    return ""


def load_config(path: Path | None = None) -> dict:
    """Read the config, filling defaults for missing keys (and a partial ``schedule``), and check it.

    Args:
        path: Config file; defaults to :func:`paths.config_file`.

    Returns:
        The merged config.

    Raises:
        FileNotFoundError: No config yet (run ``vulnscan init``).
        ValueError: The file is not valid JSON, not a JSON object, or a key has the wrong type or
            range (the message names the file and the key).
    """
    path = path or paths.config_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:   # JSONDecodeError, UnicodeDecodeError
        raise ValueError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(data).__name__}")
    cfg = {**copy.deepcopy(DEFAULT_CONFIG), **data}
    if isinstance(data.get("schedule"), dict):   # an older or hand-written config may set only the hour
        cfg["schedule"] = {**DEFAULT_CONFIG["schedule"], **data["schedule"]}
    problem = _config_problem(cfg)
    if problem:
        raise ValueError(f"{path}: {problem}")
    return cfg


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


def run_lock_path() -> Path:
    """Return the daily run lock, next to the heartbeat.

    Returns:
        ``<state>/daily.lock``.
    """
    return paths.state_dir() / "daily.lock"


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file and a rename, so a reader never sees half of it.

    Raises:
        OSError: The write or the rename failed (``path`` is then unchanged).
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_heartbeat() -> tuple[_dt.datetime, int] | str:
    """Return (last run time, last exit code) from the heartbeat, or why it cannot be used.

    A naive ``last_run`` is taken as UTC; a missing or non-integer ``exit`` counts as 0.
    """
    try:
        data = json.loads(heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:   # ValueError: JSONDecodeError, UnicodeDecodeError
        return str(exc)
    if not isinstance(data, dict):
        return "not a JSON object"
    raw = data.get("last_run")
    if not isinstance(raw, str):
        return f"last_run is not a timestamp: {repr(raw)[:40]}"
    try:
        last = _dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        return str(exc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=_dt.UTC)
    code = data.get("exit", 0)
    return last, code if _is_int(code) else 0


def stale_warning(max_hours: float | None = None) -> str:
    """Return a warning when a configured daily run has not completed recently.

    Every command calls this, so it never raises: an unreadable config uses the default threshold
    and an unreadable heartbeat is itself the warning.

    Args:
        max_hours: Threshold; defaults to the config's ``stale_after_hours``.

    Returns:
        Warning text, or "" when fresh or when no daily run is configured.
    """
    if not paths.config_file().exists():
        return ""
    if max_hours is None:
        try:
            max_hours = float(load_config()["stale_after_hours"])
        except (OSError, ValueError):
            max_hours = float(DEFAULT_CONFIG["stale_after_hours"])
    if not heartbeat_path().exists():
        return "vulnscan: no daily run has completed yet — run `python -m agentteams.vulnscan daily` or `schedule install`"
    beat = _read_heartbeat()
    if isinstance(beat, str):
        return f"vulnscan: heartbeat unreadable ({beat}) — the daily run may be broken"
    last, last_exit = beat
    age = (_dt.datetime.now(_dt.UTC) - last).total_seconds() / 3600
    if age > max_hours:
        return f"vulnscan: last daily run was {age:.0f}h ago (> {max_hours:.0f}h) — check `vulnscan schedule status`"
    if last_exit == EXIT_UNSCANNED:
        return "vulnscan: the last daily run was only partially scanned — see its summary.md"
    if last_exit == EXIT_COMPROMISE:
        return "vulnscan: the last daily run found MALICIOUS packages or compromise indicators — see its summary.md"
    return ""


def _previous_summary(state: Path, today: str) -> tuple[Report | None, list[str]]:
    """Return the newest readable earlier summary and a note for each unreadable one skipped.

    Returns:
        (report or None when no earlier run is readable, notes for the summary).
    """
    notes: list[str] = []
    for run in reversed([d for d in paths.run_dirs(state) if d.name < today]):
        try:
            data = json.loads((run / "summary.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            rep = Report.from_dict(data)
            new_finding_keys(rep, None)        # every finding must have a usable key
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:   # malformed shapes
            notes.append(f"the {run.name} summary.json is unreadable ({type(exc).__name__}); not compared with it")
            continue
        if notes:
            notes.append(f"NEW marks compare with the {run.name} run")
        return rep, notes
    if notes:
        notes.append("no earlier summary is readable, so every finding is marked NEW")
    return None, notes


def _git(repo: Path, *args: str, timeout: int = _GIT_TIMEOUT, push: bool = False) -> subprocess.CompletedProcess:
    """Run git in ``repo`` hardened exactly like the executor (unattended, scheduled use).

    Repository config cannot run hooks, an fsmonitor, a custom ssh command or a credential helper,
    and inherited ``GIT_*`` variables are dropped (:func:`executor.git_proc`).

    Raises:
        subprocess.TimeoutExpired: git ran longer than ``timeout`` seconds.
    """
    return executor.git_proc(repo, *args, timeout=timeout, push=push)


def _reuse_problem(repo: Path, wt: Path, branch: str, head: str) -> tuple[str, str]:
    """Check an existing same-day worktree before staging into it again.

    It must be a registered worktree of ``repo`` on ``branch``, with no commits that are not on
    ``head`` and no change :func:`executor.validate_diff` refuses.

    Returns:
        (problem or "", the worktree's HEAD commit to validate against).
    """
    if wt.is_symlink():
        return "it is a symbolic link", ""
    entry: dict[str, str] = {}
    for block in _git(repo, "worktree", "list", "--porcelain").stdout.split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if fields.get("worktree") and Path(fields["worktree"]).resolve() == wt.resolve():
            entry = fields
    if not entry:
        return "it is not a worktree of this repository", ""
    if entry.get("branch") != f"refs/heads/{branch}":
        return f"it is not on {branch}", ""
    base = entry.get("HEAD", "")
    if not base or _git(repo, "merge-base", "--is-ancestor", base, head).returncode:
        return f"it has commits that are not on {head}", ""
    try:
        executor.validate_diff(wt, base)
    except executor.ExecError as exc:
        return f"it has changes the daily run did not make ({exc})", ""
    return "", base


def _discard_worktree(repo: Path, wt: Path, branch: str) -> None:
    """Remove a staged worktree and its branch (a refused change is never left for review)."""
    _git(repo, "worktree", "remove", "--force", str(wt))
    _git(repo, "branch", "-D", branch)


def stage_worktree_fixes(repo: Path, cfg: dict, date: str) -> tuple[str, str]:
    """Create ``<repo>.worktrees/vulnscan-<date>`` off ``origin/HEAD`` and apply fixes there.

    The cooldown is never below :data:`executor.MIN_COOLDOWN_DAYS` and the result must pass
    :func:`executor.validate_diff` (only dependency pins and lockfiles change); a refused result is
    removed. A same-day rerun reuses the worktree only after :func:`_reuse_problem` finds nothing.
    Without ``origin/HEAD`` the default branch is unknown and nothing is staged.

    Args:
        repo: Repository root.
        cfg: Daily config.
        date: Run date (YYYY-MM-DD).

    Returns:
        (worktree path or "", markdown fix result or the reason nothing was staged).
    """
    if not (repo / ".git").exists():
        return "", f"{repo}: not a git repository"
    notes: list[str] = []
    try:
        fetched = _git(repo, "fetch", "-q", "origin", timeout=_FETCH_TIMEOUT, push=True)
    except subprocess.TimeoutExpired:
        notes.append(f"`git fetch` timed out after {_FETCH_TIMEOUT}s; staged on the last fetched origin/HEAD")
    else:
        if fetched.returncode:
            notes.append(f"`git fetch` failed ({fetched.stderr.strip()[:200]}); staged on the last fetched origin/HEAD")
    head = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if not head:
        return "", (f"{repo}: origin/HEAD is not set, so the default branch is unknown; nothing staged "
                    "(run `git remote set-head origin --auto`)")
    base = _git(repo, "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}").stdout.strip()
    if not base:
        return "", f"{repo}: {head} does not resolve to a commit; nothing staged"
    wt = repo.parent / f"{repo.name}.worktrees" / f"vulnscan-{date}"
    branch = f"vulnscan/{date}"
    if wt.exists() or wt.is_symlink():
        problem, base = _reuse_problem(repo, wt, branch, head)
        if problem:
            return "", f"{repo}: not reusing {wt}: {problem}"
    else:
        made = _git(repo, "worktree", "add", "-q", "-b", branch, str(wt), base)
        if made.returncode:
            return "", f"{repo}: worktree add failed: {made.stderr.strip()}"
    cooldown = max(executor.MIN_COOLDOWN_DAYS, int(cfg.get("cooldown_days", executor.MIN_COOLDOWN_DAYS)))
    res = fix.fix_path(wt, apply=True, allow_major=False,   # unattended: never a major jump
                       cooldown_days=cooldown)
    try:
        executor.validate_diff(wt, base)
    except executor.ExecError as exc:
        _discard_worktree(repo, wt, branch)
        return "", f"{repo}: staged change refused, worktree removed: {exc}\n\n{res.markdown()}"
    return str(wt), res.markdown() + "".join(f"\n- {n}" for n in notes) + ("\n" if notes else "")


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
        (aggregate report, exit code, run directory). While another daily run holds
        :func:`run_lock_path`, nothing is scanned or written and the exit code is 3.
    """
    today = today or _dt.date.today().isoformat()
    state = paths.state_dir()
    run_dir = state / today
    state.mkdir(parents=True, exist_ok=True)
    with open(run_lock_path(), "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            msg = f"another daily run holds {run_lock_path()}; not starting a second one"
            print(f"vulnscan daily: {msg}", file=sys.stderr)
            busy = Report(target="daily")
            busy.feed_errors.append(msg)
            return busy, EXIT_UNSCANNED, run_dir
        try:
            return _run_locked(cfg, today, state, run_dir)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _run_locked(cfg: dict, today: str, state: Path, run_dir: Path) -> tuple[Report, int, Path]:
    """The body of :func:`run_daily`, under the daily run lock."""
    run_dir.mkdir(parents=True, exist_ok=True)
    total = Report(target="daily")
    staged: list[str] = []
    try:
        _collect(cfg, today, run_dir, total, staged)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Deliberately wide (named, not broad): whatever stops collection, the run must still write its
        # summary and heartbeat and exit 3 (unscanned), visibly naming the error, rather than die silently.
        total.feed_errors.append(f"daily run stopped early: {type(exc).__name__}: {exc}")
    report, code, written = _finish(cfg, state, today, run_dir, total, staged)
    agent = cfg.get("agent") or {}
    if written and agent.get("enabled"):
        _agent_phase(cfg, run_dir)
    return report, code, run_dir


def _agent_phase(cfg: dict, run_dir: Path) -> None:
    """Run the agent on this scan, record it in the summary and heartbeat, publish, and notify."""
    from agentteams.vulnscan import agent_run

    try:
        status = agent_run.run_agent(cfg, run_dir)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:   # docker / launcher failure
        status = {"state": "agent-error", "run_state": "agent-error",
                  "reason": f"launcher failed: {type(exc).__name__}: {exc}"[:300]}
    try:
        with open(run_dir / "summary.md", "a", encoding="utf-8") as fh:   # before publishing, so it is included
            fh.write(f"\n## Agent run\n\n- state: **{status.get('state')}**"
                     f"{' — ' + str(status.get('reason')) if status.get('reason') else ''}\n"
                     "- review what it did: `python -m agentteams.vulnscan review`\n")
    except OSError as exc:
        print(f"vulnscan daily: could not add the agent run to summary.md: {exc}", file=sys.stderr)
    published = publish_run(cfg, run_dir, str(status.get("run_id") or agent_run.executor.new_run_id(run_dir)))
    beat = _read_heartbeat_dict()
    beat["agent"] = {k: status.get(k) for k in ("state", "reason", "exit", "timed_out", "spend", "run_id")}
    beat["publish"] = published
    try:
        _write_atomic(heartbeat_path(), json.dumps(beat, indent=2))
    except OSError as exc:
        print(f"vulnscan daily: could not record the agent run in the heartbeat: {exc}", file=sys.stderr)
    if status.get("state") != "completed":
        word = status.get("state")   # notify takes only known state words; anything else is an error
        notify.send(cfg, "agent-not-completed", state=word if word in agent_run.RUN_STATES else "agent-error")


def _read_heartbeat_dict() -> dict[str, Any]:
    """Return the heartbeat object this run just wrote, or {} when it cannot be read."""
    try:
        data = json.loads(heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"vulnscan daily: heartbeat unreadable ({exc}); rewriting it", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


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
        repos = sorted({f.target for f in rep.findings if f.kind == "package" and f.fix and not f.malicious})
        for f in rep.findings:
            f.target = f"{root_s}/{f.target}" if f.target != "." else root_s
        if cfg.get("fix_mode") == "apply-worktree":
            for t in (f"{root_s}/{r}" if r != "." else root_s for r in repos):
                repo = Path(t).expanduser()
                wt, md = stage_worktree_fixes(repo, cfg, today)
                staged.append(f"- `{t}` → `{wt or 'not staged'}`")
                (run_dir / f"fix-{plan_slug(repo.resolve())}.md").write_text(md, encoding="utf-8")
        elif repos:
            _write_fix_plans(cfg, root, repos, run_dir, total)

    if cfg.get("local_runtimes", True):
        total.merge(engine.local_runtimes())

    kit = paths.server_kit_dir()
    for host in cfg.get("servers", []):
        total.merge(_scan_server(host, kit, run_dir))


def plan_slug(repo: Path) -> str:
    """Return the file-name key of a repository's fix plan: its name plus a digest of its full path.

    Two repositories with the same name under different roots get different plans.

    Args:
        repo: Resolved repository path.

    Returns:
        ``<name>-<8 hex>``.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "-", repo.name or "root") + "-" + hashlib.sha256(str(repo).encode()).hexdigest()[:8]


def _write_fix_plans(cfg: dict, root: Path, repos: list[str], run_dir: Path, total: Report) -> None:
    """Write ``fixplan-<slug>.md`` for each git repository under ``root`` that has fixable findings.

    A plan is keyed by the repository that holds the manifests (:func:`engine.repo_of`, as the scan
    groups findings), names that repository as the ``stage-fix`` target, lists file paths relative to
    it, and leaves out nested repositories (they get their own plan). A manifest outside any git
    repository still gets a plan, which says ``stage-fix`` cannot take it.

    Args:
        cfg: Daily config.
        root: A ``repo_roots`` entry (a repository or a folder of repositories).
        repos: Repositories with fixable findings, relative to ``root`` (``"."`` for ``root``).
        run_dir: The run directory.
        total: The run's report; fix-plan feed failures are added to it.
    """
    root = root.resolve()
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in discover(root, include_worktrees=bool(cfg["include_worktrees"])):
        groups[engine.repo_of(path, root)].append(path)
    for rel in repos:
        repo = root / rel
        plan = fix.fix_path(repo, apply=False, allow_major=bool(cfg["allow_major"]),
                            cooldown_days=int(cfg["cooldown_days"]), npm=False, manifests=groups.get(rel, []))
        plan.target = str(repo)
        if not (repo / ".git").exists():
            plan.target_problem = "not a git repository — fix these files by hand"
        (run_dir / f"fixplan-{plan_slug(repo)}.md").write_text(plan.markdown(), encoding="utf-8")
        total.feed_errors += [f"fix plan: {e}" for e in plan.feed_errors]


def _finish(cfg: dict, state: Path, today: str, run_dir: Path, total: Report,
            staged: list[str]) -> tuple[Report, int, bool]:
    """Write summary + heartbeat, notify, and return (report, exit code, whether the files were written).

    A write that fails (disk full, a directory in the way) is caught: the run becomes unscanned
    (exit 3, unless it found compromise indicators, exit 4), the reason goes to stderr and the
    notification is still attempted.
    """
    previous, notes = _previous_summary(state, today)
    new = new_finding_keys(total, previous)
    code = total.exit_code(cfg.get("fail_on", "high"), bool(cfg.get("include_dev")))
    md = total.markdown(new_keys=new)
    if notes:
        md += "\n## Notes\n\n" + "".join(f"- {n}\n" for n in notes)
    if staged:
        md += "\n## Fixes staged in worktrees (review, test, commit, open a PR)\n\n" + "\n".join(staged) + "\n"
    written = True
    try:
        (run_dir / "summary.md").write_text(md, encoding="utf-8")
        (run_dir / "summary.json").write_text(json.dumps(total.to_dict(), indent=2), encoding="utf-8")
        _write_atomic(heartbeat_path(), json.dumps({
            "last_run": _dt.datetime.now(_dt.UTC).isoformat(), "exit": code, "findings": len(total.findings),
            "new": len(new), "unscanned": len(total.feed_errors), "summary": str(run_dir / "summary.md")}, indent=2))
    except OSError as exc:
        written = False
        msg = f"could not write the run's summary or heartbeat: {exc}"
        total.feed_errors.append(msg)
        print(f"vulnscan daily: {msg}", file=sys.stderr)
        code = code if code == EXIT_COMPROMISE else EXIT_UNSCANNED
    if code or new:
        notify.send(cfg, "run-done", state=RUN_LABELS.get(code, NEW_FINDINGS_LABEL),
                    findings=len(total.findings), new=len(new))
    return total, code, written
