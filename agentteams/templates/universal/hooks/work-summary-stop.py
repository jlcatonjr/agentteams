#!/usr/bin/env python3
"""Work-summary Stop hook: run the work-summarizer headlessly, leaving no conversation behind.

Wire it as a Claude Code ``Stop`` hook (see ``settings.worksummary.example.json``). When the project has
work today that its daily summary does not yet reflect, it runs ``claude --print`` as
``@work-summarizer``. The run is started with ``--no-session-persistence``, so Claude Code saves
no transcript and no session list can show it. The full run is written instead to this hook's own
run archive, which is pruned after a retention period.

It is inert until an operator wires it, and it only ever acts on the one project it is installed
in. It prints nothing and never exits 2: a Stop hook must not block or steer the session.

Guards (each one closes an incident the operator-authored predecessor of this hook ran into):

* The Stop-event payload on stdin is drained and discarded, and the child's stdin is ``/dev/null``.
  ``claude --print`` merges piped stdin into its prompt, so an inherited payload would hand one
  session's closing report to the summarizer as if it were evidence.
* The child carries ``AGENTTEAMS_WS_HOOK_CHILD=1``. Its own Stop event runs this hook again, which
  exits at once on that marker.
* The hook detaches a worker and returns immediately, so the triggering session is never held up.
* One worker per project at a time (lock file holding the worker's pid; a lock is taken over only
  when it is older than the run timeout allows or its process is gone).
* The child is killed after ``AGENTTEAMS_WS_TIMEOUT`` seconds (default 420).
* Freshness gates decide skip versus run, so a still-open day does not launch a run on every Stop:
  a commit-bearing day is current when the summary is synthesized and already covers the latest
  commit; a planning-only day is current when the day's plan files are unchanged since the last
  successful run.
* The audit chain is requested once per day; later runs the same day are append-only.
* A run that fails is not retried before ``AGENTTEAMS_WS_RETRY_S`` seconds have passed, so a
  broken setup cannot launch a run on every Stop.
* The unattended run gets a narrow tool grant (read and search, the Agent tool, ``git log``,
  ``git show``, ``git cat-file``, ``ls``, and writes under ``workSummaries/`` only) and runs in
  ``dontAsk`` mode, where anything not granted is refused rather than asked about. Deny rules,
  which win over any allow rule, close the ways those git commands could write a file or read
  outside the repository, block web tools, and keep the run out of credential directories. No MCP
  server is loaded. The grant ADDS to the allow rules in the operator's own settings; it is not a
  cap on them, which is why the deny rules exist. The permission mode is fixed: no setting can
  loosen it.

Environment:

* ``CLAUDE_PROJECT_DIR`` — project root (falls back to the directory holding ``.claude``).
* ``AGENTTEAMS_WS_CLAUDE_BIN`` — the ``claude`` executable (default: found on ``PATH``).
* ``AGENTTEAMS_WS_RUN_DIR`` — where the log, state and run archive live (default:
  ``<claude config dir>/logs/agentteams-work-summary/<project>-<hash>``).
* ``AGENTTEAMS_WS_KEEP_DAYS`` — run-archive retention in days (default 14; ``0`` keeps no archive).
* ``AGENTTEAMS_WS_TIMEOUT`` — child timeout in seconds (default 420).
* ``AGENTTEAMS_WS_RETRY_S`` — wait after a failed run before trying again (default 1800).
* ``AGENTTEAMS_WS_ALLOWED_TOOLS`` — replaces the default tool grant (comma-separated rules).
* ``AGENTTEAMS_WS_DISABLE=1`` — do nothing.

An operator may put project-specific instructions in ``extra.md`` inside the run directory; its
text (first 4000 characters) is appended to the prompt. It is read from the operator's own
configuration directory, never from the repository: a file a branch or pull request can change
must not be able to instruct an unattended run. Nothing else is ever interpolated.

Usage: ``python3 work-summary-stop.py [--foreground] [--project DIR]``. ``--foreground`` runs the
worker in this process (for a scheduler or a test) instead of detaching.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHILD_MARKER = "AGENTTEAMS_WS_HOOK_CHILD"
SYNTHESIZED_MARKER = "## Plans Implemented"
#: Default grant for the unattended run. Narrow on purpose: a headless session told "you are
#: @work-summarizer" is NOT bound by that agent's ``tools:`` line, so this list is the limit.
ALLOWED_TOOLS = ",".join((
    "Read", "Grep", "Glob", "Agent",
    "Edit(workSummaries/**)", "Write(workSummaries/**)",
    "Bash(git log:*)", "Bash(git show:*)", "Bash(git cat-file:*)", "Bash(ls:*)", "Bash(date:*)",
))
#: Deny rules. They win over every allow rule, including ones the operator's settings carry.
#: The git options named here turn a read-only command into a file write (``--output``), a read
#: outside the repository (``--no-index``) or a program launch (``--ext-diff``, ``--textconv``).
DISALLOWED_TOOLS = ",".join((
    "Bash(*--output*)", "Bash(*--no-index*)", "Bash(*--ext-diff*)", "Bash(*--textconv*)",
    "Bash(*>*)", "WebFetch", "WebSearch",
    "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.gnupg/**)", "Read(~/.config/**)",
    "Read(~/.claude/**)", "Read(~/.netrc)", "Read(~/.git-credentials)", "Read(~/.docker/**)",
    "Read(~/.kube/**)", "Read(~/.npmrc)", "Read(~/.pypirc)", "Read(~/.*_history)",
    "Read(//etc/**)", "Read(**/.env*)",
    # Never the control plane, whatever the operator's own allow rules say.
    "Edit(.claude/**)", "Write(.claude/**)", "Edit(.github/**)", "Write(.github/**)",
))
PERMISSION_MODE = "dontAsk"
EXTRA_PROMPT_NAME = "extra.md"
_EXTRA_PROMPT_MAX = 4000
_PLAN_SUFFIXES = (".plan.md", ".steps.csv", ".handoff.md")
_LOCK_STALE_S = 900
_EXTRA_PATH = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


def drain_stdin(seconds: float = 2.0) -> None:
    """Read and discard whatever the harness piped in, without blocking past ``seconds``.

    Args:
        seconds: Upper bound on the time spent draining.

    Returns:
        None. The payload is deliberately not returned: it is inert data and no caller may use it.
    """
    try:
        if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
            return
        fd = sys.stdin.fileno()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready or not os.read(fd, 65536):
                return
    except (OSError, ValueError):
        return


def project_root(argv: list[str]) -> Path:
    """Resolve the project this hook acts on.

    Args:
        argv: Command-line arguments (``--project DIR`` wins over the environment).

    Returns:
        The absolute project root.
    """
    if "--project" in argv:
        i = argv.index("--project")
        if i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser().resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def run_dir(root: Path) -> Path:
    """Directory for this project's hook log, state, lock and run archive.

    Args:
        root: The project root.

    Returns:
        The directory path (not created here).
    """
    override = os.environ.get("AGENTTEAMS_WS_RUN_DIR")
    if override:
        return Path(override).expanduser()
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    return config / "logs" / "agentteams-work-summary" / f"{root.name}-{digest}"


def _log(rdir: Path, message: str) -> None:
    with contextlib.suppress(OSError):
        rdir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(rdir / "hook.log", "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")


def _load_state(rdir: Path) -> dict:
    try:
        data = json.loads((rdir / "state.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(rdir: Path, state: dict) -> None:
    with contextlib.suppress(OSError):
        tmp = rdir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, rdir / "state.json")


def commits_today(root: Path) -> tuple[int, str] | None:
    """Count today's commits and name the latest.

    Args:
        root: The project root.

    Returns:
        ``(count, latest short hash)``, or ``None`` when git cannot answer (not a repository).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--since=midnight", "--format=%h"],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    hashes = [h for h in out.stdout.split() if h]
    return len(hashes), (hashes[0] if hashes else "")


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def plan_digest(root: Path, midnight: float) -> tuple[str, int]:
    """Fingerprint the plan artifacts changed today.

    Args:
        root: The project root.
        midnight: Local midnight as a POSIX timestamp.

    Returns:
        ``(digest, file count)``; the digest is ``""`` when no plan file changed today. It covers
        each file's path, size and modification time, so any further edit changes it.
    """
    base = root / "tmp" / "by-week"
    rows: list[str] = []
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if not path.name.endswith(_PLAN_SUFFIXES):
                continue
            st = _stat(path)
            if st is not None and path.is_file() and st.st_mtime >= midnight:
                rows.append(f"{path.relative_to(root)}|{st.st_size}|{st.st_mtime_ns}")
    if not rows:
        return "", 0
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest(), len(rows)


