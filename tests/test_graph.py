"""Tests for src/graph.py — directed agent team topology graph."""

from __future__ import annotations

import json
import pytest
from agentteams.graph import (
    AgentNode,
    GraphEdge,
    TeamGraph,
    build_graph,
    generate_graph_document,
    _split_yaml,
    _extract_name,
    _extract_user_invokable,
    _extract_tools,
    _classify_agent_type,
    _mermaid_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_AGENT = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents"
user-invokable: true
tools: ['read', 'search']
model: ["Claude Sonnet 4.6"]
---
# Orchestrator
"""

DOMAIN_AGENT = """\
---
name: Primary Producer — TestProject
description: "Produces primary deliverables"
user-invokable: false
tools: ['read', 'write']
model: ["Claude Sonnet 4.6"]
---
# Primary Producer
"""

AGENT_WITH_HANDOFFS = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents"
user-invokable: true
tools: ['read', 'search', 'execute']
model: ["Claude Sonnet 4.6"]
handoffs:
  - label: Produce deliverable
    agent: primary-producer
    prompt: "Please produce the component."
    send: false
  - label: Audit quality
    agent: quality-auditor
    prompt: "Please audit the output."
    send: false
---
# Orchestrator
"""

AGENT_WITH_AGENTS_LIST = """\
---
name: Navigator — TestProject
description: "Navigates the repo"
user-invokable: false
tools: ['read', 'search']
model: ["Claude Sonnet 4.6"]
agents: ['orchestrator', 'primary-producer']
---
# Navigator
"""

WORKSTREAM_EXPERT = """\
---
name: Pipeline Core Expert — TestProject
description: "Owns the pipeline workstream"
user-invokable: false
tools: ['read', 'write']
model: ["Claude Sonnet 4.6"]
---
# Pipeline Core Expert
"""


def _make_file_map(**kwargs: str) -> dict[str, str]:
    """Build a file_map dict from slug → content pairs, auto-adding .agent.md suffix."""
    return {f"{slug}.agent.md": content for slug, content in kwargs.items()}


# ---------------------------------------------------------------------------
# _split_yaml
# ---------------------------------------------------------------------------

class TestSplitYaml:
    def test_standard_front_matter(self):
        yaml, body = _split_yaml("---\nname: Foo\n---\n# Body")
        assert "name: Foo" in yaml
        assert "# Body" in body

    def test_no_front_matter(self):
        yaml, body = _split_yaml("# Just markdown")
        assert yaml is None
        assert body == "# Just markdown"

    def test_empty_front_matter(self):
        yaml, body = _split_yaml("---\n---\n# Body")
        assert yaml is not None
        assert "# Body" in body

    def test_missing_closing_delimiter(self):
        yaml, body = _split_yaml("---\nname: Foo\n")
        assert yaml is None


# ---------------------------------------------------------------------------
# _extract_name
# ---------------------------------------------------------------------------

class TestExtractName:
    def test_extracts_role_before_em_dash(self):
        yaml = "name: Orchestrator — TestProject\n"
        assert _extract_name(yaml, "orchestrator") == "Orchestrator"

    def test_extracts_role_before_hyphen(self):
        yaml = "name: Navigator - TestProject\n"
        assert _extract_name(yaml, "navigator") == "Navigator"

    def test_strips_quotes(self):
        yaml = 'name: "Primary Producer — TestProject"\n'
        assert _extract_name(yaml, "primary-producer") == "Primary Producer"

    def test_fallback_to_slug(self):
        assert _extract_name("", "my-agent") == "my-agent"

    def test_name_without_project_suffix(self):
        yaml = "name: MyAgent\n"
        assert _extract_name(yaml, "myagent") == "MyAgent"


# ---------------------------------------------------------------------------
# _extract_user_invokable
# ---------------------------------------------------------------------------

class TestExtractUserInvokable:
    def test_true(self):
        assert _extract_user_invokable("user-invokable: true\n") is True

    def test_false(self):
        assert _extract_user_invokable("user-invokable: false\n") is False

    def test_missing_defaults_false(self):
        assert _extract_user_invokable("name: Foo\n") is False

    def test_case_insensitive_true(self):
        assert _extract_user_invokable("user-invokable: True\n") is True


# ---------------------------------------------------------------------------
# _extract_tools
# ---------------------------------------------------------------------------

class TestExtractTools:
    def test_list_of_tools(self):
        tools = _extract_tools("tools: ['read', 'search', 'execute']\n")
        assert tools == ["read", "search", "execute"]

    def test_empty_list(self):
        tools = _extract_tools("tools: []\n")
        assert tools == []

    def test_missing_tools(self):
        tools = _extract_tools("name: Foo\n")
        assert tools == []

    def test_double_quoted_tools(self):
        tools = _extract_tools('tools: ["read", "write"]\n')
        assert tools == ["read", "write"]


# ---------------------------------------------------------------------------
# _classify_agent_type
# ---------------------------------------------------------------------------

class TestClassifyAgentType:
    def test_governance_slugs(self):
        for slug in ["orchestrator", "navigator", "security", "code-hygiene"]:
            assert _classify_agent_type(slug) == "governance", slug

    def test_domain_slugs(self):
        for slug in ["primary-producer", "quality-auditor", "style-guardian"]:
            assert _classify_agent_type(slug) == "domain", slug

    def test_workstream_expert(self):
        assert _classify_agent_type("pipeline-core-expert") == "workstream_expert"
        assert _classify_agent_type("schemas-expert") == "workstream_expert"

    def test_tool_specialist(self):
        assert _classify_agent_type("tool-github") == "tool_specialist"

    def test_unknown(self):
        assert _classify_agent_type("mystery-agent") == "unknown"


# ---------------------------------------------------------------------------
# _mermaid_id
# ---------------------------------------------------------------------------

class TestMermaidId:
    def test_replaces_hyphens(self):
        assert _mermaid_id("primary-producer") == "primary_producer"

    def test_no_hyphens(self):
        assert _mermaid_id("orchestrator") == "orchestrator"

    def test_multiple_hyphens(self):
        assert _mermaid_id("code-hygiene") == "code_hygiene"


# ---------------------------------------------------------------------------
# build_graph — node parsing
# ---------------------------------------------------------------------------

class TestBuildGraphNodes:
    def test_single_agent_node(self):
        file_map = _make_file_map(orchestrator=MINIMAL_AGENT)
        graph = build_graph(file_map, project_name="TestProject")
        assert "orchestrator" in graph.nodes
        node = graph.nodes["orchestrator"]
        assert node.display_name == "Orchestrator"
        assert node.agent_type == "governance"
        assert node.user_invokable is True
        assert "read" in node.tools

    def test_domain_agent_classification(self):
        file_map = _make_file_map(**{"primary-producer": DOMAIN_AGENT})
        graph = build_graph(file_map)
        node = graph.nodes["primary-producer"]
        assert node.agent_type == "domain"
        assert node.user_invokable is False

    def test_workstream_expert_classification(self):
        file_map = _make_file_map(**{"pipeline-core-expert": WORKSTREAM_EXPERT})
        graph = build_graph(file_map)
        assert graph.nodes["pipeline-core-expert"].agent_type == "workstream_expert"

    def test_multiple_nodes(self):
        file_map = _make_file_map(
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        assert len(graph.nodes) == 2

    def test_empty_file_map(self):
        graph = build_graph({})
        assert graph.nodes == {}
        assert graph.edges == []

    def test_skips_non_agent_files(self):
        file_map = {
            "orchestrator.agent.md": MINIMAL_AGENT,
            "references/project-map.md": "# Project Map\n",
            "copilot-instructions.md": "# Instructions\n",
        }
        graph = build_graph(file_map)
        assert list(graph.nodes.keys()) == ["orchestrator"]

    def test_skips_references_directory(self):
        file_map = {
            "orchestrator.agent.md": MINIMAL_AGENT,
            "references/pipeline-graph.agent.md": DOMAIN_AGENT,
        }
        graph = build_graph(file_map)
        assert "pipeline-graph" not in graph.nodes

    def test_project_name_stored(self):
        graph = build_graph({}, project_name="MyProject")
        assert graph.project_name == "MyProject"


# ---------------------------------------------------------------------------
# build_graph — edge extraction
# ---------------------------------------------------------------------------

class TestBuildGraphEdges:
    def test_handoff_edges(self):
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_HANDOFFS,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        sources = {e.source for e in graph.edges}
        targets = {e.target for e in graph.edges}
        assert "orchestrator" in sources
        assert "primary-producer" in targets
        assert "quality-auditor" in targets

    def test_handoff_edge_type(self):
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_HANDOFFS,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        handoff_edges = [e for e in graph.edges if e.edge_type == "handoff"]
        assert len(handoff_edges) == 2

    def test_handoff_labels(self):
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_HANDOFFS,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        labels = {e.label for e in graph.edges if e.edge_type == "handoff"}
        assert "Produce deliverable" in labels or "Audit quality" in labels

    def test_agents_list_edges(self):
        file_map = _make_file_map(
            navigator=AGENT_WITH_AGENTS_LIST,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        list_targets = {e.target for e in list_edges}
        assert "orchestrator" in list_targets or "primary-producer" in list_targets

    def test_unknown_target_skipped(self):
        file_map = _make_file_map(orchestrator=AGENT_WITH_HANDOFFS)
        # Only orchestrator — targets don't exist
        graph = build_graph(file_map)
        assert graph.edges == []

    def test_no_self_edges_from_agents_list(self):
        agent_with_self = """\
---
name: Navigator — TestProject
description: "Navigates"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents: ['navigator', 'orchestrator']
---
"""
        file_map = _make_file_map(
            navigator=agent_with_self,
            orchestrator=MINIMAL_AGENT,
        )
        graph = build_graph(file_map)
        self_edges = [e for e in graph.edges if e.source == e.target]
        assert self_edges == []


# ---------------------------------------------------------------------------
# TeamGraph — adjacency
# ---------------------------------------------------------------------------

class TestTeamGraphAdjacency:
    def test_adjacency_contains_all_nodes(self):
        graph = TeamGraph(project_name="Test")
        graph.nodes["a"] = AgentNode("a", "A", "governance", True, [])
        graph.nodes["b"] = AgentNode("b", "B", "domain", False, [])
        adj = graph.adjacency()
        assert "a" in adj
        assert "b" in adj

    def test_adjacency_edge_recorded(self):
        graph = TeamGraph(project_name="Test")
        graph.nodes["a"] = AgentNode("a", "A", "governance", True, [])
        graph.nodes["b"] = AgentNode("b", "B", "domain", False, [])
        graph.edges.append(GraphEdge("a", "b", "handoff", "test"))
        adj = graph.adjacency()
        assert "b" in adj["a"]

    def test_reverse_adjacency(self):
        graph = TeamGraph(project_name="Test")
        graph.nodes["a"] = AgentNode("a", "A", "governance", True, [])
        graph.nodes["b"] = AgentNode("b", "B", "domain", False, [])
        graph.edges.append(GraphEdge("a", "b", "handoff", None))
        radj = graph.reverse_adjacency()
        assert "a" in radj["b"]
        assert radj["a"] == []


# ---------------------------------------------------------------------------
# TeamGraph — serialisation
# ---------------------------------------------------------------------------

class TestTeamGraphSerialisation:
    def _simple_graph(self) -> TeamGraph:
        graph = TeamGraph(project_name="TestProject")
        graph.nodes["orchestrator"] = AgentNode(
            "orchestrator", "Orchestrator", "governance", True, ["read", "search"]
        )
        graph.nodes["primary-producer"] = AgentNode(
            "primary-producer", "Primary Producer", "domain", False, ["read", "write"]
        )
        graph.edges.append(
            GraphEdge("orchestrator", "primary-producer", "handoff", "Produce")
        )
        return graph

    def test_to_json_valid(self):
        graph = self._simple_graph()
        data = json.loads(graph.to_json())
        assert data["project_name"] == "TestProject"
        assert "orchestrator" in data["nodes"]
        assert "primary-producer" in data["nodes"]
        assert len(data["edges"]) == 1
        assert "adjacency" in data

    def test_json_adjacency_content(self):
        graph = self._simple_graph()
        data = json.loads(graph.to_json())
        assert "primary-producer" in data["adjacency"]["orchestrator"]

    def test_to_mermaid_starts_with_flowchart(self):
        graph = self._simple_graph()
        mermaid = graph.to_mermaid()
        assert mermaid.startswith("flowchart LR")

    def test_mermaid_contains_node_ids(self):
        graph = self._simple_graph()
        mermaid = graph.to_mermaid()
        assert "orchestrator" in mermaid
        assert "primary_producer" in mermaid  # hyphens → underscores

    def test_mermaid_contains_edge(self):
        graph = self._simple_graph()
        mermaid = graph.to_mermaid()
        assert "-->" in mermaid

    def test_mermaid_classDef_present(self):
        graph = self._simple_graph()
        mermaid = graph.to_mermaid()
        assert "classDef governance" in mermaid
        assert "classDef domain" in mermaid

    def test_to_dot_valid(self):
        graph = self._simple_graph()
        dot = graph.to_dot()
        assert "digraph" in dot
        assert "orchestrator" in dot
        assert "->" in dot

    def test_to_markdown_document_sections(self):
        graph = self._simple_graph()
        doc = graph.to_markdown_document()
        assert "## Team Topology Graph" in doc
        assert "```mermaid" in doc
        assert "## Agent Roster" in doc
        assert "## Adjacency List" in doc
        assert "## DOT Source" in doc
        assert "## JSON Adjacency" in doc

    def test_markdown_contains_auto_generated_warning(self):
        graph = self._simple_graph()
        doc = graph.to_markdown_document()
        assert "Auto-generated" in doc

    def test_empty_graph_serialisation(self):
        graph = TeamGraph(project_name="Empty")
        doc = graph.to_markdown_document()
        assert "# Empty" in doc
        mermaid = graph.to_mermaid()
        assert "flowchart LR" in mermaid

    def test_dashed_edge_for_agents_list(self):
        graph = TeamGraph(project_name="Test")
        graph.nodes["a"] = AgentNode("a", "A", "governance", True, [])
        graph.nodes["b"] = AgentNode("b", "B", "domain", False, [])
        graph.edges.append(GraphEdge("a", "b", "agents-list", None))
        mermaid = graph.to_mermaid()
        assert "-.->" in mermaid


# ---------------------------------------------------------------------------
# generate_graph_document — integration
# ---------------------------------------------------------------------------

class TestGenerateGraphDocument:
    def test_returns_markdown_string(self):
        file_map = _make_file_map(
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        result = generate_graph_document(file_map, project_name="TestProject")
        assert isinstance(result, str)
        assert "# TestProject" in result

    def test_includes_mermaid_block(self):
        file_map = _make_file_map(orchestrator=MINIMAL_AGENT)
        result = generate_graph_document(file_map)
        assert "```mermaid" in result

    def test_empty_file_map(self):
        result = generate_graph_document({})
        assert isinstance(result, str)
        assert "flowchart LR" in result

    def test_full_pipeline_with_handoffs(self):
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_HANDOFFS,
            **{
                "primary-producer": DOMAIN_AGENT,
                "quality-auditor": DOMAIN_AGENT,
            },
        )
        result = generate_graph_document(file_map, project_name="PipelineTest")
        assert "orchestrator" in result
        assert "primary_producer" in result or "primary-producer" in result
