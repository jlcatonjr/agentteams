"""The audit checklist ``api_impact status`` prints under an unreviewed commit (#451 b).

The checklist used to search every changed name as a bare word over the whole tree, so a re-signed ``main`` listed 341
files, and ledgers or run reports that only quote a name were listed too. Now, for each entry:

- **A symbol** (``module::name``): files that import the module and mention the name, plus files that write
  ``<module>.<name>`` or ``from <module> import … <name>``. Only when that finds nothing does the checklist fall back
  to the bare word, and the entry is then keyed ``"<name> (bare-word fallback)"``.
- **A removed or renamed module:** files that import it, with the same fallback.
- **A command-line option, or a removed or renamed non-Python file:** the bare word or path, as before (there is no
  module to qualify it with).

Hits leave out the changed file itself, tests, the review ledger and any path matching a glob in the committed
``<ledger>/CHECKLIST_EXCLUDES`` file (one ``fnmatch`` pattern per line, ``#`` comments), so a project can drop its
record paths (ledgers, run reports). The checklist is advisory: it never changes whether a commit counts as reviewed.
"""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

#: Committed file, in the review ledger directory, listing checklist path globs to leave out.
EXCLUDES_FILE = "CHECKLIST_EXCLUDES"
#: Key suffix that marks an entry whose hits came from the bare-word search.
FALLBACK = " (bare-word fallback)"


def _grep(repo: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", "-C", str(repo), "grep", "-l", *args], capture_output=True, check=False)
    return proc.stdout.decode("utf-8", errors="replace").splitlines() if proc.returncode == 0 else []


def dotted(path: str) -> str:
    """The import name of a repo-relative module path (``pkg/mod.py`` → ``pkg.mod``; ``pkg/__init__.py`` → ``pkg``).

    Args:
        path: A ``.py`` path.

    Returns:
        The dotted name.

    Raises:
        Nothing.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _text(repo: Path, path: str) -> str:
    try:
        return (repo / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _imports(text: str, module: str) -> bool:
    """Whether ``text`` imports ``module``: ``import a.b``, ``from a.b import``, ``from a import b`` (also in parens)."""
    mod = re.escape(module)
    parent, _, leaf = module.rpartition(".")
    pats = [rf"^\s*import\s+[^\n]*\b{mod}(?![\w])", rf"^\s*from\s+{mod}\s+import\b"]
    if parent:
        lf = re.escape(leaf)
        pats.append(rf"^\s*from\s+{re.escape(parent)}\s+import\s+(?:\([^)]*\b{lf}\b[^)]*\)|[^\n(]*\b{lf}\b)")
    return any(re.search(p, text, re.MULTILINE) for p in pats)


def importers(repo: Path, module: str) -> list[str]:
    """Tracked files that import ``module`` (a dotted name).

    Args:
        repo: Repository root.
        module: The dotted module name.

    Returns:
        The paths, sorted.

    Raises:
        Nothing.
    """
    if not module:
        return []
    leaf = module.rpartition(".")[2]
    return sorted(h for h in _grep(repo, "-w", "-F", "-e", leaf) if _imports(_text(repo, h), module))


def symbol_evidence(repo: Path, module: str, name: str) -> list[str]:
    """Files with module-qualified evidence of ``name``: a file that imports ``module`` and mentions ``name``, writes
    ``<module>.<name>`` or ``<leaf>.<name>``, or imports ``name`` from ``module``.

    Args:
        repo: Repository root.
        module: The dotted module name.
        name: The bare symbol name (the last part of ``Class.method``).

    Returns:
        The paths, sorted.

    Raises:
        Nothing.
    """
    leaf = module.rpartition(".")[2]
    nm = re.escape(name)
    qualified = re.compile(rf"\b(?:{re.escape(module)}|{re.escape(leaf)})\.{nm}\b")
    from_import = re.compile(rf"^\s*from\s+{re.escape(module)}\s+import\s+(?:\([^)]*\b{nm}\b[^)]*\)|[^\n(]*\b{nm}\b)",
                             re.MULTILINE)
    hits = []
    for path in _grep(repo, "-w", "-F", "-e", name):
        text = _text(repo, path)
        if qualified.search(text) or from_import.search(text) or _imports(text, module):
            hits.append(path)
    return sorted(hits)


def read_excludes(ledger: Path) -> list[str]:
    """Globs from ``<ledger>/CHECKLIST_EXCLUDES`` in the working tree.

    Args:
        ledger: The review ledger directory.

    Returns:
        The patterns; empty when the file is absent or unreadable.

    Raises:
        Nothing.
    """
    try:
        text = (ledger / EXCLUDES_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def build(repo: Path, entries: dict[str, set[str]], *, symbols: dict[str, list[str]],
          modules: dict[str, list[str]], skip: Callable[[str], bool], excludes: list[str],
          cap: int) -> dict[str, list[str]]:
    """The checklist: term → files to audit (capped at ``cap`` plus a total line).

    Args:
        repo: Repository root.
        entries: Term → the paths the term comes from (never listed as hits).
        symbols: Symbol term → the dotted modules it changed in.
        modules: Module-stem term → the dotted modules removed or renamed.
        skip: Paths never listed (tests, the review ledger).
        excludes: Project globs never listed (:func:`read_excludes`).
        cap: Most files listed per term before only the total is given.

    Returns:
        The checklist. A symbol or module term whose qualified search found nothing, but whose bare word did, is
        keyed with :data:`FALLBACK`.

    Raises:
        Nothing.
    """
    def keep(path: str) -> bool:
        return not skip(path) and not any(fnmatchcase(path, g) for g in excludes)

    out: dict[str, list[str]] = {}
    for term, own in sorted(entries.items()):
        qualified: list[str] | None = None
        if term in symbols:
            qualified = sorted({h for m in symbols[term] for h in symbol_evidence(repo, m, term)})
        elif term in modules:
            qualified = sorted({h for m in modules[term] for h in importers(repo, m)})
        hits = [h for h in (qualified or []) if h not in own and keep(h)]
        key = term
        if not hits:
            hits = [h for h in _grep(repo, "-w", "-F", "-e", term) if h not in own and keep(h)]
            if hits and qualified is not None:
                key = term + FALLBACK
        out[key] = hits[:cap] + ([f"... {len(hits)} files in all"] if len(hits) > cap else [])
    return out
