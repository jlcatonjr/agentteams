"""Grok: no scoped tool entry reaches an agent, and the operator guidance names a working route (GROK-FU-R8-SCOPE/TYPE).

Grok Build 1.0.52 ignores a ``Name(scope)`` entry in an agent's ``tools:``: an agent with ``Read, Bash(echo:*)`` was
offered a full shell plus ``search_replace`` (an edit tool), ``monitor`` and ``workflow`` (R8 spike, grok's own
``tool_definitions.json``). The fix drops a scoped allow entry, widens a scoped deny to the whole tool, refuses an
empty effective toolset, and tells the operator (stderr, the build log, interop notices). @security grok-scope-fix
design ruling C1-C5.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import capability_map as cm
from agentteams.frameworks import _grok_tool_scoping as gts
from agentteams.frameworks.grok import GrokAdapter, grok_runtime_block, stale_scope_notices
from agentteams.interop import import_from_cai
from tests._fresh_intel import seed_offline_intel

REPO = Path(__file__).resolve().parent.parent
RETRIEVAL = "Bash(python -m agentteams.research:*)"


def _agent(tools: str, extra: str = "") -> str:  # tools: canonical tokens for the adapter, Claude text for parsers
    return f"---\nname: X\ndescription: \"d\"\ntools: {tools}\n{extra}---\n\n# X\n\nBody.\n"


def _line(text: str, key: str) -> str | None:
    return next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith(f"{key}:")), None)


# The pinned translation (C1, C3) ------------------------------------------------------------------------------

@pytest.mark.parametrize("value, tools, dropped", [
    (f"Read, Grep, Glob, {RETRIEVAL}", "Read, Grep, Glob", RETRIEVAL),
    ("Read(src/**), Grep", "Grep", "Read(src/**)"),          # a non-Bash scope is not honoured either
    ("Task(explore), Read", "Read", "Task(explore)"),        # Agent(type) would let it pick any type
    ("Agent(plan), Read", "Read", "Agent(plan)"),
])
def test_a_scoped_allow_entry_is_dropped_and_reported(value: str, tools: str, dropped: str) -> None:
    out, changes = gts.translate_grok_tools(value)
    assert out == tools and len(changes) == 1 and dropped in changes[0] and "dropped" in changes[0]


def test_a_scoped_deny_entry_becomes_the_whole_tool() -> None:
    out, changes = gts.translate_grok_tools("Bash(rm:*), Edit", deny=True)
    assert out == "Bash, Edit" and "widened deny 'Bash(rm:*)'" in changes[0]


def test_unscoped_entries_are_unchanged_and_unreported() -> None:
    assert gts.translate_grok_tools("Read, Grep, Bash, mcp__github__get_issue") == (
        "Read, Grep, Bash, mcp__github__get_issue", [])


def test_a_list_of_only_scoped_entries_is_refused() -> None:
    with pytest.raises(ValueError, match="inherits every tool"):
        gts.translate_grok_tools("Bash(git diff:*)")


def test_to_grok_tools_keeps_its_signature() -> None:
    assert gts.to_grok_tools(f"Read, {RETRIEVAL}") == "Read"


# The adapter (C2, C4) -----------------------------------------------------------------------------------------

def test_an_empty_effective_toolset_is_refused() -> None:
    # tools: Bash with a scoped Bash deny would become Bash minus Bash: grok's handling of nothing is untested.
    with pytest.raises(ValueError, match="empty effective toolset"):
        GrokAdapter().render_agent_file(_agent("['execute']", "disallowedTools: Bash(rm:*)\n"), "x", {})


def test_a_widened_deny_is_rendered_and_reported(capsys) -> None:
    manifest: dict = {}
    out = GrokAdapter().render_agent_file(_agent("['read', 'execute']", "disallowedTools: Bash(rm:*)\n"), "x", manifest)
    # Grok ignores disallowedTools (grok-scope-verify d/d2), so the deny is applied by leaving Bash out of tools:.
    assert _line(out, "disallowedTools") == "Bash" and _line(out, "tools") == "Read"
    err = capsys.readouterr().err
    assert "[GROK-SCOPE] x: widened deny 'Bash(rm:*)'" in err and "left Bash out of tools:" in err
    assert any("widened deny" in n for n in manifest["_grok_tool_notices"])


def test_a_repeat_render_does_not_repeat_the_notice(capsys) -> None:
    manifest: dict = {}
    for _ in range(2):
        GrokAdapter().render_agent_file(_agent("['read', 'retrieval']"), "r", manifest)
    assert capsys.readouterr().err.count("[GROK-SCOPE]") == 1 and len(manifest["_grok_tool_notices"]) == 1


# End to end: a real grok render through the CLI (C4) ------------------------------------------------------------

@pytest.fixture(scope="module")
def grok_render(tmp_path_factory) -> tuple[Path, subprocess.CompletedProcess]:
    root = tmp_path_factory.mktemp("grokscope")
    proc = subprocess.run(
        [sys.executable, str(REPO / "build_team.py"), "--description", str(REPO / "examples/software-project/brief.json"),
         "--framework", "grok", "--output", str(root), "--project", str(root), "--no-scan", "--yes",
         *seed_offline_intel(root, "grok")],
        cwd=root, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    return root, proc


def test_no_generated_grok_agent_carries_a_scoped_entry(grok_render) -> None:
    root, _ = grok_render
    agents = sorted((root / ".grok" / "agents").glob("*.md"))
    assert agents
    for path in agents:
        for key in ("tools", "disallowedTools"):
            value = _line(path.read_text(encoding="utf-8"), key)
            assert value is None or "(" not in value, f"{path.name} {key}: {value}"


def test_the_retrieval_agent_keeps_only_its_read_tools(grok_render) -> None:
    root, _ = grok_render
    text = (root / ".grok" / "agents" / "tool-doc-researcher.md").read_text(encoding="utf-8")
    assert _line(text, "tools") == "Read, Grep, Glob"


def test_the_operator_is_told_on_stderr_and_in_the_build_log(grok_render) -> None:
    root, proc = grok_render
    assert "[GROK-SCOPE] tool-doc-researcher: dropped" in proc.stderr and "retrieval CLI" in proc.stderr
    log = json.loads((root / ".grok" / "agents" / "references" / "build-log.json").read_text(encoding="utf-8"))
    assert any(n.startswith("tool-doc-researcher: dropped") for n in log["grok_tool_notices"])


def test_a_clean_framework_render_records_no_notice_key(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "build_team.py"), "--description", str(REPO / "examples/software-project/brief.json"),
         "--framework", "claude", "--output", str(tmp_path / ".claude" / "agents"), "--project", str(tmp_path),
         "--no-scan", "--yes", *seed_offline_intel(tmp_path / ".claude" / "agents", "claude")],
        cwd=tmp_path, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-1500:]
    log = json.loads((tmp_path / ".claude" / "agents" / "references" / "build-log.json").read_text(encoding="utf-8"))
    assert "grok_tool_notices" not in log and "[GROK-SCOPE]" not in proc.stderr


# Interop, deployed teams, export (adversarial 2, 6) ------------------------------------------------------------

def test_an_import_into_grok_drops_the_scope_and_reports_it(tmp_path) -> None:
    cai = {
        "schema_version": "2.0", "source_framework": "claude", "source_dir": "x",
        "instructions_binding": {"source_name": "CLAUDE.md", "content": "# Team brief\n"},
        "agents": [{"slug": "researcher", "name": "Researcher", "description": "finds", "body_markdown": "# R\n",
                    "capabilities": cm.capabilities_from_tokens(["read", "search", "retrieval"], "claude"),
                    "handoffs": []}],
    }
    target = tmp_path / ".grok" / "agents"
    target.mkdir(parents=True)
    res = import_from_cai(cai, "grok", target)
    tools = _line((target / "researcher.md").read_text(encoding="utf-8"), "tools")
    assert tools is not None and "(" not in tools
    assert any(n.startswith("[GROK-SCOPE] researcher: dropped") for n in res.notices)


def test_a_deployed_agent_with_a_scope_is_named(tmp_path) -> None:
    agents = tmp_path / ".grok" / "agents"
    agents.mkdir(parents=True)
    (agents / "old.md").write_text(_agent(f"Read, {RETRIEVAL}"), encoding="utf-8")
    (agents / "clean.md").write_text(_agent("Read, Grep"), encoding="utf-8")
    notices = stale_scope_notices(agents)
    assert len(notices) == 1 and "old.md" in notices[0] and "--reconcile-front-matter" in notices[0]
    _kept, guard = GrokAdapter().guard_rendered_files([("clean.md", _agent("Read"))], agents)
    assert any("old.md" in n for n in guard)


def test_a_scoped_grok_agent_exports_as_not_exact() -> None:
    exact = "injectDefaultTools: false\nmcpInheritance: none\n"
    assert cm.grok_exact_toolset(_agent("Read, Grep", exact))
    assert not cm.grok_exact_toolset(_agent(f"Read, {RETRIEVAL}", exact))


# Operator guidance (TYPE, C5) ----------------------------------------------------------------------------------

def test_the_runtime_block_names_the_working_route() -> None:
    block = grok_runtime_block()
    assert "grok --agent .grok/agents/<slug>.md" in block and "v=2" in block
    assert "never" in block and "untyped" in block and "full shell" in block
    assert "replaces" not in block   # the unconfirmed same-name shadowing claim (G15) is gone


def test_the_team_builder_usage_uses_the_path_form() -> None:
    text = (REPO / "agentteams/templates/builder/team-builder-grok.template.md").read_text(encoding="utf-8")
    assert "grok --agent .grok/agents/team-builder.md" in text and 'grok --agent "' not in text


# Review follow-ups (@security step 5 conditions 1-2; @code-hygiene step 4 item 7) ------------------------------

def test_mcp_entries_do_not_count_toward_a_non_empty_toolset() -> None:
    # Under mcpInheritance: none the agent has no MCP servers, so MCP-only (or wildcard-denied MCP) is empty.
    assert gts.effective_grok_tools("mcp__s__t", None) == []
    assert gts.effective_grok_tools("Read, mcp__s__t", "mcp__s__*") == ["Read"]
    with pytest.raises(ValueError, match="empty effective toolset"):
        GrokAdapter().render_agent_file(_agent("['read']", "disallowedTools: Read\n"), "x", {})


def test_a_scoped_todowrite_is_reported() -> None:
    out, changes = gts.translate_grok_tools("TodoWrite(x), Read")
    assert out == "Read" and "TodoWrite(x)" in changes[0]


def test_the_builder_render_reports_a_dropped_scope(capsys) -> None:
    content = "---\nname: Team Builder\ndescription: \"b\"\ntools: ['read', 'retrieval']\n---\n\n# Builder\n"
    manifest: dict = {}
    out = GrokAdapter().render_builder_file(content, manifest)
    assert "tools: Read\n" in out and "Bash(" not in out
    assert "[GROK-SCOPE] team-builder: dropped" in capsys.readouterr().err


def test_a_deployed_agent_with_only_a_scoped_deny_is_named(tmp_path) -> None:
    agents = tmp_path / ".grok" / "agents"
    agents.mkdir(parents=True)
    (agents / "deny.md").write_text(_agent("Read, Bash", "disallowedTools: Bash(rm:*)\n"), encoding="utf-8")
    (agents / "body.md").write_text(_agent("Read") + "\ntools: Bash(x:*) is mentioned in the body only\n",
                                    encoding="utf-8")
    notices = stale_scope_notices(agents)
    assert len(notices) == 1 and "deny.md" in notices[0]   # a body line is not front matter


def test_a_scoped_deny_also_exports_as_not_exact() -> None:
    exact = "injectDefaultTools: false\nmcpInheritance: none\n"
    assert not cm.grok_exact_toolset(_agent("Read, Bash", "disallowedTools: Bash(rm:*)\n" + exact))


def test_the_runtime_block_forbids_an_untyped_retry() -> None:
    assert "never\n  retry it without a type" in grok_runtime_block()


# grok-scope-verify findings: MCPTool breaks the list; disallowedTools is ignored -----------------------------

@pytest.mark.parametrize("value", ["Read, MCPTool", "MCPTool, Grep"])
def test_mcptool_is_dropped_because_it_widens_the_whole_list(value: str) -> None:
    out, changes = gts.translate_grok_tools(value)
    assert "MCPTool" not in out and changes and "discards the whole list" in changes[0]


def test_an_unscoped_deny_is_applied_by_absence(capsys) -> None:
    out = GrokAdapter().render_agent_file(_agent("['read', 'edit']", "disallowedTools: Edit\n"), "x", {})
    assert "Edit" not in _line(out, "tools").split(", ") and _line(out, "disallowedTools") == "Edit"
    assert "left Edit out of tools:" in capsys.readouterr().err


# @security delta conditions 1-3 ---------------------------------------------------------------------------------

def test_an_mcp_wildcard_deny_removes_the_matching_allow_entries() -> None:
    from agentteams.frameworks.grok import _translate_agent_tools
    tools, _ = _translate_agent_tools({}, "x", "Read, mcp__s__t, mcp__other__u", "mcp__s__*")
    assert tools == "Read, mcp__other__u"


@pytest.mark.parametrize("allow", ["Write", "WebFetch, WebSearch", "Write, mcp__s__t"])
def test_a_list_that_grants_nothing_is_refused(allow: str) -> None:
    from agentteams.frameworks.grok import _translate_agent_tools
    with pytest.raises(ValueError, match="empty effective toolset"):
        _translate_agent_tools({}, "x", allow, None)


def test_a_task_deny_removes_agent() -> None:
    from agentteams.frameworks.grok import _translate_agent_tools
    tools, disallowed = _translate_agent_tools({}, "x", "Read, Task", "Task")
    assert tools == "Read" and disallowed == "Agent"


def test_an_mcptool_deny_is_dropped_without_error() -> None:
    from agentteams.frameworks.grok import _translate_agent_tools
    tools, disallowed = _translate_agent_tools({}, "x", "Read, Grep", "MCPTool")
    assert tools == "Read, Grep" and not disallowed
