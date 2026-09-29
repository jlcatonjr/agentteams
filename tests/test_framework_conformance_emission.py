"""Tests for the opt-in framework-conformance standard-check emission.

Covers the three seams that make the ≤24h trigger a durable, opt-in generator capability:
  * ``framework_research.build_framework_placeholders`` resolves the
    ``FRAMEWORK_CONFORMANCE_STANDARD_CHECK`` placeholder to the block or the empty string;
  * ``cli.generate._framework_conformance_enabled`` decides on/off (explicit brief flag, else
    framework-adapters component presence);
  * ``analyze.build_manifest`` carries an explicit brief bool into the manifest.

No network: ``build_framework_placeholders`` reads whatever snapshot exists under the module tree,
offline. See ``references/provider-adapter-refresh.procedure.md`` §7.
"""

from __future__ import annotations

from pathlib import Path

from agentteams import framework_research as fr
from agentteams.analyze import build_manifest
from agentteams.cli.generate import _framework_conformance_enabled

_SECTION_HEADING = "## Standard Conformance Check"


# --- placeholder resolution --------------------------------------------------


def test_placeholder_block_when_enabled(tmp_path: Path) -> None:
    ph = fr.build_framework_placeholders(tmp_path, offline=True, conformance_check_enabled=True)
    block = ph["FRAMEWORK_CONFORMANCE_STANDARD_CHECK"]
    assert _SECTION_HEADING in block
    assert "agentteams --agent-check" in block
    assert "@framework-adapters-expert" in block


def test_placeholder_empty_when_disabled(tmp_path: Path) -> None:
    ph = fr.build_framework_placeholders(tmp_path, offline=True, conformance_check_enabled=False)
    assert ph["FRAMEWORK_CONFORMANCE_STANDARD_CHECK"] == ""


def test_placeholder_defaults_off(tmp_path: Path) -> None:
    # The builder defaults conformance_check_enabled=False so a caller that never opts in emits
    # nothing (byte-identical to the pre-feature framework-watch reference).
    ph = fr.build_framework_placeholders(tmp_path, offline=True)
    assert ph["FRAMEWORK_CONFORMANCE_STANDARD_CHECK"] == ""


# --- opt-in gate -------------------------------------------------------------


def test_gate_on_for_framework_adapters_team() -> None:
    manifest = {"components": [{"slug": "framework-adapters"}, {"slug": "test-suite"}]}
    assert _framework_conformance_enabled(manifest) is True


def test_gate_off_without_adapters_component() -> None:
    manifest = {"components": [{"slug": "chapters"}, {"slug": "figures"}]}
    assert _framework_conformance_enabled(manifest) is False


def test_gate_explicit_true_overrides_absent_component() -> None:
    manifest = {"framework_conformance_check": True, "components": [{"slug": "chapters"}]}
    assert _framework_conformance_enabled(manifest) is True


def test_gate_explicit_false_overrides_adapters_component() -> None:
    manifest = {"framework_conformance_check": False, "components": [{"slug": "framework-adapters"}]}
    assert _framework_conformance_enabled(manifest) is False


# --- brief → manifest propagation -------------------------------------------


def test_manifest_carries_explicit_true() -> None:
    manifest = build_manifest({"project_goal": "x", "framework_conformance_check": True})
    assert manifest["framework_conformance_check"] is True


def test_manifest_omits_field_when_absent() -> None:
    manifest = build_manifest({"project_goal": "x"})
    assert "framework_conformance_check" not in manifest


def test_manifest_ignores_non_bool() -> None:
    # A non-bool value is not propagated (the gate then falls back to component detection).
    manifest = build_manifest({"project_goal": "x", "framework_conformance_check": "yes"})
    assert "framework_conformance_check" not in manifest
