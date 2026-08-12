"""Unit tests for the generalized three-way sync classifier (B.1/B.2/B.3).

Covers the 6 classification cases named in the plan's §5 testing strategy:

1. unchanged — no divergence between canonical and native
2. native-moved-clean — native edited, canonical unchanged → apply
3. canonical-moved-clean — canonical edited, native unchanged → proposal
4. both-moved-conflict — both sides changed → proposal
5. capability-key-changed-clean-otherwise — native-moved on a capability
   field, must still route to human review, never auto-apply (§6.1)
6. no-baseline-exists — no recorded baseline → report only, apply nothing

Plus tests for the baseline writer (B.2): write/load/delete/has_baseline.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.sync_classifier import (
    Action,
    Classification,
    classify_sync,
    is_capability_field,
)
from agentteams.sync_baseline import (
    BASELINE_SCHEMA_VERSION,
    baseline_path,
    delete_baseline,
    has_baseline,
    load_baseline,
    write_baseline,
)


# ---------------------------------------------------------------------------
# Fixtures
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
    """Build a minimal CAI agent dict for testing."""
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


def _make_cai(agents: list[dict]) -> dict:
    """Build a minimal CAI document wrapping the given agents."""
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00+00:00",
        "source_framework": "goose",
        "source_dir": "/test/native",
        "instructions_binding": {"source_name": "", "content": ""},
        "agents": sorted(agents, key=lambda a: a["slug"]),
    }


# ---------------------------------------------------------------------------
# B.2: Baseline writer tests
# ---------------------------------------------------------------------------

class TestBaselineWriter:
    """Tests for sync_baseline.write/load/delete/has_baseline (B.2)."""

    def test_write_and_load_round_trip(self, tmp_path: Path):
        """A baseline written and loaded preserves agent content exactly."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        agents = [_make_agent(slug="alpha"), _make_agent(slug="beta")]
        cai = _make_cai(agents)

        written_path = write_baseline(canonical_dir, "goose", cai, native_source_dir="/test/native")
        assert written_path == canonical_dir / "sync-baselines" / "goose.json"
        assert written_path.is_file()

        loaded = load_baseline(canonical_dir, "goose")
        assert loaded is not None
        assert loaded["schema_version"] == BASELINE_SCHEMA_VERSION
        assert loaded["framework"] == "goose"
        assert loaded["source_dir"] == "/test/native"
        assert "created_at" in loaded
        # Agent content preserved
        assert len(loaded["agents"]) == 2
        assert loaded["agents"][0]["slug"] == "alpha"
        assert loaded["agents"][1]["slug"] == "beta"

    def test_load_returns_none_when_no_baseline(self, tmp_path: Path):
        """No baseline file → load_baseline returns None."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        assert load_baseline(canonical_dir, "goose") is None

    def test_has_baseline(self, tmp_path: Path):
        """has_baseline returns True/False correctly."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        assert has_baseline(canonical_dir, "goose") is False
        write_baseline(canonical_dir, "goose", _make_cai([_make_agent()]))
        assert has_baseline(canonical_dir, "goose") is True

    def test_delete_baseline(self, tmp_path: Path):
        """delete_baseline removes the file, returns True; second call returns False."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        write_baseline(canonical_dir, "goose", _make_cai([_make_agent()]))
        assert delete_baseline(canonical_dir, "goose") is True
        assert has_baseline(canonical_dir, "goose") is False
        assert delete_baseline(canonical_dir, "goose") is False

    def test_multiple_framework_baselines_coexist(self, tmp_path: Path):
        """Baselines for different frameworks don't clobber each other."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()
        write_baseline(canonical_dir, "goose", _make_cai([_make_agent(slug="g")]))
        write_baseline(canonical_dir, "claude", _make_cai([_make_agent(slug="c")]))

        assert has_baseline(canonical_dir, "goose")
        assert has_baseline(canonical_dir, "claude")

        goose = load_baseline(canonical_dir, "goose")
        claude = load_baseline(canonical_dir, "claude")
        assert goose["agents"][0]["slug"] == "g"
        assert claude["agents"][0]["slug"] == "c"

    def test_baseline_path(self, tmp_path: Path):
        """baseline_path returns the expected location."""
        canonical_dir = tmp_path / "canonical"
        p = baseline_path(canonical_dir, "goose")
        assert p == canonical_dir / "sync-baselines" / "goose.json"


