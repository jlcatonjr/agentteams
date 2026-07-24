"""
Tests for agentteams/frameworks/ — CopilotVSCodeAdapter, CopilotCLIAdapter, ClaudeAdapter.
"""

from pathlib import Path
import pytest

from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.goose import GooseAdapter, _goosehints_content


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_MANIFEST = {"project_name": "TestProject", "framework": "copilot-vscode"}

FULL_YAML_CONTENT = """\
---
name: Navigator — TestProject
description: "Navigate the project"
user-invokable: false
tools: ['read', 'edit', 'search']
model: ["Claude Sonnet 4.6 (copilot)"]
---

# Navigator

Body text.
"""

CONTENT_NO_YAML = """\
# Navigator

Body text.
"""

CONTENT_WITH_HANDOFFS = """\
---
name: Orchestrator — TestProject
description: "Coordinate all agents"
user-invokable: true
tools: ['read', 'edit', 'search']
model: ["Claude Sonnet 4.6 (copilot)"]
---

# Orchestrator

Main body.

## Handoff Instructions

- Hand off to `@navigator` for file lookups.
- Hand off to `@security` for destructive operations.

## Rules

Always close with auditor.
"""

CONTENT_WITH_YAML_HANDOFFS = """\
---
name: Orchestrator — TestProject
description: "Coordinate all agents"
user-invokable: true
tools: ['read', 'edit', 'search']
model: ["Claude Sonnet 4.6 (copilot)"]
handoffs:
    - label: File lookup
        agent: navigator
        prompt: "Inspect the relevant files."
        send: false
    - label: Security review
        agent: security
        prompt: "Review the destructive operation."
        send: true
---

# Orchestrator

Main body.
"""


# ===========================================================================
# CopilotVSCodeAdapter
# ===========================================================================

