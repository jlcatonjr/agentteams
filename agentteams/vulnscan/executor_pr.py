"""The text a staged fix carries (its commit, its PR) and the ``gh pr create`` call (split from executor.py, CH-07).

``stage-fix`` commits ``security(deps): fix known-vulnerable dependency versions (<digest>)``. ``stage-major``
(semver-major upgrades, a grant must name it, Rule 15) commits ``security(deps)!: major upgrade of
known-vulnerable dependencies (<digest>)`` with a body listing each package ``old → new`` and a ``BREAKING
CHANGE`` footer, and its staged state records ``"major": true``. ``open-pr`` then titles that PR ``security(deps)!:
MAJOR upgrade ...``, says in the body that it may need code changes and that CI must cover it, and labels it
:data:`MAJOR_LABEL`. The label is created when missing; when it still cannot be applied, the PR is opened without
it: a missing label never fails the PR (only gh's own "label not found" error falls back, :func:`label_refused`).

``stage-image`` (an end-of-life base image's ``FROM`` tag moved to a new runtime line, :mod:`.image_fix`; a grant
must name it) is a major change of its own kind: it commits and titles ``security(image)!: MAJOR upgrade of
end-of-life base images (<digest>)``, lists each ``image old → new (path)``, carries a ``BREAKING CHANGE`` footer,
says in the body that CI must build the image and run the tests in it, and gets the same :data:`MAJOR_LABEL`.

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


#: A staged change's mode: ``stage-fix``, ``stage-major`` or ``stage-image``.
MODES: tuple[str, ...] = ("fix", "major", "image")


def resolve_mode(major: bool = False, mode: str = "") -> str:
    """Return the one mode a staging call means, from the legacy ``major`` flag and/or ``mode``.

    Args:
        major: The legacy ``stage-major`` flag.
        mode: ``"fix"``, ``"major"``, ``"image"`` or ``""`` (then ``major`` decides).

    Returns:
        The mode.

    Raises:
        ValueError: ``mode`` is unknown, or contradicts ``major`` (``major=True`` with any mode but ``"major"``).
    """
    if mode and mode not in MODES:
        raise ValueError(f"unknown staging mode {mode!r}")
    if major and mode not in ("", "major"):
        raise ValueError(f"contradictory staging mode: major=True with mode={mode!r}")
    return mode or ("major" if major else "fix")


def major_changes(changes: list[Any]) -> list[Any]:
    """Return the semver-major upgrades a fix took (``fix`` rows with ``major`` set, :class:`.fix.PinChange`).

    Args:
        changes: :attr:`.fix.FixResult.changes`.

    Returns:
        The major rows.
    """
    return [c for c in changes if c.action == "fix" and c.major]


def commit_args(digest: str, changes: list[Any], *, major: bool = False, mode: str = "") -> list[str]:
    """Return the ``git commit`` message arguments (``-m`` pairs) for a staged fix.

    Args:
        digest: The change digest (the branch name's suffix).
        changes: :attr:`.fix.FixResult.changes`.
        major: The op is ``stage-major`` (legacy flag; :func:`resolve_mode`).
        mode: :data:`MODES` (``"image"``: the op is ``stage-image``).

    Returns:
        The arguments.

    Raises:
        TypeError: ``digest`` is not a string or ``changes`` not a list.
        ValueError: ``digest`` is empty or not plain hex/word characters, or the mode is unknown or contradictory.
    """
    if not isinstance(digest, str) or not isinstance(changes, list):
        raise TypeError("commit_args takes a string digest and a list of changes")
    if not re.fullmatch(r"\w{1,64}", digest):
        raise ValueError(f"malformed digest {digest[:80]!r}")
    mode = resolve_mode(major, mode)
    major = mode == "major"
    if mode == "image":
        listed = "\n".join(_CONTROL.sub("?", f"- {c.name} {c.old} → {c.new} ({c.path}, {c.note})")
                           for c in major_changes(changes))
        return ["-m", f"security(image)!: MAJOR upgrade of end-of-life base images ({digest})",
                "-m", listed,
                "-m", ("Staged by the agentteams vulnscan executor (stage-image: Dockerfile FROM tags only, each moved "
                       "to the newest supported release line of the same variant)."),
                "-m", ("BREAKING CHANGE: the runtime in these images moves to a new release line; code and "
                       "dependencies may need changes.")]
    if not major:
        return ["-m", f"security(deps): fix known-vulnerable dependency versions ({digest})",
                "-m", "Staged by the agentteams vulnscan executor (deterministic fix; lockfile/pin changes only)."]
    listed = "\n".join(_CONTROL.sub("?", f"- {c.name} {c.old} → {c.new} ({c.path})") for c in major_changes(changes))
    return ["-m", f"security(deps)!: major upgrade of known-vulnerable dependencies ({digest})",
            "-m", listed,
            "-m", "Staged by the agentteams vulnscan executor (stage-major: semver-major upgrades; dependency "
                  "manifests and lockfiles only).",
            "-m", "BREAKING CHANGE: major versions of the dependencies above; code changes may be needed."]


def title(branch: str, *, major: bool = False, mode: str = "") -> str:
    """Return the PR title for a staged ``vulnscan/<date>-<digest>`` branch.

    Args:
        branch: The staged branch.
        major: The staged change is a ``stage-major`` one (legacy flag; :func:`resolve_mode`).
        mode: :data:`MODES`.

    Returns:
        The title.

    Raises:
        ValueError: The mode is unknown or contradictory.
    """
    digest = branch.rsplit("-", 1)[-1]
    mode = resolve_mode(major, mode)
    major = mode == "major"
    if mode == "image":
        return f"security(image)!: MAJOR upgrade of end-of-life base images ({digest})"
    if major:
        return f"security(deps)!: MAJOR upgrade of known-vulnerable dependencies ({digest})"
    return f"security(deps): fix known-vulnerable dependencies ({digest})"


def body(plan: str, rationale: str, *, major: bool = False, left: dict[str, list[int]] | None = None,
         kept: list[str] | None = None, mode: str = "") -> str:
    """Return the PR body, passed through the publish scrubber (home paths, host name, private IPs).

    Args:
        plan: The staged fix plan (Markdown).
        rationale: The agent's rationale (untrusted; quoted by :func:`sanitize`).
        major: The staged change is a ``stage-major`` one.
        left: ``{package: [PR numbers]}`` the change left to other open PRs (D1), listed as "left to #N".
        kept: The staged result's ``kept_off_registry`` notes: lockfile entries resolved off the npm registry that
            the change kept unchanged. Each is listed, so the PR never reads as registry-only.
        mode: :data:`MODES` (``"image"``: a ``stage-image`` change; :func:`resolve_mode`).

    Returns:
        The body.

    Raises:
        ValueError: The mode is unknown or contradictory.
    """
    from agentteams.vulnscan import publish   # lazy: publish imports the executor

    mode = resolve_mode(major, mode)
    major = mode == "major"

    head = ("**MAJOR upgrade.** This PR moves dependencies to a new semver-major version to fix known "
            "vulnerabilities. Major versions can break APIs, so the code may need changes; CI must cover the "
            "affected code paths before this is merged.\n\n") if major else ""
    scope = f"dependency {'manifests' if major else 'pins'} and lockfiles only"
    if mode == "image":
        head = ("**MAJOR base image upgrade.** This PR moves end-of-life base images to the newest supported "
                "release line of the same variant. A new runtime line (for example Python 3.9 to 3.15) can remove "
                "modules, change behaviour and need new wheels, so the code and its dependencies may need changes; "
                "CI must build the image and run the tests in it before this is merged.\n\n")
        scope = "Dockerfile `FROM` tags only"
    text = (f"{head}Automated security fix staged by the agentteams **vulnscan executor** "
            f"(deterministic; {scope}).\n\n{plan}\n\n"
            f"{_kept_section(kept)}{_left_section(left)}"
            f"### Agent rationale (untrusted text, quoted)\n\n{sanitize(rationale)}\n\n"
            "Tests run in CI. This PR may be merged automatically once every check, a test check included, "
            "passes, so CI must cover the change.\n")
    return publish.rewrite_paths(text)


def _kept_section(kept: list[str] | None) -> str:
    """The PR body's list of off-registry lockfile entries kept unchanged from the base, or ""."""
    rows = [f"- `{_CONTROL.sub('?', note).replace('`', '?')[:400]}`" for note in (kept if isinstance(kept, list) else [])
            if isinstance(note, str)]
    return ("### Off-registry sources kept unchanged (not from the npm registry)\n\n"
            "The lockfile still installs these from outside the registry allowlist. The change left each entry "
            "byte for byte as on the base branch (same URL, integrity and dependencies).\n\n"
            + "\n".join(rows) + "\n\n") if rows else ""


def _left_section(left: dict[str, list[int]] | None) -> str:
    """The PR body's list of packages left to other open PRs ("left to #227"), or ""."""
    rows = [f"- `{_CONTROL.sub('?', str(name)).replace('`', '?')[:100]}`: left to "
            + ", ".join(f"#{n}" for n in nums if isinstance(n, int)) for name, nums in sorted((left or {}).items())]
    return ("### Left to other open PRs (not duplicated)\n\n" + "\n".join(rows) + "\n\n") if rows else ""


def create(target_repo: str, base_branch: str, branch: str, body_file: Path, *, major: bool = False,
           open_url: Callable[[], str], mode: str = "") -> str:
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
        mode: :data:`MODES` (``"image"``: its own title, labelled like a major change; :func:`resolve_mode`).

    Returns:
        The PR URL ("" when gh printed none).

    Raises:
        ExecError: ``gh pr create`` failed.
        ValueError: The mode is unknown or contradictory.
    """
    mode = resolve_mode(major, mode)

    def gh(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *argv], cwd=gh_cwd(), capture_output=True, text=True, env=gh_push_env(),
                              timeout=300, check=False)

    argv = ["pr", "create", "--repo", target_repo, "--base", base_branch, "--head", branch,
            "--title", title(branch, mode=mode), "--body-file", str(body_file)]
    if mode in ("major", "image"):
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
