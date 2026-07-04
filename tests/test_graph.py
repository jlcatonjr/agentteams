"""Tests for src/graph.py — directed agent team topology graph."""

from __future__ import annotations

import json
import re
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
    _extract_yaml_sequence,
    _extract_agents_list,
    _parse_handoff_blocks,
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

AGENT_WITH_UNQUOTED_AGENTS_LIST = """\
---
name: Navigator — TestProject
description: "Navigates the repo"
user-invokable: false
tools: ['read', 'search']
model: ["Claude Sonnet 4.6"]
agents: [orchestrator, primary-producer]
---
# Navigator
"""

AGENT_WITH_DOUBLE_QUOTED_AGENTS_LIST = """\
---
name: Navigator — TestProject
description: "Navigates the repo"
user-invokable: false
tools: ['read', 'search']
model: ["Claude Sonnet 4.6"]
agents: ["orchestrator", "primary-producer"]
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

AGENT_WITH_BLOCK_TOOLS = """\
---
name: Navigator — TestProject
description: "Navigates the repo"
user-invokable: false
tools:
  - read
  - search
  - execute
model: ["Claude Sonnet 4.6"]
---
# Navigator
"""

AGENT_WITH_BLOCK_AGENTS_LIST = """\
---
name: Navigator — TestProject
description: "Navigates the repo"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents:
  - orchestrator
  - primary-producer
---
# Navigator
"""

AGENT_WITH_BLOCK_AGENTS_THEN_HANDOFFS = """\
---
name: Navigator — TestProject
description: "Navigates the repo"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents:
  - orchestrator
handoffs:
  - label: Route to producer
    agent: primary-producer
    prompt: "Go."
    send: false
---
# Navigator
"""

AGENT_WITH_AGENT_FIRST_HANDOFFS = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents"
user-invokable: true
tools: ['read', 'search', 'execute']
model: ["Claude Sonnet 4.6"]
handoffs:
  - agent: primary-producer
    label: Produce deliverable
    prompt: "Please produce the component."
    send: false
  - agent: quality-auditor
    label: Audit quality
    prompt: "Please audit the output."
    send: false
---
# Orchestrator
"""

AGENT_WITH_MIXED_LABEL_PRESENCE = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents"
user-invokable: true
tools: ['read', 'search', 'execute']
model: ["Claude Sonnet 4.6"]
handoffs:
  - agent: primary-producer
    prompt: "No label here."
    send: false
  - agent: quality-auditor
    label: Audit quality
    prompt: "Please audit the output."
    send: false
