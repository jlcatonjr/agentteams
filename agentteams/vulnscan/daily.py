"""The daily run: scan the user's repos, local runtimes and servers; plan or stage fixes; notify.

Configuration is ``vulnscan.json`` (see :data:`DEFAULT_CONFIG`) in the user's config
dir. That file names the user's own repos and hosts, so it is never inside a repository.

One run:

1. Scans each effective root (:func:`coverage.effective_roots`: the explicit ``repo_roots``, plus the fix
   checkout of every registry entry at ``scan`` or ``remediate``, minus ``exclude`` paths and duplicates), writes a fix plan per git repository (``fixplan-<name>-<digest>.md``,
   naming the repository as the ``stage-fix`` target), and in
   ``fix_mode = "apply-worktree"`` stages non-major, OSV-clean fixes in a fresh git
   worktree per repository. It never commits, pushes or merges.
2. Checks the runtimes on this machine (``local_runtimes``).
3. Runs the *packaged* server kit's read-only ``L1-audit.sh`` (with the user's kit dir as its
   config) on each ``servers`` host and analyzes
   its inventory. Server *changes* are never automatic; they remain a person running
   ``run-host.sh --apply``.
4. Writes ``<state>/<date>/summary.{md,json}``, marking findings new since the last run, with the coverage
   state of every enrolled workspace.
5. Updates ``<state>/heartbeat.json`` (atomically) and sends a notification.

Staging (step 1, ``apply-worktree``) and the server audits (step 3) run git, npm and the server kit in the tools
container (:mod:`.subproc_confine`); when it is unavailable they fail closed (nothing staged, the host unscanned).
Fix planning only parses files and stays on the host.

Only one daily run works at a time: a second one finds ``<state>/daily.lock`` held and exits 3
without touching the first run's files. A host that cannot be reached, a feed that fails, or a
summary / heartbeat that cannot be written makes the run *unscanned* (exit 3). It is never
reported as clean.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import datetime as _dt
import fcntl
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agentteams.vulnscan import coverage, docker_readiness, effectiveness, engine, executor, fix, merge_digest, notify
from agentteams.vulnscan import osv, paths, registry, remediation_status, repo_alias, scan_mirror
from agentteams.vulnscan import subproc_confine, subproc_confine_ssh
# Split from this module and the names scripts and tests use are re-exported, so ``daily.<name>`` keeps working for them:
from agentteams.vulnscan.checkout_freshness import _freshness, _freshness_section
from agentteams.vulnscan.infra_retry import (INFRA_RETRY_FILE, _archive_summary, _max_retries,
                                             _retries, _stale, infra_retry_due)
from agentteams.vulnscan.manifests import discover
from agentteams.vulnscan.report import (EXIT_COMPROMISE, EXIT_FINDINGS, EXIT_UNSCANNED, Report,
                                        new_finding_keys)
from agentteams.vulnscan.worktree_fixes import stage_worktree_fixes   # re-exported, as above

#: The audit's final verdict line; its presence proves the script ran to completion.
_AUDIT_DONE = re.compile(r"^RESULT (clean|action-needed|COMPROMISE)", re.M)

#: ``run-done`` notification state per exit code; any other code with new findings is NEW_FINDINGS_LABEL.
RUN_LABELS: dict[int, str] = {EXIT_COMPROMISE: "COMPROMISE INDICATORS", EXIT_UNSCANNED: "partially unscanned",
                              EXIT_FINDINGS: "action needed"}
NEW_FINDINGS_LABEL = "new findings"

_GIT_TIMEOUT = 600            # seconds for any other git step

_FAIL_ON = ("critical", "high", "moderate", "low", "any", "none")
_FIX_MODES = ("plan", "apply-worktree")
#: What stops one scan root or one fix plan (recorded for it; the others are kept). The same as stops a whole run.
_ITEM_ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError)
MAX_SCAN_WORKERS = 8         # each scan already sends up to 8 OSV requests at once: a ceiling, not a target
_BOOL_KEYS = ("include_worktrees", "include_dev", "local_runtimes", "allow_major", "unpinned_ranges")

DEFAULT_CONFIG: dict = {
    "repo_roots": [],            # e.g. ["~/code"] — a repo or a folder of repos (enrolled projects come from the registry)
    "exclude": [],               # paths never scanned: a root inside one is skipped, findings inside a root dropped
    "include_worktrees": False,
    "servers": [],               # host names from <server kit>/hosts.conf
    "fail_on": "high",           # critical | high | moderate | low | any | none
    "unscored_as": "high",       # what an advisory with no CVSS vector or severity label counts as
    "include_dev": False,
    "unpinned_ranges": False,    # true: also check unpinned Python requirement ranges (their names go to OSV)
    "cooldown_days": 0,          # ignored: Rule S-10's cooldown never holds a fix for a scanned vulnerability
    "local_runtimes": True,
    "fix_mode": "plan",          # plan | apply-worktree
    "allow_major": False,
    "scan_workers": 4,           # scan roots and fix plans looked up at once (1 = one after another)
    "notify": "desktop",         # none | desktop | command:<program and args>
    "schedule": {"hour": 7, "minute": 30},
    "stale_after_hours": 26,
    "governance_dir": "",        # team dir whose decision log the executor checks for HALTs (C-2)
    "handoff_required": False,   # true: open-pr waits for each repository orchestrator's approval (handoff.py)
    "scan_mirror": "",           # worktree name: scan <repo>.worktrees/<name> when refreshed today (scan_mirror.py)
    # Agent mode (off by default): after the scan, the team runs in an isolated container and
    # requests actions that the executor performs only under a signed grant. See agent_run.DEFAULT_AGENT.
    "agent": {"enabled": False, "team_dir": "", "key_file": "", "budget_usd": 2.0},
    # Tools container for git, npm and the server kit in executor ops and apply-worktree staging (subproc_confine):
    # {} = the defaults (subprocesses on, fail closed); "subprocesses": false is the operator's opt-out.
    "confine": {},
    # Docker readiness (docker_readiness.ensure, before each scan): starting Docker is the operator's opt-in
    # (autostart); a self-test whose record is for another image is re-run once (auto_selftest "on-change" | "off").
    "docker": dict(docker_readiness.DEFAULTS),
    # daily --catch-up also re-runs a same-day run left partial by Docker, once Docker is ready: at most this often
    "catch_up": {"max_retries_per_day": 2},
}

MAX_RETRIES_PER_DAY = 10
#: The run's Docker reason codes (``infra.*``, from :class:`subproc_confine.Unavailable` ``code``), from
#: _collect to _finish; written to ``summary.json["infra_codes"]`` (None outside a run). Only these mark a run
#: Docker left partial: the ``tools container unavailable`` text also covers ssh, mount and allowlist refusals,
#: which a retry cannot fix.
_INFRA: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar("vulnscan_infra", default=None)


def _note_infra(exc: subproc_confine.Unavailable) -> None:
    """Record ``exc``'s Docker reason code (if any) for this run's ``summary.json["infra_codes"]``."""
    code = getattr(exc, "code", "")
    if (codes := _INFRA.get()) is not None and code in docker_readiness.CODES:
        codes.add(code)


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

    for key in ("repo_roots", "servers", "exclude"):
        value = cfg[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return f"{key} must be a list of strings{got(value)}"
    for key in _BOOL_KEYS:
        if not isinstance(cfg[key], bool):
            return f"{key} must be true or false{got(cfg[key])}"
    if not isinstance(cfg["fail_on"], str) or cfg["fail_on"] not in _FAIL_ON:
        return f"fail_on must be one of {', '.join(_FAIL_ON)}{got(cfg['fail_on'])}"
    if not isinstance(cfg["unscored_as"], str) or cfg["unscored_as"] not in osv.UNSCORED_AS:
        return f"unscored_as must be one of {', '.join(osv.UNSCORED_AS)}{got(cfg['unscored_as'])}"
    if not _is_int(cfg["cooldown_days"]) or cfg["cooldown_days"] < 0:
        return f"cooldown_days must be a whole number of days >= 0{got(cfg['cooldown_days'])}"
    if not isinstance(cfg["fix_mode"], str) or cfg["fix_mode"] not in _FIX_MODES:
        return f"fix_mode must be one of {', '.join(_FIX_MODES)}{got(cfg['fix_mode'])}"
    if not scan_mirror.valid_name(cfg["scan_mirror"]):
        return f"scan_mirror must be a worktree name (no '/'){got(cfg['scan_mirror'])}"
    if not _is_int(cfg["scan_workers"]) or not 1 <= cfg["scan_workers"] <= MAX_SCAN_WORKERS:
        return f"scan_workers must be a whole number from 1 to {MAX_SCAN_WORKERS}{got(cfg['scan_workers'])}"
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
    if problem := subproc_confine.config_problem(cfg):
        return problem
    if problem := docker_readiness.config_problem(cfg):
        return problem
    catch = cfg.get("catch_up", {})
    if not isinstance(catch, dict) or set(catch) - {"max_retries_per_day"}:
        return f"catch_up must be an object with only max_retries_per_day{got(catch)}"
    retries = catch.get("max_retries_per_day", 2)
    if not _is_int(retries) or not 0 <= retries <= MAX_RETRIES_PER_DAY:
        return f"catch_up.max_retries_per_day must be a whole number 0-{MAX_RETRIES_PER_DAY}{got(retries)}"
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
    if cfg["cooldown_days"]:
        print(f"note: {path}: cooldown_days is ignored (Rule S-10's release cooldown never holds a fix for a "
              "vulnerability the scan detected); remove the key", file=sys.stderr)
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


def catch_up_due(cfg: dict) -> tuple[bool, int | None]:
    """Return whether a catch-up run is due: no completed run within ``stale_after_hours``, or an infra retry.

    Args:
        cfg: Daily config.

    Returns:
        ``(due, hours since the last completed run)``; the hours are None when there is no usable heartbeat
        (none yet, or unreadable: due as well). A recent run is due again when :func:`infra_retry_due` says so.
    """
    stale, hours = _stale(cfg)
    return stale or infra_retry_due(cfg) is not None, hours


def run_in_progress() -> bool:
    """Return whether another daily run holds :func:`run_lock_path` (probed without blocking, released at once)."""
    if not run_lock_path().is_file():
        return False
    with open(run_lock_path(), "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
    return False


def catch_up(cfg: dict) -> tuple[Report, int, Path] | None:
    """Run a missed daily scan (``daily --catch-up``: the login / interval job), after notifying the operator.

    Args:
        cfg: Daily config.

    A recent run is re-run without a notification when Docker left it partial and is ready now
    (:func:`infra_retry_due`); the retry is counted in the run directory before it starts, and the summary it
    replaces is kept (:func:`_archive_summary`).

    Returns:
        :func:`run_daily`'s result, or None when the last run is recent (and no infra retry is due) or a daily run
        is in progress (nothing done, nothing sent: the lock is checked first, so no "running now" message
        precedes a refused run).
    """
    if run_in_progress():
        print("vulnscan daily --catch-up: a daily run is in progress; not starting another", file=sys.stderr)
        return None
    stale, hours = _stale(cfg)
    if not stale:
        retry = infra_retry_due(cfg)
        if retry is None:
            return None
        n = _retries(retry) + 1
        _write_atomic(retry / INFRA_RETRY_FILE, f"{n}\n")
        print(f"vulnscan daily --catch-up: re-running {retry.name}, left partial by Docker (retry {n} of "
              f"{_max_retries(cfg)})", file=sys.stderr)
        return run_daily(cfg)
    if hours is None:
        notify.send(cfg, "run-missing")
    else:
        notify.send(cfg, "run-stale", hours=hours)
    return run_daily(cfg)


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
        if subproc_confine.active():   # in the tools container; its logs in a subdirectory of the run
            proc = subproc_confine_ssh.run_kit(remote, host, ["L1-audit.sh"], conf_dir=kit,
                                               logs=out_dir / "server-kit-logs", timeout=1800)
        else:
            proc = subprocess.run(["bash", str(remote), host, "L1-audit.sh"], capture_output=True, text=True,
                                  env=env, timeout=1800, check=False)
    except subprocess.TimeoutExpired:
        rep = Report(target=host)
        rep.feed_errors.append(f"{host}: audit timed out")
        return rep
    except subproc_confine.Unavailable as exc:   # fail closed: unscanned, never run on the host instead
        _note_infra(exc)
        rep = Report(target=host)
        rep.feed_errors.append(f"{host}: not scanned: {exc}")
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
    """The body of :func:`run_daily`, under the daily run lock.

    Docker readiness is ensured once, first (:func:`docker_readiness.ensure`: it may start Docker or re-run a
    stale self-test only as the ``docker`` config allows); the servers, the staging, the summary's remediation
    readiness and the agent phase all use that record instead of probing again.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    total = Report(target="daily")
    staged: list[str] = []
    with docker_readiness.using(docker_readiness.ensure(cfg, start=True)), _fresh_rows():
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

    rec = docker_readiness.current()
    blocked = rec.agent() if rec is not None else ""
    try:
        if blocked:   # the run's Docker check already says the container cannot start: no second probe
            status = {"state": "not-run", "run_state": "not-run", "status_written": False,
                      "reason": f"{getattr(blocked, 'code', '')}: {blocked}"}
        else:
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
    effectiveness.agent_alert(cfg, run_dir.parent, run_dir.name, str(status.get("state")))   # 2+ runs in a row


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
    """Scan repos, local runtimes and servers into ``total`` (each repository's checkout freshness into the run's rows).

    Plans an earlier run of the same day wrote are removed first: the agent reads every ``fixplan-*.md``
    in the run directory, and a stale one would name a fix (or a target) this scan no longer has.
    """
    for old in [*run_dir.glob("fixplan-*.md"), *run_dir.glob("fix-*.md")]:
        old.unlink()   # unlink never follows a symlink
    try:
        entries = registry.load()
    except (OSError, ValueError) as exc:   # enrolled projects are then unscanned: say so, scan the explicit roots
        total.feed_errors.append(f"project registry unreadable, enrolled projects not scanned: {exc}")
        entries = []
    exclude, opted_out = coverage.operator_skips(cfg, entries)
    specs = []
    for root_spec in coverage.effective_roots(cfg, entries):
        if root_spec.scanned and not root_spec.path.is_dir():
            total.feed_errors.append(f"repo root missing: {root_spec.label}")
        elif root_spec.scanned:
            specs.append(root_spec)
    mirrors = {s.path: scan_mirror.mirror_for(cfg, s.path, today) for s in specs}   # None: scan the checkout

    def scan(spec: Any) -> Report | Exception:   # network-bound: the roots are looked up at once
        m = mirrors[spec.path]
        try:
            return engine.scan_path(m or spec.path, include_worktrees=bool(cfg["include_worktrees"]),
                                    exclude=scan_mirror.also_under(exclude, spec.path, m) if m else exclude,
                                    opted_out=set(scan_mirror.also_under(opted_out, spec.path, m)) if m else opted_out,
                                    unpinned_ranges=bool(cfg.get("unpinned_ranges", False)))   # excluded: never read
        except _ITEM_ERRORS as exc:   # this root is unscanned (recorded below); the other roots are kept
            return exc

    plan_jobs: list[tuple[Path, list[str], Path]] = []
    for root_spec, rep in zip(specs, _parallel(cfg, scan, specs), strict=True):   # merged here, in root order
        root_s, root = root_spec.label, root_spec.path
        if isinstance(rep, Exception):
            total.feed_errors.append(f"scan of {root_s} stopped: {type(rep).__name__}: {rep}"[:400])
            continue
        _drop_excluded(cfg, root, rep)   # defence in depth only
        for row in _freshness(root, rep):   # read-only, no fetch; labels fixed_upstream (T2-D)
            row["repo"] = _under(root_s, row["repo"])
            row["scanned"] = (f"mirror `{mirrors[root].name}`" if mirrors[root] else
                              "checkout (no fresh mirror today)" if cfg.get("scan_mirror") else "checkout")
            if (rows := _FRESH.get()) is not None:
                rows.append(row)
        slug = (root.name or "root") if root_spec.source == "repo_roots" else plan_slug(root.resolve())
        (run_dir / f"repos-{slug}.json").write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
        _prefix_policy_labels(rep, root_s)
        total.merge(rep)
        repos = sorted({f.target for f in rep.findings if f.kind == "package" and f.fix and not f.malicious})
        for f in rep.findings:
            f.target = _under(root_s, f.target)
        if cfg.get("fix_mode") == "apply-worktree":
            for t in (f"{root_s}/{r}" if r != "." else root_s for r in repos):
                repo = Path(t).expanduser()
                wt, md = stage_worktree_fixes(repo, cfg, today)
                staged.append(f"- `{t}` → `{wt or 'not staged'}`")
                (run_dir / f"fix-{plan_slug(repo.resolve())}.md").write_text(md, encoding="utf-8")
        elif repos:
            plan_jobs.append((mirrors[root] or root, repos, root))   # plans read the mirror, name the checkout
    _write_fix_plans(cfg, plan_jobs, run_dir, total)

    if cfg.get("local_runtimes", True):
        total.merge(engine.local_runtimes())

    kit = paths.server_kit_dir()
    for host in cfg.get("servers", []):
        try:
            with subproc_confine.scope(cfg):
                total.merge(_scan_server(host, kit, run_dir))
        except subproc_confine.Unavailable as exc:   # fail closed: the host is reported unscanned
            _note_infra(exc)
            total.feed_errors.append(f"{host}: not scanned: {exc}")


