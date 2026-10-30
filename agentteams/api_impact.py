"""api_impact.py — detect API-affecting changes so the team reviews and updates its API after each one.

The orchestrator's **API Impact Review** sub-protocol (``references/api-impact.reference.md``) needs to know,
for the commits of a session, which ones changed the project's API or structure and whether each has been
reviewed. This module answers that from git alone:

* :func:`detect` compares one diff (the staged index, or one commit) and reports what changed: Python modules
  added, removed or renamed; public module-level functions and classes, public methods of public classes and
  ``__all__`` added, removed or re-signed; command-line options (``add_argument``) and subcommands
  (``add_parser``) added or removed; ``*.schema.json`` files changed; and tracked files added, removed or renamed
  outside tests, docs and work records. Symbol-level detection is Python-only; other languages are covered at
  file level.
* :func:`status` runs :func:`detect` on every non-merge commit since the branch base (:func:`resolve_base`) and
  marks each impactful commit **reviewed** when its stable patch-id has a file in the review ledger
  (:func:`ledger_dir`). Nothing is stored between runs, so removing a worktree loses nothing, and the patch-id
  survives rebase and cherry-pick.
* :func:`record` writes one ledger file per reviewed commit, with the reviewer's note. It is a
  **self-attestation**, not a control: the ledger is committed, so the record shows in the pull request.
* :func:`doc_status` maps each impactful module to its API documentation pages (:func:`doc_pages`: tracked
  Markdown pages named after the module) and reports whether a page changed with it, which added names no page
  mentions, and which removed names a page still mentions. ``status`` and the hook both print it.
* :func:`pre_commit_check` is what the installed ``pre-commit`` hook runs. It prints a one-line summary of the
  staged impact and a line per module whose API page fell behind and, in the agentteams repository with
  ``git config agentteams.regenerateMan true``, regenerates and stages the CLI man page ``agentteams.1`` when the
  parser changed. It fails the commit only when the operator opted in with
  ``git config agentteams.apiDocsStrict true`` and a module's page was not updated (strict mode).

Nothing here reads a brief or configuration file: what runs at commit time cannot be changed by editing
project content. Stdlib only.

CLI::

    python -m agentteams.api_impact --pre-commit [--strict-capable] [--repo DIR]   # exit 3: strict block
    python -m agentteams.api_impact detect (--staged | --commit SHA) [--json]
    python -m agentteams.api_impact status [--range BASE..HEAD] [--json]   # exit 0 reviewed, 1 pending, 2 no base
    python -m agentteams.api_impact record [--range BASE..HEAD] --note TEXT
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

#: Review ledger, relative to the directory that holds the project's ``references/``.
LEDGER_SUBDIR = "api-impact-reviews"

#: Path prefixes whose added/removed/renamed files are not structural API changes.
STRUCTURAL_EXCLUDES: tuple[str, ...] = (
    "docs/", "docs_src/", "tests/", "test/", "tmp/", "workSummaries/", "references/plans/",
)

#: Most files a checklist entry lists before it reports only the total.
CHECKLIST_CAP = 15

_MAN_INPUTS = ("build_team.py", "agentteams/cli/", "agentteams/man.py")


@dataclass
class Impact:
    """What one diff changed in the project's API or structure.

    Attributes:
        modules_added: Python modules (repo-relative paths) added.
        modules_removed: Python modules removed.
        modules_renamed: ``(old, new)`` pairs of renamed Python modules.
        symbols_added: ``module::name`` entries for new public symbols.
        symbols_removed: ``module::name`` entries for removed public symbols.
        symbols_changed: ``module::name`` entries whose signature changed.
        cli_added: Command-line options or subcommands added.
        cli_removed: Command-line options or subcommands removed.
        schemas_changed: ``*.schema.json`` files added, removed or modified.
        structural_added: Other tracked files added (outside :data:`STRUCTURAL_EXCLUDES`).
        structural_removed: Other tracked files removed.
        structural_renamed: ``(old, new)`` pairs of other renamed files.
        unparseable: Python files whose old or new version does not parse.
        regenerated: Derived artifacts regenerated and staged (pre-commit only).
    """

    modules_added: list[str] = field(default_factory=list)
    modules_removed: list[str] = field(default_factory=list)
    modules_renamed: list[tuple[str, str]] = field(default_factory=list)
    symbols_added: list[str] = field(default_factory=list)
    symbols_removed: list[str] = field(default_factory=list)
    symbols_changed: list[str] = field(default_factory=list)
    cli_added: list[str] = field(default_factory=list)
    cli_removed: list[str] = field(default_factory=list)
    schemas_changed: list[str] = field(default_factory=list)
    structural_added: list[str] = field(default_factory=list)
    structural_removed: list[str] = field(default_factory=list)
    structural_renamed: list[tuple[str, str]] = field(default_factory=list)
    unparseable: list[str] = field(default_factory=list)
    regenerated: list[str] = field(default_factory=list)

    @property
    def impactful(self) -> bool:
        """True when any category is non-empty."""
        return any(getattr(self, f) for f in self.__dataclass_fields__)

    def summary(self) -> str:
        """One line naming each non-empty category and its count."""
        parts = [f"{len(getattr(self, f))} {f.replace('_', ' ')}"
                 for f in self.__dataclass_fields__ if getattr(self, f)]
        return ", ".join(parts) if parts else "no API impact"


@dataclass
class CommitStatus:
    """Review state of one commit in a :func:`status` range.

    Attributes:
        commit: Full commit SHA.
        subject: First line of the commit message.
        patch_id: Stable patch-id (ledger key); empty for an empty diff.
        impact: What the commit changed.
        reviewed: True when the ledger holds a file for ``patch_id``.
        checklist: Changed name → tracked files that mention it (capped at :data:`CHECKLIST_CAP`).
        docs: API documentation state per impactful module (:func:`doc_status`); empty once reviewed.
    """

    commit: str
    subject: str
    patch_id: str
    impact: Impact
    reviewed: bool
    checklist: dict[str, list[str]] = field(default_factory=dict)
    docs: list["DocStatus"] = field(default_factory=list)


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True, stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout if proc.returncode == 0 else b""


def _blob(repo: Path, spec: str) -> str | None:
    out = subprocess.run(["git", "-C", str(repo), "cat-file", "-p", spec], capture_output=True, check=False)
    return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else None


def _name_status(raw: bytes) -> list[tuple[str, str, str]]:
    """Parse ``--name-status -z`` output into ``(status, old_path, new_path)``."""
    fields = raw.decode("utf-8", errors="replace").split("\0")
    out: list[tuple[str, str, str]] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            # A copy leaves the source in place: to the API it is an added file.
            out.append(("A" if status[0] == "C" else "R", fields[i + 2] if status[0] == "C" else fields[i + 1],
                        fields[i + 2]))
            i += 3
        else:
            out.append((status[0], fields[i + 1], fields[i + 1]))
            i += 2
    return out


def _in_head(repo: Path, rel: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{rel}"],
                          capture_output=True, check=False).returncode == 0


def _has_head(repo: Path) -> bool:
    return bool(_git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False))


def _parent(repo: Path, commit: str) -> str | None:
    """The commit's first parent, or ``None`` for a root commit."""
    parents = _git(repo, "rev-list", "--parents", "-n", "1", commit).decode().split()
    return parents[1] if len(parents) > 1 else None


