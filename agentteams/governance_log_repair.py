"""governance_log_repair.py — an approved, backed-up repair of malformed governance-log rows.

The governance logs are append-only, so a malformed row (wrong field count) cannot be fixed by appending, and
since item 06 the security gate refuses every decision in a malformed decisions log. This module repairs such
rows, under three constraints set by the operator (2026-11-11):

* **It never guesses.** The only shape it proposes a fix for is the one observed in the field: an unquoted comma
  in free text, which splits one cell into two, the second fragment starting with a space. A record with *k*
  extra fields where exactly *k* fields after the first start with a space has each such fragment re-joined to
  the cell before it with ``,``. Any other malformed record gets no proposal and is listed for a hand fix.
* **Nothing is written without approval.** :func:`plan_repair` (CLI: ``repair LOG``) only previews. It shows each
  record, the proposed row cell by cell under its header, and a digest of the whole proposal. :func:`apply_repair`
  (CLI: ``repair LOG --apply DIGEST``) writes only when the recomputed digest equals the one approved, so the
  approval binds to exactly the preview that was read, and to the file as it was then.
* **Everything else stays byte-identical.** A backup ``LOG.bak-<UTC timestamp>`` is written first. Only the
  malformed records' line spans are replaced, rendered with the log's own line ending. The result is re-checked
  with :func:`agentteams.governance_logs.check_log`, and the backup is restored if it is still malformed.

Refused outright: a log with a ``prev_digest`` column (editing a row breaks the hash chain, and with it every
gate decision), and a malformed record that carries a ``signature`` (editing it voids the signature).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: unlocked
    fcntl = None  # type: ignore[assignment]


@dataclass
class RepairItem:
    """One malformed record and what the repair would do with it.

    Attributes:
        record: 1-based record number after the header (the numbering ``check_log`` reports).
        first_line: 1-based first physical line of the record in the file.
        last_line: 1-based last physical line of the record.
        original: The record's cells as read.
        fixed: The proposed cells (header width), or ``None`` when no fix is proposed.
        reason: Why no fix is proposed, or a short description of the fix.
    """

    record: int
    first_line: int
    last_line: int
    original: list[str]
    fixed: list[str] | None
    reason: str


@dataclass
class RepairPlan:
    """The preview :func:`plan_repair` returns.

    Attributes:
        path: The log.
        header: The header row.
        items: One entry per malformed record.
        refusal: A reason the whole log cannot be repaired here, or ``""``.
        digest: sha256 over the file bytes and the proposal; ``--apply`` must quote it.
    """

    path: Path
    header: list[str]
    items: list[RepairItem] = field(default_factory=list)
    refusal: str = ""
    digest: str = ""

    @property
    def fixable(self) -> bool:
        """True when there is something to repair and every malformed record has a proposal."""
        return not self.refusal and bool(self.items) and all(i.fixed is not None for i in self.items)


def _records_with_spans(text: str) -> list[tuple[list[str], int, int]]:
    """Parse ``text`` into ``(cells, first_line, last_line)``, physical lines 1-based."""
    out: list[tuple[list[str], int, int]] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    prev = 0
    for cells in reader:
        out.append((cells, prev + 1, reader.line_num))
        prev = reader.line_num
    return out


def _propose(cells: list[str], width: int) -> tuple[list[str] | None, str]:
    extra = len(cells) - width
    if extra <= 0:
        return None, f"has {len(cells)} fields, fewer than the header's {width}: no safe fix; repair by hand"
    splits = [j for j in range(1, len(cells)) if cells[j].startswith(" ")]
    if len(splits) != extra:
        return None, (f"has {extra} extra field(s) but {len(splits)} field(s) start with a space, so the split "
                      f"points are ambiguous: repair by hand")
    fixed = list(cells)
    for j in reversed(splits):
        fixed[j - 1] = fixed[j - 1] + "," + fixed[j]
        del fixed[j]
    return fixed, f"re-joined {extra} fragment(s) split by an unquoted comma"


def plan_repair(path: Path) -> RepairPlan:
    """Preview the repair of one governance log. Writes nothing.

    Args:
        path: The log file.

    Returns:
        RepairPlan: the malformed records, a proposed fix for each where one is safe, any whole-log refusal,
        and the digest ``apply_repair`` requires.

    Raises:
        OSError: The file cannot be read.
        UnicodeDecodeError: The file is not UTF-8.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    records = [r for r in _records_with_spans(text) if r[0]]
    plan = RepairPlan(path=path, header=records[0][0] if records else [])
    if not records:
        plan.refusal = "the log has no header row"
    width = len(plan.header)
    sig_col = plan.header.index("signature") if "signature" in plan.header else None
    for number, (cells, first, last) in enumerate(records[1:], 1):
        if len(cells) == width:
            continue
        fixed, reason = _propose(cells, width)
        if fixed is not None and sig_col is not None and fixed[sig_col].strip():
            fixed, reason = None, "carries a signature; editing it would void the signature: repair by hand"
        plan.items.append(RepairItem(number, first, last, cells, fixed, reason))
    if "prev_digest" in plan.header and plan.items:
        plan.refusal = ("the log is hash-chained (prev_digest); editing a row breaks the chain and every gate "
                        "decision with it. Repair it with a reviewed migration, not this command")
    proposal = [[i.record, i.original, i.fixed] for i in plan.items]
    plan.digest = hashlib.sha256(raw + json.dumps(proposal, ensure_ascii=False).encode("utf-8")).hexdigest()
    return plan


