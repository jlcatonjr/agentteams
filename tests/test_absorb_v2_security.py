"""F.5: Security checkpoint — capability carve-out across all 6 frameworks.

Adversarial tests that attempt to silently widen, narrow, or drop
capability-bearing fields through each framework's absorb path. Every
scenario MUST route to ``Action.PROPOSAL`` — never ``Action.APPLY`` —
because capability changes fan out from canonical to all derived frameworks.

The carve-out (§6.1) is the single most safety-critical rule in the absorb
design.  These tests pin it for all 6 registered frameworks, including the
4 new v2 frameworks (copilot-vscode, copilot-cli, agents-md, codex).
"""

from __future__ import annotations

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.sync_baseline import load_baseline, write_baseline
from agentteams.sync_classifier import Action, classify_sync


# ---------------------------------------------------------------------------
# Helpers (mirrors test_absorb_workflow.py)
# ---------------------------------------------------------------------------

def _make_agent(
    slug: str = "test-agent",
    name: str = "Test Agent",
    description: str = "A test agent.",
    body_markdown: str = "Body text.\n",
    capabilities: dict | None = None,
    raw_front_matter: dict | None = None,
) -> dict:
    a = {
        "slug": slug,
        "name": name,
        "description": description,
        "body_markdown": body_markdown,
        "capabilities": capabilities or {"tool_scopes": ["read", "search"]},
        "handoffs": [],
        "invariant_core_markdown": None,
        "source_path": f"agents/{slug}.md",
    }
    if raw_front_matter:
        a["raw_front_matter"] = raw_front_matter
    return a


def _make_cai(agents: list[dict], framework: str) -> dict:
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00+00:00",
        "source_framework": framework,
        "source_dir": "/test/native",
        "instructions_binding": {"source_name": "", "content": ""},
        "agents": sorted(agents, key=lambda a: a["slug"]),
    }


def _setup(agents, framework, tmp_path):
    cai = _make_cai(agents, framework)
    canonical_dir = tmp_path / "canonical"
    materialize_canonical(cai, canonical_dir)
    write_baseline(canonical_dir, framework, cai, native_source_dir="/native")
    baseline = load_baseline(canonical_dir, framework)
    assert baseline is not None
    return canonical_dir, cai, baseline


# ---------------------------------------------------------------------------
# F.5: Capability widening — must always route to PROPOSAL
# ---------------------------------------------------------------------------

class TestCapabilityWideningAllFrameworks:
    """Attempt to widen capabilities through each framework — must route to proposal."""

    @pytest.mark.parametrize("framework", [
        "copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex",
    ])
    def test_capabilities_widening_routes_to_proposal(self, tmp_path, framework):
        """Widening tool_scopes via capabilities field → PROPOSAL for every framework."""
        agents = [_make_agent(
            slug="alpha",
            capabilities={"tool_scopes": ["read", "search"]},
        )]
        canonical_dir, _, baseline = _setup(agents, framework, tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            capabilities={"tool_scopes": ["read", "search", "edit", "execute"]},
        )], framework)

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework=framework,
        )
        ar = report.agent_reports[0]
        cap = next(r for r in ar.field_results if r.field_name == "capabilities")
        assert cap.action == Action.PROPOSAL
        assert report.total_applied == 0


class TestCapabilityNarrowingAllFrameworks:
    """Attempt to narrow capabilities (drop tools) — must route to proposal."""

    @pytest.mark.parametrize("framework", [
        "copilot-vscode", "copilot-cli", "claude", "goose", "agents-md", "codex",
    ])
    def test_capabilities_narrowing_routes_to_proposal(self, tmp_path, framework):
        """Narrowing tool_scopes → PROPOSAL for every framework."""
        agents = [_make_agent(
            slug="alpha",
            capabilities={"tool_scopes": ["read", "search", "edit", "execute"]},
        )]
        canonical_dir, _, baseline = _setup(agents, framework, tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            capabilities={"tool_scopes": ["read"]},
        )], framework)

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework=framework,
        )
        ar = report.agent_reports[0]
        cap = next(r for r in ar.field_results if r.field_name == "capabilities")
        assert cap.action == Action.PROPOSAL
        assert report.total_applied == 0


# ---------------------------------------------------------------------------
# F.5: Raw front-matter capability keys — must route to PROPOSAL
# ---------------------------------------------------------------------------

