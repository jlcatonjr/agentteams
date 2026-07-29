"""Tests for agentteams.update_report.

The module's governing invariant is that a clean run behaves exactly as it did
before the report existed: no file, no output, no change to any exit code. These
tests pin that first, because a regression there would make the most common
operation noisier for no gain.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.update_report import (
    REPORT_NAME,
    build_report,
    has_attributable_events,
    write_report,
)


class _Result:
    """Minimal EmitResult stand-in carrying only the fields the report reads."""

    def __init__(self, **kwargs: object) -> None:
        self.written: list[str] = []
        self.merged: list[str] = []
        self.skipped_legacy: list[str] = []
        self.sections_preserved: list[str] = []
        self.shrink_notices: list[str] = []
        self.lost_fence_bodies: list[str] = []
        self.__dict__.update(kwargs)


def test_clean_run_has_nothing_to_attribute() -> None:
    result = _Result(written=["a.agent.md", "b.agent.md"], merged=["a.agent.md"])
    assert has_attributable_events(result) is False


def test_clean_run_writes_no_file(tmp_path: Path) -> None:
    result = _Result(written=["a.agent.md"], merged=["a.agent.md"])
    assert write_report(result, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_preserved_section_is_attributable(tmp_path: Path) -> None:
    result = _Result(
        written=["security.agent.md"],
        sections_preserved=["security_rules_invariant"],
    )
    assert has_attributable_events(result) is True
    path = write_report(result, tmp_path)
    assert path is not None and path.name == REPORT_NAME
    body = path.read_text(encoding="utf-8")
    assert "Fenced bodies preserved" in body
    assert "security_rules_invariant" in body


def test_skipped_legacy_is_recorded_as_a_failure_to_apply(tmp_path: Path) -> None:
    result = _Result(skipped_legacy=["legacy/old.agent.md"])
    body = build_report(result)
    assert "Legacy files skipped" in body
    assert "- FAIL: update not applied to `legacy/old.agent.md`" in body


def test_backup_path_recorded_when_given() -> None:
    result = _Result(sections_preserved=["core"])
    assert "Recovery" in build_report(result, backup_path=".agentteams-backups/x")
    assert "Recovery" not in build_report(result)


def test_empty_sections_are_omitted() -> None:
    body = build_report(_Result(sections_preserved=["core"]))
    assert "Legacy files skipped" not in body
    assert "Fence markers retrofitted" not in body


def test_tolerates_results_missing_optional_fields(tmp_path: Path) -> None:
    """Older EmitResult shapes lack the newer fields; absence is not an error."""

    class Minimal:
        written = ["a.md"]

    assert has_attributable_events(Minimal()) is False
    assert write_report(Minimal(), tmp_path) is None
