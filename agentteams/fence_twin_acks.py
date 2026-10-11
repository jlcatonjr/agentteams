"""fence_twin_acks.py — an operator's reviewed acknowledgement of a kept pre-fencing twin.

``agentteams.fence_twins`` keeps every unfenced twin it cannot prove is template text, and ``fleet``
reports each kept twin as REVIEW. Some twins are kept on purpose: researchteam's hand-written
CH-08..10 rows are real project content, and the operator may decide to leave them where they are.
Without a record of that decision, every fleet run raises the same REVIEW again.

**The ledger.** ``<output dir>/references/fence-twin-acks.csv``, committed with the team, with the
header ``file,heading,digest,reason``. One row acknowledges one twin:

* ``file`` — the path relative to the output dir, as merge notices print it;
* ``heading`` — the twin's heading line (``## Accuracy Rules``);
* ``digest`` — :func:`agentteams.fence_twins.ack_digest`: the first 16 hex of the sha256 of the file,
  the heading and the twin's whitespace-collapsed text. The kept-twin notice prints this row for you;
* ``reason`` — why the twin stays, at least :data:`MIN_REASON_CHARS` characters (the same bar as
  every other reasoned ledger, ``agentteams.redteam.registry.MIN_REASON_CHARS``).

**What it does.** Only the REVIEW changes: the merge prints a ``twin kept (acknowledged)`` notice in
place of ``twin kept (not provably template)``, and ``fleet`` does not count it. It never drops,
moves or edits text. A twin that is provably template text is still dropped whether or not a row
names it. When the twin's text, heading or file changes, the digest no longer matches; the row stops
applying and REVIEW returns. A row that fails validation (bad header, bad digest, short reason) is
skipped with a warning, so the twin stays under REVIEW. A row naming a file this run does not render
is reported as unused.

**Trust model (Rule 12: what this exclusion newly permits).** The digest is unkeyed and the ledger is
team content (C-4), so anyone who can write the team's files can add a row and silence one twin's
REVIEW. That is no wider than what such a writer already has: unfenced text that is not a twin is
never reviewed at all, and deleting the twin also clears its REVIEW. What the ledger adds is a
record — a committed row with a reason, in the diff that adds it — and ``fleet`` still lists every
acknowledged twin (``twins_acknowledged``) with that reason, so nothing is silenced invisibly.
Because no row is tied to the operator, ``fleet`` also reports an acknowledgement as REVIEW until an
earlier applied fleet run has shown it (``agentteams._fleet_twin_acks``): a row added or changed by
anyone is flagged for operator review once, and cannot clear REVIEW in the run that first applies it.

Stdlib only.
"""

from __future__ import annotations

import csv
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: The ledger, relative to the output dir (beside ``references/build-log.json``).
LEDGER_REL = "references/fence-twin-acks.csv"
#: The ledger header, in order.
COLUMNS: tuple[str, ...] = ("file", "heading", "digest", "reason")
#: Minimum reason length. Equal to ``agentteams.redteam.registry.MIN_REASON_CHARS`` (a test checks).
MIN_REASON_CHARS = 40
#: Hex length of an acknowledgement digest.
DIGEST_HEX = 16

_DIGEST_RE = re.compile(rf"^[0-9a-f]{{{DIGEST_HEX}}}$")
_HEADING_RE = re.compile(r"^#{2,6}[ \t]+\S")


@dataclass(frozen=True)
class TwinAck:
    """One validated ledger row.

    Attributes:
        file: Output-relative path of the file holding the twin.
        heading: The twin's heading line, stripped.
        digest: The reviewed twin's :func:`agentteams.fence_twins.ack_digest`.
        reason: Why the operator keeps the twin (at least :data:`MIN_REASON_CHARS` characters).
    """

    file: str
    heading: str
    digest: str
    reason: str


def _row_problem(row: dict[str, str]) -> str:
    """Why *row* is not a valid acknowledgement, or "" when it is."""
    file, heading = row.get("file", "").strip(), row.get("heading", "").strip()
    digest, reason = row.get("digest", "").strip(), " ".join(row.get("reason", "").split())
    if not file or file.startswith("/") or "\\" in file:
        return "file must be the output-relative path the merge notice prints"
    if not _HEADING_RE.match(heading):
        return "heading must be the twin's heading line (## ... to ###### ...)"
    if not _DIGEST_RE.match(digest):
        return f"digest must be {DIGEST_HEX} lowercase hex characters, as the kept-twin notice prints it"
    if len(reason) < MIN_REASON_CHARS:
        return f"reason is {len(reason)} characters; at least {MIN_REASON_CHARS} are required"
    return ""


def parse(text: str, *, source: str = LEDGER_REL) -> dict[str, tuple[TwinAck, ...]]:
    """Validate ledger text and group its rows by file.

    Args:
        text: The ledger's CSV text.
        source: Name used in warnings.

    Returns:
        ``{file: (TwinAck, ...)}``. A ledger with the wrong header yields nothing; an invalid row is
        skipped. Each skip prints a warning to stderr and leaves the twin under REVIEW.

    Raises:
        Nothing.
    """
    try:
        reader = csv.DictReader(io.StringIO(text))
        header = tuple(h.strip() for h in reader.fieldnames or ())
        rows = list(reader)
    except csv.Error as exc:
        print(f"  WARNING: {source}: not readable as CSV ({exc}); no twin acknowledgement applies",
              file=sys.stderr)
        return {}
    if header != COLUMNS:
        print(f"  WARNING: {source}: header must be {','.join(COLUMNS)}; no twin acknowledgement applies",
              file=sys.stderr)
        return {}
    out: dict[str, list[TwinAck]] = {}
    for line, row in enumerate(rows, start=2):
        row = {k.strip(): (v or "") for k, v in row.items() if k is not None}
        problem = _row_problem(row)
        if problem:
            print(f"  WARNING: {source} line {line}: {problem}; skipped (that twin stays under REVIEW)",
                  file=sys.stderr)
            continue
        ack = TwinAck(row["file"].strip().removeprefix("./"), row["heading"].strip(), row["digest"].strip(),
                      " ".join(row["reason"].split()))
        out.setdefault(ack.file, []).append(ack)
    return {file: tuple(acks) for file, acks in out.items()}


def load(output_dir: Path | None) -> dict[str, tuple[TwinAck, ...]]:
    """Read ``<output_dir>/references/fence-twin-acks.csv``.

    Args:
        output_dir: The run's output dir, or None.

    Returns:
        ``{file: (TwinAck, ...)}``; empty when there is no output dir or no ledger.

    Raises:
        Nothing: an unreadable ledger prints a warning and yields nothing.
    """
    if output_dir is None:
        return {}
    path = Path(output_dir) / LEDGER_REL
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  WARNING: {LEDGER_REL}: unreadable ({exc}); no twin acknowledgement applies", file=sys.stderr)
        return {}
    return parse(text)


def ledger_row(file: str, heading: str, digest: str) -> str:
    """The CSV row to record for a twin, with a placeholder where the reason goes.

    Args:
        file: Output-relative path.
        heading: The twin's heading line.
        digest: Its :func:`agentteams.fence_twins.ack_digest`.

    Returns:
        One CSV line (no newline), quoted as the ``csv`` module requires.

    Raises:
        Nothing.
    """
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(
        [file, heading, digest, f"<why this twin stays: at least {MIN_REASON_CHARS} characters>"])
    return buf.getvalue()


__all__ = [
    "COLUMNS",
    "DIGEST_HEX",
    "LEDGER_REL",
    "MIN_REASON_CHARS",
    "TwinAck",
    "ledger_row",
    "load",
    "parse",
]
