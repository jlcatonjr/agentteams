"""
architecture.py — Repository module-dependency (architecture) map.

The code-side sibling of :mod:`agentteams.graph` (which maps *agent* topology).
This module parses the ``import`` statements of a Python package via :mod:`ast`
and builds a directed dependency graph of the package's own modules.

Two views, matching the "package diagram + module detail" shape:

* **Package-level** Mermaid / DOT diagram — inter-package dependency edges only
  (readable high-level architecture).
* **Module-level** detail — full per-module adjacency in the JSON block and the
  Markdown dependency table, plus an external-dependency summary.

Output is a single Markdown document written to ``references/architecture-graph.md``,
regenerated on every commit that touches the package's ``.py`` files (via the
pre-commit hook installed by :mod:`agentteams.git_hooks`).

CLI usage
---------
    python -m agentteams.architecture .                 # auto-detect package
    python -m agentteams.architecture . --package agentteams
    python -m agentteams.architecture . --format mermaid|dot|json|markdown

Determinism: like :mod:`agentteams.graph`, every serialiser sorts nodes, edges,
and adjacency keys so the output is independent of filesystem walk order — the
guarantee the commit-refresh relies on to avoid spurious diffs.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Directory names never treated as part of the mapped package.
_EXCLUDE_DIRS = {"__pycache__", "templates", ".git", ".agentteams-backups"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ModuleNode:
    """A single Python module in the mapped package."""

    name: str          # dotted module name, e.g. "agentteams.cli.generate"
    package: str       # immediate parent package, e.g. "agentteams.cli"
    rel_path: str      # path relative to repo root, e.g. "agentteams/cli/generate.py"
    is_package: bool   # True for __init__.py modules


@dataclass
class ArchitectureGraph:
    """Directed module-dependency graph for one Python package."""

    root_package: str
    nodes: dict[str, ModuleNode] = field(default_factory=dict)
    # internal module → set of internal modules it imports
    edges: set[tuple[str, str]] = field(default_factory=set)
    # module → set of external (third-party) top-level package names
    external: dict[str, set[str]] = field(default_factory=dict)
    # module → set of repo-local top-level names OUTSIDE the mapped package
    repo_local: dict[str, set[str]] = field(default_factory=dict)

    # -- derived views ----------------------------------------------------

    def packages(self) -> list[str]:
        """Sorted list of distinct packages present."""
        return sorted({n.package for n in self.nodes.values()})

    def package_edges(self) -> list[tuple[str, str]]:
        """Sorted inter-package dependency edges (intra-package edges dropped)."""
        pkg: set[tuple[str, str]] = set()
        for src, dst in self.edges:
            sp = self.nodes[src].package if src in self.nodes else src
            dp = self.nodes[dst].package if dst in self.nodes else dst
            if sp != dp:
                pkg.add((sp, dp))
        return sorted(pkg)

    def module_adjacency(self) -> dict[str, list[str]]:
        """module → sorted list of internal modules it imports."""
        adj: dict[str, list[str]] = {name: [] for name in sorted(self.nodes)}
        for src, dst in self.edges:
            if src in adj and dst not in adj[src]:
                adj[src].append(dst)
        for v in adj.values():
            v.sort()
        return adj

    def module_reverse_adjacency(self) -> dict[str, list[str]]:
        """module → sorted list of internal modules that import it."""
        radj: dict[str, list[str]] = {name: [] for name in sorted(self.nodes)}
        for src, dst in self.edges:
            if dst in radj and src not in radj[dst]:
                radj[dst].append(src)
        for v in radj.values():
            v.sort()
        return radj

    def all_external(self) -> list[str]:
        """Sorted union of external (third-party) top-level dependency names."""
        out: set[str] = set()
        for deps in self.external.values():
            out |= deps
        return sorted(out)

    def all_repo_local(self) -> list[str]:
        """Sorted union of repo-local top-level names outside the mapped package."""
        out: set[str] = set()
        for deps in self.repo_local.values():
            out |= deps
        return sorted(out)

    # -- serialisation ----------------------------------------------------

    def to_mermaid(self) -> str:
        """Package-level Mermaid flowchart (inter-package edges only)."""
        lines = ["flowchart LR"]
        pkgs = self.packages()
        # One class per package depth so the root reads distinctly from leaves.
        lines.append("    classDef root fill:#e8eefb,stroke:#1b3fa0,color:#000")
        lines.append("    classDef sub  fill:#eef6ee,stroke:#3f8f4f,color:#000")
        for pkg in pkgs:
            pid = _node_id(pkg)
            label = pkg
            lines.append(f'    {pid}["{label}"]')
            lines.append(f"    class {pid} {'root' if pkg == self.root_package else 'sub'}")
        for src, dst in self.package_edges():
            lines.append(f"    {_node_id(src)} --> {_node_id(dst)}")
        return "\n".join(lines)

    def to_dot(self) -> str:
        """Package-level Graphviz DOT source."""
        lines = [
            f'digraph "{self.root_package} architecture" {{',
            "    rankdir=LR;",
            '    node [fontname="Helvetica", fontsize=11, shape=box, style="rounded,filled", fillcolor="#eef6ee"];',
            '    edge [fontsize=9];',
        ]
        for pkg in self.packages():
            fill = "#e8eefb" if pkg == self.root_package else "#eef6ee"
            lines.append(f'    "{pkg}" [fillcolor="{fill}"];')
        for src, dst in self.package_edges():
            lines.append(f'    "{src}" -> "{dst}";')
        lines.append("}")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Full module-level JSON (nodes, edges, adjacency, external deps)."""
        adj = self.module_adjacency()  # compute once (was rebuilt per module)
        return json.dumps(
            {
                "root_package": self.root_package,
                "modules": {
                    name: {
                        "package": node.package,
                        "path": node.rel_path,
                        "is_package": node.is_package,
                        "imports_internal": adj.get(name, []),
                        "external": sorted(self.external.get(name, set())),
                        "repo_local": sorted(self.repo_local.get(name, set())),
                    }
                    for name, node in sorted(self.nodes.items())
                },
                "package_edges": [
                    {"source": s, "target": t} for s, t in self.package_edges()
                ],
                "module_edges": [
                    {"source": s, "target": t} for s, t in sorted(self.edges)
                ],
                "external_dependencies": self.all_external(),
                "repo_local_dependencies": self.all_repo_local(),
            },
            indent=2,
        )

    def to_markdown_document(self) -> str:
        """Assemble the full architecture document."""
        adj = self.module_adjacency()
        radj = self.module_reverse_adjacency()
        pkgs = self.packages()

        lines = [
            f"# {self.root_package} — Repository Architecture Map",
            "",
            "> **Auto-generated.** Regenerated on every commit that touches the "
            f"`{self.root_package}` package. Do not edit manually — changes will be overwritten.",
            "",
            f"- Modules mapped: **{len(self.nodes)}**",
            f"- Packages: **{len(pkgs)}**",
            f"- Internal import edges: **{len(self.edges)}**",
            f"- Distinct external dependencies: **{len(self.all_external())}**",
            "",
            "---",
            "",
            "## Package Dependency Diagram",
            "",
            "Inter-package import dependencies (module-level detail in the tables below).",
            "",
            "```mermaid",
            self.to_mermaid(),
            "```",
            "",
            "---",
            "",
            "## Packages",
            "",
            "| Package | Modules | Depends on |",
            "| --- | --- | --- |",
        ]
        pkg_deps: dict[str, list[str]] = {p: [] for p in pkgs}
        for s, t in self.package_edges():
            pkg_deps[s].append(t)
        pkg_counts: dict[str, int] = {p: 0 for p in pkgs}
        for node in self.nodes.values():
            pkg_counts[node.package] = pkg_counts.get(node.package, 0) + 1
        for pkg in pkgs:
            deps = ", ".join(f"`{d}`" for d in sorted(pkg_deps.get(pkg, []))) or "—"
            lines.append(f"| `{pkg}` | {pkg_counts.get(pkg, 0)} | {deps} |")

        lines += [
            "",
            "---",
            "",
            "## Module Dependency Table",
            "",
            "| Module | Imports (internal) | Imported by |",
            "| --- | --- | --- |",
        ]
        for name in sorted(self.nodes):
            outgoing = ", ".join(f"`{m}`" for m in adj.get(name, [])) or "—"
            incoming = ", ".join(f"`{m}`" for m in radj.get(name, [])) or "—"
            lines.append(f"| `{name}` | {outgoing} | {incoming} |")

        externals = self.all_external()
        repo_local = self.all_repo_local()
        lines += [
            "",
            "---",
            "",
            "## External Dependencies",
            "",
            "Third-party (non-stdlib) top-level packages imported by the mapped package:",
            "",
            (", ".join(f"`{e}`" for e in externals) if externals
             else "_None detected (standard library only)._"),
        ]
        if repo_local:
            lines += [
                "",
                "**Repo-local (outside the mapped package):** "
                + ", ".join(f"`{e}`" for e in repo_local),
            ]
        lines += [
            "",
            "---",
            "",
            "## DOT Source",
            "",
            "```dot",
            self.to_dot(),
            "```",
            "",
            "---",
            "",
            "## JSON (module-level)",
            "",
            "```json",
            self.to_json(),
            "```",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def _node_id(dotted: str) -> str:
    """Mermaid/DOT-safe identifier from a dotted name."""
    return dotted.replace(".", "_").replace("-", "_")


_NON_PACKAGE_NAMES = {"tests", "test", "docs", "doc", "examples", "scripts", "src"}


def _package_candidates(parent: Path) -> list[Path]:
    """Importable package dirs directly under ``parent`` (excludes tests/docs/…)."""
    try:
        children = list(parent.iterdir())
    except OSError:
        return []
    return [
        d for d in children
        if d.is_dir()
        and d.name not in _EXCLUDE_DIRS
        and d.name not in _NON_PACKAGE_NAMES
        and not d.name.startswith(".")
        and (d / "__init__.py").is_file()
    ]


def _module_file_count(package_dir: Path) -> int:
    return len(_iter_module_files(package_dir))


def discover_package_root(repo_root: Path) -> Path | None:
    """Return the repo's primary importable package directory, or None.

    Looks at top-level children of ``repo_root`` and, for src-layout repos, under
    ``src/``. A package is a directory containing ``__init__.py`` (``tests``,
    ``docs``, ``examples`` and the ``scripts`` glue dir are never chosen). When
    several qualify, prefer one whose name matches the repo directory (with
    ``-``→``_`` normalisation for zip-download/hyphenated repo names); otherwise
    the one with the **most modules** (the product package), breaking ties
    alphabetically. Falls back to a ``scripts`` package only when nothing else
    qualifies, so a repo whose only package is ``scripts/`` is still mapped.
    """
    candidates = _package_candidates(repo_root)
    src = repo_root / "src"
    if src.is_dir():
        candidates += _package_candidates(src)
    if not candidates:
        # Last resort: a glue `scripts/` package (excluded above) rather than
        # returning nothing at all.
        scripts = repo_root / "scripts"
        if scripts.is_dir() and (scripts / "__init__.py").is_file():
            return scripts
        return None

    repo_norm = repo_root.name.replace("-", "_")
    for c in candidates:
        if c.name in (repo_root.name, repo_norm):
            return c
    # Most modules wins (the product package); alphabetical tiebreak.
    return min(candidates, key=lambda d: (-_module_file_count(d), d.name))


def _module_name(rel_path: Path) -> tuple[str, bool]:
    """Return (dotted module name, is_package) for a .py file path.

    ``agentteams/cli/generate.py``   -> ("agentteams.cli.generate", False)
    ``agentteams/cli/__init__.py``   -> ("agentteams.cli", True)
    """
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


def _iter_module_files(package_dir: Path) -> list[Path]:
    """All mappable ``.py`` files under ``package_dir`` (excludes _EXCLUDE_DIRS).

    Exclusion is tested only on path components *below* ``package_dir`` — never
    on absolute ancestors — so a checkout located under a directory that happens
    to be named e.g. ``templates`` does not empty the entire map.
    """
    out: list[Path] = []
    for path in package_dir.rglob("*.py"):
        rel_parts = path.relative_to(package_dir).parts
        if any(part in _EXCLUDE_DIRS for part in rel_parts):
            continue
        out.append(path)
    return sorted(out)


def _deepest_internal(dotted: str, internal: set[str]) -> str | None:
    """Longest dotted prefix of ``dotted`` that is a known internal module."""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in internal:
            return cand
    return None


def _resolve_from_base(current: str, level: int, module: str | None, is_package: bool) -> str:
    """Resolve the base module of a relative ``from ... import``.

    ``current`` is the importing module's dotted name, ``level`` the number of
    leading dots, ``is_package`` True when ``current`` is a package ``__init__``.
    Mirrors CPython's ``__package__`` semantics: for a regular module ``a.b.c``
    the single-dot anchor is its package ``a.b``; for a **package** ``a.b`` (its
    ``__init__``) the single-dot anchor is ``a.b`` itself. Getting this wrong
    lands every relative re-export in an ``__init__`` one level too shallow.
    """
    parts = current.split(".")
    base = parts if is_package else parts[:-1]     # __package__ of the importer
    if level > 1:
        drop = level - 1
        base = base[:-drop] if drop <= len(base) else []
    if module:
        return ".".join([*base, *module.split(".")])
    return ".".join(base)


def build_architecture(repo_root: Path, package_dir: Path) -> ArchitectureGraph:
    """Parse imports across ``package_dir`` and build the dependency graph."""
    repo_root = repo_root.resolve()
    package_dir = package_dir.resolve()
    root_pkg = package_dir.name

    files = _iter_module_files(package_dir)
    # Pass 1: register every module so import targets can be resolved.
    graph = ArchitectureGraph(root_package=root_pkg)
    for f in files:
        rel = f.relative_to(repo_root)
        name, is_pkg = _module_name(rel)
        if not name:
            continue
        graph.nodes[name] = ModuleNode(
            name=name,
            package=name.rsplit(".", 1)[0] if "." in name else name,
            rel_path=rel.as_posix(),
            is_package=is_pkg,
        )
    internal = set(graph.nodes)
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    repo_local = _repo_local_top_levels(repo_root, root_pkg)

    # Pass 2: extract import edges.
    for f in files:
        rel = f.relative_to(repo_root)
        current, current_is_pkg = _module_name(rel)
        if current not in graph.nodes:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, ValueError):
            continue
        graph.external.setdefault(current, set())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _record_target(graph, current, alias.name, internal, stdlib, root_pkg, repo_local)
            elif isinstance(node, ast.ImportFrom):
                is_relative = bool(node.level and node.level > 0)
                base = (
                    _resolve_from_base(current, node.level, node.module, current_is_pkg)
                    if is_relative
                    else (node.module or "")
                )
                if not base:
                    continue
                # Each imported name may itself be a submodule; names that are
                # not submodules depend on `base`'s __init__/module.
                any_nonsubmodule = False
                for alias in node.names:
                    sub = f"{base}.{alias.name}"
                    if sub in internal:
                        _add_internal_edge(graph, current, sub)
                    else:
                        any_nonsubmodule = True
                if any_nonsubmodule:
                    if is_relative:
                        # Relative imports are intra-package by definition — only
                        # ever an internal edge, never an external dependency.
                        target = _deepest_internal(base, internal)
                        if target:
                            _add_internal_edge(graph, current, target)
                    else:
                        _record_target(graph, current, base, internal, stdlib, root_pkg, repo_local)
    return graph


