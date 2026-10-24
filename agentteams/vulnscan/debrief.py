"""Interactive debrief: question the agent about a past run, in the same isolated container.

The operator resumes the run's goose session (``vulnscan-<run dir name>``, the name
:func:`agentteams.vulnscan.agent_run.run_agent` gives it) and asks the agent why it did what it
did. **The agent can explain but cannot act**:

- the request spool is mounted read-only, so the vulnscan MCP ``request`` tool cannot queue work;
- no executor serves requests during a debrief;
- the container keeps every isolation flag of an agent run (read-only rootfs, ``--cap-drop ALL``,
  ``no-new-privileges``, a non-root uid, the internal-only network);
- it reaches the model through its *own* gateway (:data:`DEBRIEF_GATEWAY`) with a small budget
  (``agent.debrief_budget_usd``, default 0.5), a hard time cap (``agent.debrief_max_minutes``,
  default 20) and a token cap (``agent.debrief_max_tokens``, an integer 1..100 000 000; bools, floats
  and strings are refused like :func:`agentteams.vulnscan.agent_run.run_limits` refuses them).

Only one of an agent run or a debrief runs at a time: :func:`run_debrief` takes
:func:`agentteams.vulnscan.agent_run.run_lock_path` non-blockingly and refuses when it is held.

How a session is resumed is the runtime driver's (:meth:`agentteams.vulnscan.agent_drivers.Driver.debrief`);
a framework without debrief support is refused with ``no debrief for <framework>`` (advisory only, never
part of the safety path). The goose facts the resume command relies on are recorded, with sources, by
:func:`verify_goose_sessions`.
"""

from __future__ import annotations

import fcntl
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import agent_drivers, agent_run, agent_upstreams, docker_mode, executor, gateway_context, paths
from agentteams.vulnscan.agent_drivers import Driver

DEBRIEF_CONTAINER = "agentteams-vulnscan-debrief"
DEBRIEF_GATEWAY = "agentteams-vulnscan-gw-debrief"
MCP_EXTENSION = agent_drivers.MCP_COMMAND
DEFAULT_DEBRIEF_BUDGET_USD = 0.5
DEFAULT_DEBRIEF_MAX_MINUTES = 20.0
DEFAULT_DEBRIEF_MAX_TOKENS = 400_000   # prompt + completion, enforced by the gateway

EXIT_REFUSED = 2          # a precondition failed (no docker, no self-test, no session, no TTY, ...)
EXIT_LOCKED = 75          # EX_TEMPFAIL: an agent run or another debrief holds the run lock
EXIT_TIMED_OUT = 124      # the hard time cap was reached and the container was removed

SQLITE_SINCE = (1, 10, 0)

SOURCES: dict[str, str] = {
    "cli_commands": "https://goose-docs.ai/docs/guides/goose-cli-commands",
    "session_management": "https://goose-docs.ai/docs/guides/sessions/session-management",
    "paths_source": "https://github.com/block/goose/blob/main/crates/goose/src/config/paths.rs",
    "session_manager_source": "https://github.com/block/goose/blob/main/crates/goose/src/session/session_manager.rs",
    "cli_source": "https://github.com/block/goose/blob/main/crates/goose-cli/src/cli.rs",
    "builder_source": "https://github.com/block/goose/blob/main/crates/goose-cli/src/session/builder.rs",
}


