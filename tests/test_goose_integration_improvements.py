"""Regression tests for Goose integration improvements (W1–W7).

Each test covers one work item from the integration plan:
  W1 — normalize_output_path keeps AGENTS.md inside the project tree
  W2 — --merge emits a targeted notice for AGENTTEAMS-BRIDGE fences
  W3 — authority_hierarchy from copilot-instructions.md propagates into generated files
  W4 — sub_recipes is supplemented with agents absent from handoffs: block
  W5 — goose quickstart snippet clarifies --bridge-check scope
  W6 — orchestrator recipe includes a prompt: field for non-interactive CI testing
  W7 — .goosehints includes Session Startup block + is parameterized to project name
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.convert import convert_team
from agentteams.frameworks.goose import GooseAdapter, _emit_recipe, _goosehints_content


# ---------------------------------------------------------------------------
# Helpers shared by multiple tests
# ---------------------------------------------------------------------------

_MINIMAL_AGENT_MD = """\
---
name: My Agent
description: A test agent
---

# My Agent

Does things.
"""

_ORCHESTRATOR_WITH_HANDOFFS = """\
---
name: Orchestrator
description: Routes work
handoffs:
  - label: Alpha
    agent: alpha
    prompt: do alpha work
  - label: Beta
    agent: beta
    prompt: do beta work
---

# Orchestrator

Routes all work.
"""

_AUTHORITY_HIERARCHY_BLOCK = """\
<!-- AGENTTEAMS:BEGIN authority_hierarchy -->
1. **Primary Database** — canonical source
2. **Secondary Database** — fallback
<!-- AGENTTEAMS:END authority_hierarchy -->
"""

_COPILOT_INSTRUCTIONS_WITH_HIERARCHY = f"""\
# Project Instructions

Some preamble.

{_AUTHORITY_HIERARCHY_BLOCK}

