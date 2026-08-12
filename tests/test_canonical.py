"""Tests for agentteams/canonical.py — the durable exploded on-disk canonical
format (durable-canonical-agent-format plan §5.5, steps E.1-E.3 / H.1).

Covers: materialize/load round-trip exactness against real repo sources,
dry-run zero-side-effect guarantee, idempotent re-materialization, skill
verbatim fidelity, invariant-core re-lift, references materialization, path
guards, the missing-team.cai.json error, and the PyYAML-free fallback parser
(PyYAML is a test-only dependency — the load path must work without it).
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from agentteams import canonical
from agentteams.interop import export_to_cai

REPO = Path(__file__).resolve().parents[1]
_COPILOT = REPO / ".github" / "agents"
_CLAUDE = REPO / ".claude" / "agents"

pytestmark = pytest.mark.skipif(
    not any(_COPILOT.glob("*.agent.md")) or not any(_CLAUDE.glob("*.md")),
    reason="repo source teams (.github/agents, .claude/agents) not found or gitignored",
)


# ---------------------------------------------------------------------------
# Round-trip exactness
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_copilot_vscode_round_trip_is_exact(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        assert canonical.load_canonical(out) == cai

    def test_claude_round_trip_is_exact(self, tmp_path):
        cai = export_to_cai(_CLAUDE, "claude")
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        assert canonical.load_canonical(out) == cai

    def test_all_handoffs_survive(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        cai2 = canonical.load_canonical(out)
        expected = sum(len(a["handoffs"]) for a in cai["agents"])
        assert sum(len(a["handoffs"]) for a in cai2["agents"]) == expected

    def test_invariant_core_round_trips_once(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        with_core = [a for a in cai["agents"] if a["invariant_core_markdown"]]
        assert with_core, "expected agents carrying an invariant-core span"
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        cai2 = canonical.load_canonical(out)
        for agent in with_core:
            loaded = next(a for a in cai2["agents"] if a["slug"] == agent["slug"])
            assert loaded["invariant_core_markdown"] == agent["invariant_core_markdown"]
            # span travels through the body exactly once, fences intact
            body = (out / "agents" / f"{agent['slug']}.md").read_text(encoding="utf-8")
            assert body.count("AGENTTEAMS:BEGIN invariant_core") == 1

    def test_skills_are_verbatim(self, tmp_path):
        cai = export_to_cai(_CLAUDE, "claude")
        assert cai["skills"], "claude source captures skills"
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        for skill in cai["skills"]:
            for f in skill["files"]:
                dest = out / "skills" / skill["slug"] / f["rel_path"]
                assert dest.read_text(encoding="utf-8") == f["content"]


# ---------------------------------------------------------------------------
# Write-path guarantees
# ---------------------------------------------------------------------------

class TestWriteGuarantees:
    def test_dry_run_has_zero_side_effects(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        out = tmp_path / "c"
        result = canonical.materialize_canonical(cai, out, dry_run=True)
        assert result.dry_run
        assert result.written  # planned writes enumerated
        assert not out.exists()  # not even mkdir

    def test_materialization_is_idempotent(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        out = tmp_path / "c"

        def tree_hash() -> dict[str, bytes]:
            return {
                str(p.relative_to(out)): p.read_bytes()
                for p in sorted(out.rglob("*"))
                if p.is_file()
            }

        canonical.materialize_canonical(cai, out)
        first = tree_hash()
        canonical.materialize_canonical(cai, out)
        assert tree_hash() == first

    def test_references_materialize_and_load(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        cai = dict(cai)
        cai["references"] = [
            {"rel_path": "references/ref-x-reference.md", "content": "# ref x\n"}
        ]
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        assert (out / "references/ref-x-reference.md").read_text() == "# ref x\n"
        cai2 = canonical.load_canonical(out)
        assert cai2["references"] == cai["references"]

    def test_skill_without_files_synthesizes_skill_md(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        cai = dict(cai)
        cai["skills"] = [
            {
                "slug": "handbuilt",
                "front_matter": {"name": "Handbuilt", "description": "d"},
                "body_markdown": "# body\n",
                "files": [],
            }
        ]
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        text = (out / "skills/handbuilt/SKILL.md").read_text()
        assert text.startswith("---\n")
        assert "Handbuilt" in text and "# body" in text


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

class TestGuards:
    def test_unsafe_agent_slug_is_refused(self, tmp_path):
        cai = {
            "schema_version": "2.0",
            "created_at": "2026-01-01T00:00:00Z",
            "source_framework": "goose",
            "instructions_binding": {"source_name": "test", "content": "test"},
            "agents": [{"slug": "../evil", "name": "evil", "body_markdown": "x", "source_path": "x.md"}],
        }
        with pytest.raises(ValueError, match="Unsafe or empty agent slug"):
            canonical.materialize_canonical(cai, tmp_path / "c")

    def test_reference_path_escape_is_refused(self, tmp_path):
        cai = {
            "schema_version": "2.0",
            "created_at": "2026-01-01T00:00:00Z",
            "source_framework": "goose",
            "instructions_binding": {"source_name": "test", "content": "test"},
            "agents": [],
            "references": [{"rel_path": "../outside.md", "content": "x"}],
        }
        with pytest.raises(ValueError, match="escapes the canonical dir"):
            canonical.materialize_canonical(cai, tmp_path / "c")

    def test_missing_team_cai_json_is_not_a_canonical_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            canonical.load_canonical(tmp_path)

    def test_non_dict_agents_entry_is_refused(self, tmp_path):
        # D.1: Schema validation catches non-object agent entries before the
        # internal guard.  Both raise ValueError; the schema message is now
        # the first to fire.
        with pytest.raises(ValueError, match="not of type 'object'"):
            canonical.materialize_canonical(
                {
                    "schema_version": "2.0",
                    "created_at": "2026-01-01T00:00:00Z",
                    "source_framework": "goose",
                    "instructions_binding": {"source_name": "test", "content": "test"},
                    "agents": ["not-a-dict"],
                },
                tmp_path / "c",
            )


# ---------------------------------------------------------------------------
# PyYAML-free fallback parser (PyYAML is a test-only dependency)
# ---------------------------------------------------------------------------

class TestFallbackParser:
    def test_round_trip_exact_without_pyyaml(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)

        real_import = builtins.__import__

        def refuse_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("simulated PyYAML absence")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = refuse_yaml
        try:
            cai2 = canonical.load_canonical(out)
        finally:
            builtins.__import__ = real_import

        assert cai2 == cai

    def test_minimal_parser_directly(self):
        text = (
            'slug: "a-b"\n'
            'name: "A B"\n'
            "capabilities:\n"
            "  tool_scopes: [read, edit]\n"
            "handoffs:\n"
            '  - to: "x"\n'
            '    prompt: "line one\\nline two"\n'
            "    send: true\n"
        )
        parsed = canonical._minimal_yaml_load(text)
        assert parsed["slug"] == "a-b"
        assert parsed["capabilities"] == {"tool_scopes": ["read", "edit"]}
        assert parsed["handoffs"] == [
            {"to": "x", "prompt": "line one\nline two", "send": True}
        ]
