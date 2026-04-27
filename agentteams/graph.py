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
    python -m src.graph /path/to/.github/agents/
    python -m src.graph /path/to/.github/agents/ --format mermaid
    python -m src.graph /path/to/.github/agents/ --format dot
    python -m src.graph /path/to/.github/agents/ --format json

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
        agent_type:    Categorical type: governance, domain, workstream-expert,
                       tool-specialist, or unknown.
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

        Returns:
            Adjacency dict where values are sorted lists of target slugs.
        """
        adj: dict[str, list[str]] = {slug: [] for slug in self.nodes}
        for edge in self.edges:
            if edge.source in adj:
                if edge.target not in adj[edge.source]:
                    adj[edge.source].append(edge.target)
        for targets in adj.values():
            targets.sort()
        return adj

    def reverse_adjacency(self) -> dict[str, list[str]]:
        """Return a dict mapping each slug to its list of direct predecessors.

        Returns:
            Reverse adjacency dict where values are sorted lists of source slugs.
        """
        radj: dict[str, list[str]] = {slug: [] for slug in self.nodes}
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
                    for e in self.edges
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
            "    classDef expert    fill:#fff8e8,stroke:#ccaa44,color:#000",
            "    classDef tool      fill:#ffe8e8,stroke:#cc6666,color:#000",
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
        for edge in self.edges:
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
        _TYPE_COLORS = {
            "governance": "#e8e8ff",
            "domain": "#e8ffe8",
            "workstream_expert": "#fff8e8",
            "tool_specialist": "#ffe8e8",
            "unknown": "#f5f5f5",
        }
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
        for edge in self.edges:
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

    def to_markdown_document(self) -> str:
        """Render a full Markdown document containing the Mermaid graph and tables.

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
            "```mermaid",
            self.to_mermaid(),
            "```",
            "",
            "---",
            "",
            "## Node Legend",
            "",
            "| Colour | Agent Type |",
            "| --- | --- |",
            "| ![governance](https://via.placeholder.com/12/e8e8ff/e8e8ff) Blue | Governance |",
            "| ![domain](https://via.placeholder.com/12/e8ffe8/e8ffe8) Green | Domain |",
            "| ![expert](https://via.placeholder.com/12/fff8e8/fff8e8) Yellow | Workstream Expert |",
            "| ![tool](https://via.placeholder.com/12/ffe8e8/ffe8e8) Red | Tool Specialist |",
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
            "## DOT Source",
            "",
            "Save the block below as `pipeline-graph.dot` and run",
            "`dot -Tsvg pipeline-graph.dot -o pipeline-graph.svg` to produce an SVG.",
            "",
            "```dot",
            self.to_dot(),
            "```",
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

#: YAML 'agents: [slug, ...]' — single-line list form
_AGENTS_LIST_RE = re.compile(r"""['"]([\w][\w-]*)['"]\s*[,\]]""")

#: 'agent: slug' (inside a handoffs block)
_HANDOFF_AGENT_RE = re.compile(r"^\s{2,}agent:\s*['\"]?([\w][\w-]*)['\"]?", re.MULTILINE)

#: 'label: "..."' or 'label: unquoted text' (inside a handoffs YAML sequence item)
_HANDOFF_LABEL_RE = re.compile(r"^\s+-\s+label:\s*['\"]?([^'\"\n]+)['\"]?\s*$", re.MULTILINE)

#: 'name: "<role> — <project>"'
_NAME_RE = re.compile(r"^name:\s*['\"]?(.+?)['\"]?\s*$", re.MULTILINE)

#: 'description: "..."'
_DESC_RE = re.compile(r'^description:\s*["\']?(.+?)["\']?\s*$', re.MULTILINE)

#: 'user-invokable: true/false'
_INVOKABLE_RE = re.compile(r"^user-invokable:\s*(true|false)", re.MULTILINE | re.IGNORECASE)

#: 'tools: [...]'
_TOOLS_RE = re.compile(r"tools\s*:\s*\[([^\]]*)\]")

#: 'agents: [...]' (the whole list line, not individual slugs)
_AGENTS_BLOCK_LINE_RE = re.compile(r"^agents:", re.MULTILINE)


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

        # Handoff edges: walk handoffs block, pairing label + agent slug
        handoff_agents = _HANDOFF_AGENT_RE.findall(yaml_block)
        handoff_labels = _HANDOFF_LABEL_RE.findall(yaml_block)
        for i, target in enumerate(handoff_agents):
            if target not in graph.nodes:
                continue
            label = handoff_labels[i] if i < len(handoff_labels) else None
            graph.edges.append(GraphEdge(
                source=slug,
                target=target,
                edge_type="handoff",
                label=label,
            ))

        # agents-list edges: slugs declared in 'agents: [...]'
        agents_block_match = _AGENTS_BLOCK_LINE_RE.search(yaml_block)
        if agents_block_match:
            # Find the inline list portion starting from 'agents:'
            tail = yaml_block[agents_block_match.start():]
            # Collect all quoted slugs until the first non-list line
            end_of_list = re.search(r"\n[a-z]", tail)
            list_text = tail[:end_of_list.start()] if end_of_list else tail
            for target in _AGENTS_LIST_RE.findall(list_text):
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


