"""Regression pins for the Phase B YAML-scanner consolidation (plan steps
B.1/B.2, test step H.5).

B.1 retired the duplicate boundary scanner ``_utils._split_yaml_front_matter``
in favor of the shared ``yaml_frontmatter.parse_yaml_front_matter``; the graph
path consumes it through ``graph_inputs._split_yaml``. B.2 replaced
``bridge_sources``' naive ``text.split("\\n---\\n", 1)`` boundary scan with the
same shared scanner. These tests pin the post-swap behavior so the two
historical bug classes cannot silently return:

1. A bare ``---`` line INSIDE a block scalar must not terminate front matter
   (the MAP-06 class both naive scanners exhibited).
2. The graph-path yaml block keeps its historical shape (no trailing newline)
   byte-for-byte, and the body is passed through untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.bridge_sources import _parse_front_matter as _bridge_parse_front_matter
from agentteams.graph_inputs import _split_yaml
from agentteams.yaml_frontmatter import parse_yaml_front_matter

REPO = Path(__file__).resolve().parents[1]

def _strip_one_newline(yaml_text: str) -> str:
    """Replicate graph_inputs._split_yaml's exact trailing-newline handling."""
    if yaml_text.endswith("\r\n"):
        return yaml_text[:-2]
    if yaml_text.endswith("\n"):
        return yaml_text[:-1]
    return yaml_text


_BLOCK_SCALAR_WITH_DASHES = (
    "---\n"
    'name: "Demo Agent — Demo"\n'
    "description: |\n"
    "  First line of the description.\n"
    "  ---\n"
    "  A separator-like line that must NOT close the front matter.\n"
    "tools:\n"
    "  - read\n"
    "  - search\n"
    "user-invokable: false\n"
    "---\n"
    "# Demo Agent\n\nBody text.\n"
)


# ---------------------------------------------------------------------------
# B.2 — bridge_sources block-style YAML regression
# ---------------------------------------------------------------------------

class TestBridgeSourcesBlockStyleRegression:
    def test_bare_dashes_inside_block_scalar_do_not_close_front_matter(self):
        meta, body = _bridge_parse_front_matter(_BLOCK_SCALAR_WITH_DASHES)
        # The naive split fired on the in-scalar '---' and truncated here.
        assert body.lstrip().startswith("# Demo Agent")
        assert "must NOT close" not in body

    def test_block_style_tools_sequence_is_a_list(self):
        meta, _ = _bridge_parse_front_matter(_BLOCK_SCALAR_WITH_DASHES)
        assert meta["tools"] == ["read", "search"]

    def test_scalar_semantics_unchanged(self):
        meta, _ = _bridge_parse_front_matter(_BLOCK_SCALAR_WITH_DASHES)
        assert meta["name"] == "Demo Agent — Demo"
        assert meta["user-invokable"] is False  # 'false' -> bool

    def test_flow_list_stays_a_string(self):
        # Inline (copilot-vscode style) lists are scalars for this parser —
        # MAP-05 behavior preserved, not widened.
        content = "---\ntools: ['read', 'edit']\n---\nBody\n"
        meta, body = _bridge_parse_front_matter(content)
        assert meta["tools"] == "['read', 'edit']"
        assert body == "Body\n"

    def test_no_front_matter_returns_empty_meta(self):
        meta, body = _bridge_parse_front_matter("# Just a heading\n")
        assert meta == {}
        assert body == "# Just a heading\n"


# ---------------------------------------------------------------------------
# B.1 — graph-path byte-identity across the scanner swap
# ---------------------------------------------------------------------------

class TestGraphScannerSwapByteIdentity:
    def test_yaml_block_shape_matches_shared_scanner_minus_trailing_newline(self):
        yaml_block, body = _split_yaml(_BLOCK_SCALAR_WITH_DASHES)
        shared_yaml, shared_body = parse_yaml_front_matter(_BLOCK_SCALAR_WITH_DASHES)
        assert shared_yaml is not None
        assert yaml_block == _strip_one_newline(shared_yaml)
        assert body == shared_body  # body bytes pass through untouched

    def test_block_scalar_boundary_is_identical_to_shared_scanner(self):
        # The graph path must see the SAME boundary the shared scanner sees —
        # a bare '---' inside the block scalar stays inside the yaml block.
        yaml_block, _ = _split_yaml(_BLOCK_SCALAR_WITH_DASHES)
        assert "must NOT close" in yaml_block
        assert "tools:" in yaml_block

    def test_no_front_matter_returns_none_and_untouched_body(self):
        content = "# Heading\n\nNo front matter here.\n"
        yaml_block, body = _split_yaml(content)
        assert yaml_block is None
        assert body == content

    def test_empty_front_matter_block(self):
        yaml_block, body = _split_yaml("---\n---\nBody only.\n")
        assert yaml_block == ""  # present-but-empty, historical shape
        assert body == "Body only.\n"

    @pytest.mark.skipif(
        not any((REPO / ".github" / "agents").glob("*.agent.md")),
        reason="repo copilot-vscode source team not found or gitignored",
    )
    def test_every_repo_agent_file_splits_identically_to_shared_scanner(self):
        """Byte-identity pin on production content: for every agent file the
        graph path consumes, the swap changed no bytes of yaml or body."""
        agents = sorted((REPO / ".github" / "agents").glob("*.agent.md"))
        assert agents, "expected agent files to pin against"
        for path in agents:
            content = path.read_text(encoding="utf-8")
            yaml_block, body = _split_yaml(content)
            shared_yaml, shared_body = parse_yaml_front_matter(content)
            if shared_yaml is None:
                assert yaml_block is None, path.name
            else:
                assert yaml_block == _strip_one_newline(shared_yaml), path.name
            assert body == shared_body, path.name
