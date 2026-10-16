"""``--migrate-decision-log``: upgrade a team's decisions-log header to the signing schema.

``decision_log.append_signed_decision_row`` refuses (fail-closed) when the team's
``references/security-decisions.log.csv`` header lacks a signing column, and nothing upgraded a
legacy header, so no signed decision could ever be recorded in a team generated before signing
existed. This standalone mode is that upgrade. ``decision_log.py`` is integrity-pinned and is only
imported here, never changed.

The rules (plan ``decision-log-migration``, Revision 1):

* **Superset, never replacement.** Every existing column stays where it is; the signing columns a
  signed row carries (:data:`~agentteams.cli.decision_log.SIGNED_DECISION_COLUMNS`) that the header
  lacks are appended in that order. ``prev_digest`` is the one exception: hash chaining is opt-in
  on that column (``_assert_decision_chain_intact``), and adding it to a log whose rows were never
  chained would break the chain at row 1. A chain needs its own genesis design.
* **Byte-preserving, rows padded.** The header line is rewritten as text, keeping its own bytes
  and line ending. Each data row keeps its bytes as a prefix: a row narrower than the new header
  gets empty cells (``,``) appended at the end of its last line, so ``governance_logs.append_row``
  accepts the log (1.0 must-fix 399/406). Only rows short by trailing signing columns are padded;
  any other width is refused, so a torn row stays visible. Padding is payload-neutral (empty
  optional fields are not signed), and the migration asserts every row's signed payload, and so the
  hash chain, is unchanged. The result must still classify as a schema ``_read_decision_rows``
  accepts.
* **``text_digest`` is added, never filled.** It binds a signed row's ``conditions``/``evidence``
  (:mod:`agentteams.cli.decision_text`). Filling it on an existing row would change that row's signed
  payload and void its signature, so the column is added empty; ``--sign-decision`` fills it on each
  new row, and ``--verify-integrity`` counts the signed rows that still do not bind their text.
* **No laundering.** A data row carrying more fields than the old header would have those values
  land in an added column (a ``signature`` nobody minted). The migration refuses that.
* **An intended revocation.** A ``signature`` column turns signing on
  (``_decision_signing_active``), so every live unsigned authorizing row stops clearing. Those rows
  are listed by line and action in ``--dry-run`` and in the real run, and a real run needs ``--yes``.
* **Backed up, atomic, idempotent.** The log is backed up with ``backup.backup_output_dir`` first
  and replaced atomically; a header that already has every column, with every row at full width,
  is a no-op (a header-current log with short rows is padded); a malformed or unknown header is
  refused.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.atomicio import write_new_atomic
from agentteams.backup import backup_output_dir
from agentteams.cli.decision_log import (
    _HALT_RETRACTION_VERDICT,
    SIGNED_DECISION_COLUMNS,
    _decision_signature_payload,
    _security_decision_schema_kind,
)
from agentteams.cli.json_mode import is_json_mode, json_stdout

#: The team-relative path of the log this mode migrates.
DECISION_LOG_REL = "references/security-decisions.log.csv"

#: Signed-schema columns the migration never adds (see the module docstring).
EXCLUDED_COLUMNS: frozenset[str] = frozenset({"prev_digest"})

#: Verdicts that clear an action while live (``security_gate._assert_destructive_action_allowed``).
_CLEARING_VERDICTS: frozenset[str] = frozenset({"PASS", "CONDITIONAL PASS"})

#: ``consumed`` values the gate reads as spent (``security_gate._latest_security_decision``).
_CONSUMED_VALUES: frozenset[str] = frozenset({"yes", "true", "1"})


class DecisionLogMigrationError(RuntimeError):
    """The migration is refused; the message names the log and what to fix."""


@dataclass(frozen=True)
class RevokedRow:
    """A live unsigned authorizing row that stops clearing once signing is active.

    Attributes:
        line: The row's first physical line in the file (the header is line 1).
        action: The row's ``action_reviewed`` (or ``decision``) value.
        verdict: The row's verdict, upper-cased.
        reactivates_halt: (property) the row is a ``HALT-RETRACTED``, so its HALT blocks again.
    """

    line: int
    action: str
    verdict: str

    @property
    def reactivates_halt(self) -> bool:
        """True for a ``HALT-RETRACTED`` row: once it stops clearing, the HALT it retracted blocks again."""
        return self.verdict == _HALT_RETRACTION_VERDICT


@dataclass(frozen=True)
class MigrationPlan:
    """What a migration of one log would do; computed without writing anything.

    Attributes:
        log_path: The decisions log.
        schema_kind: The header's schema kind (``legacy`` or ``current``).
        columns: The existing header columns, stripped, in order.
        added_columns: The columns the migration appends, in order (empty when current).
        revoked: Live unsigned authorizing rows the migration stops from clearing.
        source_bytes: The log's bytes when planned (the write refuses if they changed).
        new_bytes: The migrated bytes, or None when the header is already current.
        padded_lines: First file line of each row that gets empty cells appended.
    """

    log_path: Path
    schema_kind: str
    columns: list[str]
    added_columns: list[str]
    revoked: list[RevokedRow] = field(default_factory=list)
    source_bytes: bytes = b""
    new_bytes: bytes | None = None
    padded_lines: list[int] = field(default_factory=list)

    @property
    def current(self) -> bool:
        """True when the header carries every column the migration would add and no row is short."""
        return not self.added_columns and not self.padded_lines

    def as_report(self) -> dict[str, Any]:
        """Return the plan as a JSON-serialisable report.

        Returns:
            ``log``, ``schema_kind``, ``current``, ``header_before``, ``header_after``,
            ``added_columns``, ``padded_lines`` and ``revoked`` (each ``{"line", "action", "verdict",
            "reactivates_halt"}``).
        """
        return {
            "log": str(self.log_path),
            "schema_kind": self.schema_kind,
            "current": self.current,
            "padded_lines": list(self.padded_lines),
            "header_before": list(self.columns),
            "header_after": list(self.columns) + list(self.added_columns),
            "added_columns": list(self.added_columns),
            "revoked": [{"line": r.line, "action": r.action, "verdict": r.verdict,
                         "reactivates_halt": r.reactivates_halt} for r in self.revoked],
        }


def missing_signing_columns(columns: list[str]) -> list[str]:
    """Return the signing columns *columns* lacks, in signed-schema order, never ``prev_digest``.

    Args:
        columns: A decisions-log header's column names.

    Returns:
        Each :data:`~agentteams.cli.decision_log.SIGNED_DECISION_COLUMNS` entry absent from
        *columns* (compared stripped), excluding :data:`EXCLUDED_COLUMNS`.
    """
    present = {c.strip() for c in columns}
    return [c for c in SIGNED_DECISION_COLUMNS if c not in present and c not in EXCLUDED_COLUMNS]


def _split_header(raw: bytes, log_path: Path) -> tuple[bytes, bytes, bytes]:
    """Split *raw* into ``(header line, its line ending, the rest)`` without decoding the rest."""
    newline = raw.find(b"\n")
    if newline == -1:
        header, ending, rest = raw, b"", b""
    else:
        header, ending, rest = raw[:newline], b"\n", raw[newline + 1:]
    if header.endswith(b"\r"):
        header, ending = header[:-1], b"\r" + ending
    if not header.strip():
        raise DecisionLogMigrationError(f"{log_path} has no header line; refusing to migrate it")
    return header, ending, rest


def _parse_header(header: bytes, log_path: Path) -> list[str]:
    """Return the header's stripped columns, refusing anything malformed."""
    if header.startswith(b"\xef\xbb\xbf"):
        raise DecisionLogMigrationError(
            f"{log_path} starts with a byte-order mark, which the gate reads as part of the first "
            "column name; refusing to migrate a header it cannot classify"
        )
    try:
        text = header.decode("utf-8")
        columns = [c.strip() for c in next(csv.reader([text]))]
    except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
        raise DecisionLogMigrationError(f"{log_path} has a malformed header: {exc}") from exc
    if any(not c for c in columns):
        raise DecisionLogMigrationError(f"{log_path} has an empty column name in its header")
    duplicates = sorted({c for c in columns if columns.count(c) > 1})
    if duplicates:
        raise DecisionLogMigrationError(f"{log_path} repeats header column(s) {duplicates}")
    return columns


