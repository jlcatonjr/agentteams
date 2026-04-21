"""liaison_logs.py — Manage external CSV log files for the Repo Liaison agent.

The Repo Liaison agent tracks two kinds of log data that grow unboundedly:

  * Per-repo changelogs — one row per cross-repository file change
  * Cross-orchestrator coordination log — one row per orchestrator exchange

These are stored as CSV files in the ``references/`` directory rather than
inline markdown tables, so they stay machine-readable and do not bloat the
agent file over time.

Public API
----------
init_csv_stubs(refs_dir)
    Create the two CSV files (header row only) if they do not yet exist.
    Safe to call on every generation run — never overwrites existing data.

migrate_inline_logs(adjacent_repos_md, refs_dir)
    Scan an existing ``adjacent-repos.md`` for inline markdown log tables,
    extract any data rows to the corresponding CSV files, and rewrite the
    markdown file to reference the CSVs instead.  Returns a ``MigrateResult``
    describing what was found and moved.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: File name for the per-repo changelog CSV (relative to references/)
CHANGELOG_CSV = "adjacent-repos-changelog.csv"

#: File name for the cross-orchestrator coordination log CSV
COORD_LOG_CSV = "adjacent-repos-coordination-log.csv"

#: Column headers for each CSV
CHANGELOG_HEADERS: list[str] = ["date", "repo_name", "action", "files_changed", "summary"]
COORD_LOG_HEADERS: list[str] = ["date", "adjacent_repo", "direction", "outcome"]

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MigrateResult:
    """Result of a ``migrate_inline_logs`` call.

    Attributes:
        changelog_rows_moved:   Number of changelog rows extracted to CSV.
        coord_log_rows_moved:   Number of coordination log rows extracted to CSV.
        adjacent_repos_md_updated: True if the markdown file was rewritten.
        skipped:                True if the markdown file did not exist.
        errors:                 Human-readable messages for any failures.
    """
    changelog_rows_moved: int = 0
    coord_log_rows_moved: int = 0
    adjacent_repos_md_updated: bool = False
    skipped: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def rows_moved(self) -> int:
        """Total rows moved across both logs."""
        return self.changelog_rows_moved + self.coord_log_rows_moved

    @property
    def success(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Markdown table separator row: |---|---|...
_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$")

# A markdown table data row: starts and ends with |
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")

# Changelog table header: | Date | Action | Files changed | Summary |
_CHANGELOG_HDR_RE = re.compile(
    r"^\|\s*Date\s*\|\s*Action\s*\|\s*Files\s+changed\s*\|\s*Summary\s*\|",
    re.IGNORECASE,
)

# Coordination log table header: | Date | Adjacent repo | Direction | Outcome |
_COORD_LOG_HDR_RE = re.compile(
    r"^\|\s*Date\s*\|\s*Adjacent\s+repo\s*\|\s*Direction\s*\|\s*Outcome\s*\|",
    re.IGNORECASE,
)

# #### Changelog heading (not inside an HTML comment)
_CHANGELOG_HEADING_RE = re.compile(r"^#{1,6}\s+Changelog\s*$", re.IGNORECASE)

# ## Cross-Orchestrator Coordination Log heading
_COORD_LOG_HEADING_RE = re.compile(
    r"^#{1,6}\s+Cross-Orchestrator\s+Coordination\s+Log\s*$", re.IGNORECASE
)

# CSV reference note (already migrated marker)
_CSV_REF_RE = re.compile(r"adjacent-repos-(changelog|coordination-log)\.csv")


# ---------------------------------------------------------------------------
# Public: init_csv_stubs
# ---------------------------------------------------------------------------

def init_csv_stubs(refs_dir: Path) -> list[str]:
    """Create CSV log stubs (header row only) if they do not already exist.

    Args:
        refs_dir: Absolute path to the ``references/`` directory inside the
                  agents output directory.

    Returns:
        List of relative file names (within *refs_dir*) that were created.
        Empty if both files already existed.
    """
    created: list[str] = []
    refs_dir.mkdir(parents=True, exist_ok=True)
    for fname, headers in (
        (CHANGELOG_CSV, CHANGELOG_HEADERS),
        (COORD_LOG_CSV, COORD_LOG_HEADERS),
    ):
        target = refs_dir / fname
        if not target.exists():
            _write_csv_header(target, headers)
            created.append(fname)
    return created


# ---------------------------------------------------------------------------
# Public: migrate_inline_logs
# ---------------------------------------------------------------------------

def migrate_inline_logs(
    adjacent_repos_md: Path,
    refs_dir: Path,
) -> MigrateResult:
    """Extract inline markdown log tables from ``adjacent-repos.md`` to CSV.

    Detects two kinds of inline log tables:

    1. ``#### Changelog`` tables (per-repo, 4-column: Date/Action/Files/Summary)
       These are matched wherever they appear outside HTML comment blocks.
       The repo name is inferred from the nearest preceding ``### <Name>`` heading.

    2. ``## Cross-Orchestrator Coordination Log`` table (4-column:
       Date/Adjacent repo/Direction/Outcome)

    Any data rows found are appended to the corresponding CSV file (creating it
    with headers first if absent).  The inline table in the markdown is then
    replaced with a brief CSV reference note.

    Args:
        adjacent_repos_md: Absolute path to the ``adjacent-repos.md`` file.
        refs_dir:          Absolute path to the ``references/`` directory.

    Returns:
        MigrateResult describing what was done.
    """
    result = MigrateResult()

    if not adjacent_repos_md.exists():
        result.skipped = True
        return result

    text = adjacent_repos_md.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Ensure CSV stubs exist before we try to append
    refs_dir.mkdir(parents=True, exist_ok=True)
    _ensure_csv(refs_dir / CHANGELOG_CSV, CHANGELOG_HEADERS)
    _ensure_csv(refs_dir / COORD_LOG_CSV, COORD_LOG_HEADERS)

    new_lines, changelog_moved, coord_moved = _rewrite_inline_tables(
        lines, refs_dir
    )
    result.changelog_rows_moved = changelog_moved
    result.coord_log_rows_moved = coord_moved

    if changelog_moved > 0 or coord_moved > 0:
        try:
            adjacent_repos_md.write_text("".join(new_lines), encoding="utf-8")
            result.adjacent_repos_md_updated = True
        except OSError as exc:
            result.errors.append(f"Failed to rewrite {adjacent_repos_md}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Internal: line-by-line rewriter
# ---------------------------------------------------------------------------

def _rewrite_inline_tables(
    lines: list[str],
    refs_dir: Path,
) -> tuple[list[str], int, int]:
    """Scan lines, extract table data, return rewritten lines + counts."""
    out: list[str] = []
    i = 0
    changelog_moved = 0
    coord_moved = 0

    # Track whether we're inside an HTML comment block
    in_html_comment = False
    # Track the most recent ### repo-name heading (for changelog CSV rows)
    current_repo_name = ""

    while i < len(lines):
        raw = lines[i]
        stripped = raw.rstrip("\n").rstrip("\r")

        # Track HTML comment blocks (<!-- ... -->)
        if "<!--" in stripped:
            in_html_comment = True
        if "-->" in stripped:
            in_html_comment = False
            out.append(raw)
            i += 1
            continue

        if in_html_comment:
            out.append(raw)
            i += 1
            continue

        # Track ### repo name headings for changelog attribution
        repo_heading_match = re.match(r"^###\s+(.+?)\s*$", stripped)
        if repo_heading_match:
            current_repo_name = repo_heading_match.group(1)
            out.append(raw)
            i += 1
            continue

        # Detect #### Changelog heading
        if _CHANGELOG_HEADING_RE.match(stripped):
            # Check if already migrated (CSV reference present in next few lines)
            lookahead = "".join(l.rstrip() for l in lines[i + 1 : i + 5])
            if _CSV_REF_RE.search(lookahead):
                # Already references CSV — pass through unchanged
                out.append(raw)
                i += 1
                continue

            # Look for the table starting on the next non-blank line
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1

            if j < len(lines) and _CHANGELOG_HDR_RE.match(lines[j].rstrip()):
                # Found a changelog table: consume it, extract rows
                rows, j = _consume_table(lines, j)
                data_rows = _filter_data_rows(rows)
                if data_rows:
                    _append_csv_rows(
                        refs_dir / CHANGELOG_CSV,
                        CHANGELOG_HEADERS,
                        _tag_repo_rows(data_rows, current_repo_name),
                    )
                    changelog_moved += len(data_rows)
                # Emit the heading + replacement reference
                out.append(raw)
                out.append(
                    "\n"
                    "> Log entries are stored in "
                    f"[`{CHANGELOG_CSV}`]({CHANGELOG_CSV})."
                    " Append one row per update:"
                    " `date,repo_name,action,files_changed,summary`\n"
                )
                i = j
                continue
            else:
                out.append(raw)
                i += 1
                continue

        # Detect ## Cross-Orchestrator Coordination Log heading
        if _COORD_LOG_HEADING_RE.match(stripped):
            lookahead = "".join(l.rstrip() for l in lines[i + 1 : i + 8])
            if _CSV_REF_RE.search(lookahead):
                out.append(raw)
                i += 1
                continue

            # Scan forward for the table (may be separated by descriptive text)
            j = i + 1
            table_start = None
            # Search up to 6 lines ahead for the table header
            while j < len(lines) and j < i + 7:
                if _COORD_LOG_HDR_RE.match(lines[j].rstrip()):
                    table_start = j
                    break
                j += 1

            if table_start is not None:
                # Emit everything between heading and table as-is
                for k in range(i, table_start):
                    out.append(lines[k])
                rows, end = _consume_table(lines, table_start)
                data_rows = _filter_data_rows(rows)
                if data_rows:
                    _append_csv_rows(
                        refs_dir / COORD_LOG_CSV,
                        COORD_LOG_HEADERS,
                        data_rows,
                    )
                    coord_moved += len(data_rows)
                out.append(
                    f"Full log: [`{COORD_LOG_CSV}`]({COORD_LOG_CSV})."
                    " Append one row per coordination:"
                    " `date,adjacent_repo,direction,outcome`\n"
                )
                i = end
                continue
            else:
                out.append(raw)
                i += 1
                continue

        out.append(raw)
        i += 1

    return out, changelog_moved, coord_moved


# ---------------------------------------------------------------------------
# Table parsing helpers
# ---------------------------------------------------------------------------

def _consume_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """Read a markdown table starting at *start*, returning (rows, next_index).

    Rows are lists of cell strings (stripped, without leading/trailing ``|``).
    Includes the header row and separator row in the returned list.
    """
    rows: list[list[str]] = []
    i = start
    while i < len(lines):
        stripped = lines[i].rstrip("\n").rstrip("\r")
        if not _TABLE_ROW_RE.match(stripped):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        rows.append(cells)
        i += 1
    return rows, i


def _filter_data_rows(rows: list[list[str]]) -> list[list[str]]:
    """Return only data rows (skip header row and separator row)."""
    data: list[list[str]] = []
    for row in rows:
        joined = "|".join(row)
        if _TABLE_SEP_RE.match(f"|{joined}|"):
            continue  # separator
        if all(c.lower() in ("date", "action", "files changed", "summary",
                              "adjacent repo", "direction", "outcome",
                              "repo_name", "files_changed") for c in row):
            continue  # header
        data.append(row)
    return data


def _tag_repo_rows(rows: list[list[str]], repo_name: str) -> list[list[str]]:
    """Insert repo_name as second column in changelog rows (date, repo, action, files, summary)."""
    tagged: list[list[str]] = []
    for row in rows:
        # row from markdown: [date, action, files_changed, summary]
        # CSV target: [date, repo_name, action, files_changed, summary]
        if len(row) >= 4:
            tagged.append([row[0], repo_name, row[1], row[2], row[3]])
        else:
            # Pad with empty strings to avoid index errors
            padded = (row + [""] * 4)[:4]
            tagged.append([padded[0], repo_name, padded[1], padded[2], padded[3]])
    return tagged


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _write_csv_header(path: Path, headers: list[str]) -> None:
    """Write a CSV file containing only the header row."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)


def _ensure_csv(path: Path, headers: list[str]) -> None:
    """Create the CSV file with headers only if it does not already exist."""
    if not path.exists():
        _write_csv_header(path, headers)


def _append_csv_rows(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    """Append data rows to a CSV file, creating it with headers first if absent.

    Rows that already exist in the file (exact match on all columns) are skipped
    to prevent duplicates when a pre-migration backup is restored and migration
    is re-run.
    """
    _ensure_csv(path, headers)

    # Read existing rows for deduplication
    existing: set[tuple[str, ...]] = set()
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip header
        for existing_row in reader:
            existing.add(tuple(existing_row))

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for row in rows:
            # Pad/trim to match expected column count
            padded = (list(row) + [""] * len(headers))[: len(headers)]
            key = tuple(padded)
            if key not in existing:
                writer.writerow(padded)
                existing.add(key)  # guard against duplicates within the same batch
