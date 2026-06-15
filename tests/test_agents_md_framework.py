"""Tests for the `agents-md` framework — the cross-tool AGENTS.md emitter
(continue-dev Path B, item 5). Covers the AgentsMdAdapter contract, the
neutralization of Copilot branding in the published AGENTS.md, the two-layer
output (repo-root AGENTS.md + .agents/<slug>.md), and the generate-only CLI guard
(agents-md is not a convert/interop/bridge target)."""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

import build_team
from agentteams.frameworks.agents_md import AgentsMdAdapter
from agentteams.frameworks.registry import FRAMEWORKS

_BRIEF = "examples/data-pipeline/brief.json"


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------

class TestAgentsMdAdapter:
    def setup_method(self):
        self.adapter = AgentsMdAdapter()

    def test_registered(self):
        assert FRAMEWORKS["agents-md"] is AgentsMdAdapter

    def test_framework_id(self):
        assert self.adapter.framework_id == "agents-md"

    def test_agents_dir_is_dot_agents(self):
        assert self.adapter.get_agents_dir(Path("/proj")) == Path("/proj/.agents")

    def test_extension_is_markdown(self):
        assert self.adapter.get_file_extension("agent") == ".md"
        assert self.adapter.get_file_extension("instructions") == ".md"

    def test_instructions_map_to_repo_root_agents_md(self):
        # agents dir is one level under repo root → ../AGENTS.md
        assert self.adapter.finalize_output_path(
            "../copilot-instructions.md", "instructions"
        ) == "../AGENTS.md"

    def test_handoff_delivery_is_manifest(self):
        assert self.adapter.supports_handoffs() is False
        assert self.adapter.handoff_delivery_mode() == "manifest"

    def test_render_agent_file_strips_front_matter_and_adds_heading(self):
        content = (
            "---\nname: Database Expert\ndescription: Owns the schema\n---\n\n"
            "Body text referencing .github/agents/foo.\n\n"
            "## Handoff Instructions\n- @security for clearance\n"
        )
        out = self.adapter.render_agent_file(content, "database-expert", {})
        assert out.startswith("# Database Expert\n")
        assert "Owns the schema" in out          # description carried into the heading
        assert "---\nname:" not in out            # YAML front matter stripped
        assert ".github/agents" not in out        # path rewritten
        assert ".agents/foo" in out
        assert "## Handoff Instructions" not in out  # handoff block stripped

    def test_render_instructions_neutralizes_branding(self):
        content = (
            "<!--\nSECTION MANIFEST — copilot-instructions.template.md\n| x | y |\n-->\n\n"
            "# MyProj — Copilot Instructions\n\n"
            "> ... structure for all GitHub Copilot agents in MyProj.\n\n"
            "See `.github/agents/` for definitions.\n"
        )
        out = self.adapter.render_instructions_file(content, {})
        assert "Copilot" not in out                       # no tool branding
        assert "SECTION MANIFEST" not in out              # leaked template manifest stripped
        assert "copilot-instructions.template.md" not in out
        assert ".github/agents" not in out                # paths rewritten
        assert ".agents/" in out
        assert "— Agent Team" in out                      # retitled
        assert out.lstrip().startswith("<!-- AGENTS.md")  # shared-namespace notice on top

    def test_instructions_preserve_agentteams_fences(self):
        content = (
            "# P — Copilot Instructions\n\n"
            "<!-- AGENTTEAMS:BEGIN project_overview v=1 -->\n## Overview\nx\n"
            "<!-- AGENTTEAMS:END project_overview -->\n"
        )
        out = self.adapter.render_instructions_file(content, {})
        # fence markers must survive so --update --merge can re-render the region
        assert "<!-- AGENTTEAMS:BEGIN project_overview v=1 -->" in out
        assert "<!-- AGENTTEAMS:END project_overview -->" in out


# ---------------------------------------------------------------------------
# End-to-end generate
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path(_BRIEF).exists(), reason="data-pipeline brief not found")
class TestAgentsMdGenerate:
    def _generate(self, tmp_path: Path) -> Path:
        out = tmp_path / ".agents"
        rc = build_team.main([
            "--description", _BRIEF, "--framework", "agents-md",
            "--output", str(out), "--yes", "--no-scan",
        ])
        assert rc == 0
        return tmp_path

    def test_emits_repo_root_agents_md_and_detail_dir(self, tmp_path):
        root = self._generate(tmp_path)
        agents_md = root / "AGENTS.md"
        assert agents_md.exists(), "repo-root AGENTS.md must be emitted"
        assert (root / ".agents" / "orchestrator.md").exists()
        assert (root / ".agents" / "team-builder.md").exists()  # builder planned (B1)

    def test_agents_md_is_framework_neutral(self, tmp_path):
        text = (self._generate(tmp_path) / "AGENTS.md").read_text(encoding="utf-8")
        assert "Copilot" not in text
        assert "SECTION MANIFEST" not in text
        assert ".github/agents" not in text
        assert text.lstrip().startswith("<!-- AGENTS.md")
        # self-sufficient: roster/routing inline (the orchestrator is named)
        assert "@orchestrator" in text

    def test_handoff_manifest_sidecar_emitted(self, tmp_path):
        root = self._generate(tmp_path)
        assert (root / ".agents" / "references" / "runtime-handoffs.json").exists()


# ---------------------------------------------------------------------------
# Generate-only guard (agents-md is not a convert/interop/bridge target)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--convert-from", "--interop-from", "--bridge-from"])
def test_agents_md_rejects_non_generate_targets(tmp_path, flag):
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stderr(err):
        build_team.main(["--framework", "agents-md", flag, str(tmp_path),
                         "--output", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert "generate-only AGENTS.md emitter" in err.getvalue()