class TestCopilotVSCodeAdapter:

    def setup_method(self):
        self.adapter = CopilotVSCodeAdapter()

    # --- Identity ---

    def test_framework_id(self):
        assert self.adapter.framework_id == "copilot-vscode"

    def test_supports_handoffs(self):
        assert self.adapter.supports_handoffs() is True

    def test_vscode_tasks_rel_path(self):
        assert self.adapter.vscode_tasks_rel_path() == "../../.vscode/tasks.json"

    def test_get_file_extension_agent(self):
        assert self.adapter.get_file_extension("agent") == ".agent.md"

    def test_get_file_extension_other(self):
        assert self.adapter.get_file_extension("instructions") == ".md"

    def test_get_agents_dir(self):
        result = self.adapter.get_agents_dir(Path("/project"))
        assert result == Path("/project/.github/agents")

    def test_finalize_output_path_keeps_vscode_agent_extension(self):
        result = self.adapter.finalize_output_path("orchestrator.agent.md", "agent")
        assert result == "orchestrator.agent.md"

    # --- render_instructions_file (pass-through) ---

    def test_render_instructions_file_passthrough(self):
        content = "# Instructions\n\nSome text."
        assert self.adapter.render_instructions_file(content, MINIMAL_MANIFEST) == content

    # --- render_agent_file: content with complete valid front matter ---

    def test_render_agent_file_complete_yaml_unchanged(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert result.startswith("---\n")
        assert "name: Navigator" in result
        assert "model:" in result

    # --- render_agent_file: no front matter → prepend defaults ---

    def test_render_agent_file_no_yaml_prepends_front_matter(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "navigator", MINIMAL_MANIFEST)
        assert result.startswith("---\n")
        assert "name:" in result
        assert "model:" in result
        assert "# Navigator" in result

    def test_render_agent_file_no_yaml_slug_appears_in_name(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "my-agent", MINIMAL_MANIFEST)
        assert "My Agent" in result

    def test_render_agent_file_no_yaml_project_name_in_name(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "navigator", MINIMAL_MANIFEST)
        assert "TestProject" in result

    # --- render_agent_file: missing required keys → appended with defaults ---

    def test_render_agent_file_missing_model_key_gets_default(self):
        content_missing_model = (
            "---\n"
            "name: Navigator — TestProject\n"
            "description: \"Navigate\"\n"
            "user-invokable: false\n"
            "tools: ['read']\n"
            "---\n\n"
            "# Body\n"
        )
        result = self.adapter.render_agent_file(content_missing_model, "navigator", MINIMAL_MANIFEST)
        assert "model:" in result

    def test_render_agent_file_missing_tools_key_gets_default(self):
        content_missing_tools = (
            "---\n"
            "name: Navigator — TestProject\n"
            "description: \"Navigate\"\n"
            "user-invokable: false\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "---\n\n"
            "# Body\n"
        )
        result = self.adapter.render_agent_file(content_missing_tools, "navigator", MINIMAL_MANIFEST)
        assert "tools:" in result

    # --- render_agent_file: body content preserved ---

    def test_render_agent_file_body_preserved(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert "Body text." in result

    def test_render_agent_file_handoffs_preserved(self):
        """VS Code adapter keeps handoff sections intact."""
        result = self.adapter.render_agent_file(CONTENT_WITH_HANDOFFS, "orchestrator", MINIMAL_MANIFEST)
        assert "Handoff" in result

    # --- Bug 3: agents: list filtering based on team manifest ---

    def test_render_agent_file_filters_absent_agents_from_yaml_list(self):
        """Agents not in manifest output_files are removed from the agents: YAML list."""
        content = (
            "---\n"
            "name: Expert — TestProject\n"
            "description: 'Expert'\n"
            "user-invokable: false\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "agents: ['primary-producer', 'reference-manager', 'adversarial']\n"
            "---\n\n# Body\n"
        )
        manifest = {
            "project_name": "TestProject",
            "output_files": [
                {"path": "primary-producer.agent.md", "type": "agent"},
                {"path": "adversarial.agent.md", "type": "agent"},
                # reference-manager is intentionally absent
            ],
        }
        result = self.adapter.render_agent_file(content, "expert", manifest)
        assert "'primary-producer'" in result
        assert "'adversarial'" in result
        assert "'reference-manager'" not in result

    def test_render_agent_file_filters_absent_agents_from_yaml_list_double_and_bare(self):
        """Flow-list parser accepts double-quoted and bare slugs."""
        content = (
            "---\n"
            "name: Expert — TestProject\n"
            "description: 'Expert'\n"
            "user-invokable: false\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "agents: [\"primary-producer\", adversarial, \"reference-manager\"]\n"
            "---\n\n# Body\n"
        )
        manifest = {
            "project_name": "TestProject",
            "output_files": [
                {"path": "primary-producer.agent.md", "type": "agent"},
                {"path": "adversarial.agent.md", "type": "agent"},
            ],
        }
        result = self.adapter.render_agent_file(content, "expert", manifest)
        assert "'primary-producer'" in result
        assert "'adversarial'" in result
        assert "reference-manager" not in result

    def test_render_agent_file_filters_absent_agents_from_yaml_block_list(self):
        """Block-list agents syntax is filtered to generated team members."""
        content = (
            "---\n"
            "name: Expert — TestProject\n"
            "description: 'Expert'\n"
            "user-invokable: false\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "agents:\n"
            "  - primary-producer\n"
            "  - reference-manager\n"
            "  - adversarial\n"
            "---\n\n# Body\n"
        )
        manifest = {
            "project_name": "TestProject",
            "output_files": [
                {"path": "primary-producer.agent.md", "type": "agent"},
                {"path": "adversarial.agent.md", "type": "agent"},
            ],
        }
        result = self.adapter.render_agent_file(content, "expert", manifest)
        assert "primary-producer" in result
        assert "adversarial" in result
        assert "reference-manager" not in result

    def test_render_agent_file_filters_absent_agents_from_handoffs(self):
        """Handoff entries for agents not in the team are removed from the YAML front matter."""
        content = (
            "---\n"
            "name: Expert — TestProject\n"
            "description: 'Expert'\n"
            "user-invokable: false\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "handoffs:\n"
            "  - label: Invoke Producer\n"
            "    agent: primary-producer\n"
            "    prompt: \"Draft the component.\"\n"
            "    send: false\n"
            "  - label: Verify Citations\n"
            "    agent: reference-manager\n"
            "    prompt: \"Verify citation keys.\"\n"
            "    send: false\n"
            "---\n\n# Body\n"
        )
        manifest = {
            "project_name": "TestProject",
            "output_files": [
                {"path": "primary-producer.agent.md", "type": "agent"},
                # reference-manager is intentionally absent
            ],
        }
        result = self.adapter.render_agent_file(content, "expert", manifest)
        assert "primary-producer" in result
        assert "Invoke Producer" in result
        assert "reference-manager" not in result
        assert "Verify Citations" not in result

    def test_render_agent_file_filters_handoffs_with_flexible_formatting(self):
        """Handoff filtering tolerates common indentation and additional keys."""
        content = (
            "---\n"
            "name: Expert — TestProject\n"
            "description: 'Expert'\n"
            "user-invokable: false\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "handoffs:\n"
            "   - label: Keep\n"
            "     agent: \"primary-producer\"\n"
            "     prompt: \"Draft\"\n"
            "     send: false\n"
            "     notes: optional\n"
            "   - label: Drop\n"
            "     agent: \"reference-manager\"\n"
            "     prompt: \"Verify\"\n"
            "     send: false\n"
            "---\n\n# Body\n"
        )
        manifest = {
            "project_name": "TestProject",
            "output_files": [
                {"path": "primary-producer.agent.md", "type": "agent"},
            ],
        }
        result = self.adapter.render_agent_file(content, "expert", manifest)
        assert "primary-producer" in result
        assert "reference-manager" not in result

    def test_render_agent_file_orchestrator_always_kept_in_handoffs(self):
        """@orchestrator is always retained in handoffs even when absent from output_files."""
        content = (
            "---\n"
            "name: Expert — TestProject\n"
            "description: 'Expert'\n"
            "user-invokable: false\n"
            "tools: ['read', 'search']\n"
            "model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
            "handoffs:\n"
            "  - label: Return to Orchestrator\n"
            "    agent: orchestrator\n"
            "    prompt: \"Done.\"\n"
            "    send: false\n"
            "---\n\n# Body\n"
        )
        manifest = {"project_name": "TestProject", "output_files": []}
        result = self.adapter.render_agent_file(content, "expert", manifest)
        assert "orchestrator" in result
# ===========================================================================

class TestCopilotCLIAdapter:

    def setup_method(self):
        self.adapter = CopilotCLIAdapter()

    # --- Identity ---

    def test_framework_id(self):
        assert self.adapter.framework_id == "copilot-cli"

    def test_supports_handoffs(self):
        assert self.adapter.supports_handoffs() is False

    def test_vscode_tasks_rel_path(self):
        assert self.adapter.vscode_tasks_rel_path() is None

    def test_handoff_delivery_mode(self):
        assert self.adapter.handoff_delivery_mode() == "manifest"

    def test_get_file_extension(self):
        assert self.adapter.get_file_extension("agent") == ".md"

    def test_get_agents_dir(self):
        result = self.adapter.get_agents_dir(Path("/project"))
        assert result == Path("/project/.github/copilot")

    def test_finalize_output_path_maps_agent_to_plain_markdown(self):
        result = self.adapter.finalize_output_path("orchestrator.agent.md", "agent")
        assert result == "orchestrator.md"

    # --- render_agent_file: YAML stripped ---

    def test_render_agent_file_strips_yaml_front_matter(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert not result.startswith("---")
        assert "name:" not in result
        assert "model:" not in result

    def test_render_agent_file_preserves_body(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert "# Navigator" in result
        assert "Body text." in result

    # --- render_agent_file: handoffs stripped ---

    def test_render_agent_file_strips_handoff_heading_block(self):
        result = self.adapter.render_agent_file(CONTENT_WITH_HANDOFFS, "orchestrator", MINIMAL_MANIFEST)
        assert "Handoff" not in result

    def test_render_agent_file_preserves_non_handoff_sections(self):
        result = self.adapter.render_agent_file(CONTENT_WITH_HANDOFFS, "orchestrator", MINIMAL_MANIFEST)
        assert "## Rules" in result
        assert "Always close with auditor." in result

    def test_extract_handoffs_reads_yaml_handoff_entries(self):
        result = self.adapter.extract_handoffs(CONTENT_WITH_HANDOFFS)
        assert [entry["agent"] for entry in result] == ["navigator", "security"]
        assert result[0]["label"]

    def test_extract_handoffs_reads_yaml_front_matter_entries(self):
        result = self.adapter.extract_handoffs(CONTENT_WITH_YAML_HANDOFFS)
        assert [entry["agent"] for entry in result] == ["navigator", "security"]
        assert result[1]["send"] is True

    # --- render_agent_file: no YAML/handoffs → body preserved as-is ---

    def test_render_agent_file_clean_content_passthrough(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "navigator", MINIMAL_MANIFEST)
        assert "# Navigator" in result
        assert "Body text." in result

    # --- output always ends with newline ---

    def test_render_agent_file_ends_with_newline(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert result.endswith("\n")

    # --- render_instructions_file (pass-through) ---

    def test_render_instructions_file_passthrough(self):
        content = "# Instructions\n\nSome text."
        assert self.adapter.render_instructions_file(content, MINIMAL_MANIFEST) == content


# ===========================================================================
# ClaudeAdapter
# ===========================================================================

class TestClaudeAdapter:

    def setup_method(self):
        self.adapter = ClaudeAdapter()

    # --- Identity ---

    def test_framework_id(self):
        assert self.adapter.framework_id == "claude"

    def test_supports_handoffs(self):
        assert self.adapter.supports_handoffs() is False

    def test_vscode_tasks_rel_path(self):
        assert self.adapter.vscode_tasks_rel_path() == "../../.vscode/tasks.json"

    def test_handoff_delivery_mode(self):
        assert self.adapter.handoff_delivery_mode() == "manifest"

    def test_get_file_extension(self):
        assert self.adapter.get_file_extension("agent") == ".md"

    def test_get_agents_dir(self):
        result = self.adapter.get_agents_dir(Path("/project"))
        assert result == Path("/project/.claude/agents")

    def test_finalize_output_path_maps_agent_to_plain_markdown(self):
        result = self.adapter.finalize_output_path("orchestrator.agent.md", "agent")
        assert result == "orchestrator.md"

    def test_finalize_output_path_maps_instructions_to_claude_md(self):
        result = self.adapter.finalize_output_path("../copilot-instructions.md", "instructions")
        assert result == "../CLAUDE.md"

    # --- render_agent_file: VS Code YAML replaced by Claude front matter ---

    def test_render_agent_file_starts_with_claude_front_matter(self):
        """Output must start with '---' (Claude Code front matter, not VS Code YAML)."""
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert result.startswith("---\n")

    def test_render_agent_file_contains_allowed_tools(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert "allowed-tools:" in result

    def test_render_agent_file_no_vscode_keys_in_output(self):
        """VS Code-specific keys must not appear in the Claude output."""
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert "user-invokable:" not in result
        assert "tools: [" not in result
        # model: from VS Code format should not be passed through
        assert "claude sonnet 4.6 (copilot)" not in result.lower()

    def test_render_agent_file_extracts_name_from_vscode_yaml(self):
        """name: from VS Code YAML must appear in the Claude front matter."""
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        # The VS Code name was 'Navigator — TestProject'
        assert "name: Navigator" in result

    def test_render_agent_file_extracts_description_from_vscode_yaml(self):
        """description: from VS Code YAML must appear in the Claude front matter."""
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert "description:" in result
        assert "Navigate the project" in result

    def test_render_agent_file_preserves_body(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert "# Navigator" in result
        assert "Body text." in result

    # --- render_agent_file: no input YAML → front matter derived from slug ---

    def test_render_agent_file_no_input_yaml_still_emits_front_matter(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "navigator", MINIMAL_MANIFEST)
        assert result.startswith("---\n")
        assert "allowed-tools:" in result

    def test_render_agent_file_no_input_yaml_name_derived_from_slug(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "my-agent", MINIMAL_MANIFEST)
        assert "My Agent" in result

    def test_render_agent_file_no_input_yaml_body_preserved(self):
        result = self.adapter.render_agent_file(CONTENT_NO_YAML, "navigator", MINIMAL_MANIFEST)
        assert "# Navigator" in result
        assert "Body text." in result

    # --- render_agent_file: handoffs stripped ---

    def test_render_agent_file_strips_handoff_heading_block(self):
        result = self.adapter.render_agent_file(CONTENT_WITH_HANDOFFS, "orchestrator", MINIMAL_MANIFEST)
        assert "Handoff" not in result

    def test_render_agent_file_preserves_non_handoff_sections(self):
        result = self.adapter.render_agent_file(CONTENT_WITH_HANDOFFS, "orchestrator", MINIMAL_MANIFEST)
        assert "## Rules" in result
        assert "Always close with auditor." in result

    # --- output always ends with newline ---

    def test_render_agent_file_ends_with_newline(self):
        result = self.adapter.render_agent_file(FULL_YAML_CONTENT, "navigator", MINIMAL_MANIFEST)
        assert result.endswith("\n")

    # --- render_instructions_file (pass-through) ---

    def test_render_instructions_file_passthrough(self):
        content = "# Instructions\n\nSome text."
        assert self.adapter.render_instructions_file(content, MINIMAL_MANIFEST) == content


# ===========================================================================
# Cross-adapter consistency
# ===========================================================================

def test_cli_strips_yaml_to_plain_markdown():
    """CLI adapter produces plain Markdown with no YAML front matter at all."""
    cli = CopilotCLIAdapter()
    result = cli.render_agent_file(FULL_YAML_CONTENT, "nav", MINIMAL_MANIFEST)
    assert not result.startswith("---")
    assert "# Navigator" in result
    assert "name:" not in result
    assert "model:" not in result


def test_claude_replaces_vscode_yaml_with_claude_yaml():
    """Claude adapter replaces VS Code YAML with Claude Code-compatible front matter."""
    claude = ClaudeAdapter()
    result = claude.render_agent_file(FULL_YAML_CONTENT, "nav", MINIMAL_MANIFEST)
    # Must have front matter (Claude format)
    assert result.startswith("---\n")
    # Must contain Claude key
    assert "allowed-tools:" in result
    # Must NOT contain VS Code keys
    assert "user-invokable:" not in result
    assert "# Navigator" in result


def test_cli_and_claude_both_strip_handoffs():
    """Both CLI and Claude adapters remove handoff sections."""
    cli = CopilotCLIAdapter()
    claude = ClaudeAdapter()
    assert "Handoff" not in cli.render_agent_file(CONTENT_WITH_HANDOFFS, "nav", MINIMAL_MANIFEST)
    assert "Handoff" not in claude.render_agent_file(CONTENT_WITH_HANDOFFS, "nav", MINIMAL_MANIFEST)


def test_all_three_adapters_preserve_rules_section():
    """All three adapters preserve non-handoff body sections."""
    vscode = CopilotVSCodeAdapter()
    cli = CopilotCLIAdapter()
    claude = ClaudeAdapter()
    for adapter in (vscode, cli, claude):
        result = adapter.render_agent_file(CONTENT_WITH_HANDOFFS, "nav", MINIMAL_MANIFEST)
        assert "## Rules" in result, f"{adapter.framework_id} dropped ## Rules"
        assert "Always close with auditor." in result


def test_vscode_adapter_get_agents_dir_is_path_object():
    adapter = CopilotVSCodeAdapter()
    result = adapter.get_agents_dir(Path("/some/project"))
    assert isinstance(result, Path)


def test_cli_adapter_get_agents_dir_is_path_object():
    adapter = CopilotCLIAdapter()
    result = adapter.get_agents_dir(Path("/some/project"))
    assert isinstance(result, Path)


def test_claude_adapter_get_agents_dir_is_path_object():
    adapter = ClaudeAdapter()
    result = adapter.get_agents_dir(Path("/some/project"))
    assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# Tool docs as skills (Claude) vs reference docs (Copilot)
# ---------------------------------------------------------------------------

_TOOL_DOC_BODY = """\
# PostgreSQL — Database Reference — TestProject

> Operational reference for **PostgreSQL 15**.

## Key API Surface

<!-- AGENTTEAMS:BEGIN tool_api_surface v=1 -->
SELECT, INSERT, CREATE TABLE
<!-- AGENTTEAMS:END tool_api_surface -->

## Schema Management

1. Versioned migrations only
"""

_SKILL_MANIFEST = {
    "project_name": "TestProject",
    "framework": "claude",
    "tool_agents": [{"slug": "tool-postgresql", "tool_name": "PostgreSQL"}],
}


def test_claude_render_skill_file_injects_front_matter():
    """Claude skills get name + description front matter; body is preserved."""
    out = ClaudeAdapter().render_skill_file(_TOOL_DOC_BODY, "tool-postgresql", _SKILL_MANIFEST)
    assert out.startswith("---\n")
    assert "name: tool-postgresql" in out
    assert "description:" in out
    assert "PostgreSQL" in out  # description derived from tool_name
    # No agent-only keys leak into a skill.
    fm = out.split("---", 2)[1]
    assert "allowed-tools" not in fm
    assert "handoffs" not in fm
    # Operational body + fence preserved.
    assert "## Schema Management" in out
    assert "AGENTTEAMS:BEGIN tool_api_surface" in out


def test_claude_render_skill_file_strips_stray_front_matter_and_handoffs():
    """A stray agent front matter / handoff block is removed before re-wrapping."""
    stray = (
        "---\nname: Old Agent\nhandoffs:\n  - label: x\n    agent: y\n---\n"
        "## Handoff Instructions\n- hand off to @z\n\n" + _TOOL_DOC_BODY
    )
    out = ClaudeAdapter().render_skill_file(stray, "tool-postgresql", _SKILL_MANIFEST)
    assert "Old Agent" not in out
    assert "Handoff Instructions" not in out
    assert out.count("---\n") >= 2  # exactly one (new) front matter block at top
    assert out.startswith("---\nname: tool-postgresql\n")


def test_base_adapter_render_skill_file_is_noop_for_copilot():
    """Copilot adapters never emit skills — default render_skill_file is identity."""
    body = _TOOL_DOC_BODY
    assert CopilotVSCodeAdapter().render_skill_file(body, "tool-postgresql", {}) == body
    assert CopilotCLIAdapter().render_skill_file(body, "tool-postgresql", {}) == body


# ===========================================================================
# GooseAdapter
# ===========================================================================

# Manifest whose roster contains navigator + security, so handoffs to them are
# recognized as valid team targets.
GOOSE_MANIFEST = {
    "project_name": "TestProject",
    "framework": "goose",
    "output_files": [
        {"path": "navigator.agent.md"},
        {"path": "security.agent.md"},
    ],
}

# Same roster minus security — used to prove non-team targets are dropped.
GOOSE_MANIFEST_NO_SECURITY = {
    "project_name": "TestProject",
    "framework": "goose",
    "output_files": [{"path": "navigator.agent.md"}],
}

# An agent that hands off to navigator in BOTH the YAML block and the body —
# Goose must collapse it to a single reference.
GOOSE_DUP_HANDOFF_CONTENT = """\
---
name: Quality Auditor — TestProject
description: "Audit deliverables"
handoffs:
    - label: File lookup
        agent: navigator
        prompt: "Inspect the files."
        send: false
---

# Quality Auditor

Audit body.

## Handoff Instructions

- Hand off to `@navigator` for file lookups.
"""


class TestGooseAdapter:

    def setup_method(self):
        self.adapter = GooseAdapter()

    # --- Identity ---

    def test_framework_id(self):
        assert self.adapter.framework_id == "goose"

    def test_supports_handoffs(self):
        assert self.adapter.supports_handoffs() is True

    def test_vscode_tasks_rel_path(self):
        assert self.adapter.vscode_tasks_rel_path() == "../../.vscode/tasks.json"

    def test_handoff_delivery_mode_native(self):
        # Handoffs are encoded into recipes, not a sidecar manifest.
        assert self.adapter.handoff_delivery_mode() == "native"

    def test_get_file_extension(self):
        assert self.adapter.get_file_extension("agent") == ".yaml"
        assert self.adapter.get_file_extension("instructions") == ".md"

    def test_get_agents_dir(self):
        assert self.adapter.get_agents_dir(Path("/project")) == Path("/project/.goose/recipes")

    # --- Path finalization ---

    def test_finalize_output_path_instructions_to_agents_md(self):
        result = self.adapter.finalize_output_path("../copilot-instructions.md", "instructions")
        assert result == "../../AGENTS.md"

    def test_finalize_output_path_agent_to_yaml(self):
        result = self.adapter.finalize_output_path("quality-auditor.agent.md", "agent")
        assert result == "quality-auditor.yaml"

    # --- .goosehints integrator ---

    def test_extra_output_files_emits_goosehints(self):
        extras = self.adapter.extra_output_files(GOOSE_MANIFEST)
        extras_by_path = dict(extras)
        assert "../../.goosehints" in extras_by_path
        # Integrates AGENTS.md via the @file content-include.
        assert extras_by_path["../../.goosehints"].startswith("@AGENTS.md")

    # --- capabilities reference ---

    def test_extra_output_files_emits_capabilities_reference(self):
        extras = self.adapter.extra_output_files(GOOSE_MANIFEST)
        extras_by_path = dict(extras)
        assert "references/goose-capabilities-reference.md" in extras_by_path
        content = extras_by_path["references/goose-capabilities-reference.md"]
        # States the diagnosed capability plainly: developer's shell has no network
        # sandbox, regardless of which optional extensions a team additionally enables.
        assert "no code-level\nnetwork sandbox" in content
        assert "developer" in content
        # Must not assert that opt-in extensions are active for this team.
        assert "computercontroller" in content
        assert "not** enabled unless your own recipe" in content
        # Cross-links the runtime-agnostic CLI-competency methodology doc.
        assert "references/cli-tool-discovery.reference.md" in content
        assert "references/skill-generation.reference.md" in content
        # Context-bloat management: states this is already a native Goose feature, not
        # something to prompt an agent to monitor/trigger itself (empirically confirmed
        # this session — see tmp/by-week/2026-W30/goose-context-bloat-management.plan.md).
        assert "context_mgmt" in content
        assert "GOOSE_AUTO_COMPACT_THRESHOLD" in content
        assert "autoCompactThreshold" in content
        assert "not exposed in the interactive `goose configure` wizard" in content
        assert "no recipe-level equivalent" in content
        assert "inert" in content

    def test_capabilities_reference_states_agentteams_research_verify_first(self):
        """tmp/by-week/2026-W30/web-browsing-playwright-cli.plan.md Design decision 8: must not
        assert agentteams.research is installed for this team (goose.py's
        _goose_capabilities_content takes no manifest parameter, so it genuinely cannot know) --
        phrase as verify-first guidance, matching this same paragraph's existing MCP-search-server
        sentence, never as a presence claim."""
        extras = self.adapter.extra_output_files(GOOSE_MANIFEST)
        content = dict(extras)["references/goose-capabilities-reference.md"]
        assert "agentteams.research" in content
        assert "If this project has its own optional `agentteams.research` module installed" in content
        assert "python -m agentteams.research --help" in content

    def test_capabilities_reference_separates_non_extension_capability_from_extension_list(self):
        """Framework-adapters-expert audit finding: agentteams.research isn't a Goose extension
        at all (not part of recipe_extensions) -- must not read as filed under the '## Other
        builtin extensions (opt-in per agent, via recipe_extensions)' heading above it."""
        extras = self.adapter.extra_output_files(GOOSE_MANIFEST)
        content = dict(extras)["references/goose-capabilities-reference.md"]
        assert "## Capability that isn't a Goose extension at all" in content
        extensions_heading = content.index("## Other builtin extensions")
        capability_heading = content.index("## Capability that isn't a Goose extension at all")
        agentteams_research_mention = content.index("If this project has its own optional")
        assert extensions_heading < capability_heading < agentteams_research_mention

    def test_capabilities_reference_states_no_builtin_extension_renders_js(self):
        extras = self.adapter.extra_output_files(GOOSE_MANIFEST)
        content = dict(extras)["references/goose-capabilities-reference.md"]
        assert "none of the\nbuiltin extensions execute JavaScript" in content
        assert 'python -m agentteams.research browser "<url>"' in content

    def test_capabilities_reference_points_at_skill_generation_worked_example(self):
        extras = self.adapter.extra_output_files(GOOSE_MANIFEST)
        content = dict(extras)["references/goose-capabilities-reference.md"]
        assert 'worked example ("a page `fetch` can\'t' in content

    def test_goosehints_links_to_capabilities_reference(self):
        content = _goosehints_content("Acme Team")
        assert ".goose/recipes/references/goose-capabilities-reference.md" in content

    # --- render_agent_file: orchestrator → sub_recipes (delegation) ---

    def test_orchestrator_builds_sub_recipes(self):
        result = self.adapter.render_agent_file(
            CONTENT_WITH_YAML_HANDOFFS, "orchestrator", GOOSE_MANIFEST
        )
        assert "sub_recipes:" in result
        assert 'name: "navigator"' in result
        assert 'path: "./navigator.yaml"' in result
        assert 'path: "./security.yaml"' in result

    def test_orchestrator_declares_summon_extension(self):
        result = self.adapter.render_agent_file(
            CONTENT_WITH_YAML_HANDOFFS, "orchestrator", GOOSE_MANIFEST
        )
        assert "name: summon" in result
        assert "name: developer" in result

    def test_orchestrator_is_valid_recipe_shape(self):
        result = self.adapter.render_agent_file(
            CONTENT_WITH_YAML_HANDOFFS, "orchestrator", GOOSE_MANIFEST
        )
        assert result.startswith('version: "1.0.0"\n')
        assert "title: " in result
        assert "instructions: |" in result
        assert "# Orchestrator" in result  # body preserved as instructions
        assert result.endswith("\n")

    def test_orchestrator_filters_non_team_handoff_targets(self):
        result = self.adapter.render_agent_file(
            CONTENT_WITH_YAML_HANDOFFS, "orchestrator", GOOSE_MANIFEST_NO_SECURITY
        )
        assert "./navigator.yaml" in result
        assert "./security.yaml" not in result

    # --- render_agent_file: specialist → load() references (no delegation) ---

    def test_specialist_uses_load_references_not_sub_recipes(self):
        result = self.adapter.render_agent_file(
            CONTENT_WITH_YAML_HANDOFFS, "quality-auditor", GOOSE_MANIFEST
        )
        assert "sub_recipes:" not in result
        assert 'load("navigator")' in result
        assert 'load("security")' in result
        assert "## Delegation & references (Goose)" in result
        assert "name: summon" in result  # summon needed for load

    def test_specialist_without_handoffs_has_no_summon_or_load(self):
        result = self.adapter.render_agent_file(
            FULL_YAML_CONTENT, "navigator", GOOSE_MANIFEST
        )
        assert "sub_recipes:" not in result
        assert "load(" not in result
        assert "name: summon" not in result
        assert "name: developer" in result  # developer is always present

    # --- one reference per target agent (dedupe across YAML + body) ---

    def test_handoffs_deduped_by_agent(self):
        # Specialist form: navigator appears in YAML block AND body → one load().
        spec = self.adapter.render_agent_file(
            GOOSE_DUP_HANDOFF_CONTENT, "quality-auditor", GOOSE_MANIFEST
        )
        assert spec.count('load("navigator")') == 1
        # Orchestrator form: navigator appears once as a sub_recipe.
        orch = self.adapter.render_agent_file(
            GOOSE_DUP_HANDOFF_CONTENT, "orchestrator", GOOSE_MANIFEST
        )
        assert orch.count('path: "./navigator.yaml"') == 1

    # --- render_instructions_file strips stray front matter ---

    def test_render_instructions_file_strips_front_matter(self):
        content = "---\nname: x\n---\n\n# Team\n\nBrief."
        out = self.adapter.render_instructions_file(content, GOOSE_MANIFEST)
        assert "name: x" not in out
        assert "# Team" in out

    # --- builder → runnable recipe (Phase-1 item 1) ---

    def test_get_file_extension_builder_is_yaml(self):
        # The team-builder must emit as a recipe, not a stray .md in .goose/recipes.
        assert self.adapter.get_file_extension("builder") == ".yaml"

    def test_render_builder_file_emits_recipe(self):
        content = (
            "---\nname: team-builder\ndescription: Build a team\n---\n\n"
            "# Team Builder\n\nConduct the intake then run build_team."
        )
        out = self.adapter.render_builder_file(content, GOOSE_MANIFEST)
        assert out.startswith('version: "1.0.0"')
        assert "title:" in out
        assert "instructions: |" in out
        assert "name: developer" in out          # can write files / run build_team
        assert "sub_recipes:" not in out         # builder is not the orchestrator
        assert "# Team Builder" in out           # body preserved in instructions block

    def test_builder_finalizes_to_yaml_path(self):
        # _guess_file_type routes team-builder.agent.md → "builder"; goose maps it to .yaml.
        assert self.adapter.finalize_output_path("team-builder.agent.md", "builder") == "team-builder.yaml"

    # --- AGENTS.md / .goosehints placement is correct relative to the agents dir
    #     (Phase-1 item 2: the ../../ paths land at the repo root by design — the
    #     same contract claude uses for CLAUDE.md; a flat --output is misuse) ---

    def test_instructions_and_goosehints_resolve_to_repo_root(self):
        agents_dir = Path("/project/.goose/recipes")  # == get_agents_dir(/project)
        assert self.adapter.get_agents_dir(Path("/project")) == agents_dir

        instr = self.adapter.finalize_output_path("../copilot-instructions.md", "instructions")
        assert (agents_dir / instr).resolve() == Path("/project/AGENTS.md")

        hints_path, _ = self.adapter.extra_output_files(GOOSE_MANIFEST)[0]
        assert (agents_dir / hints_path).resolve() == Path("/project/.goosehints")