def decide(root: Path, state: dict, today: str, midnight: float) -> dict:
    """Decide whether a summarizer run is due, and of which kind.

    Args:
        root: The project root.
        state: The persisted hook state (read only here).
        today: Today's date, ``YYYY-MM-DD``.
        midnight: Local midnight as a POSIX timestamp.

    Returns:
        ``{"skip": reason}`` when no run is due. Otherwise a dict with ``kind`` (``"commits"`` or
        ``"plan-only"``), ``mode`` (``"create"`` or ``"append"``), ``count``, ``latest`` and
        ``digest``, plus ``reason`` for the log.
    """
    summary = root / "workSummaries" / "daily" / f"{today}.md"
    try:
        text = summary.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    mode = "append" if text.strip() else "create"
    day = state.get(today) if isinstance(state.get(today), dict) else {}
    retry_s = _int_env("AGENTTEAMS_WS_RETRY_S", 1800)
    failed_at = day.get("failed_at")
    if isinstance(failed_at, (int, float)) and 0 <= time.time() - failed_at < retry_s:
        return {"skip": f"last run failed less than {retry_s}s ago"}
    digest, plan_files = plan_digest(root, midnight)
    git = commits_today(root)
    count, latest = git if git else (0, "")

    if count > 0:
        synthesized = SYNTHESIZED_MARKER in text
        if synthesized and latest and (latest in text or day.get("commit") == latest):
            return {"skip": f"summary synthesized and current (covers {latest})"}
        return {"kind": "commits", "mode": mode, "count": count, "latest": latest, "digest": digest,
                "reason": f"{count} commit(s) today, latest {latest}, mode={mode}"}
    if plan_files:
        if text.strip() and day.get("plan_digest") == digest:
            return {"skip": "plan files unchanged since the last successful run"}
        return {"kind": "plan-only", "mode": mode, "count": 0, "latest": "", "digest": digest,
                "reason": f"{plan_files} plan file(s) changed today, no commits, mode={mode}"}
    return {"skip": "no commits or plan-file activity today"}