def _parse_version(goose_version: str | None) -> tuple[int, int, int] | None:
    """Return ``(major, minor, patch)`` from a goose version string, or None when unparseable."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?", goose_version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def verify_goose_sessions(goose_version: str | None = None) -> dict[str, Any]:
    """Return the verified goose session-storage and resume facts, with source URLs.

    Researched 2026-10-22 against the goose docs (now hosted by AAIF at goose-docs.ai) and the
    ``main`` branch of the goose source (github.com/block/goose, served as aaif-goose/goose).
    Facts that could not be confirmed from those sources carry the value ``"unverified"``.

    Args:
        goose_version: The goose release in the image (e.g. ``"1.12.0"``), or None when unknown.
            Storage differs before and after 1.10.0.

    Returns:
        A dict with ``storage``, ``resume``, ``extensions_on_resume``, ``run_name_resumable``,
        ``container_path`` (comparison with :data:`agent_run.GOOSE_SESSIONS_IN_CONTAINER`),
        ``version_notes`` and ``sources`` (name -> URL). Each fact names the source keys it rests on.
    """
    ver = _parse_version(goose_version)
    if ver is None:
        storage_kind = "unverified (goose version unknown: sqlite from 1.10.0, JSONL files before)"
    elif ver >= SQLITE_SINCE:
        storage_kind = "sqlite"
    else:
        storage_kind = "jsonl"
    data_dir = f"{agent_run.AGENT_HOME}/.local/share/goose"
    return {
        "goose_version": goose_version or "unknown",
        "storage": {
            "kind": storage_kind,
            "sqlite_db": "<data_dir>/sessions/sessions.db",
            "legacy_jsonl": "<data_dir>/sessions/*.jsonl (pre-1.10.0; imported once, then unmanaged)",
            "legacy_jsonl_exact_layout": "unverified",
            "data_dir_linux": "$XDG_DATA_HOME/goose, else $HOME/.local/share/goose (etcetera XDG strategy)",
            "override_env": "GOOSE_PATH_ROOT (absolute path only) -> data dir becomes <root>/data",
            "xdg_data_home_honoured": "verified 2026-10-22 with goose 1.37.0 (macOS host build): the store was "
                                      "$XDG_DATA_HOME/goose/sessions/sessions.db; tests/test_vulnscan_live.py "
                                      "re-checks it against the installed goose",
            "sources": ["session_management", "paths_source", "session_manager_source"],
        },
        "resume": {
            "interactive": ["goose", "session", "--resume", "--name", "<session>"],
            "by_id": ["goose", "session", "--resume", "--session-id", "<id>"],
            "name_lookup": "matches session name or id across list_sessions(); errors if absent "
                           "(\"No session found with name ...\"), never creates one",
            "sources": ["cli_commands", "session_management", "cli_source"],
        },
        "run_name_resumable": {
            "fact": "goose run --name X creates a session with user-provided name X; "
                    "goose session --resume --name X finds it by name",
            "session_type_filter_on_named_lookup": "unverified (list_sessions() body not read)",
            "sources": ["cli_commands", "cli_source"],
        },
        "extensions_on_resume": {
            "fact": "resume re-loads the session's saved enabled extensions and appends CLI "
                    "--with-extension ones; --no-profile is ignored on resume",
            "re_supply_required": False,
            "re_supply_harmless": "unverified (dedup is documented only for builtins; a duplicate "
                                  "stdio extension may be renamed, replaced or rejected)",
            "sources": ["builder_source", "session_manager_source"],
        },
        "container_path": {
            "expected_data_dir": data_dir,
            "provisional_constant": agent_run.GOOSE_SESSIONS_IN_CONTAINER,
            "matches": agent_run.GOOSE_SESSIONS_IN_CONTAINER == data_dir,
            "note": "valid while HOME is AGENT_HOME, XDG_DATA_HOME is AGENT_HOME/.local/share (agent_run sets "
                    "it) and GOOSE_PATH_ROOT is unset; XDG_DATA_HOME was verified on a macOS host build, not "
                    "inside the Linux container. XDG_STATE_HOME is GOOSE_STATE_IN_CONTAINER, inside the "
                    "sessions mount, so goose's logs land in <run>/agent/goose-data/state/goose/logs",
        },
        "version_notes": [
            "1.10.0: sessions moved from per-session .jsonl files to sessions/sessions.db (sqlite)",
            "behaviour before 1.10.0 (resume flags, extension restore): unverified",
        ],
        "sources": dict(SOURCES),
    }


def session_name(run_dir: Path) -> str:
    """Return the goose session name an agent run used.

    Args:
        run_dir: Run directory.

    Returns:
        ``vulnscan-<run dir name>`` (as set by :func:`agent_run.run_agent`).
    """
    return f"vulnscan-{run_dir.name}"


def resume_command(session: str, driver: Driver | None = None) -> list[str]:
    """Return the in-container command that resumes a named session interactively (the driver's).

    Args:
        session: Session name.
        driver: The runtime driver (default goose: ``goose session --resume --name``, re-passing the vulnscan
            MCP extension; see :func:`verify_goose_sessions`).

    Returns:
        argv.

    Raises:
        ValueError: ``session`` is empty or not a plain name, or the driver has no debrief.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", session or ""):
        raise ValueError(f"invalid session name: {session!r}")
    d = driver or agent_drivers.GOOSE
    support = d.debrief()
    if support is None:
        raise ValueError(f"no debrief for {d.name}")
    return support.resume(session)


def debrief_argv(a: dict[str, Any], tag: str, run_dir: Path, session: str, user: str | None = None) -> list[str]:
    """Return the ``docker run`` command for the debrief container.

    Same isolation as an agent run (via :func:`agent_run.agent_base`), with ``-it``, its own name,
    the request spool mounted read-only, and its own gateway. As in a run, ``/work/run/gateway`` and
    ``/work/run/debrief`` (this debrief's own gateway files) are masked by an empty read-only tmpfs (R6).

    Args:
        a: Agent settings (:func:`agent_run.agent_cfg`).
        tag: Image tag.
        run_dir: The past run's directory (mounted read-only; its goose sessions dir read-write).
        session: Session name to resume.
        user: The container user for the Docker mode (R4, :func:`agent_run.container_user`); None: the default.

    Returns:
        argv.

    Raises:
        ValueError: ``session`` is not a plain name, or the framework has no driver or no debrief.
    """
    d = agent_run.driver_of(a)
    team = Path(str(a["team_dir"] or run_dir)).expanduser().resolve()
    base = agent_run.agent_base(tag, run_dir, agent_run.sessions_dir(run_dir, d), team, name=DEBRIEF_CONTAINER,
                                gateway=DEBRIEF_GATEWAY, spool_mode="ro", interactive=True, driver=d, user=user)
    return [*base, *agent_drivers.run_env_args(d, a), tag, *resume_command(session, d)]


def debrief_limits(a: dict[str, Any]) -> tuple[float, float]:
    """Return the debrief budget and time cap.

    Args:
        a: Agent settings.

    Returns:
        ``(budget_usd, max_minutes)``.

    Raises:
        ValueError: A configured value is not a real number in range: the same rules as
            :func:`agent_run.run_limits` (bools, strings, None, NaN and infinity are refused; X11).
    """
    budget = agent_run.positive_number(a.get("debrief_budget_usd", DEFAULT_DEBRIEF_BUDGET_USD),
                                       "agent.debrief_budget_usd", agent_run.BUDGET_USD_CEILING)
    minutes = agent_run.positive_number(a.get("debrief_max_minutes", DEFAULT_DEBRIEF_MAX_MINUTES),
                                        "agent.debrief_max_minutes", agent_run.MAX_MINUTES_CEILING)
    return budget, minutes


def debrief_max_tokens(a: dict[str, Any]) -> int:
    """Return the debrief's token cap (``agent.debrief_max_tokens``), enforced by its gateway.

    Args:
        a: Agent settings.

    Returns:
        The cap (a positive integer).

    Raises:
        ValueError: The configured value is not an integer in ``1..MAX_TOKENS_TOTAL_CEILING`` (bools, floats
            and strings are refused, as in :func:`agent_run.run_limits`).
    """
    cap = a.get("debrief_max_tokens", DEFAULT_DEBRIEF_MAX_TOKENS)
    ceiling = agent_run.MAX_TOKENS_TOTAL_CEILING
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= ceiling:
        raise ValueError(f"agent.debrief_max_tokens must be an integer 1..{ceiling} (got {cap!r})")
    return cap


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _refuse(code: int, why: str) -> int:
    print(f"debrief refused: {why}", file=sys.stderr)
    return code


def _preflight(cfg: dict[str, Any], a: dict[str, Any], run_dir: Path) -> str:
    """Return why a debrief cannot start, or ``""`` when it can (lock not checked here)."""
    try:
        driver = agent_run.driver_of(a)
    except ValueError as exc:
        return str(exc)
    if driver.debrief() is None:
        return f"no debrief for {driver.name}"
    if not shutil.which("docker"):
        return "docker is not installed"
    if not a.get("key_file"):
        return "agent.key_file is required"
    team_problem = agent_run.team_dir_problem(str(a.get("team_dir") or ""), agent_upstreams.secret_paths(a))
    if team_problem:   # checked before anything is mounted, as for a run
        return f"agent.team_dir: {team_problem}"
    if not agent_run.sessions_dir(run_dir, driver).is_dir():
        return f"no {driver.name} sessions for run {run_dir.name} (was the agent run?)"
    if not _stdin_is_tty():
        return "a debrief is interactive and needs a terminal"
    ok, why = agent_run.selftest_ok(cfg)
    return "" if ok else why


def _start_gateway(a: dict[str, Any], tag: str, spend_dir: Path, budget_usd: float, user: str | None = None) -> None:
    """Start the debrief's gateway, as :func:`agent_run._start_gateway` (gateway_context.DlpRefused; R5)."""
    agent_run._docker(["docker", "rm", "-f", DEBRIEF_GATEWAY], check=False)
    dlp = gateway_context.prepare(spend_dir, skip_ok=a.get("dlp_skip_ok") or [])   # B2: the host's DLP context
    agent_run._docker(agent_run.gateway_argv(a, tag, spend_dir, name=DEBRIEF_GATEWAY, budget_usd=budget_usd,
                                             max_tokens_total=debrief_max_tokens(a), dlp=dlp, user=user))
    try:
        agent_run._docker(["docker", "network", "connect", agent_run.NETWORK, DEBRIEF_GATEWAY])
    except RuntimeError:   # the gateway already exited: its DLP-context refusal, if any, is the reason
        gateway_context.raise_if_refused(spend_dir)
        raise
    time.sleep(2)
    gateway_context.raise_if_refused(spend_dir)


def _cleanup() -> None:
    agent_run._docker(["docker", "rm", "-f", DEBRIEF_CONTAINER], check=False)
    agent_run._docker(["docker", "rm", "-f", DEBRIEF_GATEWAY], check=False)


def run_debrief(cfg: dict[str, Any], run_dir: Path) -> int:
    """Resume a past run's session interactively (the driver's resume command), with the terminal attached.

    Holds :func:`agent_run.run_lock_path` for the whole debrief and refuses when it is already held.
    Starts a dedicated gateway (budget ``agent.debrief_budget_usd``), runs the container with
    inherited stdio, removes it at ``agent.debrief_max_minutes``, and always removes the container
    and the gateway. Spend and an outcome record go to ``<run>/debrief/<UTC timestamp>/``.

    Args:
        cfg: Daily config.
        run_dir: The past run's directory.

    Returns:
        The container's exit code; :data:`EXIT_TIMED_OUT` at the time cap; :data:`EXIT_LOCKED`
        when the run lock is held; :data:`EXIT_REFUSED` when a precondition fails or a debrief limit
        (``agent.debrief_budget_usd``, ``debrief_max_minutes``, ``debrief_max_tokens``) is invalid.

    Raises:
        RuntimeError: A ``docker`` command needed to start the gateway failed (cleanup still runs).
        SystemExit: SIGTERM arrived during the debrief (status :data:`agent_run.SIGTERM_EXIT`), after
            the container and the gateway were removed and the outcome recorded (M4).
    """
    a = agent_run.agent_cfg(cfg)
    try:   # all three limits before anything starts: a bad one is a refusal, not a traceback
        budget, minutes = debrief_limits(a)
        debrief_max_tokens(a)
    except ValueError as exc:
        return _refuse(EXIT_REFUSED, str(exc))
    why = _preflight(cfg, a, run_dir)
    if why:
        return _refuse(EXIT_REFUSED, why)
    lock_path = agent_run.run_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _refuse(EXIT_LOCKED, "an agent run or another debrief holds the run lock")
        try:
            with agent_run.sigterm_raises_exit():   # M4: a SIGTERM still runs the cleanup below
                return _locked_debrief(a, run_dir, budget, minutes)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _locked_debrief(a: dict[str, Any], run_dir: Path, budget: float, minutes: float) -> int:
    """Run the debrief; the caller holds the run lock and has turned SIGTERM into ``SystemExit``.

    A SIGTERM (``SystemExit`` with :data:`agent_run.SIGTERM_EXIT`) is recorded as ``terminated``
    after the container and the gateway are removed, then re-raised.
    """
    tag = agent_run.image_tag(a)
    mode = agent_run.detect_docker_mode()   # R4: the user the self-test ran as, or no debrief (as a run)
    user = agent_run.container_user(mode.user)
    if mode.problem or user != docker_mode.recorded_user(paths.state_dir() / "agent-selftest.json", agent_run.AGENT_UID):
        return _refuse(EXIT_REFUSED, mode.problem or "the Docker mode changed since the last passing self-test — "
                                                     "re-run agent-selftest")   # before any directory is made
    started = datetime.now(UTC)
    out = run_dir / "debrief" / started.strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    for d in (executor.spool_dir() / "requests", executor.spool_dir() / "results",
              *(run_dir / m for m in agent_run.MASKED_RUN_DIRS)):   # R6: the masks' mountpoints, before docker run
        d.mkdir(parents=True, exist_ok=True)
    argv = debrief_argv(a, tag, run_dir, session_name(run_dir), user=user)
    timed_out = terminated = False
    rc = EXIT_REFUSED
    agent_run._ensure_network()
    try:
        _cleanup()
        _start_gateway(a, tag, out, budget, user)
        proc = subprocess.Popen(argv)                    # inherited stdio: the operator's terminal
        try:
            rc = proc.wait(timeout=60 * minutes)
        except subprocess.TimeoutExpired:
            timed_out = True
            agent_run._docker(["docker", "rm", "-f", DEBRIEF_CONTAINER], check=False)
            proc.kill()
            proc.wait()
            rc = EXIT_TIMED_OUT
            print(f"debrief stopped: the {minutes:g}-minute cap was reached", file=sys.stderr)
    except gateway_context.DlpRefused as exc:   # R5: the DLP check cannot cover every secret; no debrief started
        print(f"debrief not started: {exc}", file=sys.stderr)
    except SystemExit as exc:
        if exc.code == agent_run.SIGTERM_EXIT:   # recorded below; the finally removes both containers
            terminated, rc = True, agent_run.SIGTERM_EXIT
        raise
    finally:
        _cleanup()
        record = {"session": session_name(run_dir), "started": started.isoformat(),
                  "finished": datetime.now(UTC).isoformat(), "exit": rc, "timed_out": timed_out,
                  "terminated": terminated, "budget_usd": budget, "max_minutes": minutes}
        (out / "status.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return rc