def _repo_local_top_levels(repo_root: Path, root_pkg: str) -> set[str]:
    """Top-level importable names living in the repo but OUTSIDE the mapped package.

    A top-level ``build_team.py`` (or a sibling package) imported by the mapped
    package is repo-local, not a third-party PyPI dependency; distinguishing the
    two keeps the external-dependency list honest.
    """
    out: set[str] = set()
    try:
        children = list(repo_root.iterdir())
    except OSError:
        return out
    for p in children:
        if p.name == root_pkg or p.name.startswith("."):
            continue
        if p.suffix == ".py" and p.is_file():
            out.add(p.stem)
        elif p.is_dir() and (p / "__init__.py").is_file():
            out.add(p.name)
    return out


def _add_internal_edge(graph: ArchitectureGraph, src: str, dst: str) -> None:
    if dst != src:
        graph.edges.add((src, dst))


def _record_target(
    graph: ArchitectureGraph,
    current: str,
    dotted: str,
    internal: set[str],
    stdlib: frozenset[str],
    root_pkg: str,
    repo_local: set[str],
) -> None:
    """Classify one imported dotted name: internal edge, repo-local, or external."""
    top = dotted.split(".", 1)[0]
    if top == root_pkg:
        target = _deepest_internal(dotted, internal)
        if target:
            _add_internal_edge(graph, current, target)
        return
    if not top or top in stdlib:
        return
    if top in repo_local:
        graph.repo_local.setdefault(current, set()).add(top)
    else:
        graph.external.setdefault(current, set()).add(top)


