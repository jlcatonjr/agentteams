"""
Tests for agentteams/frameworks/ — CopilotVSCodeAdapter, CopilotCLIAdapter, ClaudeAdapter.
"""

from pathlib import Path
import pytest

from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.claude import ClaudeAdapter


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