# ---------------------------------------------------------------------------
# B.1: Classifier tests — the 6 classification cases
# ---------------------------------------------------------------------------

class TestClassifierCases:
    """Tests for the 6 classification cases from the plan's testing strategy."""

    def test_case1_unchanged(self):
        """Case 1: canonical and native are identical → all fields unchanged."""
        agent = _make_agent(body_markdown="Original body.\n")
        baseline_agent = _make_agent(body_markdown="Original body.\n")
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([agent]), _make_cai([agent]), baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        assert all(r.classification == Classification.UNCHANGED for r in ar.field_results)
        assert not ar.has_changes
        assert len(ar.applied) == 0
        assert len(ar.proposals) == 0

    def test_case2_native_moved_clean(self):
        """Case 2: native edited body, canonical unchanged → native-moved, apply."""
        canonical_agent = _make_agent(body_markdown="Original body.\n")
        native_agent = _make_agent(body_markdown="Edited by user.\n")
        baseline_agent = _make_agent(body_markdown="Original body.\n")
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY
        assert len(ar.applied) >= 1
        assert "Safe to absorb" in body_result.notice

    def test_case3_canonical_moved_clean(self):
        """Case 3: canonical edited body, native unchanged → canonical-moved, proposal."""
        canonical_agent = _make_agent(body_markdown="Updated in canonical.\n")
        native_agent = _make_agent(body_markdown="Original body.\n")
        baseline_agent = _make_agent(body_markdown="Original body.\n")
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.CANONICAL_MOVED
        assert body_result.action == Action.PROPOSAL
        assert "stale" in body_result.notice.lower()

    def test_case4_both_moved_conflict(self):
        """Case 4: both sides changed body → both-moved-conflict, proposal."""
        canonical_agent = _make_agent(body_markdown="Canonical version.\n")
        native_agent = _make_agent(body_markdown="Native version.\n")
        baseline_agent = _make_agent(body_markdown="Original body.\n")
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.BOTH_MOVED_CONFLICT
        assert body_result.action == Action.PROPOSAL
        assert "CONFLICT" in body_result.notice

    def test_case5_capability_key_always_routes_to_human_review(self):
        """Case 5: capability field changed on native side, canonical unchanged.

        Even though the three-way table would classify this as native-moved
        (clean, one-sided), the capability-key carve-out (§6.1) MUST route
        it to human review (proposal), never auto-apply.

        This is the single most safety-critical test in the entire plan.
        """
        canonical_agent = _make_agent(
            capabilities={"tool_scopes": ["read", "search"]},
        )
        # Native side WIDENED the capability grant
        native_agent = _make_agent(
            capabilities={"tool_scopes": ["read", "search", "edit", "execute"]},
        )
        baseline_agent = _make_agent(
            capabilities={"tool_scopes": ["read", "search"]},
        )
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        cap_result = next(r for r in ar.field_results if r.field_name == "capabilities")
        # The classification is still native-moved (that's the factual state)
        assert cap_result.classification == Classification.NATIVE_MOVED
        # BUT the action MUST be proposal, never apply — the carve-out
        assert cap_result.action == Action.PROPOSAL
        assert "CAPABILITY" in cap_result.notice or "capability" in cap_result.notice.lower()
        assert "§6.1" in cap_result.notice
        # Ensure nothing is auto-applied for capability fields
        assert all(r.action != Action.APPLY for r in ar.field_results
                    if r.field_name == "capabilities")

    def test_case5_capability_narrowing_also_routes_to_human_review(self):
        """A capability NARROWING (removing tools) also routes to human review.

        Even though front_matter_merge.py allows narrowing with clean
        provenance, the sync classifier is more conservative: ANY change to
        a capability field routes to human review.  This is deliberate —
        the absorb direction is a new, broader channel than the template
        render path, and the cost of a wrong auto-apply is higher (it fans
        out to all frameworks).
        """
        canonical_agent = _make_agent(
            capabilities={"tool_scopes": ["read", "search", "edit", "execute"]},
        )
        # Native side NARROWED the grant
        native_agent = _make_agent(
            capabilities={"tool_scopes": ["read"]},
        )
        baseline_agent = _make_agent(
            capabilities={"tool_scopes": ["read", "search", "edit", "execute"]},
        )
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        cap_result = next(r for r in ar.field_results if r.field_name == "capabilities")
        assert cap_result.classification == Classification.NATIVE_MOVED
        assert cap_result.action == Action.PROPOSAL

    def test_case5_raw_front_matter_with_capability_key(self):
        """raw_front_matter containing a capability key (tools) is capability-bearing."""
        canonical_agent = _make_agent(
            raw_front_matter={"tools": "['read', 'search']", "user-invokable": "true"},
        )
        native_agent = _make_agent(
            raw_front_matter={"tools": "['read', 'search', 'edit']", "user-invokable": "true"},
        )
        baseline_agent = _make_agent(
            raw_front_matter={"tools": "['read', 'search']", "user-invokable": "true"},
        )
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="claude",
        )
        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.classification == Classification.NATIVE_MOVED
        assert rfm_result.action == Action.PROPOSAL

    def test_case5_raw_front_matter_without_capability_key_can_apply(self):
        """raw_front_matter with NO capability keys is NOT capability-bearing.

        A raw_front_matter with only metadata (e.g. a custom label) is a
        normal field that CAN be auto-applied when cleanly native-moved.
        """
        canonical_agent = _make_agent(
            raw_front_matter={"custom-label": "old"},
        )
        native_agent = _make_agent(
            raw_front_matter={"custom-label": "new"},
        )
        baseline_agent = _make_agent(
            raw_front_matter={"custom-label": "old"},
        )
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="claude",
        )
        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.classification == Classification.NATIVE_MOVED
        assert rfm_result.action == Action.APPLY

    def test_case5_capability_key_dropped_from_native_still_proposal(self):
        """B.4 security regression: native DROPS a capability key from
        raw_front_matter — must still route to human review, never auto-apply.

        Before the B.4 security fix, ``is_capability_field`` only checked the
        native value for capability keys.  If the native side removed
        ``tools`` from ``raw_front_matter``, the native value no longer had
        capability keys, so the classifier would auto-apply the removal —
        violating §6.1's rule that capability fields ALWAYS route to human
        review regardless of classification cleanliness.

        The fix: ``is_capability_field_any_side`` checks all three values
        (canonical, native, baseline).  If ANY of them has capability keys,
        the field is treated as capability-bearing.
        """
        canonical_agent = _make_agent(
            raw_front_matter={"tools": "['read', 'search', 'edit']", "user-invokable": "true"},
        )
        # Native DROPPED the tools key — only metadata remains
        native_agent = _make_agent(
            raw_front_matter={"user-invokable": "false"},
        )
        baseline_agent = _make_agent(
            raw_front_matter={"tools": "['read', 'search', 'edit']", "user-invokable": "true"},
        )
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="claude",
        )
        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        # Classification is still native-moved (that's the factual state)
        assert rfm_result.classification == Classification.NATIVE_MOVED
        # BUT the action MUST be proposal — the carve-out holds even when
        # the native side dropped the capability key
        assert rfm_result.action == Action.PROPOSAL
        assert "CAPABILITY" in rfm_result.notice or "capability" in rfm_result.notice.lower()

    def test_case5_capability_key_only_in_baseline_still_proposal(self):
        """B.4 security regression: capability key present in baseline only.

        Edge case: canonical and native both dropped the capability key, but
        the baseline still records it.  This is a both-moved conflict, but
        the capability carve-out must still apply — the field is
        capability-bearing because the baseline says so.
        """
        canonical_agent = _make_agent(
            raw_front_matter={"user-invokable": "false"},
        )
        native_agent = _make_agent(
            raw_front_matter={"user-invokable": "true"},
        )
        baseline_agent = _make_agent(
            raw_front_matter={"tools": "['read']", "user-invokable": "false"},
        )
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline,
            framework="claude",
        )
        ar = report.agent_reports[0]
        rfm_result = next(
            r for r in ar.field_results if r.field_name == "raw_front_matter"
        )
        assert rfm_result.action == Action.PROPOSAL

    def test_case6_no_baseline_exists(self):
        """Case 6: no baseline → nothing applied, report only.

        This mirrors front_matter_merge.py's rule: an unknown baseline means
        "the project may have edited everything," so nothing is auto-applied.
        The classifier still reports divergences for human review.
        """
        canonical_agent = _make_agent(body_markdown="Canonical body.\n")
        native_agent = _make_agent(body_markdown="Native body.\n")
        # No baseline at all

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([native_agent]),
            baseline=None,
            framework="goose",
        )
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NO_BASELINE
        assert body_result.action == Action.KEEP
        assert "No baseline" in body_result.notice
        assert len(ar.applied) == 0

    def test_case6_no_baseline_unchanged_still_unchanged(self):
        """No baseline + canonical and native identical → unchanged (not no-baseline)."""
        agent = _make_agent(body_markdown="Same body.\n")
        report = classify_sync(
            _make_cai([agent]), _make_cai([agent]), baseline=None,
            framework="goose",
        )
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.UNCHANGED
        assert body_result.action == Action.KEEP


