"""The executor's leaf definitions: its error type, git identity and the hardened git / gh environments.

Split from executor.py (CH-07) so that modules the executor imports (:mod:`.executor_pr`, :mod:`.fix`) can use
them without importing the executor back. :mod:`.executor` re-exports every name here, so ``executor.ExecError``,
``executor.gh_env`` and the rest keep working for existing callers and tests. Imports only :mod:`.paths`.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentteams.vulnscan import paths

GIT_IDENTITY = ("agentteams-vulnscan", "vulnscan@agentteams.invalid")


class ExecError(RuntimeError):
    """An action was refused or failed; the message goes to the ledger and the review."""


def clean_git_env(global_config: str) -> dict[str, str]:
    """Return the environment without ``GIT_*`` variables, system config off and ``global_config`` as global config.

    Args:
        global_config: The file git reads as its global config.

    Returns:
        The environment.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # GIT_LFS_SKIP_SMUDGE: git-lfs (fix.LFS_FILTER, plan C2) writes pointer files on checkout and downloads nothing
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": global_config, "GIT_TERMINAL_PROMPT": "0",
                "GIT_LFS_SKIP_SMUDGE": "1"})
    return env


def git_env() -> dict[str, str]:
    """Return the executor's git environment: :func:`clean_git_env` with the executor's fixed identity.

    Returns:
        The environment.
    """
    gcfg = paths.state_dir() / "executor.gitconfig"
    gcfg.parent.mkdir(parents=True, exist_ok=True)
    gcfg.write_text(f"[user]\n\tname = {GIT_IDENTITY[0]}\n\temail = {GIT_IDENTITY[1]}\n", encoding="utf-8")
    env = clean_git_env(str(gcfg))
    # repository config cannot rename the executor's commits (its branches are recognised by this identity)
    env.update({"GIT_AUTHOR_NAME": GIT_IDENTITY[0], "GIT_AUTHOR_EMAIL": GIT_IDENTITY[1],
                "GIT_COMMITTER_NAME": GIT_IDENTITY[0], "GIT_COMMITTER_EMAIL": GIT_IDENTITY[1]})
    return env


def gh_env() -> dict[str, str]:
    """Return the environment for a read-only ``gh`` call made outside a repository operation.

    Inherited ``GIT_*`` variables are dropped, system and global git config are off, and ``gh`` never prompts.
    Shared by publish and notes (read-only API calls). The executor's own ``gh pr`` calls use
    :func:`gh_push_env`, which adds the hardened git environment.

    Returns:
        The environment.
    """
    return clean_git_env(os.devnull) | {"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}


def gh_push_env() -> dict[str, str]:
    """Return the hardened git environment for gh, with no global git config and no repository above its cwd.

    Public for ``executor_supersede`` and ``executor_pr``, which run ``gh pr`` under a grant.

    Returns:
        The environment.
    """
    return git_env() | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CEILING_DIRECTORIES": str(paths.state_dir())}


def gh_cwd() -> Path:
    """Return gh's working directory: empty, so gh (given ``--repo``) never reads or runs a checkout's config (W1).

    Public for the other modules that run ``gh`` (effectiveness, executor_supersede, executor_pr).

    Returns:
        The directory (created).
    """
    (d := paths.state_dir() / "gh-cwd").mkdir(parents=True, exist_ok=True)
    return d
