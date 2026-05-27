"""Tests for agentteams.instructions_split (Phase 4)."""

from __future__ import annotations

from agentteams import instructions_split as ins


_SAMPLE = """# ResearchTeam — Copilot Instructions

> Project conventions.

## Project Overview

Goal: do research.
"""


def test_render_contains_original_bytes_verbatim():
    out = ins.render_cache_split(copilot_instructions=_SAMPLE)
    assert _SAMPLE.rstrip() in out


def test_render_includes_boundary_marker_once():
    out = ins.render_cache_split(copilot_instructions=_SAMPLE)
    assert out.count(ins.DYNAMIC_BOUNDARY_MARKER) == 1


def test_render_boundary_after_stable_preamble():
    out = ins.render_cache_split(copilot_instructions=_SAMPLE)
    idx_src = out.find(_SAMPLE.rstrip())
    idx_marker = out.find(ins.DYNAMIC_BOUNDARY_MARKER)
    assert 0 <= idx_src < idx_marker


def test_render_dynamic_section_records_source_sha():
    out = ins.render_cache_split(copilot_instructions=_SAMPLE)
    assert "Source SHA-256:" in out
    # Hash should appear; verify by recomputing.
    import hashlib
    expected = hashlib.sha256(_SAMPLE.encode("utf-8")).hexdigest()
    assert expected in out


def test_render_dynamic_section_records_source_path():
    out = ins.render_cache_split(
        copilot_instructions=_SAMPLE,
        source_relative_path=".github/custom-path.md",
    )
    assert ".github/custom-path.md" in out


def test_render_dynamic_section_records_timestamp():
    out = ins.render_cache_split(
        copilot_instructions=_SAMPLE,
        build_timestamp="2026-05-27T00:00:00+00:00",
    )
    assert "2026-05-27T00:00:00+00:00" in out


def test_verify_equivalence_passes_on_rendered_output():
    out = ins.render_cache_split(copilot_instructions=_SAMPLE)
    assert ins.verify_equivalence(cache_split_text=out, original=_SAMPLE)


def test_verify_equivalence_fails_when_stable_section_altered():
    out = ins.render_cache_split(copilot_instructions=_SAMPLE)
    tampered = out.replace("do research", "do mischief")
    assert not ins.verify_equivalence(cache_split_text=tampered, original=_SAMPLE)


def test_verify_equivalence_fails_when_boundary_missing():
    # Take just the stable portion (no boundary, no dynamic section).
    bare = "header\n\n" + _SAMPLE.rstrip()
    assert not ins.verify_equivalence(cache_split_text=bare, original=_SAMPLE)


def test_render_empty_input_still_produces_valid_layout():
    out = ins.render_cache_split(copilot_instructions="")
    # Empty needle: still verifiable; boundary still emitted exactly once.
    assert out.count(ins.DYNAMIC_BOUNDARY_MARKER) == 1