def generate_architecture_document(
    repo_root: Path, package_dir: Path | None = None
) -> str | None:
    """Build the graph and return the full Markdown document.

    Returns None when no importable package is found under ``repo_root``.
    """
    repo_root = repo_root.resolve()
    pkg = package_dir or discover_package_root(repo_root)
    if pkg is None:
        return None
    graph = build_architecture(repo_root, pkg)
    if not graph.nodes:
        return None
    return graph.to_markdown_document()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agentteams.architecture",
        description="Build a directed module-dependency map of a Python package.",
    )
    parser.add_argument("repo_root", metavar="REPO_ROOT", help="Repository root.")
    parser.add_argument("--package", default=None,
                        help="Package directory to map (default: auto-detect).")
    parser.add_argument("--format", choices=["markdown", "mermaid", "dot", "json"],
                        default="markdown", help="Output format (default: markdown).")
    parser.add_argument("--output", "-o", default=None,
                        help="Write to FILE instead of stdout.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.is_dir():
        print(f"Error: not a directory: {repo_root}", file=sys.stderr)
        return 1
    pkg = Path(args.package).resolve() if args.package else discover_package_root(repo_root)
    if pkg is None:
        print(f"Error: no importable package found under {repo_root}", file=sys.stderr)
        return 1

    graph = build_architecture(repo_root, pkg)
    if not graph.nodes:
        print(f"Error: no modules found in {pkg}", file=sys.stderr)
        return 1

    if args.format == "markdown":
        output = graph.to_markdown_document()
    elif args.format == "mermaid":
        output = "```mermaid\n" + graph.to_mermaid() + "\n```"
    elif args.format == "dot":
        output = graph.to_dot()
    else:
        output = graph.to_json()

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Architecture map written to {out_path}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