def render_plan(plan: RepairPlan) -> str:
    """Render a preview for the operator.

    Args:
        plan: From :func:`plan_repair`.

    Returns:
        str: A human-readable preview, ending with the ``--apply`` command when the plan is fixable.

    Raises:
        None.
    """
    lines = [f"{plan.path}: {len(plan.items)} malformed record(s); header has {len(plan.header)} fields"]
    for item in plan.items:
        span = f"line {item.first_line}" + (f"-{item.last_line}" if item.last_line != item.first_line else "")
        lines.append(f"\nrecord {item.record} ({span}): {len(item.original)} fields; {item.reason}")
        if item.fixed is None:
            for j, cell in enumerate(item.original):
                lines.append(f"    [{j}] {cell!r}")
            continue
        for name, cell in zip(plan.header, item.fixed):
            lines.append(f"    {name:>20}: {cell!r}")
    if plan.refusal:
        lines.append(f"\nREFUSED: {plan.refusal}")
    elif not plan.items:
        lines.append("nothing to repair")
    elif plan.fixable:
        lines.append(f"\nNothing written. To apply exactly this, after review:\n"
                     f"    python -m agentteams.governance_logs repair {plan.path} --apply {plan.digest}")
    else:
        lines.append("\nNot every malformed record has a safe fix; repair those by hand, then preview again.")
    return "\n".join(lines)


def apply_repair(path: Path, digest: str) -> Path:
    """Apply the previewed repair, bound to ``digest``; return the backup path.

    Args:
        path: The log file.
        digest: The digest the preview printed; the repair is refused unless the recomputed one is equal.

    Returns:
        Path: The backup written before the change.

    Raises:
        ValueError: The plan is refused or not fully fixable, the digest does not match (the file or the
            proposal changed since the preview), or the repaired file still fails ``check_log`` (the backup
            is restored first).
        OSError: The file or the backup cannot be written.
    """
    from agentteams.governance_logs import check_log

    fd = os.open(path, os.O_RDWR)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        plan = plan_repair(path)
        if plan.refusal:
            raise ValueError(f"refused: {plan.refusal}")
        if not plan.fixable:
            raise ValueError("not every malformed record has a safe fix; nothing written")
        if plan.digest != digest:
            raise ValueError("the digest does not match the current preview (the file or the proposal changed); "
                             "preview again and approve the new digest. Nothing written")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        for item in sorted(plan.items, key=lambda i: i.first_line, reverse=True):
            old_last = lines[item.last_line - 1]
            ending = "\r\n" if old_last.endswith("\r\n") else ("\n" if old_last.endswith("\n") else "")
            buf = io.StringIO()
            csv.writer(buf, lineterminator=ending or "\n").writerow(item.fixed)
            rendered = buf.getvalue() if ending else buf.getvalue().rstrip("\n")
            lines[item.first_line - 1:item.last_line] = [rendered]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        with backup.open("xb") as fh:
            fh.write(raw)
        tmp = path.with_name(f".{path.name}.repair-tmp")
        tmp.write_bytes("".join(lines).encode("utf-8"))
        os.replace(tmp, path)
        problems = check_log(path)
        if problems:
            path.write_bytes(raw)
            raise ValueError("the repaired log still fails check (backup restored): " + "; ".join(problems[:3]))
        return backup
    finally:
        os.close(fd)


__all__ = ["RepairItem", "RepairPlan", "apply_repair", "plan_repair", "render_plan"]
