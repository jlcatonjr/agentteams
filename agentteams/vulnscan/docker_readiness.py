"""Docker readiness: the one probe of what the vulnscan containers need, with a reason code per failure (T2-A).

Before this module four places each probed Docker their own way and gave one message for every failure
(``subproc_confine.unavailable_reason``, ``agent_run.docker_unreachable``, the ``tools_container`` entry of
``remediation_status.collect`` and the executor's batch check). They now all ask :func:`tools_problem` or
:func:`docker_problem`, and the daily run asks :func:`ensure` once, before it scans.

:func:`ensure` checks six layers in order and returns a frozen :class:`Readiness`:

| Layer | Checked | Failure code |
|---|---|---|
| ``cli`` | the ``docker`` CLI is on PATH | ``infra.docker_daemon`` |
| ``daemon`` | ``docker version`` answers (after starting Docker, when allowed) | ``infra.docker_daemon``, or ``infra.no_gui_session`` when Docker Desktop cannot be started |
| ``tools_image`` | the tools image is built (confinement on and something uses it) | ``infra.tools_image`` |
| ``agent_image`` | the agent image is built (agent mode on) | ``infra.tools_image`` |
| ``tools_selftest`` | a passing ``tools-selftest`` record for that image and the container flags | ``infra.selftest`` |
| ``agent_selftest`` | a passing ``agent-selftest`` record for the agent image (``agent_run.selftest_ok``) | ``infra.selftest`` |

A layer nothing in the config uses is ``off``; one an earlier failure hides is ``skipped``.

**What it may do** (``ensure(cfg, start=True)``, and only then):

- start Docker when the ``docker.autostart`` config key is true (default **false**: generated teams never launch
  an app unless their operator opts in). macOS: ``open -g -a Docker``, after checking that a GUI login session
  exists (``launchctl print gui/<uid>``); Linux: ``systemctl --user start docker-desktop``, else ``docker``
  (rootless). Never sudo. It then polls ``docker version`` every :data:`POLL_S` seconds up to
  ``docker.start_timeout_s``;
- run ``tools-selftest`` / ``agent-selftest`` when ``docker.auto_selftest`` is ``on-change`` (the default) and the
  recorded result is for another image (or the tools container flags changed): once per image change, never
  again for an image whose self-test already failed. The caller holds the daily run lock (``daily`` does;
  ``vulnscan ready --ensure`` takes it).

It never builds an image and never changes a grant. Every subprocess goes through a runner
(``runner(argv, timeout) -> CompletedProcess``; by default the docker client of the module whose container is
checked, ``subproc_confine._docker`` or ``agent_run._docker``), so tests pass a fake one.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

#: The reason codes (stable: the remediation report and the catch-up retry match on them).
DAEMON = "infra.docker_daemon"
IMAGE = "infra.tools_image"
SELFTEST = "infra.selftest"
NO_GUI = "infra.no_gui_session"
CODES = (DAEMON, IMAGE, SELFTEST, NO_GUI)

DEFAULTS: dict[str, Any] = {"autostart": False, "start_timeout_s": 240, "auto_selftest": "on-change"}
AUTO_SELFTEST = ("on-change", "off")
MAX_START_TIMEOUT_S = 1800
PROBE_TIMEOUT = 15          # seconds for ``docker version`` (a hung client must not hang the run)
INSPECT_TIMEOUT = 60        # seconds for ``docker image inspect``
POLL_S = 5
LAYERS = ("cli", "daemon", "tools_image", "agent_image", "tools_selftest", "agent_selftest")
COMMANDS = ("ready",)
EXIT_READY, EXIT_CONFIG, EXIT_DEGRADED = 0, 2, 3

Runner = Callable[[list[str], int], subprocess.CompletedProcess]

_DAEMON_DOWN = "docker is not reachable (the daemon does not answer `docker version`)"


class Reason(str):
    """A one-line reason that also carries its reason code (``""`` when the probe found nothing)."""

    code: str

    def __new__(cls, message: str = "", code: str = "") -> Reason:
        obj = super().__new__(cls, message)
        obj.code = code if message else ""
        return obj


@dataclass(frozen=True)
class Layer:
    """One layer's result: ``ok``, ``fail``, ``off`` (not used by this config) or ``skipped`` (an earlier failure)."""

    state: str
    code: str = ""
    message: str = ""