---
# Orchestrator
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

    def test_block_scalar_with_embedded_dash_separator(self):
        """MAP-17 / MAP-06 regression: embedded '---' inside a block scalar must not
        be treated as the closing delimiter."""
        content = (
            "---\n"
            "name: Foo\n"
            "notes: |\n"
            "  some text\n"
            "  ---\n"
            "  more text\n"
            "---\n"
            "# Body\n"
        )
        yaml_block, body = _split_yaml(content)
        assert yaml_block is not None, "front matter must be detected"
        assert "name: Foo" in yaml_block
        # Stricter: block-scalar content must remain in yaml_block (not leak into body);
        # the embedded --- must not be mistaken for the closing delimiter.
        assert "  more text" in yaml_block, (
            "block-scalar content after embedded --- must remain in yaml_block, not be cut off"
        )
        assert "  more text" not in body, (
            "block-scalar content must not leak into the body"
        )
        # Stricter: body must begin with the real body heading
        assert body.strip().startswith("# Body"), (
            f"body must start with '# Body', got: {body[:40]!r}"
        )

    def test_mid_line_dashes_not_treated_as_closing_delimiter(self):
        """'---' embedded mid-line in a YAML value must not close the front matter."""
        content = "---\nname: foo---bar\n---\n# Body\n"
        yaml_block, body = _split_yaml(content)
        assert yaml_block is not None
        assert "name: foo---bar" in yaml_block
        assert "# Body" in body

    def test_empty_front_matter_block_falsy_but_not_none(self):
        """'---\\n---\\n' produces yaml_block='' (falsy). Callers that need to distinguish
        'empty front matter present' from 'no front matter' must use 'is not None'."""
        yaml_block, body = _split_yaml("---\n---\n# Body\n")
        assert yaml_block == "", "empty front matter must be empty string, not None"
        assert yaml_block is not None
        assert "# Body" in body

    def test_embedded_dashes_in_description_value_not_split(self):
        """Regression for MAP-17: '---' inside a scalar must not cause early split."""
        content = (
            "---\n"
            "name: My Agent\n"
            "description: 'Use range notation foo---bar in your queries'\n"
            "user-invokable: true\n"
            "---\n"
            "# My Agent\n"
        )
        yaml, body = _split_yaml(content)
        assert yaml is not None, "yaml_block must not be None"
        assert "description: 'Use range notation foo---bar" in yaml
        assert "user-invokable: true" in yaml
        assert "# My Agent" in body

    def test_embedded_dashes_in_name_value_not_split(self):
        """'---' embedded directly in a YAML name value does not terminate front matter."""
        content = "---\nname: My---Agent\nuser-invokable: false\n---\n# Body"
        yaml, body = _split_yaml(content)
        assert yaml is not None
        assert "name: My---Agent" in yaml
        assert "user-invokable: false" in yaml
        assert "# Body" in body

    def test_closing_delimiter_without_trailing_newline(self):
        """A closing '---' with no trailing newline (end-of-file) is still recognised."""
        yaml, body = _split_yaml("---\nname: Foo\n---")
        assert yaml is not None
        assert "name: Foo" in yaml
        assert body == ""

    def test_indented_triple_dash_is_not_closing_delimiter(self):
        """A '---' preceded by whitespace (inside a block scalar) is NOT the delimiter."""
        content = (
            "---\n"
            "name: Foo\n"
            "notes: >\n"
            "  multiline value with --- inside\n"
            "---\n"
            "# Body\n"
        )
        yaml, body = _split_yaml(content)
        assert yaml is not None
        # The indented line "  multiline value with --- inside" must NOT end parsing early.
        assert "name: Foo" in yaml
        assert "notes" in yaml
        assert "# Body" in body

    def test_multiple_embedded_dashes_only_line_boundary_closes(self):
        """When YAML block has several embedded ---, only the line-boundary one closes."""
        content = (
            "---\n"
            "a: foo---bar\n"
            "b: baz---qux\n"
            "c: end---value\n"
            "---\n"
            "body text\n"
        )
        yaml, body = _split_yaml(content)
        assert yaml is not None
        assert "a: foo---bar" in yaml
        assert "b: baz---qux" in yaml
        assert "c: end---value" in yaml
        assert "body text" in body

    def test_crlf_line_endings_recognised(self):
        """CRLF-encoded files are handled correctly via .rstrip('\\r') on the delimiter line."""
        content = "---\r\nname: Foo\r\n---\r\n# Body"
        yaml, body = _split_yaml(content)
        assert yaml is not None, "yaml_block must not be None for CRLF input"
        assert "name: Foo" in yaml
        assert "# Body" in body


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
# _parse_handoff_blocks
# ---------------------------------------------------------------------------