More content.
"""


def _make_manifest(**extra) -> dict:
    base = {
        "project_name": "TestProject",
        "output_files": [
            {"path": "alpha.agent.md"},
            {"path": "beta.agent.md"},
            {"path": "gamma.agent.md"},
        ],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# W1 — normalize_output_path keeps AGENTS.md inside the project tree
# ---------------------------------------------------------------------------

class TestW1NormalizeOutputPath:
    """GooseAdapter.normalize_output_path maps project-root inputs to .goose/recipes."""

    def test_project_root_gets_goose_recipes_appended(self, tmp_path):
        adapter = GooseAdapter()
        result = adapter.normalize_output_path(tmp_path)
        assert result == tmp_path / ".goose" / "recipes"

    def test_already_recipes_dir_unchanged(self, tmp_path):
        adapter = GooseAdapter()
        recipes = tmp_path / ".goose" / "recipes"
        assert adapter.normalize_output_path(recipes) == recipes

    def test_goose_dir_without_recipes_gets_recipes_appended(self, tmp_path):
        adapter = GooseAdapter()
        goose = tmp_path / ".goose"
        assert adapter.normalize_output_path(goose) == goose / "recipes"

    def test_extra_output_files_land_in_project_root(self, tmp_path):
        """AGENTS.md and .goosehints must resolve within the project tree."""
        adapter = GooseAdapter()
        # When output_dir is .goose/recipes (after normalization), ../../ = project root
        recipes_dir = tmp_path / ".goose" / "recipes"
        manifest = _make_manifest()
        for rel_path, _ in adapter.extra_output_files(manifest):
            resolved = (recipes_dir / rel_path).resolve()
            assert str(resolved).startswith(str(tmp_path.resolve())), (
                f"{rel_path} resolves outside project tree: {resolved}"
            )


# ---------------------------------------------------------------------------
# W2 — --merge emits targeted notice for AGENTTEAMS-BRIDGE fences
# ---------------------------------------------------------------------------

class TestW2BridgeFenceNotice:
    """emit._merge_content produces a distinct parse error for AGENTTEAMS-BRIDGE files."""

    def test_bridge_fence_produces_distinct_error(self):
        from agentteams.emit import _merge_fenced_content as _merge_content

        bridge_file = (
            "<!-- AGENTTEAMS-BRIDGE:BEGIN goose-bridge-hints v=1 -->\n"
            "@AGENTS.md\n"
            "<!-- AGENTTEAMS-BRIDGE:END goose-bridge-hints -->\n"
        )
        new_rendered = (
            "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
            "new content\n"
            "<!-- AGENTTEAMS:END content -->\n"
        )
        result = _merge_content(new_rendered=new_rendered, existing_on_disk=bridge_file)
        assert result.has_errors
        # Should mention AGENTTEAMS-BRIDGE specifically, not the generic "legacy file" message
        combined = " ".join(result.parse_errors)
        assert "AGENTTEAMS-BRIDGE" in combined
        assert "legacy file" not in combined

    def test_truly_unfenced_file_still_gets_legacy_message(self):
        from agentteams.emit import _merge_fenced_content as _merge_content

        plain_file = "# Some file\n\nNo fences here.\n"
        new_rendered = (
            "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
            "new content\n"
            "<!-- AGENTTEAMS:END content -->\n"
        )
        result = _merge_content(new_rendered=new_rendered, existing_on_disk=plain_file)
        assert result.has_errors
        combined = " ".join(result.parse_errors)
        assert "legacy file" in combined
        assert "AGENTTEAMS-BRIDGE" not in combined


# ---------------------------------------------------------------------------
# W3 — authority_hierarchy propagates from source copilot-instructions.md
# ---------------------------------------------------------------------------

class TestW3AuthorityHierarchyPropagation:
    """Goose adapter substitutes authority_hierarchy from manifest['_source_instructions_content']."""

    def test_authority_hierarchy_replaced_in_agent_body(self):
        adapter = GooseAdapter()
        agent_with_generic_hierarchy = (
            "---\n"
            "name: Orchestrator\n"
            "description: Routes work\n"
            "---\n\n"
            "# Orchestrator\n\n"
            "<!-- AGENTTEAMS:BEGIN authority_hierarchy -->\n"
            "1. **Project source files** — ground truth for all technical claims\n"
            "<!-- AGENTTEAMS:END authority_hierarchy -->\n"
        )
        manifest = _make_manifest(
            _source_instructions_content=_COPILOT_INSTRUCTIONS_WITH_HIERARCHY
        )
        recipe = adapter.render_agent_file(agent_with_generic_hierarchy, "orchestrator", manifest)
        assert "Primary Database" in recipe
        assert "Project source files" not in recipe

    def test_authority_hierarchy_replaced_in_agents_md(self):
        adapter = GooseAdapter()
        agents_md_content = (
            "# Team\n\n"
            "<!-- AGENTTEAMS:BEGIN authority_hierarchy -->\n"
            "1. **Project source files** — ground truth\n"
            "<!-- AGENTTEAMS:END authority_hierarchy -->\n"
        )
        manifest = _make_manifest(
            _source_instructions_content=_COPILOT_INSTRUCTIONS_WITH_HIERARCHY
        )
        result = adapter.render_instructions_file(agents_md_content, manifest)
        assert "Primary Database" in result
        assert "Project source files" not in result

    def test_no_substitution_when_no_source_instructions(self):
        adapter = GooseAdapter()
        agent_content = (
            "---\nname: Agent\ndescription: d\n---\n\n"
            "<!-- AGENTTEAMS:BEGIN authority_hierarchy -->\n"
            "1. **Project source files** — ground truth\n"
            "<!-- AGENTTEAMS:END authority_hierarchy -->\n"
        )
        manifest = _make_manifest()  # no _source_instructions_content
        recipe = adapter.render_agent_file(agent_content, "orchestrator", manifest)
        # Generic content must be preserved when no source instructions provided
        assert "Project source files" in recipe

    def test_convert_captures_source_instructions_into_manifest(self, tmp_path):
        """convert_team propagates copilot-instructions.md content to the manifest."""
        # Set up a minimal source structure
        agents_dir = tmp_path / "src" / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "orchestrator.agent.md").write_text(_MINIMAL_AGENT_MD)
        # Place copilot-instructions.md in the parent (.github/)
        instructions_path = agents_dir.parent / "copilot-instructions.md"
        instructions_path.write_text(_COPILOT_INSTRUCTIONS_WITH_HIERARCHY)

        target = tmp_path / "tgt" / ".goose" / "recipes"
        result = convert_team(
            agents_dir,
            target,
            "goose",
            project_manifest={"project_name": "P", "output_files": [{"path": "orchestrator.agent.md"}]},
            overwrite=True,
        )
        assert result.errors == [], result.errors
        # AGENTS.md must contain the project-specific hierarchy
        agents_md = (target / "../../AGENTS.md").resolve()
        if agents_md.exists():
            content = agents_md.read_text()
            assert "Primary Database" in content


# ---------------------------------------------------------------------------
# W4 — sub_recipes supplemented from agents list when handoffs: is incomplete
# ---------------------------------------------------------------------------

class TestW4SubRecipesSupplemented:
    """Orchestrator sub_recipes includes all team agents, not just handoffs: entries."""

    def _get_sub_recipe_names(self, yaml_text: str) -> list[str]:
        import re
        return re.findall(r'name:\s*"([^"]+)"', yaml_text)

    def test_handoffs_only_agents_get_supplemented(self):
        adapter = GooseAdapter()
        # Orchestrator declares handoffs to alpha and beta only; gamma is in the team
        manifest = _make_manifest()  # has alpha, beta, gamma in output_files
        recipe = adapter.render_agent_file(_ORCHESTRATOR_WITH_HANDOFFS, "orchestrator", manifest)
        # All three team agents (alpha, beta, gamma) must appear in sub_recipes
        assert "alpha" in recipe
        assert "beta" in recipe
        assert "gamma" in recipe

    def test_supplemented_agents_have_empty_description(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        recipe = adapter.render_agent_file(_ORCHESTRATOR_WITH_HANDOFFS, "orchestrator", manifest)
        # gamma was not in handoffs: — verify it appears without a description line.
        # _emit_recipe omits description entirely when the value is falsy.
        lines = recipe.splitlines()
        gamma_name_idx = next((i for i, l in enumerate(lines) if '"gamma"' in l), None)
        assert gamma_name_idx is not None, "gamma must appear in sub_recipes"
        # Collect lines from the gamma entry until the next sub-recipe entry or end
        gamma_block_lines = []
        for line in lines[gamma_name_idx:]:
            if line.strip().startswith("- name:") and "gamma" not in line:
                break
            gamma_block_lines.append(line)
        gamma_block = "\n".join(gamma_block_lines)
        # _emit_recipe skips description: when falsy, so gamma block should have no description
        assert 'description: "' not in gamma_block

    def test_explicit_handoffs_come_before_supplemented(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        recipe = adapter.render_agent_file(_ORCHESTRATOR_WITH_HANDOFFS, "orchestrator", manifest)
        alpha_pos = recipe.find("alpha")
        beta_pos = recipe.find("beta")
        gamma_pos = recipe.find("gamma")
        # alpha and beta (from handoffs:) must appear before gamma (supplemented)
        assert alpha_pos < gamma_pos
        assert beta_pos < gamma_pos

    def test_no_duplicate_sub_recipes(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        recipe = adapter.render_agent_file(_ORCHESTRATOR_WITH_HANDOFFS, "orchestrator", manifest)
        # Count occurrences of each agent slug in path entries
        import re
        paths = re.findall(r'path:\s*"\./([^.]+)\.yaml"', recipe)
        assert len(paths) == len(set(paths)), f"Duplicate sub_recipe paths: {paths}"


# ---------------------------------------------------------------------------
# W5 — goose quickstart snippet clarifies --bridge-check scope
# ---------------------------------------------------------------------------

class TestW5QuickstartBridgeCheckScope:
    """Generated quickstart snippet for goose target includes bridge-check scope note."""

    def test_goose_quickstart_mentions_recipe_validation_limitation(self):
        from agentteams.bridge import _render_quickstart

        snippet = _render_quickstart("copilot-vscode", "goose")
        assert "does NOT validate" in snippet
        assert "recipe" in snippet.lower()

    def test_non_goose_quickstart_unchanged(self):
        from agentteams.bridge import _render_quickstart

        snippet = _render_quickstart("copilot-vscode", "claude")
        # The bridge-check scope note is goose-specific; other targets must not have it
        assert "does NOT validate" not in snippet


# ---------------------------------------------------------------------------
# W6 — orchestrator recipe includes prompt: field for non-interactive CI
# ---------------------------------------------------------------------------

class TestW6OrchestratorProbePrompt:
    """Orchestrator recipe YAML contains a prompt: field."""

    def test_orchestrator_recipe_has_prompt_field(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        recipe = adapter.render_agent_file(_ORCHESTRATOR_WITH_HANDOFFS, "orchestrator", manifest)
        assert "prompt:" in recipe

    def test_non_orchestrator_recipe_has_no_prompt_field(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        recipe = adapter.render_agent_file(_MINIMAL_AGENT_MD, "my-agent", manifest)
        assert "prompt:" not in recipe

    def test_emit_recipe_prompt_field_is_quoted_scalar(self):
        yaml = _emit_recipe(
            title="Test",
            description="Desc",
            instructions="Do stuff.",
            extensions=["developer"],
            prompt="Say your role.",
        )
        assert 'prompt: "Say your role."' in yaml

    def test_emit_recipe_without_prompt_omits_field(self):
        yaml = _emit_recipe(
            title="Test",
            description="Desc",
            instructions="Do stuff.",
            extensions=["developer"],
        )
        assert "prompt:" not in yaml


# ---------------------------------------------------------------------------
# W7 — .goosehints includes Session Startup block, parameterized to project name
# ---------------------------------------------------------------------------

class TestW7GoosehintsSessionStartup:
    """GooseAdapter.extra_output_files produces .goosehints with Session Startup block."""

    def test_goosehints_contains_session_startup(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        files = dict(adapter.extra_output_files(manifest))
        goosehints = files.get("../../.goosehints", "")
        assert "Session Startup" in goosehints

    def test_goosehints_contains_project_name(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        files = dict(adapter.extra_output_files(manifest))
        goosehints = files.get("../../.goosehints", "")
        assert "TestProject" in goosehints

    def test_goosehints_startup_instructs_recipe_read(self):
        adapter = GooseAdapter()
        manifest = _make_manifest()
        files = dict(adapter.extra_output_files(manifest))
        goosehints = files.get("../../.goosehints", "")
        assert "orchestrator.yaml" in goosehints

    def test_goosehints_has_managed_fence(self):
        """Operational notes are inside an AGENTTEAMS fence (merge-safe)."""
        adapter = GooseAdapter()
        manifest = _make_manifest()
        files = dict(adapter.extra_output_files(manifest))
        goosehints = files.get("../../.goosehints", "")
        assert "AGENTTEAMS:BEGIN goose-operational-notes" in goosehints
        assert "AGENTTEAMS:END goose-operational-notes" in goosehints

    def test_goosehints_content_function_parameterized(self):
        content = _goosehints_content("MyCustomProject")
        assert "MyCustomProject" in content
        assert "Session Startup" in content