_OFF = Layer("off", "", "not used by this config")


@dataclass(frozen=True)
class Readiness:
    """What :func:`ensure` found, layer by layer (see the module docstring)."""

    cli: Layer = _OFF
    daemon: Layer = _OFF
    tools_image: Layer = _OFF
    agent_image: Layer = _OFF
    tools_selftest: Layer = _OFF
    agent_selftest: Layer = _OFF
    tools_tag: str = ""
    agent_tag: str = ""
    actions: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """Whether no layer failed."""
        return all(getattr(self, name).state != "fail" for name in LAYERS)

    def problem(self, *names: str) -> Reason:
        """Return the first failing layer among ``names`` (default: all) as a :class:`Reason`, or ``Reason()``.

        Args:
            *names: Layer names, in :data:`LAYERS` order.

        Returns:
            The failure's message and code, or an empty reason.
        """
        for name in names or LAYERS:
            layer = getattr(self, name)
            if layer.state == "fail":
                return Reason(layer.message, layer.code)
        return Reason()

    def tools(self) -> Reason:
        """Return why the tools container cannot be used, or ``Reason()``."""
        return self.problem("cli", "daemon", "tools_image", "tools_selftest")

    def agent(self) -> Reason:
        """Return why the agent container cannot be used, or ``Reason()``."""
        return self.problem("cli", "daemon", "agent_image", "agent_selftest")

    def to_dict(self) -> dict[str, dict[str, str]]:
        """Return ``{layer: {"state", "code", "message"}}`` (``summary.json["remediation"]["docker"]``)."""
        return {name: asdict(getattr(self, name)) for name in LAYERS}


# ---------------------------------------------------------------- configuration

def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return the ``docker`` block merged over :data:`DEFAULTS`.

    Args:
        cfg: Daily config (or None).

    Returns:
        The settings.
    """
    block = (cfg or {}).get("docker")
    return {**DEFAULTS, **(block if isinstance(block, dict) else {})}


def config_problem(cfg: dict[str, Any]) -> str:
    """Return why ``cfg["docker"]`` is malformed, or "" (absent means the defaults).

    Args:
        cfg: Daily config.

    Returns:
        The problem, as ``"docker.<key> must be ..."``, or "".
    """
    block = cfg.get("docker", {})
    if not isinstance(block, dict):
        return f"docker must be an object (got {repr(block)[:80]})"
    unknown = sorted(set(block) - set(DEFAULTS))
    if unknown:
        return f"docker has unknown keys {unknown} (known: {', '.join(DEFAULTS)})"
    s = settings(cfg)
    if not isinstance(s["autostart"], bool):
        return f"docker.autostart must be true or false (got {repr(s['autostart'])[:80]})"
    t = s["start_timeout_s"]
    if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= MAX_START_TIMEOUT_S:
        return f"docker.start_timeout_s must be a whole number of seconds 1-{MAX_START_TIMEOUT_S} (got {repr(t)[:80]})"
    if s["auto_selftest"] not in AUTO_SELFTEST:
        return f"docker.auto_selftest must be one of {', '.join(AUTO_SELFTEST)} (got {repr(s['auto_selftest'])[:80]})"
    return ""


# ---------------------------------------------------------------- the run's record

_CURRENT: contextvars.ContextVar[Readiness | None] = contextvars.ContextVar("vulnscan_docker", default=None)


def current() -> Readiness | None:
    """Return the record the daily run is using (:func:`using`), or None outside one."""
    return _CURRENT.get()


@contextlib.contextmanager
def using(rec: Readiness) -> Iterator[Readiness]:
    """Make ``rec`` the answer of :func:`tools_problem` and :func:`docker_problem` (no re-probe) inside the block.

    Args:
        rec: :func:`ensure`'s result.

    Yields:
        ``rec``.
    """
    token = _CURRENT.set(rec)
    try:
        yield rec
    finally:
        _CURRENT.reset(token)


# ---------------------------------------------------------------- probes

def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _tools_runner(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run through ``subproc_confine._docker``: the tools probes use the tools module's docker client."""
    from agentteams.vulnscan import subproc_confine

    return subproc_confine._docker(argv, check=False, timeout=timeout)


