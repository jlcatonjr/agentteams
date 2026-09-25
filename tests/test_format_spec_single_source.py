"""Single-source-of-truth guards for provider format facts.

``agentteams/frameworks/format_spec.py`` is the one home for per-provider format
facts. Two consumers used to hold their own copies and drift silently:

* the emit adapters (``frameworks/*.py``) — the front-matter keys they write and
  the agent-file extension; and
* the upstream-doc watcher (``framework_research.FRAMEWORK_REGISTRY``) — the doc
  URL, watch tokens, and locations.

These tests fail loudly the moment either consumer diverges from the spec, and
they guard the one piece of name-coupling that can silently disable the watcher:
``framework_research._load_local_adapter_constants`` reads Claude's constants out
of source by *regex on the constant names*, so a rename would make the watcher
read an empty adapter with no error. (Assessment
references/plans/2026-09-22-provider-adapter-freshness-assessment.report.md §3.1-3.2.)
"""

from __future__ import annotations

from pathlib import Path

import agentteams
from agentteams import framework_research as fr
from agentteams.frameworks.format_spec import (
    AGENT_FILE_EXTENSION,
    COPILOT_INSTRUCTIONS_FILENAME,
    FORMAT_SPECS,
    spec_by_adapter_id,
)
from agentteams.frameworks.registry import FRAMEWORKS

_REPO_ROOT = Path(agentteams.__file__).resolve().parents[1]


def test_every_adapter_has_a_format_spec():
    """Every registered adapter id maps to exactly one FormatSpec."""
    for adapter_id in FRAMEWORKS:
        spec = spec_by_adapter_id(adapter_id)
        assert spec is not None, f"no FormatSpec for adapter id {adapter_id!r}"
    # And no spec dangles without a live adapter.
    for spec in FORMAT_SPECS.values():
        assert spec.adapter_id in FRAMEWORKS, (
            f"FormatSpec {spec.research_id!r} names adapter id {spec.adapter_id!r} "
            "that is absent from registry.FRAMEWORKS"
        )


def test_spec_emitted_keys_match_the_adapter():
    """A spec's emitted_front_matter_keys must equal what the adapter declares.

    This is the cross-check that lets the watcher (which reads the spec) reason
    about what the emitter actually writes without a second hand-maintained copy.
    """
    for spec in FORMAT_SPECS.values():
        adapter = FRAMEWORKS[spec.adapter_id]()
        actual = tuple(adapter.required_front_matter_keys())
        assert spec.emitted_front_matter_keys == actual, (
            f"{spec.adapter_id}: FormatSpec.emitted_front_matter_keys="
            f"{spec.emitted_front_matter_keys} but adapter.required_front_matter_keys()="
            f"{actual}"
        )


def test_framework_registry_is_derived_from_specs():
    """framework_research.FRAMEWORK_REGISTRY must agree with FORMAT_SPECS exactly."""
    assert list(fr.FRAMEWORK_REGISTRY.keys()) == list(FORMAT_SPECS.keys())
    for rid, entry in fr.FRAMEWORK_REGISTRY.items():
        spec = FORMAT_SPECS[rid]
        assert entry["label"] == spec.label
        assert entry["source_url"] == spec.source_url
        assert entry["expert_ref"] == spec.expert_ref
        assert entry["expected_keys"] == list(spec.expected_doc_tokens)
        assert entry["expected_locations"] == list(spec.expected_locations)


def test_legacy_claude_scan_vocab_tracks_the_spec():
    """The legacy single-framework scan constants stay single-sourced from the spec."""
    claude = FORMAT_SPECS["claude"]
    assert fr.EXPECTED_FRONT_MATTER_KEYS == list(claude.expected_doc_tokens)
    assert fr.EXPECTED_LOCATIONS == list(claude.expected_locations)
    assert fr.CLAUDE_DOC_URL == claude.source_url


def test_agent_extension_matches_the_emitting_adapter():
    """The canonical agent-file extension equals what the Copilot adapter emits."""
    adapter = FRAMEWORKS["copilot-vscode"]()
    assert adapter.get_file_extension("agent") == AGENT_FILE_EXTENSION
    assert adapter.get_file_extension("builder") == AGENT_FILE_EXTENSION


def test_load_local_adapter_constants_regex_still_matches_live_claude():
    """Guard the name-coupling: the watcher regex must still parse live claude.py.

    framework_research._load_local_adapter_constants greps claude.py for the
    constant names _CLAUDE_REQUIRED_KEYS / _CLAUDE_DEFAULT_ALLOWED_TOOLS. If either
    is renamed, the function silently returns empty lists and the daily drift diff
    becomes vacuous with no error. This asserts the regex still finds both against
    the real source tree, so such a rename fails CI here instead of in production.
    """
    got = fr._load_local_adapter_constants(_REPO_ROOT)
    assert got["required_front_matter_keys"], (
        "watcher could not parse _CLAUDE_REQUIRED_KEYS from live claude.py — "
        "was the constant renamed? Update framework_research._KEY_LIST_RE."
    )
    assert got["default_allowed_tools"], (
        "watcher could not parse _CLAUDE_DEFAULT_ALLOWED_TOOLS from live claude.py — "
        "was the constant renamed? Update framework_research._DEFAULT_TOOLS_RE."
    )


def test_copilot_instructions_filename_is_single_sourced_in_the_planner():
    """The canonical instructions filename has one home: the emit planner uses the
    constant, not a raw literal, so a rename is a one-site edit there."""
    assert COPILOT_INSTRUCTIONS_FILENAME == "copilot-instructions.md"
    planner = (_REPO_ROOT / "agentteams" / "output_plan.py").read_text(encoding="utf-8")
    assert "COPILOT_INSTRUCTIONS_FILENAME" in planner, "planner no longer imports the constant"
    # The raw literal must NOT reappear in the planned instructions path.
    assert '"../copilot-instructions.md"' not in planner
    assert "'../copilot-instructions.md'" not in planner


def test_copilot_vscode_required_yaml_keys_are_single_sourced():
    """copilot_vscode's emit-time presence check derives from the one key tuple,
    which in turn matches the spec — no divergent hand-maintained copy (conflict F1)."""
    from agentteams.frameworks import copilot_vscode as cv

    spec = FORMAT_SPECS["copilot_vscode"]
    assert cv._VSCODE_FRONT_MATTER_KEYS == spec.emitted_front_matter_keys
    assert cv._REQUIRED_YAML_KEYS == set(spec.emitted_front_matter_keys)
    # The from-scratch builder and the defaults map share one model-id constant.
    assert cv._YAML_DEFAULTS["model"] == cv._COPILOT_MODEL_DEFAULT


def test_audit_required_yaml_keys_derive_from_format_spec():
    """audit.py's universal agent-file front-matter check derives from the single source,
    so the fourth copy (conflict F1, 2026-W39) can no longer silently drift from the spec."""
    from agentteams import audit

    assert audit._REQUIRED_YAML_KEYS == FORMAT_SPECS["copilot_vscode"].emitted_front_matter_keys
