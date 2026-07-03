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
        """Sorted union of external top-level dependency names."""
        out: set[str] = set()
        for deps in self.external.values():
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
        return json.dumps(
            {
                "root_package": self.root_package,
                "modules": {
                    name: {
                        "package": node.package,
                        "path": node.rel_path,
                        "is_package": node.is_package,
                        "imports_internal": self.module_adjacency().get(name, []),
                        "external": sorted(self.external.get(name, set())),
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
        lines += [
            "",
            "---",
            "",
            "## External Dependencies",
            "",
            (", ".join(f"`{e}`" for e in externals) if externals
             else "_None detected (standard library only)._"),
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


def discover_package_root(repo_root: Path) -> Path | None:
    """Return the repo's primary importable package directory, or None.

    A package directory is a top-level child of ``repo_root`` that contains an
    ``__init__.py``. When several exist, prefer one whose name matches the repo
    directory name; otherwise the first in sorted order. ``tests`` and other
    excluded names are never chosen.
    """
    candidates = sorted(
        d for d in repo_root.iterdir()
        if d.is_dir()
        and d.name not in _EXCLUDE_DIRS
        and d.name != "tests"
        and not d.name.startswith(".")
        and (d / "__init__.py").is_file()
    )
    if not candidates:
        return None
    for c in candidates:
        if c.name == repo_root.name:
            return c
    return candidates[0]


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
    """All mappable ``.py`` files under ``package_dir`` (excludes _EXCLUDE_DIRS)."""
    out: list[Path] = []
    for path in package_dir.rglob("*.py"):
        if any(part in _EXCLUDE_DIRS for part in path.parts):
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


def _resolve_from_base(current: str, level: int, module: str | None) -> str:
    """Resolve the base module of a relative ``from ... import``.

    ``current`` is the importing module's dotted name. ``level`` is the number
    of leading dots. Returns the absolute base package/module the import targets.
    """
    parts = current.split(".")
    base = parts[:-1]                 # package of the current module
    if level > 1:
        base = base[: -(level - 1)] if level - 1 <= len(base) else []
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

    # Pass 2: extract import edges.
    for f in files:
        rel = f.relative_to(repo_root)
        current, _ = _module_name(rel)
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
                    _record_target(graph, current, alias.name, internal, stdlib, root_pkg)
            elif isinstance(node, ast.ImportFrom):
                base = (
                    _resolve_from_base(current, node.level, node.module)
                    if node.level and node.level > 0
                    else (node.module or "")
                )
                if not base:
                    continue
                # Each imported name may itself be a submodule.
                matched_submodule = False
                for alias in node.names:
                    sub = f"{base}.{alias.name}"
                    if sub in internal:
                        _add_internal_edge(graph, current, sub)
                        matched_submodule = True
                if not matched_submodule:
                    _record_target(graph, current, base, internal, stdlib, root_pkg)
    return graph


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
) -> None:
    """Classify one imported dotted name as an internal edge or external dep."""
    top = dotted.split(".", 1)[0]
    if top == root_pkg:
        target = _deepest_internal(dotted, internal)
        if target:
            _add_internal_edge(graph, current, target)
        return
    if top and top not in stdlib:
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
