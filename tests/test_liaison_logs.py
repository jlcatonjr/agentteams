"""Tests for agentteams/liaison_logs.py"""

import csv
from pathlib import Path

import pytest

from agentteams.liaison_logs import (
    CHANGELOG_CSV,
    CHANGELOG_HEADERS,
    COORD_LOG_CSV,
    COORD_LOG_HEADERS,
    MigrateResult,
    init_csv_stubs,
    migrate_inline_logs,
)


# ---------------------------------------------------------------------------
# init_csv_stubs
# ---------------------------------------------------------------------------

def test_init_csv_stubs_creates_both_files(tmp_path):
    refs = tmp_path / "references"
    created = init_csv_stubs(refs)
    assert set(created) == {CHANGELOG_CSV, COORD_LOG_CSV}
    assert (refs / CHANGELOG_CSV).exists()
    assert (refs / COORD_LOG_CSV).exists()


def test_init_csv_stubs_writes_correct_headers(tmp_path):
    refs = tmp_path / "references"
    init_csv_stubs(refs)

    with (refs / CHANGELOG_CSV).open(newline="", encoding="utf-8") as fh:
        row = next(csv.reader(fh))
    assert row == CHANGELOG_HEADERS

    with (refs / COORD_LOG_CSV).open(newline="", encoding="utf-8") as fh:
        row = next(csv.reader(fh))
    assert row == COORD_LOG_HEADERS


def test_init_csv_stubs_does_not_overwrite_existing(tmp_path):
    refs = tmp_path / "references"
    refs.mkdir()
    existing = refs / CHANGELOG_CSV
    existing.write_text("sentinel content\n", encoding="utf-8")

    created = init_csv_stubs(refs)
    # Only the coordination log should be created
    assert CHANGELOG_CSV not in created
    assert COORD_LOG_CSV in created
    # Existing file must be untouched
    assert existing.read_text(encoding="utf-8") == "sentinel content\n"


def test_init_csv_stubs_idempotent(tmp_path):
    refs = tmp_path / "references"
    init_csv_stubs(refs)
    created_second = init_csv_stubs(refs)
    assert created_second == []


def test_init_csv_stubs_creates_refs_dir(tmp_path):
    refs = tmp_path / "does" / "not" / "exist"
    init_csv_stubs(refs)
    assert refs.exists()


# ---------------------------------------------------------------------------
# migrate_inline_logs — skipped cases
# ---------------------------------------------------------------------------

def test_migrate_skipped_when_md_absent(tmp_path):
    refs = tmp_path / "references"
    result = migrate_inline_logs(tmp_path / "adjacent-repos.md", refs)
    assert result.skipped is True
    assert result.rows_moved == 0


def test_migrate_no_op_when_no_tables(tmp_path):
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        "# Adjacent Repository Registry\n\n*No entries.*\n",
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    result = migrate_inline_logs(md, refs)
    assert result.rows_moved == 0
    assert result.adjacent_repos_md_updated is False


# ---------------------------------------------------------------------------
# migrate_inline_logs — coordination log
# ---------------------------------------------------------------------------

def _make_coord_log_md(rows: list[str]) -> str:
    table_rows = "\n".join(rows)
    return (
        "# Adjacent Repository Registry\n\n"
        "## Cross-Orchestrator Coordination Log\n\n"
        "Full log stored here.\n\n"
        "| Date | Adjacent repo | Direction | Outcome |\n"
        "|------|--------------|-----------|--------|\n"
        f"{table_rows}\n"
    )


def test_migrate_extracts_coord_log_rows(tmp_path):
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        _make_coord_log_md(
            ["| 2025-01-10 | my-other-repo | outbound | ACCEPT |",
             "| 2025-02-20 | third-repo | inbound | REJECT |"]
        ),
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    result = migrate_inline_logs(md, refs)
    assert result.coord_log_rows_moved == 2
    assert result.adjacent_repos_md_updated is True

    with (refs / COORD_LOG_CSV).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == COORD_LOG_HEADERS
    assert rows[1] == ["2025-01-10", "my-other-repo", "outbound", "ACCEPT"]
    assert rows[2] == ["2025-02-20", "third-repo", "inbound", "REJECT"]


