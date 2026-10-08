"""gitread.py — ``agentteams-gitread``: read-only git history over MCP, for agents without a shell.

Tools: ``git_log``, ``git_show``, ``git_diff``, ``git_blame``, ``git_status``. ``git`` runs from argv lists (never a
shell) inside an isolation built from @security's review of the catalogue design (C2, C6), because
repository-local configuration can make even read commands run programs or reach the network:

* **Environment:** an allowlist only (``PATH`` and a C locale), plus ``GIT_CONFIG_NOSYSTEM=1``,
  ``GIT_CONFIG_GLOBAL=/dev/null``, ``GIT_CEILING_DIRECTORIES`` (discovery can't leave the project),
  ``GIT_NO_LAZY_FETCH=1`` and ``GIT_TERMINAL_PROMPT=0`` (no promisor fetch, the only network path),
  ``GIT_LITERAL_PATHSPECS=1``.
* **Config overrides on every call:** ``core.fsmonitor``, ``core.hooksPath``, ``core.pager``, ``diff.external``,
  ``core.untrackedCache``, ``log.showSignature``, ``protocol.allow=never``, ``status.submoduleSummary``; global
  ``--no-pager --no-replace-objects --no-optional-locks`` (status would otherwise write ``.git/index``) and an
  explicit ``--work-tree`` (``core.worktree`` is ignored).
* **Per command:** ``--no-ext-diff --no-textconv`` wherever they apply (blame applies textconv by default),
  ``--no-color``, ``--ignore-submodules=all``. Formats contain no ``%G`` (signature) placeholders.
* **Also per transport:** ``protocol.<name>.allow=never`` for file, git, ssh, http, https and ext (a repository's
  own per-protocol setting outranks ``protocol.allow``), and ``log.mailmap=false``, ``mailmap.file=``,
  ``mailmap.blob=`` (mailmap can name any file).
* **Refused outright:** a launch directory that is not a work-tree root; a ``.git``, ``commondir`` or object
  alternates that reach a repository outside the project (a registered linked worktree is allowed); and any
  ``filter.*`` in the repository's config (clean/process filters run on worktree
  reads). Blame and diff take explicit revisions, never the worktree.
* **Arguments:** refs match a strict pattern and can't start with ``-``; paths must be relative and inside the
  project; refs and paths go after ``--end-of-options`` (blame, which mis-parses it, relies on the
  leading-``-`` check) and paths after ``--``. A wall-clock timeout bounds every call; output is capped.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from agentteams.mcp_servers._stdio import ToolError

SERVER_NAME = "agentteams-gitread"
TIMEOUT_SECONDS = 15
MAX_LOG = 200
MAX_OUTPUT = 60 * 1024
_REF_RE = re.compile(r"[A-Za-z0-9._/@{}~^-]{1,200}")
_CONFIG = (
    "core.fsmonitor=false", "core.hooksPath=/dev/null", "core.pager=cat", "diff.external=",
    "core.untrackedCache=false", "log.showSignature=false", "protocol.allow=never", "status.submoduleSummary=false",
    # A repository's own protocol.<name>.allow outranks protocol.allow, and git < 2.44 ignores GIT_NO_LAZY_FETCH:
    # deny each transport by name, so a promisor lazy fetch can't reach the network or run ext::/ssh programs.
    *(f"protocol.{p}.allow=never" for p in ("file", "git", "ssh", "http", "https", "ext")),
    # mailmap.file / mailmap.blob can name any file; log and blame read them by default.
    "log.mailmap=false", "mailmap.file=", "mailmap.blob=",
)

TOOLS: list[dict[str, Any]] = [
    {"name": "git_log", "description": "Commit history (hash, author, date, subject). Read-only.",
     "inputSchema": {"type": "object", "properties": {
         "rev": {"type": "string"}, "n": {"type": "integer", "minimum": 1, "maximum": MAX_LOG},
         "path": {"type": "string"}}}},
    {"name": "git_show", "description": "One commit: metadata, stat and patch. Read-only.",
     "inputSchema": {"type": "object", "required": ["rev"], "properties": {
         "rev": {"type": "string"}, "path": {"type": "string"}}}},
    {"name": "git_diff", "description": "Diff between two revisions (never the worktree). Read-only.",
     "inputSchema": {"type": "object", "required": ["base", "head"], "properties": {
         "base": {"type": "string"}, "head": {"type": "string"}, "path": {"type": "string"}}}},
    {"name": "git_blame", "description": "Line authorship of a file at a revision (never the worktree). Read-only.",
     "inputSchema": {"type": "object", "required": ["rev", "path"], "properties": {
         "rev": {"type": "string"}, "path": {"type": "string"}}}},
    {"name": "git_status", "description": "Working-tree status (porcelain), without locks. Read-only.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _env(root: Path) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", "LANG": "C",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CEILING_DIRECTORIES": str(root.resolve().parent),
            "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1"}


def _base(root: Path) -> list[str]:
    argv = ["git", "-C", str(root), f"--work-tree={root.resolve()}",
            "--no-pager", "--no-replace-objects", "--no-optional-locks"]
    for item in _CONFIG:
        argv += ["-c", item]
    return argv


def _run(root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(_base(root) + args, cwd=root, env=_env(root), stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=TIMEOUT_SECONDS, shell=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"git timed out after {TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise ToolError(f"git could not run: {exc}") from exc
    out = proc.stdout[:MAX_OUTPUT].decode("utf-8", "replace")
    if proc.returncode != 0:
        raise ToolError(f"git exited {proc.returncode}: {proc.stderr[:400].decode('utf-8', 'replace').strip()}")
    return out + ("\n…[truncated]" if len(proc.stdout) > MAX_OUTPUT else "")


def _check_repository(root: Path) -> None:
    """Refuse unless ``root`` is a work-tree root whose history is the project's own.

    A ``.git`` file (``gitdir: …``), a symlink, a ``commondir`` file, or object alternates can all point git at
    another repository's history. Required:

    * the common dir (``--git-common-dir``, where objects and refs live) and the git dir are both inside the
      project; or, for a registered linked worktree, the git dir is ``<common>/worktrees/<name>`` and its
      ``gitdir`` back-link names this project's ``.git`` file (a worktree of a repository elsewhere serves that
      repository's history by design, so only its own registration is accepted);
    * the common dir borrows no objects (``objects/info/alternates`` / ``http-alternates``).
    """
    dot = root / ".git"
    if not dot.exists():
        raise ToolError(f"{root} is not the root of a git work tree; the MCP client must launch this server there")
    project = root.resolve()
    try:
        gitdir = Path(_run(root, ["rev-parse", "--absolute-git-dir"]).strip()).resolve()
        common = Path(_run(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()).resolve()
    except ToolError as exc:
        raise ToolError(f"cannot resolve the git dir: {exc}") from exc
    info = common / "objects" / "info"
    if (info / "alternates").exists() or (info / "http-alternates").exists():
        raise ToolError("the repository borrows objects from elsewhere (alternates); refusing")
    if gitdir.is_relative_to(project) and common.is_relative_to(project):
        return
    backlink = gitdir / "gitdir"
    try:
        linked = backlink.is_file() and Path(backlink.read_text(encoding="utf-8").strip()).resolve() == dot.resolve()
    except OSError:
        linked = False
    registered = gitdir.parent.name == "worktrees" and gitdir.parent.parent == common
    if not (dot.is_file() and not dot.is_symlink() and registered and linked):
        raise ToolError("the project's .git points at a repository outside the project; refusing")


def _refuse_filters(root: Path) -> None:
    """Refuse when the repository config defines any filter (clean/process filters run on worktree reads)."""
    try:
        proc = subprocess.run(_base(root) + ["config", "--includes", "--name-only", "--get-regexp", r"^filter\."],
                              cwd=root, env=_env(root), stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=TIMEOUT_SECONDS, shell=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ToolError(f"could not read the repository config: {exc}") from exc
    if proc.returncode == 0 and proc.stdout.strip():
        raise ToolError("the repository config defines filter.* drivers, which could run programs; refusing")


def _ref(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value) or value.startswith("-"):
        raise ToolError(f"{field} must be a plain revision (letters, digits, ._/@{{}}~^-), not starting with '-'")
    return value


def _path(root: Path, value: Any) -> str:
    if not isinstance(value, str) or not value or "\0" in value or value.startswith(("-", "/")):
        raise ToolError("path must be a relative path inside the project")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()) or ".git" in Path(value).parts:
        raise ToolError("path must be inside the project and outside .git")
    return value


def call(name: str, arguments: dict[str, Any], root: Path) -> Any:
    """Run one tool.

    Args:
        name: The tool name.
        arguments: The tool arguments.
        root: The project root (a git work tree).

    Returns:
        ``{"output": text}``.

    Raises:
        ToolError: Bad arguments, a configured filter, a timeout, or a git failure.
    """
    _check_repository(root)
    _refuse_filters(root)
    path = arguments.get("path")
    tail = ["--", _path(root, path)] if path is not None else []
    if name == "git_log":
        n = arguments.get("n", 20)
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_LOG:
            raise ToolError(f"n must be an integer from 1 to {MAX_LOG}")
        args = ["log", "--no-color", "--no-ext-diff", f"-n{n}", "--date=iso-strict",
                "--format=%H%x09%an%x09%ad%x09%s", "--end-of-options", _ref(arguments.get("rev", "HEAD"), "rev")]
    elif name == "git_show":
        args = ["show", "--no-color", "--no-ext-diff", "--no-textconv", "--stat", "--patch", "--format=fuller",
                "--end-of-options", _ref(arguments.get("rev"), "rev")]
    elif name == "git_diff":
        args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all",
                "--end-of-options", _ref(arguments.get("base"), "base"), _ref(arguments.get("head"), "head")]
    elif name == "git_blame":
        if path is None:
            raise ToolError("git_blame needs a path")
        # blame mis-parses ``--end-of-options REV -- PATH`` (the path becomes a revision); the ref is already
        # validated not to start with ``-``, and ``--`` still fences the path.
        args = ["blame", "--no-textconv", _ref(arguments.get("rev"), "rev")]
    elif name == "git_status":
        args, tail = ["status", "--porcelain=v1", "--ignore-submodules=all"], []
    else:
        raise ToolError(f"unknown tool {name!r}")
    return {"output": _run(root, args + tail)}
