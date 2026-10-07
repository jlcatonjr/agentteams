"""git_exec.py — the shared git core behind agentteams' private ``_git`` helpers (CH-08, 2026-10-07).

Six modules kept their own ``_git`` helper or a copy of the hardening below (multi_sync, fleet, source_provenance,
operator_signing, signer_location, proposals; integrity's manifest check too). Each keeps its own return shape (a
tuple, a ``CompletedProcess``, ``str | None``, ``bytes``), but they now build and run the command here, so the
hardening is written once. Other one-off git calls (git_hooks, shrink_allow, branch_inventory, output_target,
session_scan, redteam) are out of this consolidation's scope.

**Read-only hardening.** A read-only git command run in a repository an agent can write should not let that
repository's own config run code: ``core.fsmonitor`` is executed by ``status``/``ls-files``, and a hooks path can
point anywhere (@security, PR-D C1). ``hardened=True`` adds command-line ``-c`` overrides, which beat the repo's
config, and an environment that takes no optional locks and never prompts. It closes those two paths, not
every one: a configured filter driver (``filter.<name>.clean``) can still run during ``status`` on a racily-clean
file. Commands that commit (the fleet snapshot) must not use it, since commit hooks are part of their contract.

Integrity-pinned: the pinned ``proposals.py``, ``operator_signing.py``, ``signer_location.py`` and
``integrity.py`` import it. Stdlib only.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Command-line config that overrides a repository's own: no fsmonitor, no hooks, no untracked cache.
HARDENING_CONFIG: tuple[str, ...] = (
    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.untrackedCache=false",
)
#: :data:`HARDENING_CONFIG` without the ``core.hooksPath`` override (see ``override_hooks`` on :func:`git_argv`).
HARDENING_CONFIG_KEEP_HOOKS: tuple[str, ...] = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false")
#: Environment for a hardened command: no optional locks (a read never blocks a writer), never prompt.
HARDENED_ENV: dict[str, str] = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


def git_argv(cwd: Path | None, *args: str, hardened: bool = False, override_hooks: bool = True) -> list[str]:
    """Build a git argv.

    Args:
        cwd: The repository, passed as ``-C``; ``None`` to rely on the process working directory.
        *args: The git subcommand and its arguments.
        hardened: Add :data:`HARDENING_CONFIG`.
        override_hooks: With *hardened*, also override ``core.hooksPath``. Pass False when the command must see
            the repository's real hooks path (``rev-parse --git-path hooks``, which the runner's snapshot watches
            for a planted hook); read-only commands run no hooks, so nothing is lost.

    Returns:
        The argv list.

    Raises:
        Nothing.
    """
    config: tuple[str, ...] = ()
    if hardened:
        config = HARDENING_CONFIG if override_hooks else HARDENING_CONFIG_KEEP_HOOKS
    return ["git", *config, *(("-C", str(cwd)) if cwd is not None else ()), *args]


def run_git(cwd: Path | None, *args: str, hardened: bool = False, env: dict[str, str] | None = None,
            timeout: float | None = None, text: bool = True, devnull_stdin: bool = False,
            run_cwd: Path | None = None, override_hooks: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the completed process (never raises on a non-zero exit).

    Args:
        cwd: The repository, passed as ``-C`` (``None`` to omit it).
        *args: The git subcommand and its arguments.
        hardened: Add :data:`HARDENING_CONFIG`, and :data:`HARDENED_ENV` over *env* (or the process
            environment when *env* is ``None``).
        env: The environment; ``None`` inherits the process environment.
        timeout: Seconds before the command is killed.
        text: Decode stdout/stderr as text.
        devnull_stdin: Give the command ``/dev/null`` as stdin.
        run_cwd: The process working directory (for callers that pass ``cwd=None``).
        override_hooks: See :func:`git_argv`.

    Returns:
        The completed process.

    Raises:
        OSError: When git can't be started.
        subprocess.SubprocessError: On a timeout.
        ValueError: When *text* output can't be decoded.
    """
    if hardened:
        env = {**(os.environ if env is None else env), **HARDENED_ENV}
    return subprocess.run(git_argv(cwd, *args, hardened=hardened, override_hooks=override_hooks),
                          capture_output=True, text=text, env=env,
                          timeout=timeout, check=False, cwd=run_cwd,
                          stdin=subprocess.DEVNULL if devnull_stdin else None)
