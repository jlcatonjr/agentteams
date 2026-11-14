"""The audit checklist ``api_impact status`` prints under an unreviewed commit (#451 b).

The checklist used to search every changed name as a bare word over the whole tree, so a re-signed ``main`` listed 341
files, and ledgers or run reports that only quote a name were listed too. Now, for each entry:

- **A symbol** (``module::name``): files that import the module and mention the name, plus files that write
  ``<module>.<name>`` or ``from <module> import … <name>``. Relative imports (``from .tool import main``) are resolved
  against the importing file's package, and a leading ``src/`` is not part of a module's name.
- **A removed or renamed module:** files that import it. A term that is both a symbol and a module gets both sets.
- **A command-line option, or a removed or renamed non-Python file:** the bare word or path, as before (there is no
  module to qualify it with).

Only when the qualified search finds no file other than the changed one does the checklist fall back to the bare word,
and the entry is then keyed ``"<name> (bare-word fallback)"``. Qualified hits that are all left out (tests, say) give
an empty entry, not a fallback.

Hits leave out the changed file itself, tests, the review ledger and any path matching a glob in
``<ledger>/CHECKLIST_EXCLUDES`` as committed at the tip of the range checked (one ``fnmatch`` pattern per line, ``#``
comments), so a project can drop its record paths (ledgers, run reports). The checklist is advisory: it never changes
whether a commit counts as reviewed. Known limits, all toward listing too much: an ``import x as y`` alias is matched
by its module only, ``<leaf>.<name>`` matches any module with that leaf, and an ``import`` line inside a docstring
counts as an import.
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

#: File, in the review ledger directory, listing checklist path globs to leave out (read at the range's tip).
EXCLUDES_FILE = "CHECKLIST_EXCLUDES"
#: Key suffix that marks an entry whose hits came from the bare-word search.
FALLBACK = " (bare-word fallback)"
#: Source-root directories that are not part of a module's import name.
SRC_ROOTS = ("src",)

_RELATIVE = re.compile(r"^(\s*from\s+)(\.+)([\w.]*)(\s+import\b)", re.MULTILINE)


def _need(value: object, kind: type | tuple[type, ...], name: str) -> None:
    if not isinstance(value, kind):
        raise TypeError(f"{name} must be {getattr(kind, '__name__', kind)}, got {type(value).__name__}")


def _grep(repo: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", "-C", str(repo), "grep", "-l", *args], capture_output=True, check=False)
    return proc.stdout.decode("utf-8", errors="replace").splitlines() if proc.returncode == 0 else []


def dotted(path: str) -> str:
    """The import name of a repo-relative module path (``pkg/mod.py`` → ``pkg.mod``; ``pkg/__init__.py`` → ``pkg``;
    ``src/pkg/mod.py`` → ``pkg.mod``).

    Args:
        path: A ``.py`` path.

    Returns:
        The dotted name.

    Raises:
        TypeError: When ``path`` is not a string.
    """
    _need(path, str, "path")
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if len(parts) > 1 and parts[0] in SRC_ROOTS:
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _absolutize(text: str, path: str) -> str:
    """``text`` with each relative ``from .x import`` rewritten to the absolute module, given the file's ``path``."""
    package = dotted(path).split(".") if path.endswith("/__init__.py") else dotted(path).split(".")[:-1]

    def repl(m: re.Match[str]) -> str:
        up = len(m.group(2)) - 1
        if up > len(package):
            return m.group(0)
        base = package[:len(package) - up] if up else package
        target = ".".join([*base, m.group(3)] if m.group(3) else base)
        return f"{m.group(1)}{target}{m.group(4)}" if target else m.group(0)

    return _RELATIVE.sub(repl, text)


def _text(repo: Path, path: str) -> str:
    try:
        text = (repo / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _absolutize(text, path) if path.endswith(".py") else text


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
    """Tracked files that import ``module`` (a dotted name), relative imports resolved.

    Args:
        repo: Repository root.
        module: The dotted module name.

    Returns:
        The paths, sorted.

    Raises:
        TypeError: When an argument has the wrong type.
    """
    _need(repo, Path, "repo")
    _need(module, str, "module")
    if not module:
        return []
    leaf = module.rpartition(".")[2]
    return sorted(h for h in _grep(repo, "-w", "-F", "-e", leaf) if _imports(_text(repo, h), module))


def symbol_evidence(repo: Path, module: str, name: str) -> list[str]:
    """Files with module-qualified evidence of ``name``: a file that imports ``module`` and mentions ``name``, writes
    ``<module>.<name>`` or ``<leaf>.<name>``, or imports ``name`` from ``module`` (relative imports resolved).

    Args:
        repo: Repository root.
        module: The dotted module name.
        name: The bare symbol name (the last part of ``Class.method``).

    Returns:
        The paths, sorted.

    Raises:
        TypeError: When an argument has the wrong type.
    """
    _need(repo, Path, "repo")
    _need(module, str, "module")
    _need(name, str, "name")
    if not name:
        return []
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


def read_excludes(repo: Path, tip: str, ledger_rel: str) -> list[str]:
    """Globs from ``<tip>:<ledger_rel>/CHECKLIST_EXCLUDES``, the file as committed at the range's tip.

    A file that is not UTF-8 is ignored with a warning on standard error, so a mis-encoded file is not silently read
    as no excludes.

    Args:
        repo: Repository root.
        tip: The commit whose tree holds the file.
        ledger_rel: The review ledger's repo-relative directory.

    Returns:
        The patterns; empty when the file is absent at ``tip`` or not UTF-8.

    Raises:
        TypeError: When an argument has the wrong type.
    """
    _need(repo, Path, "repo")
    _need(tip, str, "tip")
    _need(ledger_rel, str, "ledger_rel")
    if tip.startswith("-"):
        return []
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "-p", f"{tip}:{ledger_rel}/{EXCLUDES_FILE}"],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        return []
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        print(f"agentteams: {ledger_rel}/{EXCLUDES_FILE} at {tip} is not UTF-8; its globs are ignored",
              file=sys.stderr)
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
        The checklist. A symbol or module term whose qualified search found no file but its own, and whose bare word
        did, is keyed with :data:`FALLBACK`.

    Raises:
        TypeError: When an argument has the wrong type.
    """
    _need(repo, Path, "repo")
    for value, name in ((entries, "entries"), (symbols, "symbols"), (modules, "modules")):
        _need(value, dict, name)
    _need(excludes, list, "excludes")
    _need(cap, int, "cap")
    if not callable(skip):
        raise TypeError("skip must be callable")

    def keep(path: str) -> bool:
        return not skip(path) and not any(fnmatchcase(path, g) for g in excludes)

    out: dict[str, list[str]] = {}
    for term, own in sorted(entries.items()):
        key = term
        if term in symbols or term in modules:
            found = {h for m in symbols.get(term, []) for h in symbol_evidence(repo, m, term)}
            found |= {h for m in modules.get(term, []) for h in importers(repo, m)}
            raw = sorted(found - own)
            hits = [h for h in raw if keep(h)]
            if not raw:
                hits = [h for h in _grep(repo, "-w", "-F", "-e", term) if h not in own and keep(h)]
                key = term + FALLBACK if hits else term
        else:
            hits = [h for h in _grep(repo, "-w", "-F", "-e", term) if h not in own and keep(h)]
        out[key] = hits[:cap] + ([f"... {len(hits)} files in all"] if len(hits) > cap else [])
    return out