def _records(rest: bytes, log_path: Path) -> list[tuple[int, int, list[str]]]:
    """Parse the data lines into ``(first file line, last data line, fields)``; blank lines are skipped.

    The last data line is 1-based within *rest* (the line whose end the record ends on).
    """
    try:
        reader = csv.reader(io.StringIO(rest.decode("utf-8"), newline=""))
        records: list[tuple[int, int, list[str]]] = []
        previous = 0
        for fields in reader:
            start = previous + 2  # the header is line 1
            previous = reader.line_num
            if fields:
                records.append((start, previous, fields))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DecisionLogMigrationError(f"{log_path} has malformed data rows: {exc}") from exc
    return records


def _revoked_rows(columns: list[str], records: list[tuple[int, int, list[str]]]) -> list[RevokedRow]:
    """Live unsigned authorizing rows: unconsumed PASS / CONDITIONAL PASS, and every HALT-RETRACTED.

    A HALT-RETRACTED row ignores ``consumed`` (``_assert_no_unretracted_halt``), so once it stops
    authenticating the HALT it retracted is in force again; it is listed whatever its ``consumed``.
    """
    revoked: list[RevokedRow] = []
    for line, _end, fields in records:
        row = dict(zip(columns, (f.strip() for f in fields)))
        verdict = (row.get("verdict") or row.get("status") or "").upper()
        consumed = (row.get("consumed") or "").lower() in _CONSUMED_VALUES
        if (verdict in _CLEARING_VERDICTS and not consumed) or verdict == _HALT_RETRACTION_VERDICT:
            action = row.get("action_reviewed") or row.get("decision") or ""
            revoked.append(RevokedRow(line=line, action=action, verdict=verdict))
    return revoked