def _changed_paths(repo: Path, commit: str) -> list[str]:
    """Paths one commit changed, deletions and both sides of renames included."""
    root = [] if _parent(repo, commit) else ["--root"]
    raw = _git(repo, "diff-tree", "--no-commit-id", "-r", "--name-only", "-z", *root, commit)
    return [p for p in raw.decode("utf-8", errors="replace").split("\0") if p]


# ---------------------------------------------------------------------------
# Public surface of a Python module
# ---------------------------------------------------------------------------

def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({ast.unparse(node.args)}){returns}"


def surface(source: str) -> dict[str, str] | None:
    """Return the public API surface of one Python module.

    Keys are public module-level functions and classes, ``Class.method`` for public methods (and ``__init__``)
    of public classes, ``__all__``, ``cli:<option>`` for every ``add_argument`` option string and
    ``cli-cmd:<name>`` for every ``add_parser`` subcommand. Values are signatures (``__all__``: the sorted list).

    Args:
        source: The module's source text.

    Returns:
        The surface map, or ``None`` when the source does not parse.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            out[node.name] = _signature(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out[node.name] = f"({', '.join(ast.unparse(b) for b in node.bases)})"
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        not item.name.startswith("_") or item.name == "__init__"):
                    out[f"{node.name}.{item.name}"] = _signature(item)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets) and node.value is not None:
                try:
                    out["__all__"] = repr(sorted(ast.literal_eval(node.value)))
                except (ValueError, TypeError):
                    out["__all__"] = ast.unparse(node.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            strings = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if node.func.attr == "add_argument":
                for opt in strings:
                    if opt.startswith("-"):
                        out[f"cli:{opt}"] = ""
            elif node.func.attr == "add_parser" and strings:
                out[f"cli-cmd:{strings[0]}"] = ""
    return out


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return (any(part in ("tests", "test") for part in p.parts[:-1])
            or p.name.startswith("test_") or p.name.endswith("_test.py") or p.name == "conftest.py")


def _excluded(path: str, ledger_rel: str) -> bool:
    if path.startswith(ledger_rel.rstrip("/") + "/") or path.startswith(STRUCTURAL_EXCLUDES):
        return True
    return "/expected/" in f"/{path}"


def detect(repo: Path, *, commit: str | None = None) -> Impact:
    """Report the API impact of the staged index, or of one commit.

    Args:
        repo: Repository root.
        commit: A commit to inspect; ``None`` inspects the staged index against ``HEAD``.

    Returns:
        The :class:`Impact` of that diff.

    Raises:
        RuntimeError: When git fails (not a repository, unknown commit).
    """
    repo = Path(repo)
    ledger_rel = ledger_dir(repo).relative_to(repo.resolve()).as_posix()
    if commit is None:
        raw = _git(repo, "diff", "--cached", "--name-status", "-M", "-z")
        old_ref = "HEAD" if _has_head(repo) else None
        new_ref = ""  # the index: ":path"
    else:
        old_ref = _parent(repo, commit)
        args = ["diff-tree", "--no-commit-id", "-r", "-M", "--name-status", "-z"]
        raw = _git(repo, *args, *(["--root"] if old_ref is None else []), commit)
        new_ref = commit
    imp = Impact()
    for status, old, new in _name_status(raw):
        if _excluded(new, ledger_rel) and _excluded(old, ledger_rel):
            continue
        if new.endswith(".schema.json") or old.endswith(".schema.json"):
            imp.schemas_changed.append(new)
        is_py = new.endswith(".py") or old.endswith(".py")
        if is_py and not (_is_test(new) and _is_test(old)):
            if status == "A":
                imp.modules_added.append(new)
            elif status == "D":
                imp.modules_removed.append(old)
            elif status == "R":
                imp.modules_renamed.append((old, new))
            old_src = _blob(repo, f"{old_ref}:{old}") if old_ref and status != "A" else ""
            new_src = _blob(repo, f"{new_ref}:{new}") if status != "D" else ""
            # A blob that cannot be read is unknown, not empty (empty would report every symbol removed).
            before = surface(old_src) if old_src is not None else None
            after = surface(new_src) if new_src is not None else None
            if before is None or after is None:
                imp.unparseable.append(new)
                continue
            module = new if status != "D" else old
            for name in sorted(after.keys() - before.keys()):
                (imp.cli_added if name.startswith("cli") else imp.symbols_added).append(
                    name.split(":", 1)[1] if name.startswith("cli") else f"{module}::{name}")
            for name in sorted(before.keys() - after.keys()):
                (imp.cli_removed if name.startswith("cli") else imp.symbols_removed).append(
                    name.split(":", 1)[1] if name.startswith("cli") else f"{module}::{name}")
            for name in sorted(before.keys() & after.keys()):
                if before[name] != after[name]:
                    imp.symbols_changed.append(f"{module}::{name}")
        elif not is_py:
            new_counts = not (_excluded(new, ledger_rel) or _is_test(new))
            old_counts = not (_excluded(old, ledger_rel) or _is_test(old))
            if status == "A" and new_counts:
                imp.structural_added.append(new)
            elif status == "D" and old_counts:
                imp.structural_removed.append(old)
            elif status == "R" and (old_counts or new_counts):
                imp.structural_renamed.append((old, new))
    return imp


# ---------------------------------------------------------------------------
# Branch base, patch-ids and the review ledger
# ---------------------------------------------------------------------------

def ledger_dir(repo: Path) -> Path:
    """Return the review-ledger directory: ``references/api-impact-reviews/`` at the root when ``references/``
    exists there, else under the agent directory's ``references/``.

    Args:
        repo: Repository root.

    Returns:
        The ledger directory (it need not exist yet).
    """
    root = Path(repo).resolve()
    if (root / "references").is_dir():
        return root / "references" / LEDGER_SUBDIR
    for agents in (".github/agents", ".claude/agents", "agents"):
        if (root / agents / "references").is_dir():
            return root / agents / "references" / LEDGER_SUBDIR
    return root / "references" / LEDGER_SUBDIR


def _default_branch(repo: Path) -> str:
    for ref in ("origin/HEAD", "origin/main", "origin/master"):
        name = _git(repo, "rev-parse", "--abbrev-ref", "-q", ref, check=False).decode().strip()
        if name and name != "origin/HEAD":
            return name
    return next((b for b in ("main", "master")
                 if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{b}", check=False)), "")


def resolve_base(repo: Path) -> str | None:
    """Find where this branch's own commits start.

    On a feature branch: the merge-base of ``HEAD`` with the default branch (``origin/HEAD``, ``origin/main``,
    ``origin/master``, else a local ``main``/``master``). On the default branch itself: ``@{upstream}``, so the
    range is the unpushed commits. A detached ``HEAD`` that is already on the default branch has no base.

    Args:
        repo: Repository root.

    Returns:
        A commit-ish for the range start, or ``None`` when no base can be found (the caller reports the review
        as pending, never as passed).
    """
    repo = Path(repo)
    if not _has_head(repo):
        return None
    default = _default_branch(repo)
    if not default:
        return None
    current = _git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).decode().strip()
    if current and current == default.split("/", 1)[-1]:
        upstream = _git(repo, "rev-parse", "-q", "--verify", "@{upstream}", check=False).decode().strip()
        return upstream or None
    base = _git(repo, "merge-base", "HEAD", default, check=False).decode().strip()
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    if not base or (base == head and not current):
        return None
    return base


def patch_id(repo: Path, commit: str) -> str:
    """Return the commit's stable patch-id, with the review ledger left out of the diff.

    Args:
        repo: Repository root.
        commit: Commit SHA.

    Returns:
        The patch-id, or ``""`` for a commit whose diff (ledger excluded) is empty.
    """
    repo = Path(repo)
    ledger_rel = ledger_dir(repo).relative_to(repo.resolve()).as_posix()
    # Plumbing, not porcelain, so diff.algorithm / textconv / prefix settings cannot change the id.
    parent = _parent(repo, commit)
    base = [parent] if parent else ["--root"]
    diff = _git(repo, "diff-tree", "-p", "--no-ext-diff", "--no-textconv", "--no-color", *base, commit,
                "--", ".", f":(exclude){ledger_rel}")
    out = _git(repo, "patch-id", "--stable", stdin=diff).decode().split()
    return out[0] if out else ""


def _checklist(repo: Path, imp: Impact) -> dict[str, list[str]]:
    names: dict[str, set[str]] = {}
    for entry in (*imp.symbols_removed, *imp.symbols_changed):
        module, name = entry.split("::", 1)
        names.setdefault(name.split(".")[-1], set()).add(module)
    for path in (*imp.modules_removed, *(o for o, _ in imp.modules_renamed),
                 *imp.structural_removed, *(o for o, _ in imp.structural_renamed)):
        names.setdefault(PurePosixPath(path).stem if path.endswith(".py") else path, set()).add(path)
    for opt in imp.cli_removed:
        names.setdefault(opt, set())
    out: dict[str, list[str]] = {}
    for term, own in sorted(names.items()):
        hits = _git(repo, "grep", "-l", "-w", "-F", "-e", term, check=False).decode().splitlines()
        hits = [h for h in hits if h not in own and not _is_test(h)]
        out[term] = hits[:CHECKLIST_CAP] + ([f"... {len(hits)} files in all"] if len(hits) > CHECKLIST_CAP else [])
    return out


def status(repo: Path, rev_range: str | None = None) -> tuple[str | None, list[CommitStatus]]:
    """Review state of every impactful non-merge commit in a range.

    Args:
        repo: Repository root.
        rev_range: ``BASE..HEAD``; defaults to ``<resolve_base()>..HEAD``.

    Returns:
        ``(range_used, commits)``; ``range_used`` is ``None`` when no base could be resolved (``commits`` is then
        empty and the review counts as pending).
    """
    repo = Path(repo)
    if rev_range is None:
        base = resolve_base(repo)
        if base is None:
            return None, []
        rev_range = f"{base}..HEAD"
    shas = _git(repo, "rev-list", "--no-merges", "--reverse", rev_range).decode().split()
    ledger = ledger_dir(repo)
    # A page counts as updated for a commit when that commit or a later one in the range changed it.
    later: list[set[str]] = []
    acc: set[str] = set()
    for sha in reversed(shas):
        acc = acc | set(_changed_paths(repo, sha))
        later.append(acc)
    later.reverse()
    out: list[CommitStatus] = []
    for i, sha in enumerate(shas):
        imp = detect(repo, commit=sha)
        if not imp.impactful:
            continue
        pid = patch_id(repo, sha)
        subject = _git(repo, "log", "-1", "--format=%s", sha).decode().strip()
        rel = (ledger / f"{pid}.json").relative_to(repo.resolve()).as_posix()
        # Reviewed only when the ledger file is committed: an uncommitted record does not count.
        reviewed = bool(pid) and _in_head(repo, rel)
        out.append(CommitStatus(sha, subject, pid, imp, reviewed, {} if reviewed else _checklist(repo, imp),
                                [] if reviewed else doc_status(repo, imp, later[i], ref="HEAD")))
    return rev_range, out


def record(repo: Path, note: str, rev_range: str | None = None) -> list[Path]:
    """Write a ledger file for every unreviewed impactful commit in the range.

    Args:
        repo: Repository root.
        note: What the review covered and changed; required.
        rev_range: As for :func:`status`.

    Returns:
        The ledger files written.

    Raises:
        ValueError: When ``note`` is empty, or no base can be resolved and no range was given.
    """
    if not note.strip():
        raise ValueError("record needs a non-empty --note saying what the review covered and changed")
    used, commits = status(repo, rev_range)
    if used is None:
        raise ValueError("no branch base could be resolved; pass --range BASE..HEAD")
    ledger = ledger_dir(repo)
    written: list[Path] = []
    for c in commits:
        target = ledger / f"{c.patch_id}.json"
        if c.reviewed or not c.patch_id or target.is_file():
            continue
        ledger.mkdir(parents=True, exist_ok=True)
        body = {"patch_id": c.patch_id, "commit": c.commit, "subject": c.subject, "impact": c.impact.summary(),
                "note": note.strip(), "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        written.append(target)
    return written


# ---------------------------------------------------------------------------
# API documentation pages
# ---------------------------------------------------------------------------

@dataclass
class DocStatus:
    """Whether one impactful module's API documentation page kept up with the change.

    Attributes:
        module: Repo-relative module path (the new path for renames).
        pages: Tracked Markdown pages that document it (:func:`doc_pages`), sorted.
        state: ``"updated"`` (a page is among the touched paths), ``"not-updated"`` or ``"no-page"``.
        added_module: True when the diff added the module (or renamed it in).
        undocumented: Added public names that no page mentions (only checked when the module has pages).
        stale: Removed public names that a page still mentions; for a removed or renamed-away module, its key.
        public_api: True when the module is public (no leading ``_``) and the diff added public names to it.
    """

    module: str
    pages: list[str]
    state: str
    added_module: bool
    undocumented: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    public_api: bool = False

    @property
    def blocking(self) -> bool:
        """Whether strict mode fails the commit for this module.

        Returns:
            True when its pages were not updated, or it is a new public module with public names and no page.
        """
        return self.state == "not-updated" or (self.state == "no-page" and self.added_module and self.public_api)

    @property
    def reportable(self) -> bool:
        """Whether the module gets a line in the hook's or ``status``'s output.

        Returns:
            True when it is :attr:`blocking`, or has ``undocumented`` or ``stale`` names.
        """
        return self.blocking or bool(self.undocumented or self.stale)


#: Page stems that never document a single module.
_GENERIC_PAGE_STEMS = frozenset({"index", "readme", "changelog"})

#: Path prefixes whose Markdown files are never API pages (agent files and references among them).
_PAGE_EXCLUDES: tuple[str, ...] = ("tmp/", "tests/", "test/", "workSummaries/", "references/", "agents/")


def _module_key(path: str) -> str | None:
    p = PurePosixPath(path)
    if p.suffix != ".py" or _is_test(path) or p.name == "__main__.py":
        return None
    if p.name == "__init__.py":
        return p.parent.name or None
    return p.stem


def _tracked_pages(repo: Path, ref: str) -> list[str]:
    """Candidate pages at ``ref``; for the index (``""``), those in ``HEAD`` too, so a page staged for deletion
    still maps to its module (and counts as touched)."""
    raws = ([_git(repo, "ls-files", "-s", "-z", check=False)]
            + ([_git(repo, "ls-tree", "-r", "-z", "HEAD", check=False)] if _has_head(repo) else [])
            if ref == "" else [_git(repo, "ls-tree", "-r", "-z", ref, check=False)])
    pages: set[str] = set()
    for raw in raws:
        for record in raw.decode("utf-8", errors="replace").split("\0"):
            meta, _, path = record.partition("\t")
            # A symlink's blob is its target path, not the page's text.
            if meta and not meta.startswith("120000"):
                pages.add(path)
    out: set[str] = set()
    for path in pages:
        p = PurePosixPath(path)
        if (not path or p.suffix.lower() != ".md" or any(part.startswith(".") for part in p.parts)
                or path.startswith(_PAGE_EXCLUDES) or "/expected/" in f"/{path}"
                or p.stem.lower() in _GENERIC_PAGE_STEMS):
            continue
        out.add(path)
    return sorted(out)


def _pages_for(pages: list[str], key: str | None) -> list[str]:
    if not key:
        return []
    return [p for p in pages if PurePosixPath(p).stem.lower().replace("-", "_") == key.lower()]


def doc_pages(repo: Path, module: str, *, ref: str = "HEAD") -> list[str]:
    """Return the tracked Markdown pages that document one module.

    A page matches when its stem, with ``-`` read as ``_`` and case ignored, equals the module's key: the file
    stem, or the package name for ``__init__.py``. Pages under dot-directories, ``tmp/``, ``tests/``, ``test/``,
    ``workSummaries/``, ``references/``, ``agents/`` or an ``expected/`` tree, and ``index``/``readme``/
    ``changelog`` pages, never match. Test files and ``__main__.py`` have no pages.

    Args:
        repo: Repository root.
        module: Repo-relative module path.
        ref: Tree to read: a commit-ish, or ``""`` for the index.

    Returns:
        Matching page paths, sorted.
    """
    return _pages_for(_tracked_pages(Path(repo), ref), _module_key(module))


def _page_text(repo: Path, page: str, ref: str) -> str:
    return _blob(repo, f"{ref}:{page}") or ""


def _mentions(text: str, name: str) -> bool:
    return re.search(r"\b" + re.escape(name) + r"\b", text) is not None


def _checked_name(entry: str) -> str | None:
    """The word a page must mention for ``module::name``; ``None`` for dunders, which are never checked."""
    last = entry.split("::", 1)[1].split(".")[-1]
    return None if last.startswith("__") and last.endswith("__") else last


def doc_status(repo: Path, imp: Impact, touched: Iterable[str], *, ref: str = "HEAD") -> list[DocStatus]:
    """Report, for each module with API impact, its documentation pages and whether they kept up.

    Args:
        repo: Repository root.
        imp: The impact to map.
        touched: Paths changed alongside the impact (staged paths, or the commit and later commits of a range).
        ref: Tree to read pages from: a commit-ish, or ``""`` for the index.

    Returns:
        One :class:`DocStatus` per module, sorted by module.
    """
    repo = Path(repo)
    pages = _tracked_pages(repo, ref)
    touched = set(touched)
    texts: dict[str, str] = {}

    def text(page: str) -> str:
        if page not in texts:
            texts[page] = _page_text(repo, page, ref)
        return texts[page]

    modules: dict[str, DocStatus] = {}

    def entry(module: str, *, added: bool = False) -> DocStatus:
        if module not in modules:
            modules[module] = DocStatus(module, _pages_for(pages, _module_key(module)), "", added)
        modules[module].added_module |= added
        return modules[module]

    for module in imp.modules_added:
        entry(module, added=True)
    for old, new in imp.modules_renamed:
        ds = entry(new, added=True)
        old_pages = [p for p in _pages_for(pages, _module_key(old)) if p not in ds.pages]
        if old_pages:
            ds.pages = sorted({*ds.pages, *old_pages})
            ds.stale.append(str(_module_key(old)))
    for module in imp.modules_removed:
        ds = entry(module)
        if ds.pages:
            ds.stale.append(str(_module_key(module)))
    for names, added in ((imp.symbols_added, True), (imp.symbols_removed, False), (imp.symbols_changed, None)):
        for name in names:
            module = name.split("::", 1)[0]
            ds = entry(module)
            key = _module_key(module)
            if added and key and not key.startswith("_"):
                ds.public_api = True
            word = _checked_name(name)
            # With no page there is nothing to check: the module is reported only as a new public module.
            if added is None or word is None or not ds.pages:
                continue
            mentioned = any(_mentions(text(p), word) for p in ds.pages)
            if added and not mentioned:
                ds.undocumented.append(name.split("::", 1)[1])
            elif not added and mentioned:
                ds.stale.append(name.split("::", 1)[1])
    out: list[DocStatus] = []
    for module in sorted(modules):
        ds = modules[module]
        if _module_key(module) is None:
            continue
        ds.state = ("no-page" if not ds.pages
                    else "updated" if any(p in touched for p in ds.pages) else "not-updated")
        ds.undocumented.sort()
        ds.stale.sort()
        out.append(ds)
    return out


# ---------------------------------------------------------------------------
# Pre-commit
# ---------------------------------------------------------------------------

def _regenerate_man_page(repo: Path, staged: list[str]) -> str | None:
    """In the agentteams repository, regenerate ``agentteams.1`` when the parser is staged. Returns a message."""
    # Opt-in through git config, which lives outside the working tree: files in the tree alone never make
    # the hook run the tree's own code.
    if _git(repo, "config", "--bool", "agentteams.regenerateMan", check=False).strip() != b"true":
        return None
    if not ((repo / "build_team.py").is_file() and (repo / "agentteams" / "man.py").is_file()
            and _git(repo, "ls-files", "agentteams.1", check=False).strip()):
        return None
    if not any(p == i or (i.endswith("/") and p.startswith(i)) for p in staged for i in _MAN_INPUTS):
        return None
    if _git(repo, "diff", "--name-only", "--", *_MAN_INPUTS, check=False).strip():
        return "agentteams.1 not regenerated: the parser has unstaged edits (stage them, or run python -m agentteams.man)"
    env = {**os.environ, "PYTHONPATH": str(repo)}
    try:
        proc = subprocess.run([sys.executable, "-P", "-m", "agentteams.man"], cwd=repo, env=env,
                              capture_output=True, check=False, timeout=60)
    except subprocess.TimeoutExpired:
        return "agentteams.1 not regenerated: python -m agentteams.man timed out"
    if proc.returncode != 0 or not proc.stdout:
        return "agentteams.1 not regenerated: python -m agentteams.man failed"
    target = repo / "agentteams.1"
    if target.read_bytes() == proc.stdout:
        return None
    target.write_bytes(proc.stdout)
    _git(repo, "add", "agentteams.1")
    return "regenerated"


#: Most per-module API-docs lines the hook prints before it reports only the remainder.
DOC_LINES_CAP = 10


def _strict(repo: Path) -> bool:
    # Git config, outside the working tree: files in the tree alone never turn blocking on.
    return _git(repo, "config", "--bool", "agentteams.apiDocsStrict", check=False).strip() == b"true"


def _doc_names(d: DocStatus) -> list[str]:
    """``undocumented: …`` and ``stale: …`` labels, for the non-empty lists only."""
    return [f"{label}: {', '.join(names)}" for label, names in (("undocumented", d.undocumented),
                                                                ("stale", d.stale)) if names]


def _doc_line(d: DocStatus) -> str:
    detail = ("new module with no API page" if d.state == "no-page"
              else f"{', '.join(d.pages)} {'updated' if d.state == 'updated' else 'not updated'}")
    parts = _doc_names(d)
    extras = f" ({'; '.join(parts)})" if parts else ""
    return f"agentteams: API docs — {d.module}: {detail}{extras}"


def pre_commit_check(repo: Path, *, strict_capable: bool = False) -> tuple[str, bool]:
    """Run the pre-commit step: report the staged impact and its API pages, regenerate derived artifacts, and
    decide whether strict mode blocks the commit.

    Args:
        repo: Repository root.
        strict_capable: True when the calling hook fails the commit on exit 3. An older hook ignores the exit
            code, so without this flag strict mode prints a re-install notice instead of claiming a block.

    Returns:
        ``(message, blocked)``: the lines the hook prints (empty when there is nothing to say), and True only
        when ``git config agentteams.apiDocsStrict`` is true, ``strict_capable`` is set and a module is
        :attr:`DocStatus.blocking`.

    Raises:
        RuntimeError: When git fails (not a repository).
    """
    repo = Path(repo)
    entries = _name_status(_git(repo, "diff", "--cached", "--name-status", "-M", "-z"))
    staged = [new for _, _, new in entries]
    man = _regenerate_man_page(repo, staged)
    imp = detect(repo)
    if man == "regenerated":
        imp.regenerated.append("agentteams.1")
    lines: list[str] = []
    if imp.impactful:
        lines.append(f"agentteams: API impact — {imp.summary()}. Run the API Impact Review before closing the "
                     "session (python -m agentteams.api_impact status).")
    if man and man != "regenerated":
        lines.append(f"agentteams: {man}")
    docs = doc_status(repo, imp, {p for _, old, new in entries for p in (old, new)}, ref="") if imp.impactful else []
    reportable = [d for d in docs if d.reportable]
    lines += [_doc_line(d) for d in reportable[:DOC_LINES_CAP]]
    if len(reportable) > DOC_LINES_CAP:
        lines.append(f"agentteams: API docs — ... {len(reportable) - DOC_LINES_CAP} more")
    blocked = False
    if any(d.blocking for d in docs) and _strict(repo):
        if strict_capable:
            blocked = True
            lines.append("agentteams: commit blocked (agentteams.apiDocsStrict) — stage the page(s) above, or "
                         "bypass once with git commit --no-verify, or git config agentteams.apiDocsStrict false")
        else:
            lines.append("agentteams: agentteams.apiDocsStrict is set, but this pre-commit hook predates strict "
                         "mode — re-install it (agentteams --update) so it can block")
    return "\n".join(lines), blocked


def pre_commit(repo: Path) -> str:
    """Run the pre-commit step as an advisory hook (:func:`pre_commit_check` without ``strict_capable``).

    Args:
        repo: Repository root.

    Returns:
        The message the hook prints (empty when there is nothing to say).

    Raises:
        RuntimeError: When git fails (not a repository).
    """
    return pre_commit_check(repo)[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_status(used: str | None, commits: list[CommitStatus]) -> None:
    if used is None:
        print("API review PENDING: no branch base could be resolved (pass --range BASE..HEAD).")
        return
    pending = [c for c in commits if not c.reviewed]
    print(f"range: {used} — {len(commits)} impactful commit(s), {len(pending)} unreviewed")
    for c in commits:
        print(f"\n{'reviewed ' if c.reviewed else 'PENDING  '} {c.commit[:12]} {c.subject}")
        print(f"  impact: {c.impact.summary()}")
        for key, values in asdict(c.impact).items():
            for v in values:
                print(f"    {key}: {' -> '.join(v) if isinstance(v, (list, tuple)) else v}")
        if not c.reviewed and c.checklist:
            print("  audit checklist (files that mention each changed name):")
            for term, files in c.checklist.items():
                print(f"    {term}: {', '.join(files) if files else '(no other mentions)'}")
        reportable = [d for d in c.docs if d.reportable]
        if reportable:
            print("  API docs:")
            for d in reportable:
                pages = f" — {', '.join(d.pages)}" if d.pages else ""
                print(f"    {d.module}: {d.state}{pages}{''.join(f'; {n}' for n in _doc_names(d))}")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m agentteams.api_impact``.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: ``--pre-commit`` 3 when strict mode blocks the commit (``--strict-capable`` only), else 0;
        ``status`` 0 when everything is reviewed, 1 when a commit is
        pending, 2 when no base could be resolved; ``record`` 0, or 2 on a refused note or missing base.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agentteams.api_impact",
                                     description="Detect API-affecting changes and track their review.")
    parser.add_argument("--pre-commit", action="store_true",
                        help="Run the pre-commit step (exit 3 only when strict mode blocks the commit).")
    parser.add_argument("--strict-capable", action="store_true",
                        help="With --pre-commit: the calling hook fails the commit on exit 3.")
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    sub = parser.add_subparsers(dest="cmd")
    d = sub.add_parser("detect", help="Report one diff's impact.")
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--commit")
    d.add_argument("--json", action="store_true")
    s = sub.add_parser("status", help="Review state of the branch's commits.")
    s.add_argument("--range", dest="rev_range")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("record", help="Record the review of the range's unreviewed commits.")
    r.add_argument("--range", dest="rev_range")
    r.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    # Pages are listed relative to the top level, so a subdirectory --repo must not shift them.
    top = _git(repo, "rev-parse", "--show-toplevel", check=False).decode().strip()
    if top:
        repo = Path(top)

    if args.pre_commit:
        blocked = False
        try:
            msg, blocked = pre_commit_check(repo, strict_capable=args.strict_capable)
        # Expected failures only; anything else exits 1, which the hook also ignores (it acts on 3 alone).
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            msg = f"agentteams: api-impact check failed ({type(exc).__name__}: {exc}); commit unaffected"
        if msg:
            print(msg, file=sys.stderr)
        return 3 if blocked else 0
    if args.cmd == "detect":
        imp = detect(repo, commit=None if args.staged else args.commit)
        print(json.dumps(asdict(imp), indent=2) if args.json else imp.summary())
        return 0
    if args.cmd == "status":
        used, commits = status(repo, args.rev_range)
        if args.json:
            print(json.dumps({"range": used, "commits": [asdict(c) for c in commits]}, indent=2))
        else:
            _print_status(used, commits)
        if used is None:
            return 2
        return 1 if any(not c.reviewed for c in commits) else 0
    if args.cmd == "record":
        try:
            written = record(repo, args.note, args.rev_range)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        for path in written:
            print(f"recorded {path.relative_to(repo)}")
        if not written:
            print("nothing to record: every impactful commit in the range is already reviewed")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
