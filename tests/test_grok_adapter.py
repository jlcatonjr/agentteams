"""xAI Grok Build adapter (``--framework grok``): Phase 1 acceptance (plan tmp/by-week/2026-W42).

Covers the Component Brief's checklist (grok-adapter.brief.md §8, items 1–13 and 24):

* registration, layout and paths (``.grok/agents``, the shared repo-root ``AGENTS.md``);
* fail-closed tool translation (``_grok_tool_scoping``) and the two keys that make ``tools:`` an exact limit
  (``injectDefaultTools: false``, ``mcpInheritance: none``: an empty list or the defaults widen the agent);
* no Claude-only artifacts under ``.grok/``; no model/permissionMode keys; write_policy refusal;
* the instructions render (Grok notice + one ``grok_runtime`` fence, idempotent) and the builder;
* the CLI refusals of convert / bridge / interop / absorb;
* the version pin lives only in ``FormatSpec``.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest

from agentteams.frameworks import _grok_tool_scoping as gts
from agentteams.frameworks._agents_md_ownership import GROK_AGENTS_MD_MARKER, GROK_FENCE_ID
from agentteams.frameworks.base import FrameworkAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.format_spec import FORMAT_SPECS, FormatSpec
from agentteams.frameworks.grok import (
    GROK_AGENT_FRONT_MATTER_KEYS,
    GROK_EMITTED_KEYS,
    GrokAdapter,
    grok_runtime_block,
)
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.yaml_frontmatter import parse_yaml_front_matter

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATES = _REPO / "agentteams" / "templates"


def _agent(tools: str, *, extra: str = "", name: str = "Navigator — Demo") -> str:
    return f"---\nname: {name}\ndescription: \"Finds files\"\ntools: {tools}\n{extra}---\n\n# Navigator\n\nBody.\n"


def _keys(text: str) -> list[str]:
    yaml_text, _ = parse_yaml_front_matter(text)
    assert yaml_text is not None
    return [m.group(1) for m in re.finditer(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:", yaml_text, re.MULTILINE)]


def _value(text: str, key: str) -> str | None:
    yaml_text, _ = parse_yaml_front_matter(text)
    m = re.search(rf"^{key}:\s*(.*)$", yaml_text or "", re.MULTILINE)
    return m.group(1).strip() if m else None


# 1–2, 9–10: registration and layout -------------------------------------------------------------

def test_registered_and_subclasses_claude() -> None:
    assert FRAMEWORKS["grok"] is GrokAdapter
    assert issubclass(GrokAdapter, ClaudeAdapter)
    assert GrokAdapter().framework_id == "grok"


@pytest.mark.parametrize("given, expected", [
    (".", ".grok/agents"), ("x/.grok", "x/.grok/agents"), ("x/.grok/agents", "x/.grok/agents"),
])
def test_paths(given: str, expected: str, tmp_path: Path) -> None:
    a = GrokAdapter()
    assert a.get_agents_dir(tmp_path) == tmp_path / ".grok" / "agents"
    assert a.normalize_output_path(Path(given)) == Path(expected)


def test_instructions_go_to_repo_root_agents_md() -> None:
    a = GrokAdapter()
    out = a.finalize_output_path("../copilot-instructions.md", "instructions")
    assert out == "../../AGENTS.md" and "CLAUDE" not in out


def test_layout_hooks() -> None:
    a = GrokAdapter()
    assert a.vscode_tasks_rel_path() is None
    assert a.framework_root_prefix() == ".grok"
    assert a.skill_output_rel_path("x") == "../skills/x/SKILL.md"
    assert a.has_skill_concept() and not a.supports_handoffs() and a.handoff_delivery_mode() == "manifest"


def test_no_claude_artifacts_under_grok() -> None:
    a = GrokAdapter()
    manifest = {"project_name": "Demo"}
    files = a.extra_output_files(manifest)
    # The neutral baseline plus the .grok/hooks stub that keeps the dir launcher-protected (Phase 2, P2-A).
    assert files[:-1] == FrameworkAdapter.extra_output_files(a, manifest)
    assert files[-1][0] == "../hooks/README.md" and "fail open" in files[-1][1]
    joined = " ".join(p for p, _ in files)
    for forbidden in ("constitutional-gate", "settings.hooks", "runner"):
        assert forbidden not in joined


# 4–8: agent front matter ------------------------------------------------------------------------

def test_exact_toolset_keys_on_every_agent() -> None:
    out = GrokAdapter().render_agent_file(_agent("['read', 'search']"), "navigator", {})
    assert _value(out, "tools") == "Read, Grep, Glob"
    assert _value(out, "injectDefaultTools") == "false"
    assert _value(out, "mcpInheritance") == "none"
    assert set(_keys(out)) <= set(GROK_EMITTED_KEYS) <= set(GROK_AGENT_FRONT_MATTER_KEYS)


def test_task_maps_to_agent_and_todowrite_is_dropped() -> None:
    out = GrokAdapter().render_agent_file(_agent("['read', 'todo', 'agent']"), "orchestrator", {})
    assert _value(out, "tools") == "Read, Agent"


def test_scoped_retrieval_is_kept_verbatim() -> None:
    out = GrokAdapter().render_agent_file(_agent("['read', 'retrieval']"), "researcher", {})
    assert _value(out, "tools") == "Read, Bash(python -m agentteams.research:*)"


def test_todo_only_refuses_rather_than_widening() -> None:
    with pytest.raises(ValueError, match="inherits every tool"):
        GrokAdapter().render_agent_file(_agent("['todo']"), "x", {})


def test_no_model_or_permission_mode_emitted() -> None:
    text = _agent("['read']", extra='model: ["Claude Opus 4.8 (copilot)"]\npermissionMode: plan\n')
    out = GrokAdapter().render_agent_file(text, "x", {})
    assert "model" not in _keys(out) and "permissionMode" not in _keys(out)


def test_untranslatable_disallowed_tools_refuses() -> None:
    with pytest.raises(ValueError, match="widen"):
        GrokAdapter().render_agent_file(_agent("['read']", extra="disallowedTools: TodoWrite\n"), "x", {})
    out = GrokAdapter().render_agent_file(_agent("['read', 'execute']", extra="disallowedTools: Task\n"), "x", {})
    assert _value(out, "disallowedTools") == "Agent"


def test_write_policy_grant_is_refused() -> None:
    manifest = {"write_policy": "orchestrator-only",
                "mcp_grants": {"primary-producer": {"tools": ["write_file"]}}}
    with pytest.raises(ValueError, match="orchestrator-only"):
        GrokAdapter().render_agent_file(_agent("['read', 'edit']"), "primary-producer", manifest)


@pytest.mark.parametrize("path", sorted((_TEMPLATES / "universal").glob("*.template.md"))
                         + sorted((_TEMPLATES / "domain").glob("*.template.md")), ids=lambda p: p.name)
def test_every_template_renders_within_the_grok_vocabulary(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    yaml_text, _ = parse_yaml_front_matter(text)
    if yaml_text is None or not re.search(r"^name:", yaml_text, re.MULTILINE):
        pytest.skip("not an agent template")
    out = GrokAdapter().render_agent_file(text, path.name.split(".")[0], {"project_name": "Demo"})
    assert set(_keys(out)) <= set(GROK_EMITTED_KEYS)
    for entry in gts.split_tool_list(_value(out, "tools") or ""):
        assert re.match(r"^[A-Za-z]+", entry).group(0) in gts.GROK_TOOL_NAMES, entry


# tool scoping module ----------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("Read, Grep, Glob", "Read, Grep, Glob"),
    ("Bash(git diff:*), Read", "Bash(git diff:*), Read"),
    ("Task, Read, Read", "Agent, Read"),
    ("mcp__github__get_issue", "mcp__github__get_issue"),
])
def test_to_grok_tools(value: str, expected: str) -> None:
    assert gts.to_grok_tools(value) == expected


@pytest.mark.parametrize("value", ["Read, LS", "NotebookEdit", "Bash(unbalanced", "TodoWrite"])
def test_to_grok_tools_refuses(value: str) -> None:
    with pytest.raises(ValueError):
        gts.to_grok_tools(value)


# 11–12: instructions and builder ----------------------------------------------------------------

def test_instructions_render() -> None:
    a = GrokAdapter()
    out = a.render_instructions_file("# Team\n\nRules.\n", {})
    assert out.startswith(GROK_AGENTS_MD_MARKER)
    assert out.count(GROK_AGENTS_MD_MARKER) == 1
    assert out.count(f"AGENTTEAMS:BEGIN {GROK_FENCE_ID}") == 1
    assert "Copilot Instructions" not in out
    assert a.render_instructions_file(out, {}) == out  # idempotent: no stacked notice or fence


def test_runtime_block_tells_operator_to_review_before_trusting() -> None:
    block = grok_runtime_block()
    assert block.index("Review") < block.index("grok --trust")


def test_builder_render() -> None:
    template = (_TEMPLATES / "builder" / "team-builder-grok.template.md").read_text(encoding="utf-8")
    out = GrokAdapter().render_builder_file(template, {})
    assert _value(out, "name") == "Team Builder — Agent Teams Module"
    assert _value(out, "tools") == "Read, Edit, Write, Grep, Glob, Bash"
    assert _value(out, "injectDefaultTools") == "false" and _value(out, "mcpInheritance") == "none"
    assert "CLAUDE.md" not in out


def test_builder_without_canonical_tools_refuses() -> None:
    with pytest.raises(ValueError):
        GrokAdapter().render_builder_file("---\nname: B\ndescription: \"d\"\n---\nbody\n", {})


# 13: CLI refusals --------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [  # Phase 3 (Codex posture): interop/absorb accept grok; convert/bridge never do
    ["--framework", "grok", "--convert-from", "x"],
    ["--framework", "grok", "--bridge-from", "x"],
    ["--framework", "claude", "--bridge-from", "x", "--bridge-source-framework", "grok"],
])
def test_cli_refuses_unvetted_flows(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    import build_team

    with pytest.raises(SystemExit) as exc:
        build_team.main(argv)
    assert exc.value.code == 2
    assert "grok" in capsys.readouterr().err


# 24: version pin --------------------------------------------------------------------------------

def test_version_pinned_only_in_format_spec() -> None:
    assert "pinned_version" in {f.name for f in fields(FormatSpec)}
    assert FormatSpec.__dataclass_fields__["pinned_version"].default is None
    pinned = FORMAT_SPECS["grok"].pinned_version
    assert pinned and re.fullmatch(r"\d+\.\d+\.\d+", pinned)
    from agentteams.framework_research import FRAMEWORK_REGISTRY

    assert FRAMEWORK_REGISTRY["grok"]["pinned_version"] == pinned
    assert f"Grok Build {pinned}" in (_REPO / "references" / "grok-agent-infrastructure-expert.md").read_text(
        encoding="utf-8")
    assert pinned not in (_REPO / "agentteams" / "frameworks" / "grok.py").read_text(encoding="utf-8")
