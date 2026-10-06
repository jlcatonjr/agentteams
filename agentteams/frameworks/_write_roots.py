"""Write-root validation: the one chokepoint between brief data and an OS write boundary.

A project brief's ``workspace_write_roots`` / ``coordination_write_roots`` (and a signed grant's
targets) become the sandbox's writable set: Claude ``allowWrite``, the goose Seatbelt
``(allow file-write* …)`` and the goose Linux runner's ``--writable`` flags. The brief is
agent-writable C-4 data, so a sandboxed agent that edits it could widen its NEXT sandbox (or, via
the goose runner script, inject shell into the operator's unsandboxed shell). Every emitter calls
:func:`validate_write_roots` on the FINAL union of roots, so a refused shape never reaches any
boundary (follow-up #2, ``brief-json-privilege-tamper``, 2026-09-30; @security CLEAR_WITH_CONDITIONS).

Two layers:

* **Hard bans** (:func:`validate_write_roots`): shapes no operator needs and an attacker wants:
  ``/``, ``~``, ``$HOME`` and its ancestors, the signing-key dir (at, inside, or an ancestor),
  home persistence and credential paths, ``~user``, the project's own control plane (``.git``,
  ``.claude``, ``.goose``, ``.codex``, ``.github/agents``, ``.github/hooks``, ``sandbox``,
  ``.agentteams``), an ancestor of the project, and any shell / SBPL / glob metacharacter.
* **External roots** (:func:`is_external_root`): a root outside the project (absolute, ``~/…`` or
  ``../…``) is allowed by this module but must be ACCEPTED by the operator at generation
  (``--accept-write-root``; :func:`unaccepted_external_roots`) unless the protected live baseline
  already holds it.

HONEST LIMITS: the home denylist is NOT exhaustive; operator acceptance of every external root
is the real control. The checks are lexical, plus a best-effort ``realpath`` of a root that exists at
generation (a symlink created later is resolved by the OS at session time, not here). A
sibling-repo root still exposes THAT repo's control plane (inherent to coordination). When no
project root is known (a direct library call), the project-ancestor and project-equality checks
are skipped; every CLI and multi_sync path sets :data:`PROJECT_ROOT_KEY`.
"""

from __future__ import annotations

import os
import posixpath
import sys
from pathlib import Path
from typing import Any, Iterable

#: Transient manifest key carrying the project root. Honoured ONLY as a :class:`pathlib.Path`: a
#: JSON brief or manifest can only yield a string, so an input-supplied value is ignored (the
#: same pattern as ``SIBLING_DENY_DIRS_KEY``). Never persisted.
PROJECT_ROOT_KEY = "_project_root"

#: Transient manifest key carrying ``((root, source), …)`` provenance for the widening notice.
#: Honoured only as a ``tuple`` (JSON yields lists). Never persisted.
PROVENANCE_KEY = "_write_root_provenance"

#: Brief/manifest keys that must never carry an acceptance (acceptance is operator argv only).
FORBIDDEN_ACCEPTANCE_KEYS: tuple[str, ...] = ("accept_write_root", "accept_write_roots")

_SIGNING_KEY_DIR = "~/.config/agentteams/keys"  # == _sandbox_emit.SIGNING_KEY_DIR (test-locked)

#: Characters that can break out of a JSON/SBPL/bash string, expand, or glob. Refused outright.
_FORBIDDEN_CHARS = frozenset("\"'$`\\;&|<>*?[]{}()!#")

#: Project-relative control-plane dirs a write root may not be at or inside.
_PROJECT_PROTECTED: tuple[str, ...] = (
    ".git", ".claude", ".goose", ".codex", ".github/agents", ".github/hooks", "sandbox", ".agentteams",
)

#: Home persistence and credential paths (``~``-relative) a write root may not be at or inside.
_HOME_PROTECTED: tuple[str, ...] = (
    "~/.ssh", "~/.gnupg", "~/.aws", "~/.bashrc", "~/.bash_profile", "~/.bash_login", "~/.profile",
    "~/.zshrc", "~/.zprofile", "~/.zshenv", "~/.gitconfig", "~/.local/bin", "~/.config/systemd",
    "~/.config/autostart", "~/.config/git", "~/.config/gh", "~/.config/goose", "~/.claude",
    "~/.config/fish", "~/.config/gcloud", "~/.kube", "~/.docker", "~/.azure", "~/.netrc",
    "~/.local/share/systemd", "~/.pki", "~/.password-store",
    _SIGNING_KEY_DIR,
)


