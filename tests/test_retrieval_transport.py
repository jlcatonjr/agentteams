"""Retrieval transport guards — the `retrieval` tool token, and the standing no-MCP decision.

Policy: references/retrieval-transport-policy.md. External retrieval in this project is
CLI-mediated. Neither MCP servers nor host-native WebSearch/WebFetch are the transport.

These are governance tests. They exist because a gap and a decision look identical from the
outside: without them, the next agent reading the 2026-07-30 retrieval report will read
"no retrieval transport wired up" as an oversight and helpfully add one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.frameworks.claude import (
    _CLAUDE_DEFAULT_ALLOWED_TOOLS,
    _RETRIEVAL_CLI_SCOPE,
    _VSCODE_TO_CLAUDE_TOOLS,
    _map_allowed_tools,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES = _REPO_ROOT / "agentteams" / "templates"
_POLICY = _REPO_ROOT / "references" / "retrieval-transport-policy.md"


def _agent(tools: str) -> str:
    return f"---\nname: X\ndescription: \"d\"\ntools: {tools}\nmodel: [\"m\"]\n---\n\nBody.\n"


# --- the token maps to a scoped command, not a shell -----------------------

def test_retrieval_token_maps_to_a_scoped_bash_permission():
    assert _VSCODE_TO_CLAUDE_TOOLS["retrieval"] == (_RETRIEVAL_CLI_SCOPE,)
    assert _RETRIEVAL_CLI_SCOPE.startswith("Bash(")
    assert _RETRIEVAL_CLI_SCOPE.endswith(":*)")
    assert "agentteams.research" in _RETRIEVAL_CLI_SCOPE


def test_retrieval_grant_is_not_bare_bash():
    """The whole point: web access must not imply arbitrary command execution."""
    mapped = _map_allowed_tools(_agent("['read', 'retrieval']"))
    tools = [t.strip() for t in mapped.split(",")]
    assert "Bash" not in tools, "retrieval must never widen to unrestricted Bash"
    assert _RETRIEVAL_CLI_SCOPE in mapped


def test_retrieval_scope_contains_no_comma():
    """`allowed-tools` is a comma-separated list; a comma in the scope would split it in two
    and produce two malformed entries."""
    assert "," not in _RETRIEVAL_CLI_SCOPE


def test_execute_still_maps_to_unrestricted_bash():
    assert "Bash" in _map_allowed_tools(_agent("['read', 'execute']")).split(", ")


def test_execute_absorbs_retrieval_rather_than_emitting_both():
    """Emitting `Bash, Bash(...)` would imply the scope constrains something. It does not."""
    mapped = _map_allowed_tools(_agent("['read', 'execute', 'retrieval']"))
    assert "Bash" in [t.strip() for t in mapped.split(",")]
    assert _RETRIEVAL_CLI_SCOPE not in mapped


# --- least privilege for read-only agents ----------------------------------

def test_read_only_auditor_mapping_is_unchanged():
    """Regression guard on the existing least-privilege invariant."""
    mapped = _map_allowed_tools(_agent("['read', 'search']"))
    assert mapped == "Read, Grep, Glob"


def test_no_read_only_auditor_template_gained_retrieval():
    """A read-only agent's invariant is constitutional; network egress is a side effect.

    Retrieval belongs to agents whose charter is external verification, and those agents are
    enumerated explicitly rather than discovered by accident.
    """
    allowed = {"tool-doc-researcher", "reference-manager"}
    offenders = []
    for path in _TEMPLATES.rglob("*.template.md"):
        head = path.read_text(encoding="utf-8")[:1200]
        for line in head.splitlines():
            if line.startswith("tools:") and "retrieval" in line:
                stem = path.name.replace(".template.md", "")
                if stem not in allowed:
                    offenders.append(stem)
    assert not offenders, (
        f"templates gained the retrieval token without a policy update: {sorted(offenders)}. "
        f"Add them to references/retrieval-transport-policy.md and to this test's allowlist, "
        f"or remove the grant."
    )


def test_the_two_external_verification_agents_do_have_retrieval():
    """The converse guard: the grant must not silently disappear either."""
    for stem in ("tool-doc-researcher", "reference-manager"):
        matches = list(_TEMPLATES.rglob(f"{stem}.template.md"))
        assert matches, f"{stem} template not found"
        head = matches[0].read_text(encoding="utf-8")[:1200]
        tools_line = next(ln for ln in head.splitlines() if ln.startswith("tools:"))
        assert "retrieval" in tools_line, f"{stem} lost its retrieval grant"


# --- no host-native web tools ----------------------------------------------

def test_no_vocabulary_token_grants_websearch_or_webfetch():
    """Policy: retrieval is CLI-mediated. Host-native web tools are deliberately not wired."""
    granted = {tool for tools in _VSCODE_TO_CLAUDE_TOOLS.values() for tool in tools}
    assert "WebSearch" not in granted
    assert "WebFetch" not in granted


def test_default_allowed_tools_grants_no_web_tool():
    assert "WebSearch" not in _CLAUDE_DEFAULT_ALLOWED_TOOLS
    assert "WebFetch" not in _CLAUDE_DEFAULT_ALLOWED_TOOLS


# --- no MCP in the retrieval path ------------------------------------------

#: Real MCP wiring, as opposed to prose about MCP. The research modules deliberately DO
#: mention MCP in comments — explaining why it is not the transport is the point of the policy
#: — so a bare substring check would flag the documentation it is supposed to protect.
_MCP_WIRING_PATTERNS = (
    "import mcp",
    "from mcp",
    "mcpservers",
    "mcp_servers",
    "mcp_server",
    ".mcp.json",
    "modelcontextprotocol",
)


def _executable_source(path: Path) -> str:
    """Return a module's source with docstrings and comments removed, lowercased.

    Comments and docstrings are where this project explains its *refusal* to use MCP, so they
    must be excluded before checking for MCP usage — otherwise the guard fires on the very
    documentation that records the decision.
    """
    import ast
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    docstrings = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.string.strip("rbuf'\"") in docstrings:
            continue
        pieces.append(tok.string)
    return " ".join(pieces).lower()


def test_the_research_package_has_no_mcp_wiring():
    """The retrieval implementation must not introduce an MCP dependency or definition.

    Checks executable source only — the modules' own comments discuss MCP precisely because
    the decision not to use it needs recording next to the code it governs.
    """
    research = _REPO_ROOT / "agentteams" / "research"
    for path in sorted(research.glob("*.py")):
        code = _executable_source(path)
        found = [p for p in _MCP_WIRING_PATTERNS if p in code]
        assert not found, (
            f"{path.name} contains MCP wiring {found}. External retrieval is CLI-mediated by "
            f"standing operator decision — see references/retrieval-transport-policy.md."
        )


def test_the_guard_would_actually_catch_mcp_wiring(tmp_path):
    """A guard that cannot fail is not a guard.

    Proves the docstring/comment stripping did not neuter the check: real wiring in executable
    code is still detected, while the same words in a comment are correctly ignored.
    """
    offending = tmp_path / "offending.py"
    offending.write_text('"""Doc."""\nimport mcp\n', encoding="utf-8")
    assert any(p in _executable_source(offending) for p in _MCP_WIRING_PATTERNS)

    innocent = tmp_path / "innocent.py"
    innocent.write_text('"""We deliberately do not import mcp."""\n# no mcp_servers here\nx = 1\n',
                        encoding="utf-8")
    assert not any(p in _executable_source(innocent) for p in _MCP_WIRING_PATTERNS)


def test_retrieval_transport_policy_is_recorded():
    """A refusal that is not written down gets reversed by the next helpful agent."""
    assert _POLICY.is_file(), "references/retrieval-transport-policy.md is missing"
    text = _POLICY.read_text(encoding="utf-8")
    for expected in ("MCP", "WebSearch", "CLI", "python -m agentteams.research"):
        assert expected in text, f"policy does not address {expected!r}"


@pytest.mark.parametrize("subcommand", ["search", "fetch", "browser", "scholar"])
def test_policy_documents_every_cli_subcommand(subcommand):
    """If a retrieval surface exists but the policy does not name it, the policy is stale."""
    assert subcommand in _POLICY.read_text(encoding="utf-8")