def _assert_migrated(new_bytes: bytes, columns: list[str], added: list[str], log_path: Path) -> None:
    """Re-read the migrated bytes as the gate would and check the tool's own invariants."""
    reader = csv.DictReader(io.StringIO(new_bytes.decode("utf-8"), newline=""))
    fieldnames = [c.strip() for c in (reader.fieldnames or [])]
    if fieldnames != columns + added:
        raise DecisionLogMigrationError(
            f"migrated header of {log_path} reads back as {fieldnames}, not {columns + added}"
        )
    try:
        _security_decision_schema_kind(fieldnames)
    except RuntimeError as exc:
        raise DecisionLogMigrationError(f"migrated header of {log_path} no longer classifies: {exc}") from exc
    for index, row in enumerate(reader, start=1):
        filled = [c for c in added if (row.get(c) or "").strip()]
        overflow = [v for v in (row.get(None) or []) if v.strip()]  # DictReader's restkey
        if filled or overflow:
            raise DecisionLogMigrationError(
                f"data row {index} of {log_path} would carry a value in added column(s) "
                f"{filled or 'beyond the header'}; refusing (no laundering)"
            )


def _pad_records(rest: bytes, records: list[tuple[int, int, list[str]]], final: list[str],
                 log_path: Path) -> tuple[bytes, list[int]]:
    """Append empty cells to rows narrower than *final*, at the end of each row's last line.

    Only a row short by trailing signing columns (the run of *final*'s last columns that are
    :data:`~agentteams.cli.decision_log.SIGNED_DECISION_COLUMNS`) is padded; any other width is
    refused, so a torn or truncated row is never silently filled in.

    Args:
        rest: The log's bytes after the header line.
        records: ``(first file line, last data line, fields)`` per row, from :func:`_records`.
        final: The header the rows must match after migration.
        log_path: The log, for messages.

    Returns:
        ``(padded bytes, first file line of each padded row)``.

    Raises:
        DecisionLogMigrationError: A row is narrower than any legacy width (torn or truncated).
    """
    signing = set(SIGNED_DECISION_COLUMNS)
    trailing = 0
    for column in reversed(final):
        if column not in signing:
            break
        trailing += 1
    narrowest = len(final) - trailing
    lines = io.StringIO(rest.decode("utf-8"), newline="").readlines()
    padded: list[int] = []
    for start, end, fields in records:
        if len(fields) >= len(final):
            continue
        if len(fields) < narrowest:
            raise DecisionLogMigrationError(
                f"{log_path} line {start} has {len(fields)} fields; a row of this log has at least "
                f"{narrowest}. Refusing to pad a torn or truncated row: fix it first"
            )
        line = lines[end - 1]
        body = line.rstrip("\r\n")
        lines[end - 1] = body + "," * (len(final) - len(fields)) + line[len(body):]
        padded.append(start)
    return "".join(lines).encode("utf-8"), padded