class TestParseHandoffBlocks:
    """Unit tests for _parse_handoff_blocks — the MAP-03 fix."""

    def test_label_first_order_returns_correct_pairs(self):
        """label: before agent: (existing supported order) must still work."""
        yaml = (
            "handoffs:\n"
            "  - label: Produce deliverable\n"
            "    agent: primary-producer\n"
            "    send: false\n"
            "  - label: Audit quality\n"
            "    agent: quality-auditor\n"
            "    send: false\n"
        )
        pairs = _parse_handoff_blocks(yaml)
        assert pairs == [
            ("primary-producer", "Produce deliverable"),
            ("quality-auditor", "Audit quality"),
        ]

    def test_agent_first_order_returns_correct_pairs(self):
        """agent: before label: (the Failure 2 bug case) must capture the label."""
        yaml = (
            "handoffs:\n"
            "  - agent: primary-producer\n"
            "    label: Produce deliverable\n"
            "    send: false\n"
            "  - agent: quality-auditor\n"
            "    label: Audit quality\n"
            "    send: false\n"
        )
        pairs = _parse_handoff_blocks(yaml)
        assert pairs == [
            ("primary-producer", "Produce deliverable"),
            ("quality-auditor", "Audit quality"),
        ]

    def test_missing_label_yields_none_without_skewing_next(self):
        """When item N has no label:, item N+1 must still receive its own label."""
        yaml = (
            "handoffs:\n"
            "  - agent: primary-producer\n"
            "    send: false\n"
            "  - agent: quality-auditor\n"
            "    label: Audit quality\n"
            "    send: false\n"
        )
        pairs = _parse_handoff_blocks(yaml)
        assert pairs == [
            ("primary-producer", None),       # no label — must be None, not skewed
            ("quality-auditor", "Audit quality"),  # must retain its own label
        ]

    def test_no_handoffs_section_returns_empty(self):
        """YAML with no handoffs: key must return an empty list."""
        yaml = "name: Foo\ntools: ['read']\n"
        assert _parse_handoff_blocks(yaml) == []

    def test_item_with_no_agent_key_is_skipped(self):
        """An item that has label: but no agent: must be silently skipped."""
        yaml = (
            "handoffs:\n"
            "  - label: Orphaned label\n"
            "    send: false\n"
        )
        assert _parse_handoff_blocks(yaml) == []

    def test_handoffs_section_does_not_bleed_into_next_key(self):
        """agent: values that appear under a sibling key (e.g. agents:) must not
        be captured as handoff targets."""
        yaml = (
            "handoffs:\n"
            "  - agent: primary-producer\n"
            "    send: false\n"
            "agents: ['orchestrator']\n"
        )
        pairs = _parse_handoff_blocks(yaml)
        assert len(pairs) == 1
        assert pairs[0][0] == "primary-producer"

    def test_all_items_without_labels_return_none(self):
        """All items lacking label: must all yield None, with no cross-contamination."""
        yaml = (
            "handoffs:\n"
            "  - agent: alpha\n"
            "    send: false\n"
            "  - agent: beta\n"
            "    send: false\n"
        )
        pairs = _parse_handoff_blocks(yaml)
        assert pairs == [("alpha", None), ("beta", None)]


# ---------------------------------------------------------------------------
# _classify_agent_type
# ---------------------------------------------------------------------------