def build_prompt(root: Path, decision: dict, today: str, audit_ran: bool, extra: str = "") -> str:
    """Compose the summarizer prompt from facts this hook observed itself.

    Args:
        root: The project root.
        decision: The run decision from :func:`decide`.
        today: Today's date, ``YYYY-MM-DD``.
        audit_ran: Whether this hook already requested the audit chain today.
        extra: Operator instructions from :func:`extra_prompt` (may be empty).

    Returns:
        The prompt text. It contains no session content and nothing read from the repository:
        only the path, the date, the write mode, a commit count read from git, and ``extra``.
    """
    name, mode = root.name, decision["mode"]
    head = (
        f"You are @work-summarizer for the {name} repository. Write today's daily work summary "
        f"({today}) to workSummaries/daily/{today}.md using {mode} mode, scoped to THIS repository "
        f"({root}) only. "
    )
    if audit_ran:
        audit = (
            "An audit pass (@adversarial + @conflict-auditor) was already requested today for this "
            f"repository. Do NOT re-run them; only bring today's summary up to date in {mode} mode."
        )
    else:
        audit = (
            "Run @adversarial and @conflict-auditor before finalizing, recording findings where "
            "your team's audit-output contract says (one file per day per type, never a new ad hoc "
            "file). This is the only audit pass this hook requests for this repository today."
        )
    contract = (
        f"A synthesized summary keeps exactly one '{SYNTHESIZED_MARKER}' section and names each "
        "commit it covers by short hash. If the file holds an unsynthesized incremental-capture "
        "section, fold it into the synthesized body, de-duplicating against commits already "
        "covered, then remove that section. Ensure every Required Summary Contract Field is "
        "present. "
    )
    if decision["kind"] == "commits":
        body = (
            "Gather evidence: run 'git log --oneline --since=midnight --stat' here, and read "
            "this repository's tmp/by-week/ plan artifacts for intent. " + contract + audit +
            " Then run Workflow D (Automatic Backfill Sweep) for THIS repository, within the "
            "limits your team's work-summary-backfill reference defines. The executed-work gate is "
            f"pre-satisfied: this hook counted {decision['count']} commit(s) today."
        )
    else:
        body = (
            "When this hook looked, git showed no commits since midnight; that may be stale. FIRST "
            "run 'git log --oneline --since=midnight' yourself and report the ACTUAL result; if "
            "commits exist, treat this as a commit-bearing day. Never state a commit count you did "
            "not just read from git. Otherwise use the *.plan.md and *.steps.csv files under "
            "tmp/by-week/ modified since midnight as primary evidence of planning, investigation "
            "and research work. Do NOT run Workflow D. " + contract + audit
        )
    return head + body + extra