def _assert_payloads_unchanged(old_bytes: bytes, new_bytes: bytes, log_path: Path) -> None:
    """Refuse unless every row's signed payload (and so the hash chain) reads back identically.

    Args:
        old_bytes: The log before migration.
        new_bytes: The migrated log.
        log_path: The log, for messages.

    Raises:
        DecisionLogMigrationError: A row's signed payload would change.
    """
    def payloads(raw: bytes) -> list[str]:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        return [_decision_signature_payload({k: v or "" for k, v in row.items() if k}) for row in reader]

    if payloads(old_bytes) != payloads(new_bytes):
        raise DecisionLogMigrationError(f"migrating {log_path} would change a row's signed payload; refusing")


def plan_migration(log_path: Path) -> MigrationPlan:
    """Work out the migration of one decisions log, writing nothing.

    Args:
        log_path: The team's ``references/security-decisions.log.csv``.

    Returns:
        The plan. ``plan.current`` is True (and ``new_bytes`` None) when there is nothing to add.

    Raises:
        DecisionLogMigrationError: The log is missing, a symlink or not a regular file; its header
            is malformed or of an unknown schema; its data rows are malformed; or a data row has
            a value past the old header that would land in an added column.
    """
    if log_path.is_symlink() or not log_path.is_file():
        raise DecisionLogMigrationError(f"no regular decisions log at {log_path}")
    try:
        raw = log_path.read_bytes()
    except OSError as exc:
        raise DecisionLogMigrationError(f"unable to read {log_path}: {exc}") from exc
    header, ending, rest = _split_header(raw, log_path)
    columns = _parse_header(header, log_path)
    try:
        kind = _security_decision_schema_kind(columns)
    except RuntimeError as exc:
        raise DecisionLogMigrationError(f"{log_path}: unknown header schema; {exc}") from exc
    added = missing_signing_columns(columns)
    records = _records(rest, log_path)
    if not added:
        padded_rest, padded = _pad_records(rest, records, columns, log_path)
        if not padded:
            return MigrationPlan(log_path, kind, columns, [], [], raw, None)
        new_bytes = header + ending + padded_rest
        _assert_payloads_unchanged(raw, new_bytes, log_path)
        return MigrationPlan(log_path, kind, columns, [], [], raw, new_bytes, padded)
    for line, _end, fields in records:
        overflow = [f for f in fields[len(columns):] if f.strip()]
        if overflow:
            raise DecisionLogMigrationError(
                f"{log_path} line {line} has {len(fields)} fields for a {len(columns)}-column header; "
                f"its extra value(s) would land in added column(s) {added[:len(fields) - len(columns)]}. "
                "Refusing (no laundering): fix the row first"
            )
    revoked = _revoked_rows(columns, records) if "signature" in added else []
    padded_rest, padded = _pad_records(rest, records, columns + added, log_path)
    new_bytes = header + ("," + ",".join(added)).encode("utf-8") + ending + padded_rest
    _assert_migrated(new_bytes, columns, added, log_path)
    _assert_payloads_unchanged(raw, new_bytes, log_path)
    return MigrationPlan(log_path, kind, columns, added, revoked, raw, new_bytes, padded)


def apply_migration(plan: MigrationPlan, team_dir: Path, *, framework: str = "") -> Path:
    """Back the log up, then replace it atomically with the migrated bytes.

    Args:
        plan: A plan from :func:`plan_migration` that is not current.
        team_dir: The team's agents directory (the backup root).
        framework: Recorded in the backup manifest.

    Returns:
        The backup directory.

    Raises:
        DecisionLogMigrationError: The plan is current, the backup was not taken, or the log
            changed since it was planned.
        OSError: The atomic replace failed (the log is then unchanged).
    """
    if plan.new_bytes is None:
        raise DecisionLogMigrationError(f"{plan.log_path} is already current; nothing to apply")
    backup = backup_output_dir(team_dir, files_to_backup=[DECISION_LOG_REL],
                               reason="migrate-decision-log", framework=framework)
    if backup.backup_path is None:
        raise DecisionLogMigrationError(f"the backup of {plan.log_path} was not taken; refusing to write")
    if plan.log_path.is_symlink() or plan.log_path.read_bytes() != plan.source_bytes:
        raise DecisionLogMigrationError(f"{plan.log_path} changed after it was planned; re-run the migration")
    mode = stat.S_IMODE(plan.log_path.stat().st_mode)
    write_new_atomic(plan.log_path.parent, plan.log_path.name, plan.new_bytes, mode=mode)
    return backup.backup_path


