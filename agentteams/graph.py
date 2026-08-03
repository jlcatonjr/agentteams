"""
graph.py — Directed graph inference for agent team topology.

Parses generated agent files (from in-memory rendered content or disk) to
build a directed graph of the agent team.  Each node is an agent; each edge
is a declared connection via YAML ``handoffs:`` or ``agents:`` list entries.

Outputs
-------
* Mermaid flowchart — embeddable in Markdown; rendered by GitHub and VS Code
* DOT (Graphviz) source — for external tooling
* JSON adjacency list — for programmatic use
* Human-readable Markdown document combining all of the above

CLI usage
---------
    python -m agentteams.graph /path/to/.github/agents/
    python -m agentteams.graph /path/to/.github/agents/ --format mermaid
    python -m agentteams.graph /path/to/.github/agents/ --format dot
    python -m agentteams.graph /path/to/.github/agents/ --format json

The graph document is regenerated automatically on every ``build_team.py``
run and written to ``references/pipeline-graph.md``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams._utils import _split_yaml_front_matter  # shared YAML splitter (MAP-17)
from agentteams import svg_render

# Front-matter extraction lives in graph_inputs.py (CH-07 carve): this module owns the graph
# MODEL and its rendering; that one owns the step before — reading an agent file's front matter
# into the values a node is built from. Re-exported so every existing
# `from agentteams.graph import _extract_tools` still resolves.
from agentteams.graph_inputs import (  # noqa: F401
    _HANDOFF_AGENT_KEY_RE,
    _HANDOFF_ITEM_BOUNDARY_RE,
    _HANDOFF_LABEL_KEY_RE,
    _HANDOFFS_SECTION_RE,
    _INVOKABLE_RE,
    _NAME_RE,
    _classify_agent_type,
    _extract_agents_list,
    _extract_name,
    _extract_tools,
    _extract_user_invokable,
    _extract_yaml_sequence,
    _mermaid_id,
    _parse_handoff_blocks,
    _split_yaml,
)

#: Agent-type → (fill, stroke) palette shared by the topology SVGs.
_SVG_PALETTE = {
    "governance": ("#e8e8ff", "#6666cc"),
    "domain": ("#e8ffe8", "#66aa66"),
    "workstream_expert": ("#fff8e8", "#ccaa44"),
    "tool_specialist": ("#ffe8e8", "#cc6666"),
    "unknown": ("#f5f5f5", "#999999"),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

#: Agent type classification (matches agent taxonomy)
AGENT_TYPES = {
    # Governance
    "orchestrator", "navigator", "security", "code-hygiene", "adversarial",
    "conflict-auditor", "conflict-resolution", "cleanup", "agent-updater",
    "agent-refactor", "repo-liaison", "git-operations", "team-builder",
    # Domain (archetype names)
    "primary-producer", "quality-auditor", "style-guardian", "technical-validator",
    "format-converter", "output-compiler", "reference-manager", "visual-designer",
    "cohesion-repairer",
}


@dataclass
class AgentNode:
    """Metadata for a single agent node in the team graph.

    Attributes:
        slug:          Machine-readable identifier derived from filename.
        display_name:  Human-readable name from YAML ``name:`` field.
        agent_type:    Categorical type: governance, domain, workstream_expert,
                       tool_specialist, or unknown.
        user_invokable: True if the agent can be invoked directly by a user.
        tools:         Declared tool list from YAML.
    """

    slug: str
    display_name: str
    agent_type: str
    user_invokable: bool
    tools: list[str]


@dataclass
class GraphEdge:
    """A directed edge between two agent nodes.

    Attributes:
        source:    Slug of the originating agent.
        target:    Slug of the target agent.
        edge_type: ``"handoff"`` (declared in ``handoffs:`` YAML block) or
                   ``"agents-list"`` (declared in ``agents:`` YAML list).
        label:     Optional human-readable label (from handoff ``label:`` key).
    """

    source: str
    target: str
    edge_type: str  # "handoff" | "agents-list"
    label: str | None


@dataclass
class TeamGraph:
    """Complete directed graph of the agent team.

    Attributes:
        project_name: Name of the project this graph belongs to.
        nodes:        Dict mapping slug → AgentNode.
        edges:        List of directed GraphEdge instances.
    """

    project_name: str
    nodes: dict[str, AgentNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    def adjacency(self) -> dict[str, list[str]]:
        """Return a dict mapping each slug to its list of direct successors.

        Keys are emitted in sorted slug order so the serialised adjacency (JSON
        block) is independent of the input ``file_map`` iteration order — the
        same determinism guarantee as :meth:`ordered_edges`.

        Returns:
            Adjacency dict where values are sorted lists of target slugs.
        """
        adj: dict[str, list[str]] = {slug: [] for slug in sorted(self.nodes)}
        for edge in self.edges:
            if edge.source in adj:
                if edge.target not in adj[edge.source]:
                    adj[edge.source].append(edge.target)
        for targets in adj.values():
            targets.sort()
        return adj

    def reverse_adjacency(self) -> dict[str, list[str]]:
        """Return a dict mapping each slug to its list of direct predecessors.

        Keys are emitted in sorted slug order (see :meth:`adjacency`).

        Returns:
            Reverse adjacency dict where values are sorted lists of source slugs.
        """
        radj: dict[str, list[str]] = {slug: [] for slug in sorted(self.nodes)}
        for edge in self.edges:
            if edge.target in radj:
                if edge.source not in radj[edge.target]:
                    radj[edge.target].append(edge.source)
        for sources in radj.values():
            sources.sort()
        return radj

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def ordered_edges(self) -> list[GraphEdge]:
        """Return edges in a deterministic, source-grouped order.

        Edge emission order must not depend on the iteration order of the input
        ``file_map`` — otherwise the same team renders two different (but
        equivalent) graphs depending on whether it was built from the in-memory
        render pipeline (``dict(final)``, render order) or from disk
        (``rglob``, filesystem order). The commit-triggered refresh
        (:mod:`agentteams.git_hooks`) builds from disk, so without a stable
        order it would rewrite ``references/pipeline-graph.md`` with meaningless
        edge reorderings on every commit and never agree with ``--update``.

        Sort key: source slug, then edge kind (handoffs before agents-list),
        then target slug, then label. This keeps each source's out-edges grouped
        (as before) while making both the grouping and within-group order fully
        deterministic.
        """
        _kind_rank = {"handoff": 0, "agents-list": 1}
        return sorted(
            self.edges,
            key=lambda e: (
                e.source,
                _kind_rank.get(e.edge_type, 9),
                e.target,
                e.label or "",
            ),
        )

    def to_json(self) -> str:
        """Serialise the graph to a JSON adjacency list.

        Returns:
            JSON string with keys: project_name, nodes, edges, adjacency.
        """
        return json.dumps(
            {
                "project_name": self.project_name,
                "nodes": {
                    slug: {
                        "display_name": node.display_name,
                        "agent_type": node.agent_type,
                        "user_invokable": node.user_invokable,
                        "tools": node.tools,
                    }
                    for slug, node in sorted(self.nodes.items())
                },
                "edges": [
                    {
                        "source": e.source,
                        "target": e.target,
                        "edge_type": e.edge_type,
                        "label": e.label,
                    }
                    for e in self.ordered_edges()
                ],
                "adjacency": self.adjacency(),
            },
            indent=2,
        )

    def to_mermaid(self) -> str:
        """Render the graph as a Mermaid flowchart (LR direction).

        Nodes are colour-coded by type.  Handoff edges are solid arrows;
        agents-list edges are dashed arrows labelled with the edge type.

        Returns:
            Mermaid ``flowchart LR`` block as a plain string (no code fences).
        """
        lines = ["flowchart LR"]

        # Style classes by agent type
        lines += [
            "    classDef governance fill:#e8e8ff,stroke:#6666cc,color:#000",
            "    classDef domain    fill:#e8ffe8,stroke:#66aa66,color:#000",
            "    classDef workstream_expert fill:#fff8e8,stroke:#ccaa44,color:#000",
            "    classDef tool_specialist   fill:#ffe8e8,stroke:#cc6666,color:#000",
            "    classDef unknown   fill:#f5f5f5,stroke:#999,color:#000",
        ]

        # Node declarations — sanitise display name for Mermaid labels
        for slug, node in sorted(self.nodes.items()):
            safe_id = _mermaid_id(slug)
            safe_label = node.display_name.replace('"', "'")
            lines.append(f'    {safe_id}["{safe_label}"]')
            lines.append(f"    class {safe_id} {node.agent_type.replace('-', '_').replace(' ', '_')}")

        # Edge declarations — deduplicate by (source, target, type)
        seen: set[tuple[str, str, str]] = set()
        for edge in self.ordered_edges():
            key = (edge.source, edge.target, edge.edge_type)
            if key in seen:
                continue
            seen.add(key)
            src_id = _mermaid_id(edge.source)
            tgt_id = _mermaid_id(edge.target)
            if edge.edge_type == "handoff":
                arrow = "-->"
                label_part = f'|"{edge.label}"|' if edge.label else ""
            else:
                arrow = "-.->"
                label_part = ""
            lines.append(f"    {src_id} {arrow}{label_part} {tgt_id}")

        return "\n".join(lines)

    def to_dot(self) -> str:
        """Render the graph as a Graphviz DOT source file.

        Returns:
            DOT source as a plain string.
        """
        _TYPE_COLORS = {k: v[0] for k, v in _SVG_PALETTE.items()}
        lines = [
            f'digraph "{self.project_name} Agent Team" {{',
            "    rankdir=LR;",
            '    node [fontname="Helvetica", fontsize=11, shape=box, style="rounded,filled"];',
            '    edge [fontsize=9];',
        ]

        for slug, node in sorted(self.nodes.items()):
            color = _TYPE_COLORS.get(node.agent_type, "#f5f5f5")
            safe_label = node.display_name.replace('"', "'")
            lines.append(
                f'    "{slug}" [label="{safe_label}", fillcolor="{color}"];'
            )

        seen: set[tuple[str, str]] = set()
        for edge in self.ordered_edges():
            pair = (edge.source, edge.target)
            if pair in seen:
                continue
            seen.add(pair)
            style = 'style=solid' if edge.edge_type == "handoff" else 'style=dashed'
            label = f', label="{edge.label}"' if edge.label else ""
            lines.append(
                f'    "{edge.source}" -> "{edge.target}" [{style}{label}];'
            )

        lines.append("}")
        return "\n".join(lines)

    def _svg_spec(self, *, handoff_only: bool = False):
        """Build the (nodes, edges) SVG spec; optionally handoff edges only."""
        nodes = [
            svg_render.SvgNode(id=slug, label=node.display_name, kind=node.agent_type)
            for slug, node in sorted(self.nodes.items())
        ]
        seen: set[tuple[str, str]] = set()
        edges: list[svg_render.SvgEdge] = []
        for edge in self.ordered_edges():
            if handoff_only and edge.edge_type != "handoff":
                continue
            pair = (edge.source, edge.target)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(svg_render.SvgEdge(src=edge.source, dst=edge.target))
        return nodes, edges

    def to_svg(self) -> str:
        """Full agent-topology diagram (handoff + agents-list edges) as SVG."""
        nodes, edges = self._svg_spec()
        return svg_render.render_svg(
            nodes, edges, palette=_SVG_PALETTE,
            title=f"{self.project_name} — Agent Team Topology",
        )

    def to_handoff_svg(self) -> str:
        """Handoff-only control-flow backbone (agents-list edges omitted) as SVG."""
        nodes, edges = self._svg_spec(handoff_only=True)
        return svg_render.render_svg(
            nodes, edges, palette=_SVG_PALETTE,
            title=f"{self.project_name} — Handoff Backbone",
        )

    def to_markdown_document(self) -> str:
        """Render a full Markdown document referencing the SVG diagram and tables.

        The document is written to ``references/pipeline-graph.md`` by the
        pipeline and kept up-to-date on every build.  The navigator references
        it for structural queries.

        Returns:
            Complete Markdown document as a string.
        """
        slug_list = sorted(self.nodes.keys())
        adj = self.adjacency()
        radj = self.reverse_adjacency()

        lines = [
            f"# {self.project_name} — Agent Team Topology",
            "",
            "> **Auto-generated.** Regenerated on every `build_team.py` run.",
            "> Do not edit manually — changes will be overwritten.",
            "",
            "---",
            "",
            "## Team Topology Graph",
            "",
            f"![{self.project_name} agent team topology](pipeline-graph.svg)",
            "",
            "The handoff-only control-flow backbone (agents-list edges omitted):",
            "",
            f"![{self.project_name} handoff backbone](pipeline-handoffs.svg)",
            "",
            "---",
            "",
            "## Node Legend",
            "",
            "| Colour | Agent Type |",
            "| --- | --- |",
            '| <svg width="12" height="12"><rect width="12" height="12" fill="#e8e8ff" stroke="#6666cc"/></svg> Blue-lavender | Governance |',
            '| <svg width="12" height="12"><rect width="12" height="12" fill="#e8ffe8" stroke="#66aa66"/></svg> Green | Domain |',
            '| <svg width="12" height="12"><rect width="12" height="12" fill="#fff8e8" stroke="#ccaa44"/></svg> Yellow | Workstream Expert |',
            '| <svg width="12" height="12"><rect width="12" height="12" fill="#ffe8e8" stroke="#cc6666"/></svg> Red-pink | Tool Specialist |',
            "",
            "---",
            "",
            "## Agent Roster",
            "",
            "| Agent | Type | User-Invokable | Tools |",
            "| --- | --- | --- | --- |",
        ]

        for slug in slug_list:
            node = self.nodes[slug]
            invokable = "Yes" if node.user_invokable else "No"
            tools = ", ".join(node.tools) if node.tools else "—"
            lines.append(
                f"| `{slug}` | {node.agent_type} | {invokable} | {tools} |"
            )

        lines += [
            "",
            "---",
            "",
            "## Adjacency List",
            "",
            "| Agent | Receives from | Hands off to |",
            "| --- | --- | --- |",
        ]

        for slug in slug_list:
            incoming = ", ".join(f"`{s}`" for s in radj.get(slug, [])) or "—"
            outgoing = ", ".join(f"`{s}`" for s in adj.get(slug, [])) or "—"
            lines.append(f"| `{slug}` | {incoming} | {outgoing} |")

        lines += [
            "",
            "---",
            "",
            "## Diagram Source",
            "",
            "<details>",
            "<summary>Mermaid &amp; DOT source for the topology diagram above</summary>",
            "",
            "```mermaid",
            self.to_mermaid(),
            "```",
            "",
            "```dot",
            self.to_dot(),
            "```",
            "",
            "</details>",
            "",
            "---",
            "",
            "## JSON Adjacency",
            "",
            "```json",
            self.to_json(),
            "```",
        ]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------






#: 'description: "..."'
_DESC_RE = re.compile(r'^description:\s*["\']?(.+?)["\']?\s*$', re.MULTILINE)



def build_graph(
    file_map: dict[str, str],
    project_name: str = "",
) -> TeamGraph:
    """Infer the directed agent team graph from rendered file content.

    Parses YAML front matter in every ``.agent.md`` file to extract:
    - Node metadata (name, type, user-invokable, tools)
    - Directed edges from ``handoffs:`` declarations (source → target)
    - Directed edges from ``agents:`` list entries (source → target)

    Args:
        file_map:     Dict mapping relative path → file content.
        project_name: Project name to embed in the graph.

    Returns:
        Populated TeamGraph instance.
    """
    graph = TeamGraph(project_name=project_name)

    # Pass 1: collect all nodes
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue

        slug = Path(rel_path).stem.replace(".agent", "")
        yaml_block, _ = _split_yaml(content)
        if yaml_block is None:
            yaml_block = ""

        display_name = _extract_name(yaml_block, slug)
        agent_type = _classify_agent_type(slug)
        user_invokable = _extract_user_invokable(yaml_block)
        tools = _extract_tools(yaml_block)

        graph.nodes[slug] = AgentNode(
            slug=slug,
            display_name=display_name,
            agent_type=agent_type,
            user_invokable=user_invokable,
            tools=tools,
        )

    # Pass 2: collect all edges (only between known nodes)
    for rel_path, content in file_map.items():
        if not rel_path.endswith(".agent.md"):
            continue
        if "references/" in rel_path:
            continue

        slug = Path(rel_path).stem.replace(".agent", "")
        yaml_block, _ = _split_yaml(content)
        if yaml_block is None:
            continue

        # Handoff edges: parse each handoff item as a unit so that label: and
        # agent: are correctly paired regardless of key order, and so that items
        # with no label: do not skew labels for subsequent items.
        for target, label in _parse_handoff_blocks(yaml_block):
            if target not in graph.nodes:
                continue
            graph.edges.append(GraphEdge(
                source=slug,
                target=target,
                edge_type="handoff",
                label=label,
            ))

        # agents-list edges: slugs declared in 'agents:' (inline or block form)
        for target in _extract_yaml_sequence(yaml_block, "agents"):
            if target not in graph.nodes or target == slug:
                continue
            graph.edges.append(GraphEdge(
                source=slug,
                target=target,
                edge_type="agents-list",
                label=None,
            ))

    return graph


def generate_graph_document(
    file_map: dict[str, str],
    project_name: str = "",
) -> str:
    """Build the directed graph and return the full Markdown document.

    This is the main entry point called by the pipeline.

    Args:
        file_map:     Dict mapping relative path → file content.
        project_name: Project name for the document heading.

    Returns:
        Complete Markdown document as a string.
    """
    graph = build_graph(file_map, project_name=project_name)
    return graph.to_markdown_document()


def generate_graph_svg(
    file_map: dict[str, str],
    project_name: str = "",
) -> str:
    """Build the graph and return the full agent-topology SVG document."""
    graph = build_graph(file_map, project_name=project_name)
    return graph.to_svg()


def generate_graph_handoff_svg(
    file_map: dict[str, str],
    project_name: str = "",
) -> str:
    """Build the graph and return the handoff-only backbone SVG document."""
    graph = build_graph(file_map, project_name=project_name)
    return graph.to_handoff_svg()


# ---------------------------------------------------------------------------
# YAML parsing helpers
# ---------------------------------------------------------------------------



















# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def load_from_disk(agents_dir: Path) -> dict[str, str]:
    """Load all .agent.md files from an agents directory.

    Shared by the CLI entry point and the commit-triggered refresh
    (:mod:`agentteams.git_hooks`).

    Args:
        agents_dir: Path to the .github/agents/ directory.

    Returns:
        Dict mapping relative path → file content.
    """
    file_map: dict[str, str] = {}
    if not agents_dir.exists():
        return file_map
    for path in agents_dir.rglob("*.agent.md"):
        rel_parts = path.relative_to(agents_dir).parts
        # Skip dot-prefixed subdirectories (e.g. `.agentteams-backups/`): they
        # hold timestamped copies of prior agents and would otherwise be parsed
        # as live nodes, polluting the graph with stale/ghost agents. The file's
        # own name is never dot-prefixed for real agents, so only intermediate
        # directory components are checked.
        if any(part.startswith(".") for part in rel_parts[:-1]):
            continue
        try:
            file_map["/".join(rel_parts)] = path.read_text(encoding="utf-8")
        except OSError:
            pass
    return file_map


# Backward-compatible private alias (pre-existing callers / tests).
_load_from_disk = load_from_disk


def infer_project_name(file_map: dict[str, str]) -> str:
    """Infer a project name from agent ``name:`` fields.

    Agent names follow ``<Role> — <Project>`` (em dash) or ``<Role> - <Project>``
    (hyphen); the trailing segment is the project name. Returns ``""`` when no
    agent carries a splittable name. Shared by the CLI entry point and the
    commit-triggered refresh in :mod:`agentteams.git_hooks` so both derive the
    same heading.
    """
    for content in file_map.values():
        yaml_block, _ = _split_yaml(content)
        if not yaml_block:
            continue
        m = _NAME_RE.search(yaml_block)
        if not m:
            continue
        raw = m.group(1).strip().strip("'\"")
        if " — " in raw:
            return raw.split(" — ", 1)[1].strip()
        if " - " in raw:
            return raw.split(" - ", 1)[1].strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Usage:
        python -m agentteams.graph /path/to/.github/agents/
        python -m agentteams.graph /path/to/.github/agents/ --format mermaid
        python -m agentteams.graph /path/to/.github/agents/ --format dot
        python -m agentteams.graph /path/to/.github/agents/ --format json
        python -m agentteams.graph /path/to/.github/agents/ --output pipeline-graph.md

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success, 1 = error).
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m agentteams.graph",
        description="Build a directed graph of the agent team topology.",
    )
    parser.add_argument(
        "agents_dir",
        metavar="AGENTS_DIR",
        help="Path to the .github/agents/ directory to analyse.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "mermaid", "dot", "json", "svg"],
        default="markdown",
        help="Output format (default: markdown — full document).",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        default=None,
        help="Write output to FILE instead of stdout.",
    )
    parser.add_argument(
        "--project-name",
        default="",
        help="Project name for the graph heading.",
    )
    args = parser.parse_args(argv)

    agents_dir = Path(args.agents_dir)
    if not agents_dir.exists():
        print(f"Error: directory not found: {agents_dir}", file=sys.stderr)
        return 1

    file_map = _load_from_disk(agents_dir)
    if not file_map:
        print(f"Error: no .agent.md files found in {agents_dir}", file=sys.stderr)
        return 1

    project_name = args.project_name
    # Try to infer project name from a name: field if not provided
    if not project_name:
        project_name = infer_project_name(file_map)

    graph = build_graph(file_map, project_name=project_name)

    fmt = args.format
    if fmt == "markdown":
        output = graph.to_markdown_document()
    elif fmt == "mermaid":
        output = "```mermaid\n" + graph.to_mermaid() + "\n```"
    elif fmt == "dot":
        output = graph.to_dot()
    elif fmt == "json":
        output = graph.to_json()
    elif fmt == "svg":
        output = graph.to_svg()
    else:
        output = graph.to_markdown_document()

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Graph written to {out_path}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