# ---------------------------------------------------------------------------
# is_capability_field tests
# ---------------------------------------------------------------------------

class TestIsCapabilityField:
    """Tests for the capability-field detection helper."""

    def test_capabilities_is_always_capability(self):
        assert is_capability_field("capabilities", {"tool_scopes": ["read"]}) is True

    def test_raw_front_matter_with_tools_is_capability(self):
        assert is_capability_field(
            "raw_front_matter", {"tools": "['read']"}
        ) is True

    def test_raw_front_matter_with_user_invokable_is_capability(self):
        # F.2: user-invokable is a copilot-vscode capability key (controls
        # agent visibility in VS Code chat UI).  Must route to human review.
        assert is_capability_field(
            "raw_front_matter", {"user-invokable": "true"}
        ) is True

    def test_raw_front_matter_without_capability_keys_is_not(self):
        assert is_capability_field(
            "raw_front_matter", {"description": "some text"}
        ) is False

    def test_raw_front_matter_empty_is_not_capability(self):
        assert is_capability_field("raw_front_matter", {}) is False

    def test_non_capability_field_is_not(self):
        assert is_capability_field("body_markdown", "text") is False
        assert is_capability_field("name", "Agent Name") is False
        assert is_capability_field("description", "desc") is False


# ---------------------------------------------------------------------------
# Agent presence tests
# ---------------------------------------------------------------------------