# ---------------------------------------------------------------------------
# YAML parsing helpers
# ---------------------------------------------------------------------------

def _split_yaml(content: str) -> tuple[str | None, str]:
    """Split file content into YAML front matter and body.

    Args:
        content: Full file text.

    Returns:
        Tuple of (yaml_block, body). yaml_block is None if no front matter.
    """
    if not content.startswith("---"):
        return None, content
    end = content.find("---", 3)
    if end == -1:
        return None, content
    return content[3:end], content[end + 3:]


def _extract_name(yaml_block: str, fallback_slug: str) -> str:
    """Extract the display name from YAML, stripping the ' — Project' suffix.

    Args:
        yaml_block:    YAML front matter text.
        fallback_slug: Used when no name field is found.

    Returns:
        Display name string (role part only, without project suffix).
    """
    m = _NAME_RE.search(yaml_block)
    if not m:
        return fallback_slug
    raw = m.group(1).strip().strip("'\"")
    # Strip ' — ProjectName' or ' - ProjectName' suffix
    if " — " in raw:
        return raw.split(" — ")[0].strip()
    if " - " in raw:
        return raw.split(" - ")[0].strip()
    return raw


def _extract_user_invokable(yaml_block: str) -> bool:
    """Extract the user-invokable boolean from YAML.

    Args:
        yaml_block: YAML front matter text.

    Returns:
        True if user-invokable is 'true', False otherwise.
    """
    m = _INVOKABLE_RE.search(yaml_block)
    if not m:
        return False
    return m.group(1).lower() == "true"


def _extract_tools(yaml_block: str) -> list[str]:
    """Extract the tools list from YAML.

    Args:
        yaml_block: YAML front matter text.

    Returns:
        List of tool name strings.
    """
    m = _TOOLS_RE.search(yaml_block)
    if not m:
        return []
    return [t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()]


def _classify_agent_type(slug: str) -> str:
    """Classify an agent slug into its taxonomy tier.

    Args:
        slug: Machine-readable agent identifier.

    Returns:
        One of: ``"governance"``, ``"domain"``, ``"workstream_expert"``,
        ``"tool_specialist"``, ``"unknown"``.
    """
    _GOVERNANCE_SLUGS = frozenset({
        "orchestrator", "navigator", "security", "code-hygiene", "adversarial",
        "conflict-auditor", "conflict-resolution", "cleanup", "agent-updater",
        "agent-refactor", "repo-liaison", "git-operations", "team-builder",
    })
    _DOMAIN_SLUGS = frozenset({
        "primary-producer", "quality-auditor", "style-guardian", "technical-validator",
        "format-converter", "output-compiler", "reference-manager", "visual-designer",
        "cohesion-repairer",
    })
    if slug in _GOVERNANCE_SLUGS:
        return "governance"
    if slug in _DOMAIN_SLUGS:
        return "domain"
    if slug.endswith("-expert"):
        return "workstream_expert"
    if slug.startswith("tool-"):
        return "tool_specialist"
    return "unknown"


def _mermaid_id(slug: str) -> str:
    """Convert a slug to a valid Mermaid node identifier.

    Args:
        slug: Agent slug, possibly containing hyphens.

    Returns:
        Mermaid-safe identifier (hyphens replaced with underscores).
    """
    return slug.replace("-", "_")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _load_from_disk(agents_dir: Path) -> dict[str, str]:
    """Load all .agent.md files from an agents directory.

    Args:
        agents_dir: Path to the .github/agents/ directory.

    Returns:
        Dict mapping relative path → file content.
    """
    file_map: dict[str, str] = {}
    if not agents_dir.exists():
        return file_map
    for path in agents_dir.rglob("*.agent.md"):
        rel = str(path.relative_to(agents_dir))
        try:
            file_map[rel] = path.read_text(encoding="utf-8")
        except OSError:
            pass
    return file_map


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Usage:
        python -m src.graph /path/to/.github/agents/
        python -m src.graph /path/to/.github/agents/ --format mermaid
        python -m src.graph /path/to/.github/agents/ --format dot
        python -m src.graph /path/to/.github/agents/ --format json
        python -m src.graph /path/to/.github/agents/ --output pipeline-graph.md

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success, 1 = error).
    """
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m src.graph",
        description="Build a directed graph of the agent team topology.",
    )
    parser.add_argument(
        "agents_dir",
        metavar="AGENTS_DIR",
        help="Path to the .github/agents/ directory to analyse.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "mermaid", "dot", "json"],
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
        for content in file_map.values():
            yaml_block, _ = _split_yaml(content)
            if yaml_block:
                m = _NAME_RE.search(yaml_block)
                if m:
                    raw = m.group(1).strip().strip("'\"")
                    if " — " in raw:
                        project_name = raw.split(" — ", 1)[1].strip()
                        break
                    if " - " in raw:
                        project_name = raw.split(" - ", 1)[1].strip()
                        break

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