def extra_prompt(rdir: Path) -> str:
    """Read the operator's optional extra instructions for this project.

    Args:
        rdir: The hook's run directory, which lives in the operator's configuration directory.

    Returns:
        A sentence to append to the prompt, or ``""``. The file is deliberately NOT looked for in
        the repository, where a branch or pull request could plant it.
    """
    try:
        text = (rdir / EXTRA_PROMPT_NAME).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return f" Additional instructions for this project: {text[:_EXTRA_PROMPT_MAX]}" if text else ""


def child_command(claude_bin: str, prompt: str) -> list[str]:
    """Build the headless invocation.

    Args:
        claude_bin: Path to the ``claude`` executable.
        prompt: The summarizer prompt.

    Returns:
        The argv list. ``--no-session-persistence`` is what keeps the run out of every session
        list; ``stream-json`` makes the run's own output a complete record for the archive;
        ``--strict-mcp-config`` with no MCP configuration loads no MCP server.
    """
    return [
        claude_bin, "--print", prompt,
        "--allowedTools", os.environ.get("AGENTTEAMS_WS_ALLOWED_TOOLS") or ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", PERMISSION_MODE,
    ]


def child_env() -> dict[str, str]:
    """Environment for the child: marked as a hook child, and not posing as an editor session.

    Returns:
        A copy of the environment with the recursion marker set and ``CLAUDE_CODE_ENTRYPOINT``
        removed (an inherited editor entrypoint makes a headless run look like an editor session).
    """
    env = dict(os.environ)
    env[CHILD_MARKER] = "1"
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    extra = [str(Path(p).expanduser()) for p in _EXTRA_PATH]
    env["PATH"] = os.pathsep.join([env.get("PATH", "")] + extra).strip(os.pathsep)
    return env


