#!/usr/bin/env python3
"""gen_book_agent_team_structure.py — hierarchical graph of the agent team (book figure).

Ported from jlcatonjr/jameslcaton@bf7a81fe
`workingpapers/agentTeams.ATheoreticallyGroundedApproach/figures/agent_team_structure.py` when the book's canonical home
moved to agentteams (plan `book-researchteam-separation`, 2026-11-14). The graph definitions are
unchanged; only the output location (`docs_src/book/figures/`, SVG only) and the CLI changed. PDF figures
for the LaTeX edition will be produced from these SVGs by a planned `scripts/gen_book_latex.py`
(not yet in the repository).

Run: `python3 scripts/gen_book_agent_team_structure.py`  (requires graphviz `dot` on PATH and the `graphviz` Python package)

Original description:

    agent_team_structure.py
    -----------------------
    Generates a hierarchical graph of the agent team for
    "Agent Teams: A Theoretically Grounded Approach."

    Usage:
        python3 figures/agent_team_structure.py

    Output:
        figures/agent-team-structure.pdf   (default, via PDF renderer)
        figures/agent-team-structure.svg   (vector, also generated)

    The graph encodes three tiers:
      1. Orchestrator — special functional agent that manages all others
      2. Functional agents — governance (navigator distinguished as project-mapping
         specialist; remaining seven handle constitutional enforcement)
      3. Domain agents — production chain (Chapter 14)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import graphviz
except ImportError:  # pragma: no cover - exercised only without the dev tool installed
    sys.exit("gen_book: the 'graphviz' Python package is required (pip install graphviz)")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs_src" / "book" / "figures"



# ── palette ────────────────────────────────────────────────────────────────────
ORCH_BG      = "#1a3a5c"   # deep navy   — orchestrator
ORCH_FG      = "#ffffff"
NAV_BG       = "#2e6da4"   # medium blue — navigator (project-mapping specialist)
NAV_FG       = "#ffffff"
FUNC_BG      = "#4a90c4"   # steel blue  — other functional agents
FUNC_FG      = "#ffffff"
DOMAIN_CONTENT_BG = "#c8a951"   # warm amber gold  — content/voice/chapter domain agents
DOMAIN_CONTENT_FG = "#1a1a1a"
DOMAIN_TOOL_BG    = "#8fb8a8"   # muted sage green — tool-expertise domain agents
DOMAIN_TOOL_FG    = "#1a1a1a"
CLUSTER_COLOR= "#dddddd"

# ── data ───────────────────────────────────────────────────────────────────────
# Each tuple: (node_id, display_label, subtitle)
FUNC_AGENTS = [
    ("security",         "Security Agent",          "constitutional clearance"),
    ("adversarial",      "Adversarial Agent",        "presupposition critique"),
    ("conflict_auditor", "Conflict-Auditor",         "consistency verification"),
    ("conflict_res",     "Conflict-Resolution",      "contradiction repair"),
    ("code_hygiene",     "Code-Hygiene Auditor",     "technical accuracy"),
    ("agent_refactorer", "Agent-Refactorer",         "structural refactoring"),
    ("agent_updater",    "Agent-Updater",            "documentation sync"),
    ("cleanup",          "Cleanup Agent",            "artifact removal"),
]

# Each tuple: (node_id, display_label, subtitle, subtype)
# subtype: 'content' = project-domain expertise
#          'tool'    = technical-tool expertise
DOMAIN_AGENTS = [
    ("book_content",     "Book-Content Expert",      "chapter prose composition",                    "content"),
    ("expert_critic",    "Expert-Critic",            "prose-quality audit &amp; structural review",   "content"),
    ("argument_weaver",  "Argument-Weaver",          "within-section cohesion repair",               "content"),
    ("voice_style",      "Voice-Style Agent",        "voice calibration &amp; AI-tell audit",        "content"),
    ("html_builder",     "HTML-Document-Builder",    "validation &amp; HTML to LaTeX conversion",    "tool"),
    ("graphviz_expert",  "Graphviz Expert",          "publication-quality figure generation",        "tool"),
    ("latex_specialist", "LaTeX Specialist",         "manuscript compilation",                       "tool"),
    ("bibliography_mgr", "Bibliography-Manager",     "citation verification &amp; zero-fabrication", "content"),
    ("chapter_expert",   "Chapter-NN-Expert",        "per-chapter theoretical brief (x N)",          "content"),
]

# ── helpers ─────────────────────────────────────────────────────────────────────

def agent_label(display: str, subtitle: str) -> str:
    """Return an HTML-like table label for rounded-rectangle nodes.

    Args:
        display: Bold first line.
        subtitle: Smaller second line.

    Returns:
        A graphviz HTML-like label string.
    """
    return (
        f'<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2">'
        f'<TR><TD><B>{display}</B></TD></TR>'
        f'<TR><TD><FONT POINT-SIZE="16">{subtitle}</FONT></TD></TR>'
        f'</TABLE>>'
    )


def add_agent(g: graphviz.Digraph, node_id: str, display: str, subtitle: str,
              bg: str, fg: str) -> None:
    """Add a rounded agent node.

    Args:
        g: Graph to add the node to.
        node_id: Node id.
        display: Bold first label line.
        subtitle: Second label line.
        bg: Fill colour.
        fg: Font colour.
    """
    g.node(
        node_id,
        label=agent_label(display, subtitle),
        shape="box",
        style="filled,rounded",
        fillcolor=bg,
        fontcolor=fg,
        fontname="Helvetica",
        fontsize="18",
        margin="0.40,0.20",
    )


# ── graph ───────────────────────────────────────────────────────────────────────

def build() -> graphviz.Digraph:
    """Build the agent-team structure graph.

    Returns:
        The complete digraph, ready to render.
    """
    g = graphviz.Digraph(
        name="agent_team",
        comment="Agent Team Structure — Agent Teams: A Theoretically Grounded Approach",
    )
    g.attr(
        rankdir="TB",
        splines="true",
        nodesep="0.5",
        ranksep="0.9",
        bgcolor="white",
        fontname="Helvetica",
        pad="0.3",
    )

    # ── Orchestrator ────────────────────────────────────────────────────────────
    add_agent(
        g, "orchestrator",
        "Orchestrator",
        "routes all agents · holds coordinative knowledge · enforces constitutional rules",
        ORCH_BG, ORCH_FG,
    )

    # ── Navigator (special functional) ─────────────────────────────────────────
    add_agent(
        g, "navigator",
        "Navigator",
        "project mapping · authority-hierarchy lookup",
        NAV_BG, NAV_FG,
    )

    # ── Other functional agents — cluster ───────────────────────────────────────
    with g.subgraph(name="cluster_functional") as c:
        c.attr(
            label="Functional Agents — Governance Layer",
            style="filled,rounded",
            fillcolor="#eaf3fb",
            color=CLUSTER_COLOR,
            fontname="Helvetica",
            fontsize="16",
            fontcolor="#1a3a5c",
            margin="14",
        )
        for nid, display, subtitle in FUNC_AGENTS:
            add_agent(c, nid, display, subtitle, FUNC_BG, FUNC_FG)

    # ── Domain agents — cluster ──────────────────────────────────────────────────
    with g.subgraph(name="cluster_domain") as c:
        c.attr(
            label=r"Domain Agents — Production Chain  (Chapter 14)\lamber = project-domain expertise   sage = tool expertise",
            style="filled,rounded",
            fillcolor="#fdf6e3",
            color=CLUSTER_COLOR,
            fontname="Helvetica",
            fontsize="16",
            fontcolor="#5a3e00",
            margin="14",
        )
        for nid, display, subtitle, subtype in DOMAIN_AGENTS:
            bg = DOMAIN_CONTENT_BG if subtype == "content" else DOMAIN_TOOL_BG
            fg = DOMAIN_CONTENT_FG if subtype == "content" else DOMAIN_TOOL_FG
            add_agent(c, nid, display, subtitle, bg, fg)

    # ── Junction node for domain-agent trunk ─────────────────────────────────────
    # Visible tiny amber dot on its own rank just below the functional row.
    # Barycentric centering places it above the center of the domain row.
    g.node('dom_bus', '', shape='point', width='0.15',
           fixedsize='true', color='#b08a20')

    # ── Edges ────────────────────────────────────────────────────────────────────
    edge_attrs_gov = dict(
        color="#2e6da4",
        arrowhead="vee",
        arrowsize="0.8",
        penwidth="1.4",
    )
    edge_attrs_domain = dict(
        color="#b08a20",
        arrowhead="vee",
        arrowsize="0.8",
        penwidth="1.4",
        style="dashed",
    )

    # Orchestrator → Navigator  (declared FIRST — leftmost child)
    g.edge("orchestrator", "navigator",
           xlabel="project-map lookup",
           fontsize="11", fontname="Helvetica", fontcolor="#2e6da4",
           **edge_attrs_gov)

    # Orchestrator → each functional agent  (declared MIDDLE — center children)
    for nid, _, _ in FUNC_AGENTS:
        g.edge("orchestrator", nid, **edge_attrs_gov)

    # Orchestrator → dom_bus  (declared LAST — rightmost child via ordering="out")
    g.edge('orchestrator', 'dom_bus',
           xlabel="workflow dispatch",
           fontsize="11", fontname="Helvetica", fontcolor="#b08a20",
           style='dashed', color='#b08a20', penwidth='1.4',
           arrowhead='none')

    # dom_bus → each domain agent  (fan-out below the functional row)
    for nid, _, _, _ in DOMAIN_AGENTS:
        g.edge('dom_bus', nid, **edge_attrs_domain)

    # ── Rank constraints ────────────────────────────────────────────────────────
    # 5 ranks:
    #   Rank 0  – Orchestrator
    #   Rank 1  – Navigator (alone)
    #   Rank 2  – 7 functional agents (light-blue cluster)
    #   Rank 3  – dom_bus fork point (alone, centered over domain row)
    #   Rank 4  – ALL 7 domain agents (one wide row, amber cluster)

    ALL_FUNC = [nid for nid, _, _ in FUNC_AGENTS]
    ALL_DOM  = [nid for nid, _, _, _ in DOMAIN_AGENTS]

    with g.subgraph() as s:
        s.attr(rank="same")
        s.node("navigator")

    with g.subgraph() as s:
        s.attr(rank="same")
        for nid in ALL_FUNC:
            s.node(nid)

    with g.subgraph() as s:
        s.attr(rank="same")
        s.node('dom_bus')

    with g.subgraph() as s:
        s.attr(rank="same")
        for nid in ALL_DOM:
            s.node(nid)

    # Invisible spine enforces top-to-bottom ordering across all 5 ranks
    # Threads through center agent (graphviz_expert) to center the domain row
    g.edge("navigator", "security",       style="invis")
    g.edge("security",  "dom_bus",        style="invis")
    g.edge("dom_bus",   "graphviz_expert", style="invis")

    return g


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed namespace with ``out_dir``.
    """
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out-dir", type=Path, default=OUT_DIR,
                   help="directory to write SVGs into (default: docs_src/book/figures)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Render ``agent-team-structure.svg``.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code (0 on success).
    """
    args = _parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = build().render(filename=str(args.out_dir / "agent-team-structure"),
                              format="svg", cleanup=True, quiet=True)
    print(f"SVG -> {svg_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
