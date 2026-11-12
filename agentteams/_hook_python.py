"""Which Python the generated pre-commit hook runs, and the marker it leaves when it cannot run (#447 a, c).

The hook used to test ``python3`` on PATH and, when that was older than 3.11 (macOS ``/usr/bin/python3`` is 3.9), print
one line and let the commit through. Nothing recorded the skip, so the API impact check was off for every commit made
from a GUI client or a shell without the venv. The hook now resolves an interpreter itself:

1. **Release-locked repository** (``toolchain.lock`` names an ``agentteams=`` pin): the locked installation,
   ``<root>/.venv/bin/python``, run with ``-I``. It must import ``agentteams`` on 3.11+, and ``.venv`` must be
   untracked, so a commit cannot plant it. When it is unusable the hook skips: it never falls back to the agentteams
   checkout, which would run unreleased code in a repository that pins a release.
2. **Otherwise:** the interpreter recorded when the hook was installed, then ``python3.14`` … ``python3.11``, then
   ``python3`` on PATH, with the agentteams checkout on ``PYTHONPATH``. A candidate must be 3.11+, and neither its path
   nor its ``readlink -f`` target (where ``readlink -f`` exists) may be inside the working tree.

A repository counts as release-locked the way :func:`agentteams._fleet_locks.read_release_lock` decides it at the
toplevel: an ``agentteams=`` or ``agentteams.tag=`` key, and, failing closed, a ``toolchain.lock`` that is not a readable
regular file or has a non-comment line without ``=``.

A skip appends a line to :data:`MARKER` in the git directory. ``python -m agentteams.api_impact status`` prints it and
exits non-zero until a hook run whose API impact step completes (exit 0, or 3 in strict mode) removes it.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

#: Skip marker, resolved with ``git rev-parse --git-path`` (so it lives in the git directory, never in the tree).
MARKER = "agentteams-api-impact-skipped"
#: PATH candidates after the recorded interpreter, newest first.
PATH_CANDIDATES = ("python3.14", "python3.13", "python3.12", "python3.11", "python3")


def recorded_interpreter(repo_root: Path) -> str:
    """The interpreter to record in the hook: the running one, when it is 3.11+ and outside the repository.

    Args:
        repo_root: The repository the hook is installed into.

    Returns:
        An absolute path, or ``""`` when the running interpreter does not qualify.

    Raises:
        Nothing.
    """
    if sys.version_info < (3, 11) or not sys.executable:
        return ""
    exe = Path(sys.executable).absolute()
    try:
        if exe.resolve().is_relative_to(repo_root.resolve()) or exe.is_relative_to(repo_root.resolve()):
            return ""
    except OSError:
        return ""
    return str(exe)


def checkout_path(agentteams_path: str, repo_root: Path) -> str:
    """The agentteams import path the hook may put on ``PYTHONPATH`` for ``repo_root``.

    A path strictly inside the repository (agentteams installed into the repository's own ``.venv``, say) would put
    files any writer of the working tree controls on ``sys.path``, so it is dropped: the hook then relies on its
    interpreter importing agentteams by itself. The repository root itself is kept, because that is the agentteams
    source repository running its own hook.

    Args:
        agentteams_path: The directory that makes ``import agentteams`` work.
        repo_root: The repository the hook is installed into.

    Returns:
        ``agentteams_path``, or ``""`` when it resolves strictly inside ``repo_root``.

    Raises:
        Nothing.
    """
    try:
        path, root = Path(agentteams_path).resolve(), repo_root.resolve()
    except OSError:
        return agentteams_path
    return "" if path != root and path.is_relative_to(root) else agentteams_path


def render_resolver(recorded: str, checkout: str) -> str:
    """Shell lines that set ``_at_py`` (empty when no usable interpreter), ``_at_why`` and define ``_at_run``.

    The lines run inside the hook's ``if [ -n "$_at_root" ]`` branch and are indented for it.

    Args:
        recorded: The interpreter recorded at install time (may be ``""``).
        checkout: The directory put on ``PYTHONPATH`` when the hook does not use a locked installation.

    Returns:
        The shell text.

    Raises:
        Nothing.
    """
    cands = " ".join(shlex.quote(c) for c in ((recorded,) if recorded else ()) + PATH_CANDIDATES)
    return (
        "        # Interpreter (agentteams._hook_python): the locked installation in a release-locked repository,\n"
        "        # else the recorded interpreter or python3.14..3.11/python3 on PATH; 3.11+, never inside the tree.\n"
        "        _at_py=\"\"; _at_mode=\"\"; _at_phys=\"$(cd \"$_at_root\" 2>/dev/null && pwd -P)\"\n"
        f"        _at_pp={shlex.quote(checkout)}\n"
        "        # Locked when toolchain.lock pins agentteams (agentteams= or agentteams.tag=), and, failing closed as\n"
        "        # agentteams._fleet_locks does, when it is not a readable regular file or has a line without '='.\n"
        "        _at_lock=\"$_at_root/toolchain.lock\"\n"
        "        if { [ -e \"$_at_lock\" ] || [ -L \"$_at_lock\" ]; } && { [ ! -f \"$_at_lock\" ] || [ ! -r \"$_at_lock\" ] || \\\n"
        "           grep -qE '^[[:space:]]*agentteams(\\.tag)?[[:space:]]*=' \"$_at_lock\" 2>/dev/null || \\\n"
        "           grep -vE '^[[:space:]]*(#|$)' \"$_at_lock\" 2>/dev/null | grep -qv '='; }; then\n"
        "            _at_why=\"toolchain.lock pins agentteams, but $_at_root/.venv/bin/python cannot import it on Python 3.11+ (the checkout is not used in a locked repository)\"\n"
        "            if [ -z \"$(git -C \"$_at_root\" ls-files -- .venv 2>/dev/null | head -n 1)\" ] && \\\n"
        "               \"$_at_root/.venv/bin/python\" -I -c 'import sys, agentteams; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then\n"
        "                _at_py=\"$_at_root/.venv/bin/python\"; _at_mode=locked\n"
        "            fi\n"
        "        else\n"
        "            _at_why=\"no Python 3.11+ outside the working tree (tried the recorded interpreter, python3.14..python3.11, python3)\"\n"
        f"            for _at_c in {cands}; do\n"
        "                _at_p=\"$(command -v \"$_at_c\" 2>/dev/null)\" || continue\n"
        "                case \"$_at_p\" in /*) ;; *) continue ;; esac\n"
        "                _at_r=\"$(readlink -f \"$_at_p\" 2>/dev/null)\" || _at_r=\"$_at_p\"\n"
        "                case \"$_at_p\" in \"$_at_root\"/*|\"$_at_phys\"/*) continue ;; esac\n"
        "                case \"$_at_r\" in \"$_at_root\"/*|\"$_at_phys\"/*) continue ;; esac\n"
        "                if \"$_at_p\" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then\n"
        "                    _at_py=\"$_at_p\"; _at_mode=checkout; break\n"
        "                fi\n"
        "            done\n"
        "        fi\n"
        "        # -I (locked) and -P (checkout) keep the working tree off sys.path for every step.\n"
        "        _at_run() {\n"
        "            if [ \"$_at_mode\" = locked ]; then \"$_at_py\" -I \"$@\"\n"
        "            elif [ -n \"$_at_pp\" ]; then PYTHONPATH=\"$_at_pp${PYTHONPATH:+:$PYTHONPATH}\" \"$_at_py\" -P \"$@\"\n"
        "            else \"$_at_py\" -P \"$@\"; fi\n"
        "        }\n"
        f"        _at_mark=\"$(git rev-parse --git-path {MARKER} 2>/dev/null)\"\n"
    )


def marker_path(repo: Path) -> Path | None:
    """The skip marker's path for ``repo`` (it need not exist).

    Args:
        repo: Repository root.

    Returns:
        The path, or ``None`` when git is missing or cannot resolve the git directory.

    Raises:
        Nothing.
    """
    try:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", MARKER],
                              capture_output=True, text=True, check=False)
    except OSError:
        return None
    rel = proc.stdout.strip()
    if proc.returncode != 0 or not rel:
        return None
    path = Path(rel)
    return path if path.is_absolute() else Path(repo) / path


def read_skips(repo: Path) -> list[str]:
    """The skip marker's lines (``<UTC time>\\t<reason>``), oldest first.

    Args:
        repo: Repository root.

    Returns:
        The lines; empty when there is no marker or it cannot be read.

    Raises:
        Nothing.
    """
    path = marker_path(repo)
    if path is None or not path.is_file():
        return []
    try:
        return [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return []
