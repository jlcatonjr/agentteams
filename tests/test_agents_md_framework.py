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
from agentteams.frameworks.agents_md import AgentsMdAdapter, _strip_leading_synthesized_header
from agentteams.frameworks.registry import FRAMEWORKS

_BRIEF = "examples/data-pipeline/brief.json"


# --- render_agent_file idempotency (open-items backlog, D2) -----------------

def test_render_agent_file_is_idempotent_across_two_passes():
    """A body that already starts with its own heading (the normal shape)
    must not gain a duplicate heading+description on render, and re-rendering
    the already-rendered output a second time must not compound further.

    Pass 2's exact heading text legitimately differs from pass 1's (agents-md
    output carries no YAML front matter, so _extract_name_description falls
    back to a slug+project_name-derived name on re-render — a separate,
    pre-existing, documented mechanism unrelated to D2). What must hold
    across both passes is the absence of duplication, not byte-for-byte
    output equality.
    """
    adapter = AgentsMdAdapter()
    manifest = {"project_name": "P"}
    content = (
        "---\nname: Orchestrator\ndescription: \"Routes work.\"\n---\n\n"
        "# Orchestrator\n\nBody paragraph.\n"
    )
    once = adapter.render_agent_file(content, "orchestrator", manifest)
    assert once.count("# ") == 1, f"pass 1 duplicated a heading: {once!r}"
    assert once.count("Routes work.") == 1

    twice = adapter.render_agent_file(once, "orchestrator", manifest)
    assert twice.count("# ") == 1, f"pass 2 duplicated a heading: {twice!r}"
    assert twice.count("Body paragraph.") == 1, f"pass 2 duplicated the body: {twice!r}"


def test_strip_leading_header_does_not_truncate_a_paragraph_that_merely_starts_with_description():
    """2026-08-11 code-hygiene finding: comparing description against the full
    remainder via startswith() would truncate mid-sentence when the body's
    real first paragraph happens to start with the same words as description
    but continues. Must compare the bounded first paragraph for equality."""
    body = "Routes work to three teams, not just one.\n\nSecond paragraph stays.\n"
    result = _strip_leading_synthesized_header(body, "Routes work")
    assert "Routes work to three teams, not just one." in result
    assert "Second paragraph stays." in result


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
# Generate-only guard (agents-md is not a convert/bridge target; the interop
# path IS supported as of F.2 — import_from_cai writes the framework-owned
# AGENTS.md, so the mislabeling rationale no longer applies there)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--convert-from", "--bridge-from"])
def test_agents_md_rejects_non_generate_targets(tmp_path, flag):
    err = io.StringIO()
    with pytest.raises(SystemExit) as exc, redirect_stderr(err):
        build_team.main(["--framework", "agents-md", flag, str(tmp_path),
                         "--output", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert "generate-only AGENTS.md emitter" in err.getvalue()


def test_agents_md_allows_interop_target(tmp_path):
    # F.2: agents-md is a valid CAI target — validation must NOT raise.
    build_team.main(["--framework", "agents-md", "--interop-from", str(tmp_path),
                     "--output", str(tmp_path / "o"), "--dry-run"])


# --- D1: interop projection must not rewrite `.github/agents` paths ---------
#
# Reported by collector-management (2026-10-08 handoff, D1, HIGH). `render_agent_file`
# rewrote `.github/agents` -> `.agents` unconditionally. Under `--sync`/interop
# (`manifest["interop_source_framework"]` set) nothing is materialized at
# `.agents/references/`, so every rewritten path dangled: 24 `.agents/*.md` files in the
# reporting repo carried 88 `.agents/references/` paths against a directory that did not
# exist, 11 of them `.agents/references/conflict-log.csv` — the safe-append target
# `@conflict-auditor` hands to `@agent-updater`. That repo had already had governance rows
# misfiled twice from a wrong log path, so this is governance integrity, not a dead link.
# codex already skipped the rewrite under interop; agents-md now matches it.


def _interop_manifest(**extra):
    m = {"project_name": "InteropProject", "interop_source_framework": "copilot-vscode"}
    m.update(extra)
    return m


def test_interop_import_leaves_github_agents_paths_pointing_at_the_source_tree(tmp_path):
    """Acceptance test 1 from the D1 report: every path must resolve against the root.

    The fixture root holds the real `.github/agents/references/x.md`. After an interop
    projection the body must still point there, because interop copies no references into
    `.agents/`.
    """
    root = tmp_path
    (root / ".github" / "agents" / "references").mkdir(parents=True)
    (root / ".github" / "agents" / "references" / "x.md").write_text("ref\n", encoding="utf-8")
    (root / ".github" / "agents" / "foo.agent.md").write_text("# Foo\n", encoding="utf-8")

    body = (
        "# Conflict Auditor\n\nSee `.github/agents/references/x.md` and "
        "`.github/agents/foo.agent.md` before auditing.\n"
    )
    out = AgentsMdAdapter().render_agent_file(body, "conflict-auditor", _interop_manifest())

    assert ".github/agents/references/x.md" in out
    assert ".github/agents/foo.agent.md" in out
    assert ".agents/references/" not in out, "interop import must not invent .agents/references/"

    # The operative property, not just the string: every referenced path exists on disk.
    import re as _re

    for rel in _re.findall(r"`([^`]+\.md)`", out):
        assert (root / rel).exists(), f"{rel} does not resolve against the project root"


def test_native_generation_still_rewrites_to_the_agents_dir():
    """Acceptance test 2 from the D1 report: the native path is unchanged.

    Native generation does materialize `.agents/references/`, so the rewrite is correct
    there and must not be lost to the interop guard.
    """
    body = "# Conflict Auditor\n\nSee `.github/agents/references/x.md`.\n"
    out = AgentsMdAdapter().render_agent_file(body, "conflict-auditor", {"project_name": "P"})

    assert ".agents/references/x.md" in out
    assert ".github/agents" not in out


def test_native_generation_maps_agent_md_suffix_to_the_emitted_filename():
    """This adapter writes `.agents/<slug>.md`, so a rewritten cross-reference must drop
    the `.agent` infix — otherwise it names a file that is never emitted (5 such paths in
    the reporting repo)."""
    body = "# A\n\nSee `.github/agents/security.agent.md` and `.github/agents/orchestrator.agent.md`.\n"
    out = AgentsMdAdapter().render_agent_file(body, "a", {"project_name": "P"})

    assert ".agents/security.md" in out
    assert ".agents/orchestrator.md" in out
    assert ".agent.md" not in out


def test_agent_md_suffix_mapping_does_not_touch_prose_outside_the_agents_dir():
    """Scoped rewrite: a body discussing the copilot `.agent.md` convention, or a path
    under some other directory, must survive untouched."""
    body = (
        "# A\n\nCopilot names its files `<slug>.agent.md`. "
        "See `docs/legacy/orchestrator.agent.md` for the old layout.\n"
    )
    out = AgentsMdAdapter().render_agent_file(body, "a", {"project_name": "P"})

    assert "`<slug>.agent.md`" in out
    assert "docs/legacy/orchestrator.agent.md" in out