def _agent_runner(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run through ``agent_run._docker``: the agent probes use the agent module's docker client."""
    from agentteams.vulnscan import agent_run

    return agent_run._docker(argv, check=False, timeout=timeout)


def _try(run: Runner, argv: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    """Run ``argv``; None when it could not run at all (not installed, hung)."""
    try:
        return run(argv, timeout)
    except (OSError, subprocess.SubprocessError):   # incl. subproc_confine.Unavailable from its own runner
        return None


def _daemon_up(run: Runner) -> bool:
    proc = _try(run, ["docker", "version", "--format", "{{.Server.Version}}"], PROBE_TIMEOUT)
    return proc is not None and proc.returncode == 0


def _image_id(run: Runner, tag: str) -> str:
    proc = _try(run, ["docker", "image", "inspect", "-f", "{{.Id}}", tag], INSPECT_TIMEOUT)
    return proc.stdout.strip() if proc is not None and proc.returncode == 0 and proc.stdout else ""


def _tools_record(image_id: str) -> tuple[bool, bool]:
    """Return (a passing record for ``image_id`` and the current flags, a record for them exists at all)."""
    from agentteams.vulnscan import subproc_confine as sc

    try:
        rec = json.loads(sc.selftest_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, False
    if not isinstance(rec, dict):
        return False, False
    same = rec.get("image_id") == image_id and rec.get("flags_sha256") == sc.flags_sha256(image_id)
    return same and rec.get("passed") is True, same


def _tools_selftest_missing(tag: str) -> str:
    return (f"no passing tools-selftest for {tag} and the current container flags — run "
            "`python -m agentteams.vulnscan tools-selftest`")


def tools_problem(tag: str, *, runner: Runner | None = None) -> Reason:
    """Return why the tools container ``tag`` cannot be used now, or ``Reason()``; read-only (starts nothing).

    Inside :func:`using` the run's record answers for its own tag, so a daily run probes once.

    Args:
        tag: The tools image tag.
        runner: Runs one command (default ``subprocess.run``).

    Returns:
        The first failure of cli, daemon, tools image and tools self-test record, with its code.
    """
    rec = current()
    if rec is not None and rec.tools_tag == tag and rec.tools_image.state != "off":
        return rec.tools()
    run = runner or _run
    if not shutil.which("docker"):
        return Reason("docker is not installed", DAEMON)
    if not _daemon_up(run):
        return Reason(f"{_DAEMON_DOWN} — start Docker", DAEMON)
    image_id = _image_id(run, tag)
    if not image_id:
        return Reason(f"the tools image {tag} is not built — run `python -m agentteams.vulnscan tools-image build`",
                      IMAGE)
    if not _tools_record(image_id)[0]:
        return Reason(_tools_selftest_missing(tag), SELFTEST)
    return Reason()


def docker_problem(tag: str | None = None, *, runner: Runner | None = None) -> Reason:
    """Return why Docker cannot be used (or the image ``tag`` is not built), or ``Reason()``; read-only.

    Args:
        tag: An image that must exist; None checks only that the daemon answers.
        runner: Runs one command (default ``subprocess.run``).

    Returns:
        The daemon's failure, else the image's, with its code.
    """
    rec = current()
    if rec is not None and tag is not None and rec.agent_tag == tag and rec.agent_image.state != "off":
        return rec.problem("cli", "daemon", "agent_image")
    run = runner or _run
    if not _daemon_up(run):
        return Reason(f"{_DAEMON_DOWN} — start Docker (or install it) first", DAEMON)
    if tag is not None and not _image_id(run, tag):
        return Reason(f"the agent image {tag} is not built — run `python -m agentteams.vulnscan agent-image build`",
                      IMAGE)
    return Reason()


# ---------------------------------------------------------------- ensure

def needs(cfg: dict[str, Any]) -> tuple[bool, bool]:
    """Return (the tools container is used, the agent container is used) by this config.

    The tools container is used when confinement is on and servers are audited, fixes are staged
    (``apply-worktree``), agent mode is on (its requests become executor ops) or a grant is installed. The agent
    container is used when agent mode is on with ``team_dir`` and ``key_file`` set (until then the missing setting,
    not Docker, is why the agent does not run).

    Args:
        cfg: Daily config.

    Returns:
        ``(tools, agent)``.
    """
    from agentteams.vulnscan import subproc_confine

    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    enabled = bool(agent.get("enabled"))
    on = enabled and bool(agent.get("team_dir")) and bool(agent.get("key_file"))
    if not subproc_confine.enabled(cfg):
        return False, on
    if cfg.get("servers") or cfg.get("fix_mode") == "apply-worktree" or enabled:
        return True, on
    from agentteams.vulnscan import grant

    try:
        grants, problems = grant.load_active()
    except (OSError, ValueError):
        return True, on   # an unreadable grant directory still means executor ops are configured
    return bool(grants or problems), on


def _start(s: dict[str, Any], run: Runner, probe: Runner) -> Layer | None:
    """Start Docker (never sudo; ``run``) and wait for it (``probe``); return the failure, or None once it answers."""
    system = platform.system()
    if system == "Darwin":
        gui = _try(run, ["launchctl", "print", f"gui/{os.getuid()}"], PROBE_TIMEOUT)
        if gui is None or gui.returncode:
            return Layer("fail", NO_GUI, "no GUI login session, so Docker Desktop cannot be started")
        opened = _try(run, ["open", "-g", "-a", "Docker"], 60)
        if opened is None or opened.returncode:
            return Layer("fail", DAEMON, "could not launch Docker.app (`open -g -a Docker` failed)")
    elif system == "Linux":
        if not any((p := _try(run, ["systemctl", "--user", "start", unit], 60)) is not None and not p.returncode
                   for unit in ("docker-desktop", "docker")):
            return Layer("fail", DAEMON, "`systemctl --user start docker-desktop` and `... docker` both failed")
    else:
        return Layer("fail", DAEMON, f"{_DAEMON_DOWN}; starting Docker is not supported on {system}")
    deadline = time.monotonic() + int(s["start_timeout_s"])
    while not _daemon_up(probe):
        if time.monotonic() >= deadline:
            return Layer("fail", DAEMON, f"started Docker; the daemon did not answer within {s['start_timeout_s']}s")
        time.sleep(POLL_S)
    return None


def _tools_selftest_run(cfg: dict[str, Any]) -> tuple[bool, str]:
    from agentteams.vulnscan import tools_image

    try:
        res = tools_image.selftest(cfg)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
    return bool(res.get("passed")), str(res.get("error") or "")


def _agent_selftest_run(cfg: dict[str, Any]) -> tuple[bool, str]:
    from agentteams.vulnscan import agent_run

    try:
        res = agent_run.selftest(cfg)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
    return bool(res.get("passed")), str(res.get("error") or "")


def _agent_record_image() -> str | None:
    """Return the image id the agent self-test record is for, or None when there is no readable record."""
    from agentteams.vulnscan import paths

    try:
        rec = json.loads((paths.state_dir() / "agent-selftest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return str(rec.get("image") or "") if isinstance(rec, dict) else None


def _agent_tag(cfg: dict[str, Any]) -> str:
    from agentteams.vulnscan import agent_run

    return agent_run.image_tag(agent_run.agent_cfg(cfg))


def ensure(cfg: dict[str, Any], *, start: bool, runner: Runner | None = None) -> Readiness:
    """Check (and, when ``start`` and the config allow it, repair) what the vulnscan containers need; never raises.

    Args:
        cfg: Daily config (``docker``, ``confine`` and ``agent`` blocks).
        start: Allow the repairs: start Docker when ``docker.autostart`` is true, and run a self-test whose record
            is for another image when ``docker.auto_selftest`` is ``on-change``. False only reads.
        runner: Runs one command (default: the tools or the agent module's docker client, as their own probes do).

    Returns:
        The :class:`Readiness` record.
    """
    from agentteams.vulnscan import agent_run, subproc_confine

    run_tools, run_agent = runner or _tools_runner, runner or _agent_runner
    s = settings(cfg)
    use_tools, use_agent = needs(cfg)
    if not (use_tools or use_agent):
        return Readiness()
    tools_tag = subproc_confine.image_tag(subproc_confine.settings(cfg)) if use_tools else ""
    try:
        agent_tag = _agent_tag(cfg) if use_agent else ""
    except (ValueError, KeyError, TypeError) as exc:   # a malformed agent block: load_config refuses it first
        agent_tag, use_agent = "", False
        print(f"vulnscan: agent image tag unknown ({exc}); its layers are not checked", file=sys.stderr)
    actions: list[str] = []
    layers: dict[str, Layer] = {name: _OFF for name in LAYERS}
    skipped = Layer("skipped", "", "")

    def finish(hide_from: int = len(LAYERS)) -> Readiness:   # layers from LAYERS[hide_from] on: skipped (or off)
        for name in LAYERS[hide_from:]:
            used = use_tools if name.startswith("tools") else use_agent if name.startswith("agent") else True
            layers[name] = skipped if used else _OFF
        return Readiness(**layers, tools_tag=tools_tag, agent_tag=agent_tag, actions=tuple(actions))

    if not shutil.which("docker"):
        layers["cli"] = Layer("fail", DAEMON, "the docker CLI is not on this PATH (docker is not installed)")
        return finish(1)
    layers["cli"] = Layer("ok")
    run = run_tools if use_tools else run_agent
    if not _daemon_up(run):
        if start and s["autostart"]:
            actions.append("started Docker")
            failed = _start(s, runner or _run, run)   # launchctl / open / systemctl are not docker commands
        else:
            why = "docker.autostart is false" if not s["autostart"] else "this check does not start it"
            failed = Layer("fail", DAEMON, f"{_DAEMON_DOWN}; not started ({why})")
        if failed is not None:
            layers["daemon"] = failed
            return finish(2)
    layers["daemon"] = Layer("ok")
    auto = start and s["auto_selftest"] == "on-change"
    if use_tools:
        image_id = _image_id(run_tools, tools_tag)
        if not image_id:
            layers["tools_image"] = Layer("fail", IMAGE, f"the tools image {tools_tag} is not built — run "
                                                         "`python -m agentteams.vulnscan tools-image build`")
            layers["tools_selftest"] = skipped
        else:
            layers["tools_image"] = Layer("ok", "", tools_tag)
            passing, same = _tools_record(image_id)
            if passing:
                layers["tools_selftest"] = Layer("ok")
            elif same or not auto:   # this image already failed its self-test, or repairs are not allowed
                layers["tools_selftest"] = Layer("fail", SELFTEST, _tools_selftest_missing(tools_tag)
                                                 + (" (its last self-test failed)" if same else ""))
            else:
                actions.append(f"ran tools-selftest for {tools_tag}")
                ok, err = _tools_selftest_run(cfg)
                layers["tools_selftest"] = (Layer("ok", "", "self-test re-run: passed") if ok else
                                            Layer("fail", SELFTEST, f"tools-selftest failed for {tools_tag}"
                                                  + (f": {err}" if err else "")))
    if use_agent:   # a passing selftest_ok also proves the image exists (it compares the record with its id)
        ok, why = agent_run.selftest_ok(cfg)
        image_id = "" if ok else _image_id(run_agent, agent_tag)
        if ok:
            layers["agent_image"], layers["agent_selftest"] = Layer("ok", "", agent_tag), Layer("ok")
        elif not image_id:
            layers["agent_image"] = Layer("fail", IMAGE, f"the agent image {agent_tag} is not built — run "
                                                         "`python -m agentteams.vulnscan agent-image build`")
            layers["agent_selftest"] = skipped
        else:
            layers["agent_image"] = Layer("ok", "", agent_tag)
            if _agent_record_image() == image_id or not auto:   # bounded: once per image change
                layers["agent_selftest"] = Layer("fail", SELFTEST, why)
            else:
                actions.append(f"ran agent-selftest for {agent_tag}")
                ok, err = _agent_selftest_run(cfg)
                layers["agent_selftest"] = (Layer("ok", "", "self-test re-run: passed") if ok else
                                            Layer("fail", SELFTEST, f"agent-selftest failed for {agent_tag}"
                                                  + (f": {err}" if err else "")))
    return finish()


# ---------------------------------------------------------------- the `ready` command

def markdown(rec: Readiness) -> str:
    """Return one line per layer (``ready``'s text output).

    Args:
        rec: The record.

    Returns:
        ``READY`` or ``DEGRADED <code>`` first, then ``<layer>: <state> [code] message`` lines.
    """
    head = "READY" if rec.ok else f"DEGRADED {rec.problem().code}"
    lines = [head, *(f"  started/ran: {a}" for a in rec.actions)]
    for name in LAYERS:
        layer = getattr(rec, name)
        lines.append(f"  {name}: {layer.state}" + (f" [{layer.code}]" if layer.code else "")
                     + (f" {layer.message}" if layer.message else ""))
    return "\n".join(lines)


def register(sub: Any) -> None:
    """Add ``ready [--ensure] [--json]`` to the vulnscan parser.

    Args:
        sub: The parser's subparsers action.
    """
    sp = sub.add_parser("ready", help="check Docker, the images and their self-tests (exit 0 ready, 3 degraded)")
    sp.add_argument("--ensure", action="store_true",
                    help="repair what the config allows: start Docker (docker.autostart), re-run a stale self-test")
    sp.add_argument("--json", action="store_true", help="print {layer: {state, code, message}}")


def dispatch(args: argparse.Namespace, load_cfg: Callable[[], dict[str, Any]]) -> int:
    """Run ``ready``: 0 READY, 3 degraded, 2 config error.

    With ``--ensure`` the repairs run under the daily run lock; while a daily run holds it, only the read-only
    check runs (that run's own check covers it).

    Args:
        args: Parsed arguments.
        load_cfg: Reads the daily config (raises on a malformed one).

    Returns:
        The exit code.
    """
    import fcntl

    from agentteams.vulnscan import daily, paths

    try:
        cfg = load_cfg() if paths.config_file().exists() else dict(daily.DEFAULT_CONFIG)
    except (OSError, ValueError) as exc:
        print(f"config unusable: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    if not args.ensure:
        rec = ensure(cfg, start=False)
    else:
        paths.state_dir().mkdir(parents=True, exist_ok=True)
        with open(daily.run_lock_path(), "a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("vulnscan ready: a daily run is in progress; checking only", file=sys.stderr)
                rec = ensure(cfg, start=False)
            else:
                try:
                    rec = ensure(cfg, start=True)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps(rec.to_dict(), indent=2) if args.json else markdown(rec))
    return EXIT_READY if rec.ok else EXIT_DEGRADED
