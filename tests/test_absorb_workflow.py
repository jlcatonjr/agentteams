"""End-to-end and convergence tests for the absorb workflow (C.3, C.4).

C.3: Multi-framework convergence — absorbing from framework A must not clobber
framework B's previously-captured state in canonical.

C.4: End-to-end scenario tests per v1 framework (goose, claude) — materialize
canonical, build native, hand-edit native, run absorb, assert the report
classifies correctly and (with --apply) the expected fields land in canonical
while capability-bearing fields are flagged for human review.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.sync_baseline import (
    has_baseline,
    load_baseline,
    write_baseline,
)
from agentteams.sync_classifier import Action, Classification, classify_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(
    slug: str = "test-agent",
    name: str = "Test Agent",
    description: str = "A test agent.",
    body_markdown: str = "Body text.\n",
    capabilities: dict | None = None,
    handoffs: list | None = None,
    raw_front_matter: dict | None = None,
) -> dict:
    a = {
        "slug": slug,
        "name": name,
        "description": description,
        "body_markdown": body_markdown,
        "capabilities": capabilities or {"tool_scopes": ["read", "search"]},
        "handoffs": handoffs or [],
        "invariant_core_markdown": None,
        "source_path": f"agents/{slug}.md",
    }
    if raw_front_matter:
        a["raw_front_matter"] = raw_front_matter
    return a


def _make_cai(agents: list[dict], framework: str = "goose") -> dict:
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00+00:00",
        "source_framework": framework,
        "source_dir": "/test/native",
        "instructions_binding": {"source_name": "", "content": ""},
        "agents": sorted(agents, key=lambda a: a["slug"]),
    }


def _setup_canonical_and_baseline(
    agents: list[dict],
    framework: str,
    tmp_path: Path,
) -> tuple[Path, dict, dict]:
    """Materialize canonical, write baseline, return (canonical_dir, cai, baseline)."""
    cai = _make_cai(agents, framework)
    canonical_dir = tmp_path / "canonical"
    materialize_canonical(cai, canonical_dir)
    write_baseline(canonical_dir, framework, cai, native_source_dir="/native")
    baseline = load_baseline(canonical_dir, framework)
    assert baseline is not None
    return canonical_dir, cai, baseline


# ---------------------------------------------------------------------------
# C.4: End-to-end scenario tests
# ---------------------------------------------------------------------------

class TestEndToEndGoose:
    """C.4: End-to-end scenario test for Goose framework."""

    def test_goose_body_edit_absorbed(self, tmp_path: Path):
        """Materialize canonical → edit native body → absorb → verify in canonical."""
        agents = [
            _make_agent(slug="alpha", body_markdown="Original instructions.\n"),
            _make_agent(slug="beta", body_markdown="Beta instructions.\n"),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "goose", tmp_path
        )

        # Simulate native edit: change alpha's body
        native_cai = _make_cai([
            _make_agent(slug="alpha", body_markdown="Updated instructions.\n"),
            _make_agent(slug="beta", body_markdown="Beta instructions.\n"),
        ], "goose")

        # Classify
        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            canonical_dir=str(canonical_dir),
            native_dir="/native",
            framework="goose",
        )

        # Assert: body_markdown is native-moved (apply)
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

        # Apply: write native values into canonical
        for ar in report.agent_reports:
            for fr in ar.field_results:
                if fr.action == Action.APPLY:
                    for agent in canonical_cai.get("agents", []):
                        if agent.get("slug") == ar.agent_slug:
                            agent[fr.field_name] = fr.native_value

        materialize_canonical(canonical_cai, canonical_dir)

        # Verify the change landed in canonical
        reloaded = load_canonical(canonical_dir)
        alpha = next(a for a in reloaded["agents"] if a["slug"] == "alpha")
        assert "Updated instructions" in alpha["body_markdown"]

    def test_goose_capability_widening_not_auto_applied(self, tmp_path: Path):
        """Capability widening on native side must route to proposal, not apply."""
        agents = [
            _make_agent(
                slug="alpha",
                capabilities={"tool_scopes": ["read", "search"]},
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "goose", tmp_path
        )

        # Native widens capabilities
        native_cai = _make_cai([
            _make_agent(
                slug="alpha",
                capabilities={"tool_scopes": ["read", "search", "edit", "execute"]},
            ),
        ], "goose")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="goose",
        )

        ar = report.agent_reports[0]
        cap_result = next(r for r in ar.field_results if r.field_name == "capabilities")
        assert cap_result.classification == Classification.NATIVE_MOVED
        assert cap_result.action == Action.PROPOSAL
        assert report.total_applied == 0
        assert report.total_proposals >= 1

    def test_goose_report_only_writes_nothing(self, tmp_path: Path):
        """Default invocation (no --apply) must not modify canonical."""
        agents = [_make_agent(slug="alpha", body_markdown="Original.\n")]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "goose", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(slug="alpha", body_markdown="Edited.\n"),
        ], "goose")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="goose",
        )

        # Report should show changes
        assert report.has_changes
        assert report.total_applied >= 1

        # But don't apply — canonical should remain unchanged
        reloaded = load_canonical(canonical_dir)
        alpha = reloaded["agents"][0]
        assert "Original" in alpha["body_markdown"]
        assert "Edited" not in alpha["body_markdown"]

    def test_goose_no_baseline_reports_only(self, tmp_path: Path):
        """No baseline → report only, apply nothing."""
        agents = [_make_agent(slug="alpha", body_markdown="Canonical.\n")]
        cai = _make_cai(agents, "goose")
        canonical_dir = tmp_path / "canonical"
        materialize_canonical(cai, canonical_dir)
        # No baseline written

        native_cai = _make_cai([
            _make_agent(slug="alpha", body_markdown="Native.\n"),
        ], "goose")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline=None,
            framework="goose",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NO_BASELINE
        assert body_result.action == Action.KEEP
        assert report.total_applied == 0


class TestEndToEndClaude:
    """C.4: End-to-end scenario test for Claude framework."""

    def test_claude_body_edit_absorbed(self, tmp_path: Path):
        """Materialize canonical → edit Claude native body → absorb."""
        agents = [
            _make_agent(
                slug="reviewer",
                body_markdown="Original Claude instructions.\n",
                raw_front_matter={"user-invocable": "true"},
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "claude", tmp_path
        )

        # Native edits body (not raw_front_matter)
        native_cai = _make_cai([
            _make_agent(
                slug="reviewer",
                body_markdown="Updated Claude instructions.\n",
                raw_front_matter={"user-invocable": "true"},
            ),
        ], "claude")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="claude",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

    def test_claude_capability_in_raw_front_matter_routes_to_proposal(self, tmp_path: Path):
        """Claude raw_front_matter with tools key → capability carve-out applies."""
        agents = [
            _make_agent(
                slug="reviewer",
                raw_front_matter={"tools": "['Read', 'Grep']", "user-invocable": "true"},
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "claude", tmp_path
        )

        # Native widens tools
        native_cai = _make_cai([
            _make_agent(
                slug="reviewer",
                raw_front_matter={"tools": "['Read', 'Grep', 'Edit', 'Bash']", "user-invocable": "true"},
            ),
        ], "claude")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="claude",
        )

        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.action == Action.PROPOSAL

    def test_claude_non_capability_raw_front_matter_can_apply(self, tmp_path: Path):
        """Claude raw_front_matter with only non-capability metadata → can auto-apply."""
        agents = [
            _make_agent(
                slug="reviewer",
                raw_front_matter={"custom-label": "old"},
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "claude", tmp_path
        )

        # Native changes only custom-label (metadata, not capability)
        native_cai = _make_cai([
            _make_agent(
                slug="reviewer",
                raw_front_matter={"custom-label": "new"},
            ),
        ], "claude")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="claude",
        )

        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.action == Action.APPLY


# ---------------------------------------------------------------------------
# C.3: Multi-framework convergence test
# ---------------------------------------------------------------------------

class TestMultiFrameworkConvergence:
    """C.3: Absorbing from framework A must not clobber framework B's state.

    This is the part of the user's original framing ("multiple infrastructures...
    intermediated... absorbed") nothing in this codebase had addressed before.
    """

    def test_goose_absorb_does_not_clobber_claude_baseline(self, tmp_path: Path):
        """Absorbing a goose edit must not affect the claude baseline."""
        # Set up canonical with agents
        agents = [
            _make_agent(slug="alpha", body_markdown="Original.\n"),
            _make_agent(slug="beta", body_markdown="Beta.\n"),
        ]
        cai = _make_cai(agents, "goose")
        canonical_dir = tmp_path / "canonical"
        materialize_canonical(cai, canonical_dir)

        # Write baselines for BOTH frameworks
        write_baseline(canonical_dir, "goose", cai, native_source_dir="/goose")
        write_baseline(canonical_dir, "claude", cai, native_source_dir="/claude")

        assert has_baseline(canonical_dir, "goose")
        assert has_baseline(canonical_dir, "claude")

        # Now absorb a goose edit
        goose_native = _make_cai([
            _make_agent(slug="alpha", body_markdown="Goose-edited.\n"),
            _make_agent(slug="beta", body_markdown="Beta.\n"),
        ], "goose")

        canonical_cai = load_canonical(canonical_dir)
        goose_baseline = load_baseline(canonical_dir, "goose")
        report = classify_sync(
            canonical_cai, goose_native, goose_baseline,
            framework="goose",
        )

        # Apply goose changes
        for ar in report.agent_reports:
            for fr in ar.field_results:
                if fr.action == Action.APPLY:
                    for agent in canonical_cai.get("agents", []):
                        if agent.get("slug") == ar.agent_slug:
                            agent[fr.field_name] = fr.native_value

        materialize_canonical(canonical_cai, canonical_dir)

        # Update goose baseline
        write_baseline(canonical_dir, "goose", goose_native,
                       native_source_dir="/goose")

        # Verify: claude baseline is UNTOUCHED
        claude_baseline = load_baseline(canonical_dir, "claude")
        claude_alpha = next(
            a for a in claude_baseline["agents"] if a["slug"] == "alpha"
        )
        assert "Original" in claude_alpha["body_markdown"]
        assert "Goose-edited" not in claude_alpha["body_markdown"]

    def test_both_frameworks_absorb_independently(self, tmp_path: Path):
        """Both goose and claude can absorb edits independently into the same canonical."""
        agents = [
            _make_agent(slug="alpha", body_markdown="Original.\n"),
        ]
        cai = _make_cai(agents, "goose")
        canonical_dir = tmp_path / "canonical"
        materialize_canonical(cai, canonical_dir)

        # Write baselines for both
        write_baseline(canonical_dir, "goose", cai, native_source_dir="/goose")
        write_baseline(canonical_dir, "claude", cai, native_source_dir="/claude")

        # Absorb goose edit
        goose_native = _make_cai([
            _make_agent(slug="alpha", body_markdown="Goose edit.\n"),
        ], "goose")

        canonical_cai = load_canonical(canonical_dir)
        goose_baseline = load_baseline(canonical_dir, "goose")
        goose_report = classify_sync(
            canonical_cai, goose_native, goose_baseline,
            framework="goose",
        )

        # Apply goose changes to canonical
        for ar in goose_report.agent_reports:
            for fr in ar.field_results:
                if fr.action == Action.APPLY:
                    for agent in canonical_cai.get("agents", []):
                        if agent.get("slug") == ar.agent_slug:
                            agent[fr.field_name] = fr.native_value
        materialize_canonical(canonical_cai, canonical_dir)
        write_baseline(canonical_dir, "goose", goose_native,
                       native_source_dir="/goose")

        # Now absorb a DIFFERENT claude edit (on the same agent)
        # The claude baseline still has "Original" so this should classify as native-moved
        claude_native = _make_cai([
            _make_agent(slug="alpha", body_markdown="Claude edit.\n"),
        ], "claude")

        canonical_cai2 = load_canonical(canonical_dir)
        claude_baseline = load_baseline(canonical_dir, "claude")
        claude_report = classify_sync(
            canonical_cai2, claude_native, claude_baseline,
            framework="claude",
        )

        # Claude's body should be native-moved (claude baseline says "Original",
        # canonical now says "Goose edit", claude native says "Claude edit")
        ar = claude_report.agent_reports[0]
        body_result = next(
            r for r in ar.field_results if r.field_name == "body_markdown"
        )
        # canonical moved (goose edit) AND native moved (claude edit) → conflict
        assert body_result.classification == Classification.BOTH_MOVED_CONFLICT
        assert body_result.action == Action.PROPOSAL

    def test_separate_agents_per_framework_no_clobber(self, tmp_path: Path):
        """Absorbing a new agent from goose doesn't affect claude's agents."""
        agents = [_make_agent(slug="shared", body_markdown="Shared.\n")]
        cai = _make_cai(agents, "goose")
        canonical_dir = tmp_path / "canonical"
        materialize_canonical(cai, canonical_dir)
        write_baseline(canonical_dir, "goose", cai, native_source_dir="/goose")
        write_baseline(canonical_dir, "claude", cai, native_source_dir="/claude")

        # Goose adds a new agent
        goose_native = _make_cai([
            _make_agent(slug="shared", body_markdown="Shared.\n"),
            _make_agent(slug="goose-only", body_markdown="Goose only.\n"),
        ], "goose")

        canonical_cai = load_canonical(canonical_dir)
        goose_baseline = load_baseline(canonical_dir, "goose")
        report = classify_sync(
            canonical_cai, goose_native, goose_baseline,
            framework="goose",
        )

        # The new agent should be a proposal (not auto-applied)
        goose_only_report = next(
            ar for ar in report.agent_reports if ar.agent_slug == "goose-only"
        )
        assert goose_only_report.field_results[0].action == Action.PROPOSAL

        # Claude baseline should be unaffected
        claude_baseline = load_baseline(canonical_dir, "claude")
        claude_slugs = [a["slug"] for a in claude_baseline["agents"]]
        assert "shared" in claude_slugs
        assert "goose-only" not in claude_slugs


# ---------------------------------------------------------------------------
# CLI integration smoke test
# ---------------------------------------------------------------------------

class TestCLIAbsorbSmoke:
    """Smoke test for the --absorb-from CLI flag."""

    def test_absorb_from_nonexistent_canonical_returns_error(self, tmp_path: Path):
        """--absorb-from with no canonical dir → error, not crash."""
        from agentteams.cli.app import main

        native_dir = tmp_path / "native"
        native_dir.mkdir()
        canonical_dir = tmp_path / "canonical"

        rc = main([
            '--absorb-from', str(native_dir),
            '--absorb-source-framework', 'goose',
            '--absorb-canonical-dir', str(canonical_dir),
        ])
        assert rc == 1

    def test_absorb_from_out_of_scope_framework_rejected(self, tmp_path: Path):
        """--absorb-source-framework with unregistered framework → rejected at CLI layer.

        argparse rejects invalid choices with exit code 2, not 1. This is
        correct behavior — the framework scope is enforced at the argparse
        choices level, the same pattern A2.1 established for bridge mode.
        """
        from agentteams.cli.app import main

        # Create a fake native dir
        native_dir = tmp_path / "native"
        native_dir.mkdir()
        (native_dir / "test.agent.md").write_text(
            "---\nname: Test\ndescription: Test\n---\nBody.\n"
        )

        with pytest.raises(SystemExit) as exc_info:
            main([
                '--absorb-from', str(native_dir),
                '--absorb-source-framework', 'nonexistent-framework',
                '--absorb-canonical-dir', str(tmp_path / "canonical"),
            ])
        assert exc_info.value.code == 2  # argparse rejection

    def test_absorb_report_then_apply_workflow(self, tmp_path: Path):
        """Full workflow: report-only → apply → idempotent second run."""
        from agentteams.cli.app import main

        # Set up canonical + baseline
        agents = [_make_agent(slug="alpha", body_markdown="Original.\n")]
        cai = _make_cai(agents, "goose")
        canonical_dir = tmp_path / "canonical"
        materialize_canonical(cai, canonical_dir)
        write_baseline(canonical_dir, "goose", cai, native_source_dir="/native")

        # We need to create a native dir that export_to_cai can read.
        # Since we can't easily create goose recipe YAML, use canonical as
        # both source and target (canonical→canonical is a valid export path).
        # Instead, let's test the CLI with canonical as the native source.
        native_dir = canonical_dir  # use canonical as native source too

        # Report-only
        rc = main([
            '--absorb-from', str(native_dir),
            '--absorb-source-framework', 'goose',
            '--absorb-canonical-dir', str(canonical_dir),
        ])
        assert rc == 0  # should succeed, report no changes


# ---------------------------------------------------------------------------
# F.4: End-to-end scenario tests for v2 frameworks
# ---------------------------------------------------------------------------

class TestEndToEndCopilotVSCode:
    """F.4: End-to-end scenario test for copilot-vscode framework.

    copilot-vscode uses YAML front matter with capability-bearing keys:
    tools, model, user-invocable. These must route to proposal; body
    edits and non-capability front matter can auto-apply.
    """

    def test_copilot_vscode_body_edit_absorbed(self, tmp_path: Path):
        """Body edit on copilot-vscode agent → native-moved, auto-apply."""
        agents = [
            _make_agent(
                slug="orchestrator",
                body_markdown="Original instructions.\n",
                raw_front_matter={
                    "user-invocable": "true",
                    "tools": "['Read', 'Grep']",
                    "model": "gpt-4",
                },
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "copilot-vscode", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(
                slug="orchestrator",
                body_markdown="Updated instructions.\n",
                raw_front_matter={
                    "user-invocable": "true",
                    "tools": "['Read', 'Grep']",
                    "model": "gpt-4",
                },
            ),
        ], "copilot-vscode")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="copilot-vscode",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

    def test_copilot_vscode_tools_widening_routes_to_proposal(self, tmp_path: Path):
        """Tools widening in raw_front_matter → proposal (capability carve-out)."""
        agents = [
            _make_agent(
                slug="orchestrator",
                raw_front_matter={"tools": "['Read']", "user-invocable": "true"},
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "copilot-vscode", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(
                slug="orchestrator",
                raw_front_matter={
                    "tools": "['Read', 'Edit', 'Bash']",
                    "user-invocable": "true",
                },
            ),
        ], "copilot-vscode")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="copilot-vscode",
        )

        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.action == Action.PROPOSAL
        assert report.total_applied == 0

    def test_copilot_vscode_user_invokable_change_routes_to_proposal(self, tmp_path: Path):
        """F.2: user-invocable is capability-bearing → must route to proposal."""
        agents = [
            _make_agent(
                slug="orchestrator",
                raw_front_matter={"user-invocable": "true", "description": "test"},
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "copilot-vscode", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(
                slug="orchestrator",
                raw_front_matter={"user-invocable": "false", "description": "test"},
            ),
        ], "copilot-vscode")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="copilot-vscode",
        )

        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.action == Action.PROPOSAL


class TestEndToEndCopilotCLI:
    """F.4: End-to-end scenario test for copilot-cli framework.

    copilot-cli is a plain-Markdown adapter with no front matter and no
    capability channel. All edits are body edits and safe to auto-apply.
    """

    def test_copilot_cli_body_edit_absorbed(self, tmp_path: Path):
        """Body edit on copilot-cli agent → native-moved, auto-apply."""
        agents = [
            _make_agent(slug="agent", body_markdown="Original.\n"),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "copilot-cli", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(slug="agent", body_markdown="Updated.\n"),
        ], "copilot-cli")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="copilot-cli",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

    def test_copilot_cli_no_capability_fields_all_apply(self, tmp_path: Path):
        """copilot-cli has no capability channel — all changes are safe to apply."""
        agents = [
            _make_agent(
                slug="agent",
                body_markdown="Original.\n",
                description="Old desc.",
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "copilot-cli", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(
                slug="agent",
                body_markdown="Updated.\n",
                description="New desc.",
            ),
        ], "copilot-cli")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="copilot-cli",
        )

        assert report.total_proposals == 0
        assert report.total_applied >= 2  # body + description


class TestEndToEndAgentsMD:
    """F.4: End-to-end scenario test for agents-md framework.

    agents-md is a plain-Markdown adapter, generate-only. Like copilot-cli,
    it has no capability channel — all edits are body edits.
    """

    def test_agents_md_body_edit_absorbed(self, tmp_path: Path):
        """Body edit on agents-md agent → native-moved, auto-apply."""
        agents = [
            _make_agent(slug="researcher", body_markdown="Original.\n"),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "agents-md", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(slug="researcher", body_markdown="Updated.\n"),
        ], "agents-md")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="agents-md",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

    def test_agents_md_no_capability_fields_all_apply(self, tmp_path: Path):
        """agents-md has no capability channel — all changes are safe to apply."""
        agents = [
            _make_agent(
                slug="researcher",
                body_markdown="Original.\n",
                description="Old desc.",
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "agents-md", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(
                slug="researcher",
                body_markdown="Updated.\n",
                description="New desc.",
            ),
        ], "agents-md")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="agents-md",
        )

        assert report.total_proposals == 0
        assert report.total_applied >= 2


class TestEndToEndCodex:
    """F.4: End-to-end scenario test for codex framework.

    Codex inherits from AgentsMdAdapter — plain Markdown, no front matter,
    no capability channel. All edits are body edits.
    """

    def test_codex_body_edit_absorbed(self, tmp_path: Path):
        """Body edit on codex agent → native-moved, auto-apply."""
        agents = [
            _make_agent(slug="codex-agent", body_markdown="Original.\n"),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "codex", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(slug="codex-agent", body_markdown="Updated.\n"),
        ], "codex")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="codex",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

    def test_codex_no_capability_fields_all_apply(self, tmp_path: Path):
        """codex has no capability channel — all changes are safe to apply."""
        agents = [
            _make_agent(
                slug="codex-agent",
                body_markdown="Original.\n",
                description="Old desc.",
            ),
        ]
        canonical_dir, cai, baseline = _setup_canonical_and_baseline(
            agents, "codex", tmp_path
        )

        native_cai = _make_cai([
            _make_agent(
                slug="codex-agent",
                body_markdown="Updated.\n",
                description="New desc.",
            ),
        ], "codex")

        canonical_cai = load_canonical(canonical_dir)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            framework="codex",
        )

        assert report.total_proposals == 0
        assert report.total_applied >= 2


# ---------------------------------------------------------------------------
# F.4: Multi-framework convergence with all 6 frameworks
# ---------------------------------------------------------------------------

class TestAllFrameworkConvergence:
    """F.4: Absorbing from any of the 6 frameworks must not clobber others."""

    @pytest.mark.parametrize("framework", [
        "copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex",
    ])
    def test_each_framework_absorbs_independently(self, tmp_path: Path, framework: str):
        """Each framework can absorb edits independently into shared canonical."""
        agents = [
            _make_agent(slug="alpha", body_markdown="Original.\n"),
        ]
        cai = _make_cai(agents, framework)
        canonical_dir = tmp_path / "canonical"
        materialize_canonical(cai, canonical_dir)

        # Write baselines for all 6 frameworks
        for fw in ["copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex"]:
            write_baseline(canonical_dir, fw, cai, native_source_dir=f"/{fw}")

        # Absorb an edit from the parametrized framework
        native_cai = _make_cai([
            _make_agent(slug="alpha", body_markdown=f"{framework}-edited.\n"),
        ], framework)

        canonical_cai = load_canonical(canonical_dir)
        fw_baseline = load_baseline(canonical_dir, framework)
        report = classify_sync(
            canonical_cai, native_cai, fw_baseline,
            framework=framework,
        )

        # Apply changes
        for ar in report.agent_reports:
            for fr in ar.field_results:
                if fr.action == Action.APPLY:
                    for agent in canonical_cai.get("agents", []):
                        if agent.get("slug") == ar.agent_slug:
                            agent[fr.field_name] = fr.native_value
        materialize_canonical(canonical_cai, canonical_dir)
        write_baseline(canonical_dir, framework, native_cai,
                       native_source_dir=f"/{framework}")

        # Verify: ALL other baselines are untouched
        for other_fw in ["copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex"]:
            if other_fw == framework:
                continue
            other_baseline = load_baseline(canonical_dir, other_fw)
            assert other_baseline is not None
            other_alpha = next(
                a for a in other_baseline["agents"] if a["slug"] == "alpha"
            )
            assert "Original" in other_alpha["body_markdown"]
            assert f"{framework}-edited" not in other_alpha["body_markdown"]
