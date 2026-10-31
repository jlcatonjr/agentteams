"""The text a staged fix carries (its commit, its PR) and the ``gh pr create`` call (split from executor.py, CH-07).

``stage-fix`` commits ``security(deps): fix known-vulnerable dependency versions (<digest>)``. ``stage-major``
(semver-major upgrades, a grant must name it, Rule 15) commits ``security(deps)!: major upgrade of
known-vulnerable dependencies (<digest>)`` with a body listing each package ``old → new`` and a ``BREAKING
CHANGE`` footer, and its staged state records ``"major": true``. ``open-pr`` then titles that PR ``security(deps)!:
MAJOR upgrade ...``, says in the body that it may need code changes and that CI must cover it, and labels it
:data:`MAJOR_LABEL`. The label is created when missing; when it still cannot be applied, the PR is opened without
it: a missing label never fails the PR (only gh's own "label not found" error falls back, :func:`label_refused`).

The operator's auto-merge job merges a green ``vulnscan/*`` PR, majors included, so every PR body says it may be
merged automatically once every check, a test check included, passes: CI must cover the change.

Imports only :mod:`.executor_env` (a leaf) at load time; :mod:`.publish` (which imports the executor) lazily.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentteams.vulnscan.executor_env import ExecError, gh_cwd, gh_push_env

MAJOR_LABEL = "vulnscan-major"
_LABEL_COLOR = "B60205"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
#: gh's errors for a label the repository does not have (``could not add label: 'x' not found``), and nothing else.
_LABEL_ERROR = re.compile(r"could not add label|label[^\n]{0,80}\bnot found", re.I)


def sanitize(text: str, limit: int = 2000) -> str:
    """Return untrusted text quoted for a PR body: mentions and code fences neutralised, every line ``> ``-quoted.

    Args:
        text: The text (e.g. the agent's rationale).
        limit: Characters kept.

    Returns:
        The quoted text, or ``> (none)``.

    Raises:
        TypeError: ``text`` is not a string or ``limit`` not an int.
        ValueError: ``limit`` is negative.
    """
    if not isinstance(text, str) or not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("sanitize takes a string and an int limit")
    if limit < 0:
        raise ValueError("limit must be >= 0")
    text = text[:limit].replace("@", "@​").replace("```", "'''")
    return "\n".join("> " + ln for ln in text.splitlines()) or "> (none)"


def major_changes(changes: list[Any]) -> list[Any]:
    """Return the semver-major upgrades a fix took (``fix`` rows with ``major`` set, :class:`.fix.PinChange`).

    Args:
        changes: :attr:`.fix.FixResult.changes`.

    Returns:
        The major rows.
    """
    return [c for c in changes if c.action == "fix" and c.major]


def commit_args(digest: str, changes: list[Any], *, major: bool) -> list[str]:
    """Return the ``git commit`` message arguments (``-m`` pairs) for a staged fix.

    Args:
        digest: The change digest (the branch name's suffix).
        changes: :attr:`.fix.FixResult.changes`.
        major: The op is ``stage-major``.

    Returns:
        The arguments.

    Raises:
        TypeError: ``digest`` is not a string or ``changes`` not a list.
        ValueError: ``digest`` is empty or not plain hex/word characters.
    """
    if not isinstance(digest, str) or not isinstance(changes, list):
        raise TypeError("commit_args takes a string digest and a list of changes")
    if not re.fullmatch(r"\w{1,64}", digest):
        raise ValueError(f"malformed digest {digest[:80]!r}")
    if not major:
        return ["-m", f"security(deps): fix known-vulnerable dependency versions ({digest})",
                "-m", "Staged by the agentteams vulnscan executor (deterministic fix; lockfile/pin changes only)."]
    listed = "\n".join(_CONTROL.sub("?", f"- {c.name} {c.old} → {c.new} ({c.path})") for c in major_changes(changes))
    return ["-m", f"security(deps)!: major upgrade of known-vulnerable dependencies ({digest})",
            "-m", listed,
            "-m", "Staged by the agentteams vulnscan executor (stage-major: semver-major upgrades; dependency "
                  "manifests and lockfiles only).",
            "-m", "BREAKING CHANGE: major versions of the dependencies above; code changes may be needed."]


def title(branch: str, *, major: bool) -> str:
    """Return the PR title for a staged ``vulnscan/<date>-<digest>`` branch.

    Args:
        branch: The staged branch.
        major: The staged change is a ``stage-major`` one.

    Returns:
        The title.
    """
    digest = branch.rsplit("-", 1)[-1]
    if major:
        return f"security(deps)!: MAJOR upgrade of known-vulnerable dependencies ({digest})"
    return f"security(deps): fix known-vulnerable dependencies ({digest})"


def body(plan: str, rationale: str, *, major: bool, left: dict[str, list[int]] | None = None) -> str:
    """Return the PR body, passed through the publish scrubber (home paths, host name, private IPs).

    Args:
        plan: The staged fix plan (Markdown).
        rationale: The agent's rationale (untrusted; quoted by :func:`sanitize`).
        major: The staged change is a ``stage-major`` one.
        left: ``{package: [PR numbers]}`` the change left to other open PRs (D1), listed as "left to #N".

    Returns:
        The body.
    """
    from agentteams.vulnscan import publish   # lazy: publish imports the executor

    head = ("**MAJOR upgrade.** This PR moves dependencies to a new semver-major version to fix known "
            "vulnerabilities. Major versions can break APIs, so the code may need changes; CI must cover the "
            "affected code paths before this is merged.\n\n") if major else ""
    text = (f"{head}Automated security fix staged by the agentteams **vulnscan executor** "
            f"(deterministic; dependency {'manifests' if major else 'pins'} and lockfiles only).\n\n{plan}\n\n"
            f"{_left_section(left)}"
            f"### Agent rationale (untrusted text, quoted)\n\n{sanitize(rationale)}\n\n"
            "Tests run in CI. This PR may be merged automatically once every check, a test check included, "
            "passes, so CI must cover the change.\n")
    return publish.rewrite_paths(text)


def _left_section(left: dict[str, list[int]] | None) -> str:
    """The PR body's list of packages left to other open PRs ("left to #227"), or ""."""
    rows = [f"- `{_CONTROL.sub('?', str(name)).replace('`', '?')[:100]}`: left to "
            + ", ".join(f"#{n}" for n in nums if isinstance(n, int)) for name, nums in sorted((left or {}).items())]
    return ("### Left to other open PRs (not duplicated)\n\n" + "\n".join(rows) + "\n\n") if rows else ""


def create(target_repo: str, base_branch: str, branch: str, body_file: Path, *, major: bool,
           open_url: Callable[[], str]) -> str:
    """Open the PR with ``gh pr create`` (hardened gh environment, empty cwd) and return its URL.

    For a major change the :data:`MAJOR_LABEL` label is created first if missing (its failure is ignored: the
    label may exist already) and applied. When ``gh pr create`` then fails over the label, an open PR for the
    branch is claimed (``open_url``) or the PR is created again without the label.

    Args:
        target_repo: ``HOST/OWNER/REPO``.
        base_branch: The PR base.
        branch: The pushed head branch.
        body_file: The PR body file.
        major: The staged change is a ``stage-major`` one.
        open_url: Returns the URL of an open PR for ``branch`` in ``target_repo``, or "".

    Returns:
        The PR URL ("" when gh printed none).

    Raises:
        ExecError: ``gh pr create`` failed.
    """
    def gh(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *argv], cwd=gh_cwd(), capture_output=True, text=True, env=gh_push_env(),
                              timeout=300, check=False)

    argv = ["pr", "create", "--repo", target_repo, "--base", base_branch, "--head", branch,
            "--title", title(branch, major=major), "--body-file", str(body_file)]
    if major:
        gh(["label", "create", MAJOR_LABEL, "--repo", target_repo, "--color", _LABEL_COLOR,
            "--description", "vulnscan: semver-major dependency upgrade (may need code changes)"])
        proc = gh([*argv, "--label", MAJOR_LABEL])
        if proc.returncode and label_refused(proc.stderr + proc.stdout):
            url = open_url()   # gh may have opened the PR before the label failed
            if url:
                return url
            proc = gh(argv)
    else:
        proc = gh(argv)
    if proc.returncode:
        raise ExecError(f"gh pr create failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def label_refused(output: str) -> bool:
    """Return whether ``gh pr create`` output is gh's error for a missing label (the one case retried without it).

    Args:
        output: gh's stderr and stdout.

    Returns:
        True for ``could not add label`` / ``... not found`` errors; False for any other failure (it stands).
    """
    return bool(_LABEL_ERROR.search(output))