class TestRawFrontMatterCapabilityKeys:
    """raw_front_matter with capability keys (tools, model, user-invocable,
    permissionMode) must route to PROPOSAL when changed."""

    def test_tools_change_in_raw_front_matter_routes_to_proposal(self, tmp_path):
        """Tools change in raw_front_matter → PROPOSAL (via any-side check)."""
        agents = [_make_agent(
            slug="alpha",
            raw_front_matter={"tools": "['Read']", "user-invocable": "true"},
        )]
        canonical_dir, _, baseline = _setup(agents, "copilot-vscode", tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            raw_front_matter={"tools": "['Read', 'Edit', 'Bash']", "user-invocable": "true"},
        )], "copilot-vscode")

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework="copilot-vscode",
        )
        ar = report.agent_reports[0]
        rfm = next(r for r in ar.field_results if r.field_name == "raw_front_matter")
        assert rfm.action == Action.PROPOSAL

    def test_model_change_in_raw_front_matter_routes_to_proposal(self, tmp_path):
        """Model change in raw_front_matter → PROPOSAL."""
        agents = [_make_agent(
            slug="alpha",
            raw_front_matter={"model": "gpt-4", "user-invocable": "true"},
        )]
        canonical_dir, _, baseline = _setup(agents, "copilot-vscode", tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            raw_front_matter={"model": "claude-3.5-sonnet", "user-invocable": "true"},
        )], "copilot-vscode")

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework="copilot-vscode",
        )
        ar = report.agent_reports[0]
        rfm = next(r for r in ar.field_results if r.field_name == "raw_front_matter")
        assert rfm.action == Action.PROPOSAL

    def test_user_invokable_change_routes_to_proposal(self, tmp_path):
        """F.2: user-invocable change → PROPOSAL (new capability key)."""
        agents = [_make_agent(
            slug="alpha",
            raw_front_matter={"user-invocable": "true", "description": "test"},
        )]
        canonical_dir, _, baseline = _setup(agents, "copilot-vscode", tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            raw_front_matter={"user-invocable": "false", "description": "test"},
        )], "copilot-vscode")

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework="copilot-vscode",
        )
        ar = report.agent_reports[0]
        rfm = next(r for r in ar.field_results if r.field_name == "raw_front_matter")
        assert rfm.action == Action.PROPOSAL

    def test_permission_mode_change_routes_to_proposal(self, tmp_path):
        """F.2: permissionMode change → PROPOSAL (new capability key)."""
        agents = [_make_agent(
            slug="alpha",
            raw_front_matter={"permissionMode": "default", "description": "test"},
        )]
        canonical_dir, _, baseline = _setup(agents, "claude", tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            raw_front_matter={"permissionMode": "acceptEdits", "description": "test"},
        )], "claude")

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework="claude",
        )
        ar = report.agent_reports[0]
        rfm = next(r for r in ar.field_results if r.field_name == "raw_front_matter")
        assert rfm.action == Action.PROPOSAL

    def test_capability_key_dropped_from_native_still_proposal(self, tmp_path):
        """B.4 regression: native DROPS tools from raw_front_matter → still PROPOSAL.

        The any-side check catches this: baseline records tools, so the field
        is capability-bearing regardless of what the native side shows.
        """
        agents = [_make_agent(
            slug="alpha",
            raw_front_matter={"tools": "['Read', 'Edit']", "user-invocable": "true"},
        )]
        canonical_dir, _, baseline = _setup(agents, "copilot-vscode", tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            raw_front_matter={"description": "no tools here"},
        )], "copilot-vscode")

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework="copilot-vscode",
        )
        ar = report.agent_reports[0]
        rfm = next(r for r in ar.field_results if r.field_name == "raw_front_matter")
        assert rfm.action == Action.PROPOSAL

    def test_capability_key_only_in_baseline_still_proposal(self, tmp_path):
        """Capability key present ONLY in baseline → still PROPOSAL.

        Both canonical and native dropped tools, but baseline records it.
        The any-side check catches this via the baseline value.
        """
        agents = [_make_agent(
            slug="alpha",
            raw_front_matter={"description": "no tools"},
        )]
        canonical_dir, _, baseline = _setup(agents, "claude", tmp_path)

        # Corrupt baseline to add tools (simulating a historical record)
        baseline_agent = baseline["agents"][0]
        baseline_agent["raw_front_matter"] = {"tools": "['Read']", "description": "no tools"}
        write_baseline(canonical_dir, "claude", baseline, native_source_dir="/native")
        baseline = load_baseline(canonical_dir, "claude")

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            raw_front_matter={"description": "still no tools"},
        )], "claude")

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework="claude",
        )
        ar = report.agent_reports[0]
        rfm = next(r for r in ar.field_results if r.field_name == "raw_front_matter")
        assert rfm.action == Action.PROPOSAL


# ---------------------------------------------------------------------------
# F.5: Frameworks with no capability channel — body edits are safe to apply
# ---------------------------------------------------------------------------

class TestNoCapabilityChannelFrameworks:
    """copilot-cli, agents-md, and codex have no front-matter capability channel.

    For these frameworks, ALL changes are body/content edits — none are
    capability-bearing. This is the correct behavior: with no capability
    channel, there's nothing to carve out.
    """

    @pytest.mark.parametrize("framework", ["copilot-cli", "agents-md", "codex"])
    def test_body_edit_safely_applies(self, tmp_path, framework):
        """Body edits on no-capability frameworks → APPLY (not PROPOSAL)."""
        agents = [_make_agent(slug="alpha", body_markdown="Original.\n")]
        canonical_dir, _, baseline = _setup(agents, framework, tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha", body_markdown="Updated.\n",
        )], framework)

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework=framework,
        )
        ar = report.agent_reports[0]
        body = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body.action == Action.APPLY
        assert report.total_proposals == 0

    @pytest.mark.parametrize("framework", ["copilot-cli", "agents-md", "codex"])
    def test_capabilities_field_still_proposal(self, tmp_path, framework):
        """Even for no-capability-channel frameworks, the capabilities field
        is still capability-bearing by definition (§6.1). The carve-out holds
        universally — if someone somehow sets capabilities, it's a proposal.
        """
        agents = [_make_agent(
            slug="alpha",
            capabilities={"tool_scopes": ["read"]},
        )]
        canonical_dir, _, baseline = _setup(agents, framework, tmp_path)

        native_cai = _make_cai([_make_agent(
            slug="alpha",
            capabilities={"tool_scopes": ["read", "edit", "execute"]},
        )], framework)

        report = classify_sync(
            load_canonical(canonical_dir), native_cai, baseline,
            framework=framework,
        )
        ar = report.agent_reports[0]
        cap = next(r for r in ar.field_results if r.field_name == "capabilities")
        assert cap.action == Action.PROPOSAL
        assert report.total_applied == 0