def path_char_problem(path: str) -> str | None:
    """Return why ``path`` cannot be emitted into a script/profile, or ``None`` when it can.

    Shared by write roots and the goose runner's ``protected_read_paths``.

    Args:
        path: A path string taken from brief or manifest data.

    Returns:
        A short reason, or ``None``.
    """
    if not isinstance(path, str) or not path:
        return "empty or not a string"
    if path != path.strip():
        return "leading or trailing whitespace"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        return "a control character"
    bad = sorted({c for c in path if c in _FORBIDDEN_CHARS})
    if bad:
        return f"forbidden character(s) {''.join(bad)!r} (shell/SBPL/glob metacharacters)"
    return None


def _fold(path: str, platform: str) -> str:
    return path.lower() if platform == "darwin" else path


def _at_or_inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def control_plane_of(rel: str, *, platform: str | None = None) -> str | None:
    """Return the project control-plane entry a project-relative path is at or inside, else ``None``.

    The public form of the ``_PROJECT_PROTECTED`` check, for other modules (``proposals``) that must
    refuse the same paths.

    Args:
        rel: A project-relative POSIX path.
        platform: Override for ``sys.platform`` (tests); compared case-insensitively on darwin.

    Returns:
        The matching entry (e.g. ``".claude"``), or ``None``.

    Raises:
        Nothing.
    """
    plat = sys.platform if platform is None else platform
    norm = _fold(posixpath.normpath(rel), plat)
    return next((p for p in _PROJECT_PROTECTED if _at_or_inside(norm, _fold(p, plat))), None)


def project_root_of(manifest: dict[str, Any] | None) -> str | None:
    """Return the CLI-set project root (``None`` when absent or input-supplied, i.e. not a Path)."""
    value = (manifest or {}).get(PROJECT_ROOT_KEY)
    return os.path.normpath(str(value)) if isinstance(value, Path) else None


def _absolute(root: str, project_root: str | None) -> str | None:
    """Return the absolute normalized form of ``root`` (``None``: relative with no project root)."""
    if root.startswith("~/"):
        return os.path.normpath(os.path.expanduser(root))
    if os.path.isabs(root):
        return os.path.normpath(root)
    return os.path.normpath(os.path.join(project_root, root)) if project_root else None


def root_problem(root: str, project_root: str | None = None, *, platform: str | None = None) -> str | None:
    """Return why ``root`` may not be a write root, or ``None`` when it may.

    Args:
        root: One write root as written in the brief, coordination list or grant.
        project_root: The absolute project root, when known.
        platform: Override for ``sys.platform`` (tests). darwin compares case-insensitively.

    Returns:
        A short reason, or ``None``.
    """
    plat = sys.platform if platform is None else platform
    reason = path_char_problem(root)
    if reason:
        return reason
    if root.startswith("~") and root != "~" and not root.startswith("~/"):
        return "a ~user path"
    if root.rstrip("/") in ("~", "") or root.startswith("//"):
        return "the home directory or filesystem root"
    home = os.path.normpath(os.path.expanduser("~"))
    relative = not (root.startswith("~") or os.path.isabs(root))
    if relative:
        rel = posixpath.normpath(root)
        if rel != "." and not rel.startswith("../") and rel != "..":
            for p in _PROJECT_PROTECTED:
                if _at_or_inside(_fold(rel, plat), _fold(p, plat)):
                    return f"inside the project control plane ({p})"
    candidates = [_absolute(root, project_root)]
    if candidates[0] and os.path.lexists(candidates[0]):
        real = os.path.realpath(candidates[0])
        if real != candidates[0]:
            candidates.append(real)  # best-effort: a symlinked root is judged by its target too
    for absolute in filter(None, candidates):
        a = _fold(absolute, plat)
        if a == "/":
            return "the filesystem root"
        if _at_or_inside(_fold(home, plat), a):
            return "the home directory or an ancestor of it"
        for p in _HOME_PROTECTED:
            protected = _fold(os.path.normpath(os.path.expanduser(p)), plat)
            if _at_or_inside(a, protected) or _at_or_inside(protected, a):  # at, inside or above
                return f"at, inside or above a protected home path ({p})"
        if project_root:
            proj = _fold(project_root, plat)
            if a != proj and _at_or_inside(proj, a):
                return "an ancestor of the project"
            if a != proj and _at_or_inside(a, proj):
                inner = a[len(proj) + 1:]
                for p in _PROJECT_PROTECTED:
                    if _at_or_inside(inner, _fold(p, plat)):
                        return f"inside the project control plane ({p})"
    return None