class TestAgentPresence:
    """Tests for agents present in one side but not the other."""

    def test_agent_in_native_not_in_canonical(self):
        """Agent exists in native but not canonical → proposal (new agent)."""
        native_agent = _make_agent(slug="new-agent")
        baseline = {"agents": []}

        report = classify_sync(
            _make_cai([]),
            _make_cai([native_agent]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        assert ar.agent_slug == "new-agent"
        r = ar.field_results[0]
        assert r.field_name == "__agent__"
        assert r.action == Action.PROPOSAL
        assert "New agent" in r.notice

    def test_agent_in_canonical_not_in_native(self):
        """Agent in canonical but not native → proposal (removed from native)."""
        canonical_agent = _make_agent(slug="removed-agent")
        baseline = {"agents": [_make_agent(slug="removed-agent")]}

        report = classify_sync(
            _make_cai([canonical_agent]),
            _make_cai([]),
            baseline,
            framework="goose",
        )
        ar = report.agent_reports[0]
        assert ar.agent_slug == "removed-agent"
        r = ar.field_results[0]
        assert r.field_name == "__agent__"
        assert r.action == Action.PROPOSAL
        assert "Removed from native" in r.notice


# ---------------------------------------------------------------------------
# Report formatting tests
# ---------------------------------------------------------------------------

class TestReportFormatting:
    """Tests for the human-readable report output."""

    def test_report_text_includes_summary(self):
        """to_text() produces a readable summary."""
        agent = _make_agent(body_markdown="Original.\n")
        native = _make_agent(body_markdown="Edited.\n")
        baseline_agent = _make_agent(body_markdown="Original.\n")
        baseline = {"agents": [baseline_agent]}

        report = classify_sync(
            _make_cai([agent]),
            _make_cai([native]),
            baseline,
            framework="goose",
            canonical_dir="/canonical",
            native_dir="/native",
        )
        text = report.to_text()
        assert "Sync Report" in text
        assert "goose" in text
        assert "/canonical" in text
        assert "/native" in text

    def test_no_changes_report_says_so(self):
        """Report with no divergent fields says so."""
        agent = _make_agent()
        baseline = {"agents": [_make_agent()]}
        report = classify_sync(
            _make_cai([agent]), _make_cai([agent]), baseline,
            framework="goose",
        )
        text = report.to_text()
        assert "no divergent fields" in text.lower() or "No divergent" in text


# ---------------------------------------------------------------------------
# Integration test with the baseline writer
# ---------------------------------------------------------------------------

class TestBaselineAndClassifierIntegration:
    """Integration: write baseline, modify native, classify, verify result."""

    def test_write_baseline_then_classify_after_native_edit(self, tmp_path: Path):
        """End-to-end: write baseline → edit native → classify → native-moved."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()

        # Initial sync: canonical and native are identical
        agent = _make_agent(
            slug="alpha",
            body_markdown="Original instructions.\n",
            capabilities={"tool_scopes": ["read"]},
        )
        cai = _make_cai([agent])

        # Write baseline
        write_baseline(canonical_dir, "goose", cai, native_source_dir="/native")

        # Now the user edits the native body (but NOT capabilities)
        edited_native = _make_agent(
            slug="alpha",
            body_markdown="Updated instructions with new guidance.\n",
            capabilities={"tool_scopes": ["read"]},
        )
        native_cai = _make_cai([edited_native])

        # Canonical unchanged
        canonical_cai = _make_cai([_make_agent(
            slug="alpha",
            body_markdown="Original instructions.\n",
            capabilities={"tool_scopes": ["read"]},
        )])

        # Load baseline and classify
        baseline = load_baseline(canonical_dir, "goose")
        assert baseline is not None

        report = classify_sync(
            canonical_cai, native_cai, baseline,
            canonical_dir=str(canonical_dir),
            native_dir="/native",
            framework="goose",
        )

        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NATIVE_MOVED
        assert body_result.action == Action.APPLY

        # Capabilities unchanged — should be unchanged
        cap_result = next(r for r in ar.field_results if r.field_name == "capabilities")
        assert cap_result.classification == Classification.UNCHANGED

    def test_no_baseline_after_delete_classifies_as_no_baseline(self, tmp_path: Path):
        """After deleting a baseline, classification falls to no-baseline."""
        canonical_dir = tmp_path / "canonical"
        canonical_dir.mkdir()

        agent = _make_agent(slug="alpha", body_markdown="Body.\n")
        cai = _make_cai([agent])

        write_baseline(canonical_dir, "goose", cai)
        assert has_baseline(canonical_dir, "goose")

        delete_baseline(canonical_dir, "goose")
        assert not has_baseline(canonical_dir, "goose")

        # Now classify with no baseline
        native_cai = _make_cai([_make_agent(slug="alpha", body_markdown="Different.\n")])
        report = classify_sync(
            cai, native_cai, baseline=None,
            framework="goose",
        )
        ar = report.agent_reports[0]
        body_result = next(r for r in ar.field_results if r.field_name == "body_markdown")
        assert body_result.classification == Classification.NO_BASELINE
        assert body_result.action == Action.KEEP