def test_migrate_coord_log_removes_table_from_md(tmp_path):
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        _make_coord_log_md(["| 2025-03-01 | repo-x | outbound | ACCEPT |"]),
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    migrate_inline_logs(md, refs)
    updated = md.read_text(encoding="utf-8")
    # Inline table row should be gone
    assert "| 2025-03-01 |" not in updated
    # CSV reference should be present
    assert COORD_LOG_CSV in updated


def test_migrate_coord_log_skips_empty_table(tmp_path):
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        _make_coord_log_md([]),  # empty table — header + separator only
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    result = migrate_inline_logs(md, refs)
    assert result.coord_log_rows_moved == 0
    assert result.adjacent_repos_md_updated is False


# ---------------------------------------------------------------------------
# migrate_inline_logs — per-repo changelog
# ---------------------------------------------------------------------------

def _make_changelog_md(repo_name: str, rows: list[str]) -> str:
    table_rows = "\n".join(rows)
    return (
        "# Adjacent Repository Registry\n\n"
        "## Active Entries\n\n"
        f"### {repo_name}\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| **Local path** | `/path/to/repo` |\n\n"
        "#### Changelog\n\n"
        "| Date | Action | Files changed | Summary |\n"
        "|------|--------|---------------|---------|\n"
        f"{table_rows}\n"
    )


def test_migrate_extracts_changelog_rows(tmp_path):
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        _make_changelog_md(
            "MyOtherRepo",
            ["| 2025-04-01 | Updated rules | orchestrator.agent.md | New protocol added |"],
        ),
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    result = migrate_inline_logs(md, refs)
    assert result.changelog_rows_moved == 1
    assert result.adjacent_repos_md_updated is True

    with (refs / CHANGELOG_CSV).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == CHANGELOG_HEADERS
    assert rows[1][0] == "2025-04-01"
    assert rows[1][1] == "MyOtherRepo"
    assert rows[1][2] == "Updated rules"
    assert rows[1][3] == "orchestrator.agent.md"
    assert rows[1][4] == "New protocol added"


def test_migrate_changelog_removes_table_from_md(tmp_path):
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        _make_changelog_md(
            "Repo1",
            ["| 2025-05-01 | Added agent | new.agent.md | Initial entry |"],
        ),
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    migrate_inline_logs(md, refs)
    updated = md.read_text(encoding="utf-8")
    assert "| 2025-05-01 |" not in updated
    assert CHANGELOG_CSV in updated


def test_migrate_changelog_inside_html_comment_not_extracted(tmp_path):
    """Tables inside HTML comment blocks (template guides) must not be extracted."""
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        "# Registry\n\n"
        "<!-- Guide:\n"
        "#### Changelog\n\n"
        "| Date | Action | Files changed | Summary |\n"
        "|------|--------|---------------|---------|\n"
        "| 2025-01-01 | example | file.md | example row |\n"
        "-->\n\n"
        "*No entries.*\n",
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    result = migrate_inline_logs(md, refs)
    assert result.rows_moved == 0


# ---------------------------------------------------------------------------
# migrate_inline_logs — idempotency
# ---------------------------------------------------------------------------

def test_migrate_already_referencing_csv_not_doubled(tmp_path):
    """Running migrate twice must not duplicate CSV rows."""
    md = tmp_path / "adjacent-repos.md"
    md.write_text(
        _make_coord_log_md(["| 2025-06-01 | repo-y | outbound | ACCEPT |"]),
        encoding="utf-8",
    )
    refs = tmp_path / "references"
    result1 = migrate_inline_logs(md, refs)
    assert result1.coord_log_rows_moved == 1

    # Second run — md now references CSV; table is gone
    result2 = migrate_inline_logs(md, refs)
    assert result2.coord_log_rows_moved == 0

    with (refs / COORD_LOG_CSV).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    # header + exactly one data row
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# MigrateResult
# ---------------------------------------------------------------------------

def test_migrate_result_rows_moved_sum():
    r = MigrateResult(changelog_rows_moved=3, coord_log_rows_moved=2)
    assert r.rows_moved == 5


def test_migrate_result_success_no_errors():
    r = MigrateResult()
    assert r.success is True


def test_migrate_result_failure_with_errors():
    r = MigrateResult(errors=["something went wrong"])
    assert r.success is False