def _find_claude(env: dict[str, str]) -> str | None:
    explicit = os.environ.get("AGENTTEAMS_WS_CLAUDE_BIN")
    if explicit:
        return explicit if Path(explicit).expanduser().exists() else None
    return shutil.which("claude", path=env.get("PATH"))


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _lock_is_live(lock: Path, stale_s: int) -> bool:
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    if age >= stale_s:
        return False
    try:
        pid = int(lock.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # its worker died without releasing it
    except (OSError, ValueError):
        return True  # unreadable, or a live process we may not signal: leave it alone
    return True


def _create_exclusive(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return False
    os.write(fd, str(os.getpid()).encode("ascii"))
    os.close(fd)
    return True


def _acquire_lock(rdir: Path, stale_s: int) -> bool:
    lock = rdir / "lock"
    if _create_exclusive(lock):
        return True
    if _lock_is_live(lock, stale_s):
        return False
    # The lock looks dead. Only the holder of the claim file may clear it, and it looks again
    # first: of two workers that both judged the lock dead, the second finds either no claim to
    # win or, after the first has finished, a live lock. Neither can remove the other's lock.
    claim = rdir / "lock.claim"
    with contextlib.suppress(OSError):
        if time.time() - claim.stat().st_mtime > 60:
            claim.unlink()  # left by a worker that died mid-takeover
    if not _create_exclusive(claim):
        return False
    try:
        if _lock_is_live(lock, stale_s):
            return False
        with contextlib.suppress(OSError):
            lock.unlink()
        return _create_exclusive(lock)
    finally:
        with contextlib.suppress(OSError):
            claim.unlink()


def _release_lock(rdir: Path) -> None:
    lock = rdir / "lock"
    with contextlib.suppress(OSError, ValueError):
        if int(lock.read_text(encoding="ascii").strip()) == os.getpid():
            lock.unlink()


def _iso_date(name: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(name)
    except ValueError:
        return None


def prune_runs(rdir: Path, keep_days: int) -> None:
    """Delete run-archive day folders older than the retention period.

    Args:
        rdir: The hook's run directory.
        keep_days: Retention in days; ``0`` removes every archived run.

    Returns:
        None.
    """
    runs = rdir / "runs"
    if not runs.is_dir():
        return
    cutoff = _dt.date.today() - _dt.timedelta(days=keep_days)
    for day in runs.iterdir():
        date = _iso_date(day.name)
        if date is not None and date < cutoff and day.is_dir() and not day.is_symlink():
            shutil.rmtree(day, ignore_errors=True)


def work(root: Path) -> int:
    """Run at most one summarizer for ``root`` if one is due.

    Args:
        root: The project root.

    Returns:
        Always 0.
    """
    os.umask(0o077)  # the log and the run archive can hold repository content: owner-only
    rdir = run_dir(root)
    try:
        rdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    timeout = _int_env("AGENTTEAMS_WS_TIMEOUT", 420) or 420
    if not _acquire_lock(rdir, max(_LOCK_STALE_S, timeout + 60)):
        return 0
    try:
        now = _dt.datetime.now()
        today = now.strftime("%Y-%m-%d")
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        state = _load_state(rdir)
        decision = decide(root, state, today, midnight)
        if "skip" in decision:
            _log(rdir, f"skip: {decision['skip']}")
            return 0
        env = child_env()
        claude_bin = _find_claude(env)
        if not claude_bin:
            _log(rdir, "claude executable not found; set AGENTTEAMS_WS_CLAUDE_BIN")
            return 0
        day = state.get(today) if isinstance(state.get(today), dict) else {}
        prompt = build_prompt(root, decision, today, audit_ran=bool(day.get("audit")),
                              extra=extra_prompt(rdir))
        keep_days = _int_env("AGENTTEAMS_WS_KEEP_DAYS", 14)
        _log(rdir, f"run: {decision['reason']}")
        archive = None
        if keep_days > 0:
            archive = rdir / "runs" / today / f"{now.strftime('%H%M%S')}.jsonl"
            archive.parent.mkdir(parents=True, exist_ok=True)
        code: int | None = None
        try:
            with open(archive or os.devnull, "wb") as out, open(rdir / "hook.log", "ab") as err:
                code = subprocess.run(
                    child_command(claude_bin, prompt), cwd=root, env=env,
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout, check=False,
                ).returncode
        except subprocess.TimeoutExpired:
            _log(rdir, f"summarizer killed after {timeout}s")
        except OSError as exc:
            _log(rdir, f"summarizer could not start: {exc}")
        summary = root / "workSummaries" / "daily" / f"{today}.md"
        wrote = summary.is_file() and summary.stat().st_size > 0
        if code == 0 and wrote:
            # Recorded only after a run that exited cleanly AND left a summary on disk, so a failed
            # run is retried (after the back-off) instead of being mistaken for a current day.
            # The digest is taken again NOW: the run may itself have touched plan files, and a
            # digest from before it would make the next Stop look like new planning work.
            day = {"audit": True, "plan_digest": plan_digest(root, midnight)[0]}
            if decision["kind"] == "commits":
                day["commit"] = decision["latest"]
            _save_state(rdir, {today: day})
            _log(rdir, "done" + (f"; run archived at {archive}" if archive else ""))
        else:
            _save_state(rdir, {today: dict(day, failed_at=time.time())})
            _log(rdir, f"summarizer exit={code}, summary present={wrote}; retry after the back-off")
        prune_runs(rdir, keep_days)
        return 0
    finally:
        _release_lock(rdir)


def main(argv: list[str]) -> int:
    """Hook entry point.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        Always 0. (An unexpected error exits 1, which Claude Code reports without blocking.)
    """
    drain_stdin()
    if os.environ.get(CHILD_MARKER) == "1" or os.environ.get("AGENTTEAMS_WS_DISABLE") == "1":
        return 0
    root = project_root(argv)
    if "--foreground" in argv:
        return work(root)
    with contextlib.suppress(OSError):
        subprocess.Popen(  # noqa: S603 - fixed argv, our own file
            [sys.executable, str(Path(__file__).resolve()), "--foreground", "--project", str(root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