#: The run's checkout-freshness rows (:func:`_freshness`), from _collect to _finish (None outside a run).
_FRESH: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("vulnscan_fresh", default=None)


@contextlib.contextmanager
def _fresh_rows() -> Iterator[list[dict[str, Any]]]:
    """Collect the run's checkout-freshness rows (:data:`_FRESH`) for the duration of the block."""
    rows: list[dict[str, Any]] = []
    token, infra = _FRESH.set(rows), _INFRA.set(set())   # and the run's Docker reason codes (_note_infra)
    try:
        yield rows
    finally:
        _FRESH.reset(token)
        _INFRA.reset(infra)


def _operator_excluded(cfg: dict, path: Path) -> bool:
    """Return whether ``path`` is inside one of the operator's ``exclude`` paths."""
    return any(coverage.within(path, x) for x in coverage.exclusions(cfg))


def _under(root_s: str, target: str) -> str:
    """Return a scan target (relative to its root, ``"."`` for the root) named under the root's label."""
    return f"{root_s}/{target}" if target != "." else root_s


def _prefix_policy_labels(rep: Report, root_s: str) -> None:
    """Name the repositories in ``opted_out``, ``brief_off``, ``excluded`` and ``withheld`` under the root, as findings are."""
    rep.opted_out = [_under(root_s, t) for t in rep.opted_out]
    rep.brief_off = [_under(root_s, t) for t in rep.brief_off]
    for name in ("excluded", "withheld"):
        setattr(rep, name, [f"{_under(root_s, t)}: {rest}" for t, _s, rest in
                            (label.partition(": ") for label in getattr(rep, name))])