def validate_write_roots(
    roots: Iterable[str] | None,
    manifest: dict[str, Any] | None = None,
    *,
    project_root: str | None = None,
    platform: str | None = None,
) -> list[str]:
    """Refuse (fail-closed) every write root that matches a hard ban.

    Args:
        roots: The final union of write roots (``None``/empty = the default ``["."]``).
        manifest: The team manifest; only its :data:`PROJECT_ROOT_KEY` (a ``Path``) is read.
        project_root: An explicit absolute project root (wins over the manifest's).
        platform: Override for ``sys.platform`` (tests).

    Returns:
        The roots, unchanged, as a list.

    Raises:
        ValueError: One or more roots are refused; the message names each with its reason.
    """
    checked = list(roots) if roots else ["."]
    project_root = os.path.normpath(project_root) if project_root else project_root_of(manifest)
    problems = [(r, root_problem(r, project_root, platform=platform)) for r in checked]
    refused = [f"{r!r}: {why}" for r, why in problems if why]
    if refused:
        raise ValueError(
            "refused sandbox write root(s) (brief workspace_write_roots / coordination_write_roots "
            "or a grant; brief content is agent-writable data): " + "; ".join(refused)
            + ". Name a narrower directory (an external one needs --accept-write-root)."
        )
    return checked


def is_external_root(root: str, project_root: str | None) -> bool:
    """Return True when ``root`` lies outside the project (absolute, ``~/…`` or ``../…``).

    Args:
        root: A write root that passed :func:`validate_write_roots`.
        project_root: The absolute project root, when known.

    Returns:
        True for an external root. An absolute root equal to or inside the project is internal.
    """
    if root.startswith("~"):
        return True
    if os.path.isabs(root) and not project_root:
        return True
    rel = posixpath.normpath(root)
    if not os.path.isabs(root) and (rel == ".." or rel.startswith("../")):
        return True
    if not project_root:
        return False
    proj = os.path.normpath(project_root)
    a = _absolute(root, proj)
    if not _at_or_inside(a, proj):
        return True
    # A root that is lexically inside the project but a symlink (or under one) to a place outside
    # it is external: classify by its generation-time target (@security impl review #1).
    if os.path.lexists(a):
        real, real_proj = os.path.realpath(a), os.path.realpath(proj)
        if not _at_or_inside(real, real_proj):
            return True
    return False


def normalize_root(root: str) -> str:
    """Return the comparison form of a root: ``~/`` expanded (a shell-expanded
    ``--accept-write-root ~/x`` arrives as ``$HOME/x``), normalized, no trailing ``/``."""
    if root.startswith("~/"):
        return os.path.normpath(os.path.expanduser(root))
    return posixpath.normpath(root)


def unaccepted_external_roots(
    roots: Iterable[str],
    *,
    project_root: str | None,
    baseline: Iterable[str],
    accepted: Iterable[str],
    signed: Iterable[str] = (),
) -> list[str]:
    """Return the external roots that are neither in the baseline, accepted, nor signed.

    Args:
        roots: The final write roots.
        project_root: The absolute project root.
        baseline: Roots the operator already accepted, read ONLY from a file the active arm
            protects (the live ``.claude/settings.json``); empty otherwise.
        accepted: ``--accept-write-root`` values from the operator's own argv.
        signed: Roots contributed by Ed25519-verified grants (signed; exempt from acceptance,
            never from the hard bans).

    Returns:
        The roots that need ``--accept-write-root``, in order.
    """
    ok = {normalize_root(r) for r in (*baseline, *accepted, *signed) if isinstance(r, str)}
    return [r for r in roots if is_external_root(r, project_root) and normalize_root(r) not in ok]


def provenance_of(manifest: dict[str, Any] | None) -> dict[str, str]:
    """Return ``{root: source}`` from the transient provenance key (input-supplied values ignored)."""
    value = (manifest or {}).get(PROVENANCE_KEY)
    if not isinstance(value, tuple):
        return {}
    return {r: s for r, s in value if isinstance(r, str) and isinstance(s, str)}