class TestClassifyAgentType:
    def test_governance_slugs(self):
        for slug in ["orchestrator", "navigator", "security", "code-hygiene", "repo-liaison", "git-operations"]:
            assert _classify_agent_type(slug) == "governance", slug

    def test_domain_slugs(self):
        for slug in ["primary-producer", "quality-auditor", "style-guardian", "work-summarizer"]:
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

    def test_agents_list_unquoted_slugs_produce_edges(self):
        """Unquoted inline agents list must produce agents-list edges (MAP-04)."""
        file_map = _make_file_map(
            navigator=AGENT_WITH_UNQUOTED_AGENTS_LIST,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        list_targets = {e.target for e in list_edges}
        assert "orchestrator" in list_targets, (
            "orchestrator missing from agents-list edges with unquoted YAML"
        )
        assert "primary-producer" in list_targets, (
            "primary-producer missing from agents-list edges with unquoted YAML"
        )

    def test_agents_list_double_quoted_slugs_produce_edges(self):
        """Double-quoted inline agents list must produce agents-list edges."""
        file_map = _make_file_map(
            navigator=AGENT_WITH_DOUBLE_QUOTED_AGENTS_LIST,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        list_targets = {e.target for e in list_edges}
        assert "orchestrator" in list_targets
        assert "primary-producer" in list_targets

    def test_agents_list_mixed_quoting_produces_edges(self):
        """Mixed quoted/unquoted slugs in the same list must all be matched."""
        mixed_agent = """---
name: Navigator — TestProject
description: "Mixed quoting"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents: ['orchestrator', primary-producer]
---
"""
        file_map = _make_file_map(
            navigator=mixed_agent,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        list_targets = {e.target for e in list_edges}
        assert "orchestrator" in list_targets
        assert "primary-producer" in list_targets

    def test_agents_list_single_unquoted_element_produces_edge(self):
        """A single unquoted slug in agents: [slug] must produce one edge."""
        single_agent = """---
name: Navigator — TestProject
description: "Single target"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents: [orchestrator]
---
"""
        file_map = _make_file_map(
            navigator=single_agent,
            orchestrator=MINIMAL_AGENT,
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        assert len(list_edges) == 1
        assert list_edges[0].target == "orchestrator"


# ---------------------------------------------------------------------------
# build_graph — MAP-03 handoff edge corner cases
# ---------------------------------------------------------------------------

class TestHandoffEdgeCornerCases:
    """Integration tests for MAP-03 — label/agent key-order and missing-label fixes."""

    def test_agent_first_handoff_label_is_captured_on_edge(self):
        """When agent: precedes label: in the YAML block, the label must appear
        on the GraphEdge, not be silently dropped."""
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_AGENT_FIRST_HANDOFFS,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        handoff_edges = [e for e in graph.edges if e.edge_type == "handoff"]
        labels = {e.target: e.label for e in handoff_edges}
        assert labels.get("primary-producer") == "Produce deliverable"
        assert labels.get("quality-auditor") == "Audit quality"

    def test_missing_label_on_first_handoff_does_not_skew_second(self):
        """When handoff N has no label:, handoff N+1 must keep its own label.
        The bug caused handoff N+1's label to be assigned to handoff N and
        handoff N+1 to receive None."""
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_MIXED_LABEL_PRESENCE,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        handoff_edges = [e for e in graph.edges if e.edge_type == "handoff"]
        labels = {e.target: e.label for e in handoff_edges}
        # primary-producer has no label: in source — must be None
        assert labels.get("primary-producer") is None
        # quality-auditor has label: — must NOT be shifted to None by the skew bug
        assert labels.get("quality-auditor") == "Audit quality"

    def test_edge_count_correct_with_mixed_label_presence(self):
        """Both handoff edges must be emitted even when labels are absent from
        some items."""
        file_map = _make_file_map(
            orchestrator=AGENT_WITH_MIXED_LABEL_PRESENCE,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        handoff_edges = [e for e in graph.edges if e.edge_type == "handoff"]
        assert len(handoff_edges) == 2


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

    def test_mermaid_classDef_uses_full_type_names(self):
        """classDef identifiers must match _classify_agent_type() return values."""
        graph = self._simple_graph()
        mermaid = graph.to_mermaid()
        # Full names must be present
        assert "classDef workstream_expert" in mermaid
        assert "classDef tool_specialist" in mermaid
        # Old abbreviated names must NOT be present
        assert "classDef expert " not in mermaid
        assert "classDef tool " not in mermaid

    def test_mermaid_workstream_expert_node_styled(self):
        """A *-expert agent node must reference classDef workstream_expert."""
        graph = TeamGraph(project_name="Test")
        graph.nodes["pipeline-core-expert"] = AgentNode(
            "pipeline-core-expert",
            "Pipeline Core Expert",
            "workstream_expert",
            False,
            [],
        )
        mermaid = graph.to_mermaid()
        assert "classDef workstream_expert" in mermaid
        assert "class pipeline_core_expert workstream_expert" in mermaid

    def test_mermaid_tool_specialist_node_styled(self):
        """A tool-* agent node must reference classDef tool_specialist."""
        graph = TeamGraph(project_name="Test")
        graph.nodes["tool-github"] = AgentNode(
            "tool-github",
            "Tool GitHub",
            "tool_specialist",
            False,
            [],
        )
        mermaid = graph.to_mermaid()
        assert "classDef tool_specialist" in mermaid
        assert "class tool_github tool_specialist" in mermaid

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
        # The diagram is now a reference to the standalone SVG, not an inline block.
        assert "](pipeline-graph.svg)" in doc
        assert "## Agent Roster" in doc
        assert "## Adjacency List" in doc
        # Mermaid + DOT source is retained under a collapsed <details> block.
        assert "## Diagram Source" in doc
        assert "<details>" in doc
        assert "```mermaid" in doc
        assert "```dot" in doc
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
# Markdown legend — no external image URLs
# ---------------------------------------------------------------------------

class TestMarkdownLegend:
    """Ensure the Node Legend table uses no external image URLs."""

    def _doc(self) -> str:
        graph = TeamGraph(project_name="Test")
        graph.nodes["orchestrator"] = AgentNode(
            "orchestrator", "Orchestrator", "governance", True, []
        )
        return graph.to_markdown_document()

    def test_no_via_placeholder_urls(self):
        """via.placeholder.com must not appear anywhere in the rendered document."""
        assert "via.placeholder.com" not in self._doc()

    def test_legend_uses_inline_svg(self):
        """Legend colour swatches must be self-contained inline SVG elements."""
        doc = self._doc()
        assert "<svg" in doc
        assert "<rect" in doc

    def test_legend_contains_all_four_agent_type_colors(self):
        """All four agent-type fill colours must appear in the legend."""
        doc = self._doc()
        for hex_color in ("#e8e8ff", "#e8ffe8", "#fff8e8", "#ffe8e8"):
            assert hex_color in doc, f"Missing color {hex_color} in legend"

    def test_legend_contains_all_four_agent_type_labels(self):
        """All four agent-type label strings must appear in the legend."""
        doc = self._doc()
        for label in ("Governance", "Domain", "Workstream Expert", "Tool Specialist"):
            assert label in doc, f"Missing label '{label}' in legend"

    def test_legend_section_heading_present(self):
        """## Node Legend heading must be present."""
        assert "## Node Legend" in self._doc()


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


# ---------------------------------------------------------------------------
# _extract_yaml_sequence — unit tests (MAP-01)
# ---------------------------------------------------------------------------

class TestExtractYamlSequence:
    def test_inline_bracket_unquoted(self):
        yaml = "tools: [read, write]\n"
        assert _extract_yaml_sequence(yaml, "tools") == ["read", "write"]

    def test_inline_bracket_single_quoted(self):
        yaml = "tools: ['read', 'write']\n"
        assert _extract_yaml_sequence(yaml, "tools") == ["read", "write"]

    def test_inline_bracket_double_quoted(self):
        yaml = 'tools: ["read", "write"]\n'
        assert _extract_yaml_sequence(yaml, "tools") == ["read", "write"]

    def test_inline_empty(self):
        yaml = "tools: []\n"
        assert _extract_yaml_sequence(yaml, "tools") == []

    def test_block_bare_items(self):
        yaml = "tools:\n  - read\n  - write\n"
        assert _extract_yaml_sequence(yaml, "tools") == ["read", "write"]

    def test_block_single_quoted_items(self):
        yaml = "tools:\n  - 'read'\n  - 'write'\n"
        assert _extract_yaml_sequence(yaml, "tools") == ["read", "write"]

    def test_block_terminates_at_next_key(self):
        yaml = "tools:\n  - read\n  - write\nname: Foo\n"
        assert _extract_yaml_sequence(yaml, "tools") == ["read", "write"]

    def test_block_does_not_capture_sub_keys(self):
        # handoffs block follows agents; sub-keys like 'label' must not appear
        yaml = (
            "agents:\n  - orchestrator\n"
            "handoffs:\n"
            "  - label: Go\n"
            "    agent: primary-producer\n"
        )
        result = _extract_yaml_sequence(yaml, "agents")
        assert result == ["orchestrator"]
        assert "label" not in result
        assert "agent" not in result

    def test_key_absent_returns_empty(self):
        yaml = "name: Foo\nuser-invokable: true\n"
        assert _extract_yaml_sequence(yaml, "tools") == []

    def test_block_last_key_no_terminator(self):
        # block sequence is the last key — no following top-level key
        yaml = "agents:\n  - orchestrator\n  - navigator\n"
        assert _extract_yaml_sequence(yaml, "agents") == ["orchestrator", "navigator"]


# ---------------------------------------------------------------------------
# _extract_tools — block-style additions (MAP-01)
# ---------------------------------------------------------------------------

class TestExtractToolsBlockStyle:
    def test_block_style_tools(self):
        yaml = "tools:\n  - read\n  - search\n  - execute\n"
        tools = _extract_tools(yaml)
        assert tools == ["read", "search", "execute"]

    def test_block_style_single_item(self):
        yaml = "tools:\n  - read\n"
        assert _extract_tools(yaml) == ["read"]

    def test_block_style_terminates_at_next_key(self):
        yaml = "tools:\n  - read\nname: Foo\n"
        assert _extract_tools(yaml) == ["read"]


# ---------------------------------------------------------------------------
# build_graph — MAP-01 block-form agents: and tools: integration
# ---------------------------------------------------------------------------

class TestBuildGraphBlockFormYaml:
    def test_agents_list_block_form_emits_edges(self):
        """Block-style agents: sequence must produce agents-list edges."""
        file_map = _make_file_map(
            navigator=AGENT_WITH_BLOCK_AGENTS_LIST,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        targets = {e.target for e in list_edges}
        assert "orchestrator" in targets
        assert "primary-producer" in targets

    def test_agents_list_block_form_no_spurious_edges_from_handoffs(self):
        """Block agents: list followed by handoffs: must not produce edges to
        'label' or 'agent' slugs."""
        file_map = _make_file_map(
            navigator=AGENT_WITH_BLOCK_AGENTS_THEN_HANDOFFS,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        all_targets = {e.target for e in graph.edges}
        assert "label" not in all_targets
        assert "agent" not in all_targets

    def test_block_tools_reflected_in_node(self):
        """Block-style tools must populate AgentNode.tools."""
        file_map = _make_file_map(navigator=AGENT_WITH_BLOCK_TOOLS)
        graph = build_graph(file_map)
        node = graph.nodes["navigator"]
        assert "read" in node.tools
        assert "search" in node.tools
        assert "execute" in node.tools


# ---------------------------------------------------------------------------
# Edge / YAML corner cases (MAP-18)
# ---------------------------------------------------------------------------

class TestBuildGraphEdgeCornerCases:
    """Corner cases in edge extraction that were previously untested (MAP-18)."""

    # --- block-style tools: ---

    def test_block_style_tools(self):
        """_extract_tools must return correct list for block-YAML form."""
        content = """\
---
name: Builder — TestProject
description: "Builds things"
user-invokable: false
tools:
  - read
  - write
  - execute
model: ["Claude Sonnet 4.6"]
---
# Builder
"""
        file_map = {"builder.agent.md": content}
        graph = build_graph(file_map)
        assert graph.nodes["builder"].tools == ["read", "write", "execute"]

    # --- block-style agents: ---

    def test_block_style_agents_creates_edges(self):
        """Agents declared in block-YAML form must produce agents-list edges."""
        content = """\
---
name: Navigator — TestProject
description: "Navigates"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents:
  - orchestrator
  - primary-producer
---
# Navigator
"""
        file_map = _make_file_map(
            navigator=content,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        targets = {e.target for e in list_edges}
        assert "orchestrator" in targets
        assert "primary-producer" in targets

    # --- unquoted inline agents list ---

    def test_unquoted_agents_list_entries(self):
        """Inline agents: [slug1, slug2] without quotes must produce edges."""
        content = """\
---
name: Navigator — TestProject
description: "Navigates"
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6"]
agents: [orchestrator, primary-producer]
---
# Navigator
"""
        file_map = _make_file_map(
            navigator=content,
            orchestrator=MINIMAL_AGENT,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        list_edges = [e for e in graph.edges if e.edge_type == "agents-list"]
        targets = {e.target for e in list_edges}
        assert "orchestrator" in targets
        assert "primary-producer" in targets

    # --- handoff label ordering / missing label ---

    def test_handoff_missing_label_does_not_skew_subsequent_labels(self):
        """A handoff without label: must emit label=None; subsequent labels must be unaffected."""
        content = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents"
user-invokable: true
tools: ['read']
model: ["Claude Sonnet 4.6"]
handoffs:
  - agent: primary-producer
    prompt: "No label on this one."
    send: false
  - label: Audit quality
    agent: quality-auditor
    prompt: "Please audit."
    send: false
---
# Orchestrator
"""
        file_map = _make_file_map(
            orchestrator=content,
            **{"primary-producer": DOMAIN_AGENT, "quality-auditor": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        edges_by_target = {e.target: e for e in graph.edges if e.edge_type == "handoff"}

        assert "primary-producer" in edges_by_target
        assert edges_by_target["primary-producer"].label is None, (
            "Edge to primary-producer must have no label"
        )

        assert "quality-auditor" in edges_by_target
        assert edges_by_target["quality-auditor"].label == "Audit quality", (
            "Audit quality label must not be skewed onto the wrong edge"
        )

    def test_handoff_agent_before_label_captures_label(self):
        """label: must be captured even when agent: appears first in the handoff item."""
        content = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents"
user-invokable: true
tools: ['read']
model: ["Claude Sonnet 4.6"]
handoffs:
  - agent: primary-producer
    label: Produce deliverable
    prompt: "Please produce."
    send: false
---
# Orchestrator
"""
        file_map = _make_file_map(
            orchestrator=content,
            **{"primary-producer": DOMAIN_AGENT},
        )
        graph = build_graph(file_map)
        handoff_edges = [e for e in graph.edges if e.edge_type == "handoff"]
        assert len(handoff_edges) == 1
        assert handoff_edges[0].label == "Produce deliverable", (
            "label: must be captured even when agent: key comes first in the YAML item"
        )

    # --- _split_yaml robustness ---

    def test_split_yaml_embedded_triple_dash_not_treated_as_delimiter(self):
        """--- embedded inside a quoted YAML value must not close the front matter."""
        content = (
            "---\n"
            "name: Foo — TestProject\n"
            'description: "covers --- many --- cases"\n'
            "user-invokable: false\n"
            "---\n"
            "# Body\n"
        )
        yaml, body = _split_yaml(content)
        assert yaml is not None, "Front matter should be detected"
        assert "name: Foo" in yaml
        assert 'description: "covers --- many --- cases"' in yaml
        assert "# Body" in body

    # --- _parse_handoff_blocks unit tests ---

    def test_parse_handoffs_label_before_agent(self):
        """_parse_handoff_blocks with standard label-first ordering."""
        yaml = (
            "handoffs:\n"
            "  - label: Step one\n"
            "    agent: primary-producer\n"
            "  - label: Step two\n"
            "    agent: quality-auditor\n"
        )
        result = _parse_handoff_blocks(yaml)
        assert result == [
            ("primary-producer", "Step one"),
            ("quality-auditor", "Step two"),
        ]

    def test_parse_handoffs_agent_before_label(self):
        """_parse_handoff_blocks must work regardless of key order within an item."""
        yaml = (
            "handoffs:\n"
            "  - agent: primary-producer\n"
            "    label: Step one\n"
        )
        result = _parse_handoff_blocks(yaml)
        assert result == [("primary-producer", "Step one")]

    def test_parse_handoffs_missing_label_yields_none(self):
        """Entry with no label: key must produce (slug, None)."""
        yaml = (
            "handoffs:\n"
            "  - agent: primary-producer\n"
            "    prompt: No label here\n"
        )
        result = _parse_handoff_blocks(yaml)
        assert result == [("primary-producer", None)]

    def test_parse_handoffs_no_handoffs_key_returns_empty(self):
        assert _parse_handoff_blocks("name: Foo\ntools: ['read']\n") == []

    # --- _extract_agents_list unit tests ---

    def test_extract_agents_list_inline_quoted(self):
        yaml = "agents: ['orchestrator', 'primary-producer']\n"
        assert _extract_agents_list(yaml) == ["orchestrator", "primary-producer"]

    def test_extract_agents_list_inline_unquoted(self):
        yaml = "agents: [orchestrator, primary-producer]\n"
        assert _extract_agents_list(yaml) == ["orchestrator", "primary-producer"]

    def test_extract_agents_list_block(self):
        yaml = "agents:\n  - orchestrator\n  - primary-producer\n"
        assert _extract_agents_list(yaml) == ["orchestrator", "primary-producer"]

    def test_extract_agents_list_absent_returns_empty(self):
        assert _extract_agents_list("name: Foo\n") == []


# ---------------------------------------------------------------------------
# Mermaid classDef correctness (MAP-18)
# ---------------------------------------------------------------------------

class TestMermaidClassDef:
    """Verify that every node class assignment matches a defined classDef identifier."""

    def _classdef_names(self, mermaid: str) -> set[str]:
        """Extract all classDef identifiers from a mermaid string."""
        return set(re.findall(r"classDef\s+(\w+)", mermaid))

    def _class_assignments(self, mermaid: str) -> set[str]:
        """Extract all class identifiers applied to nodes: 'class <id> <classname>'."""
        return set(re.findall(r"^\s+class\s+\w+\s+(\w+)", mermaid, re.MULTILINE))

    def test_workstream_expert_class_resolves_to_defined_classdef(self):
        """workstream_expert node must reference a classDef that exists in the output."""
        graph = TeamGraph(project_name="Test")
        graph.nodes["pipeline-core-expert"] = AgentNode(
            "pipeline-core-expert", "Pipeline Core Expert", "workstream_expert", False, []
        )
        mermaid = graph.to_mermaid()
        defined = self._classdef_names(mermaid)
        assigned = self._class_assignments(mermaid)
        unresolved = assigned - defined
        assert not unresolved, (
            f"Class assignment(s) reference undefined classDef(s): {unresolved}"
        )

    def test_tool_specialist_class_resolves_to_defined_classdef(self):
        """tool_specialist node must reference a classDef that exists in the output."""
        graph = TeamGraph(project_name="Test")
        graph.nodes["tool-github"] = AgentNode(
            "tool-github", "GitHub Tool", "tool_specialist", False, []
        )
        mermaid = graph.to_mermaid()
        defined = self._classdef_names(mermaid)
        assigned = self._class_assignments(mermaid)
        unresolved = assigned - defined
        assert not unresolved, (
            f"Class assignment(s) reference undefined classDef(s): {unresolved}"
        )

    def test_all_agent_types_have_matching_classdef(self):
        """All five agent types used together must all resolve to defined classDefs."""
        graph = TeamGraph(project_name="Test")
        graph.nodes["orchestrator"] = AgentNode(
            "orchestrator", "Orchestrator", "governance", True, []
        )
        graph.nodes["primary-producer"] = AgentNode(
            "primary-producer", "Primary Producer", "domain", False, []
        )
        graph.nodes["pipeline-core-expert"] = AgentNode(
            "pipeline-core-expert", "Pipeline Core Expert", "workstream_expert", False, []
        )
        graph.nodes["tool-github"] = AgentNode(
            "tool-github", "GitHub Tool", "tool_specialist", False, []
        )
        graph.nodes["mystery-agent"] = AgentNode(
            "mystery-agent", "Mystery Agent", "unknown", False, []
        )
        mermaid = graph.to_mermaid()
        defined = self._classdef_names(mermaid)
        assigned = self._class_assignments(mermaid)
        unresolved = assigned - defined
        assert not unresolved, (
            f"Class assignment(s) reference undefined classDef(s): {unresolved}\n"
            f"Defined: {defined}\nAssigned: {assigned}"
        )

    def test_existing_classdef_governance_and_domain_still_present(self):
        """Regression guard: renaming workstream/tool classDefs must not remove governance/domain."""
        graph = TeamGraph(project_name="Test")
        graph.nodes["orchestrator"] = AgentNode(
            "orchestrator", "Orchestrator", "governance", True, []
        )
        mermaid = graph.to_mermaid()
        assert "classDef governance" in mermaid
        assert "classDef domain" in mermaid