def _drop_excluded(cfg: dict, root: Path, rep: Report) -> None:
    """Drop whatever of a scanned ``root`` lies inside an ``exclude`` path (defence in depth: ``scan_path`` skips them).

    Findings, unscannable files and the brief lists (``opted_out``, ``brief_off``, ``excluded``, ``withheld``).
    """
    inner = coverage.excluded_inside(cfg, root)
    if not inner:
        return
    base = root.resolve()

    def kept(rel: str) -> bool:
        return not any(x == base / rel or x in (base / rel).parents for x in inner)

    rep.findings = [f for f in rep.findings if kept(f.target) and (not f.locations or any(map(kept, f.locations)))]
    rep.unscannable = [u for u in rep.unscannable if kept(u[0])]
    rep.opted_out = [t for t in rep.opted_out if kept(t)]
    rep.brief_off = [t for t in rep.brief_off if kept(t)]
    rep.excluded = [x for x in rep.excluded if kept(x.partition(": ")[0]) and kept("/".join(x.split(": ", 1)))]
    rep.withheld = [x for x in rep.withheld if kept(x.partition(": ")[0])]


def plan_slug(repo: Path) -> str:
    """Return the file-name key of a repository's fix plan: its name plus a digest of its full path.

    Two repositories with the same name under different roots get different plans.

    Args:
        repo: Resolved repository path.

    Returns:
        ``<name>-<8 hex>``.
    """
    return repo_alias.alias(str(repo))   # also the agent's alias for the repository (mcp.py)