def resolve_team_dir(args: argparse.Namespace) -> Path:
    """Resolve the team agents directory the way the generate pipeline does.

    ``--self`` has already set ``--output`` (and ``--description``) in ``cli/app.py``.

    Args:
        args: Parsed CLI namespace (``framework``, ``output``, ``description``, ``project``).

    Returns:
        The agents directory.

    Raises:
        DecisionLogMigrationError: ``--description`` names a brief that cannot be loaded.
    """
    from agentteams import ingest
    from agentteams.cli.output_target import resolve_output_dir
    from agentteams.frameworks.registry import FRAMEWORKS

    adapter = FRAMEWORKS[args.framework]()
    description: dict[str, Any] = {}
    if not args.output and getattr(args, "description", None):
        try:
            description = ingest.load(Path(args.description).resolve(), scan_project=False)
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise DecisionLogMigrationError(f"--description: {exc}") from exc
    if not args.output and getattr(args, "project", None):
        description["existing_project_path"] = str(Path(args.project).resolve())
    return resolve_output_dir(args, adapter, description)[1]


def _print_plan(plan: MigrationPlan, *, dry_run: bool) -> None:
    """Print the header change and the revocation list."""
    tag = "[DRY RUN] " if dry_run else ""
    print(f"  {tag}migrate-decision-log → {plan.log_path} ({plan.schema_kind} header)")
    if plan.added_columns:
        print(f"    header: + {', '.join(plan.added_columns)}  (prev_digest is never added)")
    if plan.padded_lines:
        print(f"    pads {len(plan.padded_lines)} short row(s) with empty cells (signed payloads unchanged)")
    if not plan.revoked:
        print("    revokes: none (no live unsigned authorizing row)")
        return
    print(f"    revokes {len(plan.revoked)} live unsigned authorizing row(s); once the signature column "
          "exists they no longer clear:")
    for row in plan.revoked:
        print(f"      line {row.line}: {row.verdict} {row.action}")
        if row.reactivates_halt:
            print(f"        the HALT it retracted ({row.action}) blocks again after migration; re-issue the "
                  "retraction as a signed row")


def run_migrate_decision_log(args: argparse.Namespace) -> int:
    """Run ``--migrate-decision-log``: plan, report, and (with ``--yes``) migrate.

    Args:
        args: Parsed CLI namespace (``framework``, ``output``, ``description``, ``project``,
            ``dry_run``, ``yes``, ``json``).

    Returns:
        0 on success, a no-op or a dry run; 1 on a refusal (reported on stderr).

    Raises:
        Nothing; refusals and write failures are reported and returned as exit code 1.
    """
    try:
        team_dir = resolve_team_dir(args)
        plan = plan_migration(team_dir / DECISION_LOG_REL)
    except DecisionLogMigrationError as exc:
        print(f"[MIGRATE-DECISION-LOG] refused: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        from agentteams.cli.action_ids import describe_newly_blocking_halts

        for line in describe_newly_blocking_halts(team_dir):  # review before upgrading to 1.0
            print(f"  {line}", file=sys.stderr if is_json_mode(args) else sys.stdout)
        if is_json_mode(args):
            report = {"mode": "migrate-decision-log", "dry_run": True, **plan.as_report()}
            print(json.dumps(report, indent=2), file=json_stdout() or sys.stdout)
        elif plan.current:
            print(f"  [DRY RUN] migrate-decision-log: {plan.log_path} is already current")
        else:
            _print_plan(plan, dry_run=True)
        return 0
    if plan.current:
        print(f"  migrate-decision-log: {plan.log_path} is already current")
        return 0
    _print_plan(plan, dry_run=False)
    if not args.yes:
        print("[MIGRATE-DECISION-LOG] refused: the migration rewrites the decisions log (and may activate "
              "signing and revoke the rows listed above); review a --dry-run, then re-run with --yes",
              file=sys.stderr)
        return 1
    try:
        backup = apply_migration(plan, team_dir, framework=args.framework)
    except (DecisionLogMigrationError, OSError) as exc:
        print(f"[MIGRATE-DECISION-LOG] failed: {exc}", file=sys.stderr)
        return 1
    print(f"  ✓  migrate-decision-log: {len(plan.added_columns)} column(s) added and "
          f"{len(plan.padded_lines)} row(s) padded in {plan.log_path} (backup: {backup})")
    return 0