_ORIGIN_URL = re.compile(r'^\[remote "origin"\]\s*$(?:\n(?!\[).*)*?\n\s*url\s*=\s*(\S+)', re.M)


def _origin_owner(repo: Path) -> str | None:
    """Return ``host/owner`` of the repository's ``origin`` from ``.git/config`` (read as text; git is not run)."""
    try:
        m = _ORIGIN_URL.search((repo / ".git" / "config").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    url = re.sub(r"^[a-z+]+://(?:[^@/]+@)?|^[^@/]+@", "", m.group(1)) if m else ""   # scheme and user@
    parts = [p for p in re.split(r"[:/]", url.removesuffix(".git")) if p]
    return "/".join(parts[:2]).lower() if len(parts) >= 3 else None


def foreign_clone(repo: Path) -> str:
    """Return why ``repo`` is not a ``stage-fix`` target when it is someone else's clone nested in a repository, else "".

    A nested repository with its own ``.git`` directory (not a submodule, whose ``.git`` is a file) whose
    ``origin`` belongs to a different owner than the enclosing repository's is a local copy of a third-party
    project: a PR would go to that project. It is reported for the operator to update or remove locally.

    Args:
        repo: A git repository.

    Returns:
        The reason, or "" when ``repo`` is not a nested foreign clone.
    """
    if not (repo / ".git").is_dir():
        return ""
    outer = next((p for p in repo.parents if (p / ".git").exists()), None)
    if outer is None:
        return ""
    mine, theirs = _origin_owner(outer), _origin_owner(repo)
    if theirs is None or mine == theirs:
        return ""
    return (f"a separate clone of {theirs}'s project inside {outer.name} (not part of that repository): "
            "update or remove it locally; a PR would go to the upstream project")


def _parallel(cfg: dict, fn: Callable[[Any], Any], items: Sequence[Any]) -> list[Any]:
    """Return ``[fn(x) for x in items]``, up to ``scan_workers`` at a time, in order.

    Each call runs in a copy of the caller's context, so a ``subproc_confine`` scope (a ContextVar) applies
    exactly as it would serially. Results are read in order: the first exception is raised, as serially, after
    the calls not yet started are cancelled (those already running finish; ``fn`` must leave writing to the caller).
    """
    workers = min(int(cfg.get("scan_workers", DEFAULT_CONFIG["scan_workers"])), len(items))
    if workers <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vulnscan-scan") as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, x) for x in items]
        try:
            return [f.result() for f in futures]
        finally:   # after a failure no queued call starts (nothing is left to cancel after success)
            pool.shutdown(wait=True, cancel_futures=True)


def _write_fix_plans(cfg: dict, jobs: list[tuple[Path, list[str], Path]], run_dir: Path, total: Report) -> None:
    """Write ``fixplan-<slug>.md`` for each git repository with fixable findings, looked up in parallel.

    A plan is keyed by the repository that holds the manifests (:func:`engine.repo_of`, as the scan
    groups findings), names that repository as the ``stage-fix`` target, lists file paths relative to
    it, and leaves out nested repositories (they get their own plan). A manifest outside any git
    repository still gets a plan, which says ``stage-fix`` cannot take it.

    Args:
        cfg: Daily config.
        jobs: ``(source, repos, root)``: where the manifests are read (the root, or its scan mirror), the
            repositories with fixable findings relative to it (``"."`` for the root), and the ``repo_roots`` entry
            (a repository or a folder of repositories) the plans name.
        run_dir: The run directory.
        total: The run's report; fix-plan feed failures are added to it, in job order.
    """
    tasks: list[tuple[Path, list[Path], Path]] = []
    seen: set[Path] = set()
    for src, repos, root in jobs:
        src, root = src.resolve(), root.resolve()
        groups: dict[str, list[Path]] = defaultdict(list)
        for path in discover(src, include_worktrees=bool(cfg["include_worktrees"])):
            if not _operator_excluded(cfg, root / path.relative_to(src)):   # never read or looked up
                groups[engine.repo_of(path, src)].append(path)
        tasks += [(src / rel, groups.get(rel, []), root / rel) for rel in repos if root / rel not in seen]
        seen.update(root / rel for rel in repos)   # nested repo_roots name a repository once: one plan, one lookup

    def one(task: tuple[Path, list[Path], Path]) -> tuple[Path, str, list[str]]:   # looks up only; the caller writes
        source, manifests, repo = task
        try:
            plan = fix.fix_path(source, apply=False, allow_major=bool(cfg["allow_major"]),
                                cooldown_days=executor.REMEDIATION_COOLDOWN_DAYS, npm=False, manifests=manifests)
        except _ITEM_ERRORS as exc:   # no plan for this repository (recorded); the others are kept
            return repo, "", [f"fix plan for {repo} stopped: {type(exc).__name__}: {exc}"[:400]]
        plan.target = str(repo)
        if not (repo / ".git").exists():
            plan.target_problem = "not a git repository — fix these files by hand"
        elif foreign := foreign_clone(repo):
            plan.target_problem = foreign
        return repo, plan.markdown(), [f"fix plan: {e}" for e in plan.feed_errors]

    for repo, markdown, errors in _parallel(cfg, one, tasks):   # written here, in order, once each
        if markdown:
            (run_dir / f"fixplan-{plan_slug(repo)}.md").write_text(markdown, encoding="utf-8")
        total.feed_errors += errors


def _server_plans(cfg: dict, run_dir: Path, total: Report) -> str:
    """Write ``server-fixplan-<host>.md`` per configured host with findings (plan D1); return the summary lines.

    The plans go to ``<state>/server-plans/<date>/``, outside the run directory the agent container mounts; the summary
    (which the agent reads) names them by file name only.
    """
    from agentteams.vulnscan import server_fixplan

    kit = paths.server_kit_dir()
    home = str(Path.home())
    shown = "~" + str(kit)[len(home):] if str(kit).startswith(home + os.sep) else str(kit)
    try:
        plans = server_fixplan.write(paths.state_dir() / "server-plans" / run_dir.name, total,
                                     [str(h) for h in cfg.get("servers") or []], kit / "apps.conf", kit=shown)
    except OSError as exc:
        return f"\n## Server fix plans\n\n- could not write them: {type(exc).__name__}\n"
    if not plans:
        return ""
    return ("\n## Server fix plans (apply by hand with the server kit)\n\n"
            f"In the state directory under `server-plans/{run_dir.name}/`:\n\n"
            + "".join(f"- `{p.name}`\n" for p in plans))


def _handoff_section(cfg: dict) -> str:
    """Return the summary's handoff section (H2: staged fixes and the repository orchestrators' verdicts), or ""."""
    from agentteams.vulnscan import handoff   # lazy: executor-side state, read only

    try:
        return "\n" + handoff.markdown(handoff.status(cfg, handoff.repos_with_staged_fixes()))
    except (OSError, ValueError) as exc:
        return f"\n## Handoffs to repository orchestrators\n\n- could not read the handoff state: {exc}\n"


def _finish(cfg: dict, state: Path, today: str, run_dir: Path, total: Report,
            staged: list[str]) -> tuple[Report, int, bool]:
    """Write summary + heartbeat, notify, and return (report, exit code, whether the files were written).

    A write that fails (disk full, a directory in the way) is caught: the run becomes unscanned
    (exit 3, unless it found compromise indicators, exit 4), the reason goes to stderr and the
    notification is still attempted.
    """
    previous, notes = _previous_summary(state, today)
    new = new_finding_keys(total, previous)
    total.apply_unscored(str(cfg.get("unscored_as", "high")))
    code = total.exit_code(cfg.get("fail_on", "high"), bool(cfg.get("include_dev")))
    md = total.markdown(new_keys=new)
    md += _freshness_section(_FRESH.get() or [])   # a new section after the per-repository ones
    if notes:
        md += "\n## Notes\n\n" + "".join(f"- {n}\n" for n in notes)
    md += "\n" + coverage.section(cfg, report=total)
    eff_md, eff = effectiveness.section(total, previous)   # counts only: the summary is published
    merge_digest.write(run_dir, eff.green)   # machine-local, 0700, never published; asks per merge
    md += "\n" + eff_md
    readiness = remediation_status.collect(cfg)   # B1/B4: what would stop the executor; never raises
    md += "\n" + remediation_status.markdown(readiness)
    md += _handoff_section(cfg)
    md += _server_plans(cfg, run_dir, total)
    if staged:
        md += "\n## Fixes staged in worktrees (review, test, commit, open a PR)\n\n" + "\n".join(staged) + "\n"
    written = True
    try:
        _archive_summary(run_dir)   # a same-day re-run never loses the earlier summary (R5)
        (run_dir / "summary.md").write_text(md, encoding="utf-8")
        # private package names stay in the machine-local repos-*.json; the MCP server reads summary.json
        (run_dir / "summary.json").write_text(json.dumps({**total.to_dict(private_names=False),
                                                          "remediation": readiness,
                                                          "infra_codes": sorted(_INFRA.get() or ())}, indent=2), encoding="utf-8")
        _write_atomic(heartbeat_path(), json.dumps({
            "last_run": _dt.datetime.now(_dt.UTC).isoformat(), "exit": code, "findings": len(total.findings),
            "new": len(new), "unscanned": len(total.feed_errors), "summary": str(run_dir / "summary.md"),
            "agentteams_version": readiness["agentteams_version"], "remediation_blocked": len(readiness["blocked"])},
            indent=2))
    except OSError as exc:
        written = False
        msg = f"could not write the run's summary or heartbeat: {exc}"
        total.feed_errors.append(msg)
        print(f"vulnscan daily: {msg}", file=sys.stderr)
        code = code if code == EXIT_COMPROMISE else EXIT_UNSCANNED
    remediation_status.notify(cfg, readiness)
    if code or new:
        notify.send(cfg, "run-done", state=RUN_LABELS.get(code, NEW_FINDINGS_LABEL),
                    findings=len(total.findings), new=len(new))
    effectiveness.alerts(cfg, eff)   # remediation PRs older than STALE_DAYS or failing CI
    return total, code, written
