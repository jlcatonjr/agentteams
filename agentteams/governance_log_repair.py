"""governance_log_repair.py — an operator-approved, backed-up repair of malformed governance-log rows.

The governance logs are append-only, so a malformed row (wrong field count) cannot be fixed by appending, and
since item 06 the security gate refuses every decision in a malformed decisions log. This module repairs such
rows under the operator's constraints (2026-11-11) and @security's conditions (HALT, then re-review):

* **It never guesses, and it never touches a decision.** The only shape it proposes a fix for is the one seen in
  the field: an unquoted comma in prose, which splits one cell into two, the second fragment starting with a
  space. A record with *k* extra fields, where exactly *k* fields after the first start with a space, has each
  fragment re-joined to the cell before it with ``,``, but only when
  - every re-join lands in a free-text column (:data:`FREE_TEXT_COLUMNS`);
  - every cell before the first re-join, including the action and verdict columns, keeps its position and
    content unchanged;
  - no fragment is a bare verdict word.
  Anything else gets no proposal and is listed for a hand fix, as is any problem ``check_log`` reports that is
  not a field-count mismatch (an open quote, swallowed rows).
* **The operator approves it.** :func:`plan_repair` (CLI ``repair LOG``) only previews: each record before and
  after, cell by cell under its header, and a digest of the whole proposal. :func:`apply_repair` (CLI
  ``repair LOG --apply DIGEST``) writes only when the recomputed digest equals the approved one, and by default
  only after the operator retypes the digest's first 8 characters on an interactive terminal. That prompt is a
  speed-bump against an unattended apply, not a security boundary (an agent can allocate a pseudo-terminal).
  What makes any repair harmless is the column rules above: the decision cells (``decision``/``action_reviewed``, ``status``/``verdict``, ``scope``) stay byte-identical, and ``conditions_verified`` keeps its original text as a prefix (a re-join only appends ``,<fragment>`` to it), so its leading ``verified``/``pending`` token never changes.
* **Everything else stays byte-identical, and no concurrent append is lost.**
  - The file is locked and read once, and the plan is computed from exactly those bytes.
  - A backup ``LOG.bak-<UTC timestamp>`` is written atomically (exclusive create, fsync).
  - The log is rewritten in place through the locked descriptor (truncate, write, fsync). An ``append_row``
    waiting on the lock therefore appends to the repaired file, never to a replaced one.
  - The result is re-checked with :func:`agentteams.governance_logs.check_log`, and on failure the original
    bytes are restored in place.
* **Refused outright:** a log with a ``prev_digest`` column (an edit breaks the hash chain), and any log with a
  signing or digest column (``signature``, ``sig_scheme``, ``key_id``, ``derives_from``, ``text_digest``),
  because an edit can void a signature or a bound digest. Header names are compared after stripping whitespace, as the gate does.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import sys

from agentteams.governance_logs import LOG_NAMES, check_log, fcntl

#: Columns whose cells are prose, so a fragment of one may be re-joined into it. Never a verdict, action, date,
#: owner, id or signing column.
FREE_TEXT_COLUMNS: frozenset[str] = frozenset({
    "conditions", "conditions_verified", "evidence", "notes", "description", "summary", "resolution",
    "proposed_touch_points", "resolved_evidence", "rationale_s", "rationale_b", "rationale_c", "rationale_p",
})
#: Columns whose value decides a gate outcome; a repair must leave their position and content alone.
DECISION_COLUMNS: frozenset[str] = frozenset({"decision", "action_reviewed", "status", "verdict", "scope"})
#: Columns a gate reads by exact value: a re-join may land in one but must never move it.
POSITIONED_COLUMNS: frozenset[str] = frozenset({"conditions_verified"})
#: A log with any of these is signed or chained; a repair would void or break it.
SIGNING_COLUMNS: frozenset[str] = frozenset({
    "signature", "sig_scheme", "key_id", "derives_from", "prev_digest", "text_digest",
})
_VERDICT_WORDS = re.compile(r"^\s*(HALT|PASS|CONDITIONAL PASS|HALT-RETRACTED|REQUESTED|REVOKED)\s*$", re.IGNORECASE)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class RepairItem:
    """One malformed record and what the repair would do with it.

    Attributes:
        record: 1-based record number after the header (``check_log``'s numbering).
        first_line: 1-based first physical line of the record.
        last_line: 1-based last physical line of the record.
        original: The record's cells as read.
        fixed: The proposed cells (header width), or ``None`` when no fix is proposed.
        reason: Why no fix is proposed, or what the fix does.
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
        other_problems: ``check_log`` problems this command does not fix (open quote, swallowed rows).
        refusal: A reason the whole log cannot be repaired here, or ``""``.
        digest: sha256 over the file bytes and the proposal; ``--apply`` must quote it.
    """

    path: Path
    header: list[str]
    items: list[RepairItem] = field(default_factory=list)
    other_problems: list[str] = field(default_factory=list)
    refusal: str = ""
    digest: str = ""

    @property
    def fixable(self) -> bool:
        """True when there is something to repair and all of it has a safe proposal."""
        return (not self.refusal and not self.other_problems and bool(self.items)
                and all(i.fixed is not None for i in self.items))

    @property
    def clean(self) -> bool:
        """True when ``check_log`` finds nothing wrong."""
        return not self.refusal and not self.items and not self.other_problems


def _lines(text: str) -> list[str]:
    """Physical lines exactly as the csv module counts them (``\\r``, ``\\n``, ``\\r\\n`` only)."""
    return list(io.StringIO(text, newline=""))


def _records_with_spans(text: str) -> list[tuple[list[str], int, int]]:
    """Parse ``text`` into ``(cells, first_line, last_line)``, physical lines 1-based."""
    out: list[tuple[list[str], int, int]] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    prev = 0
    for cells in reader:
        out.append((cells, prev + 1, reader.line_num))
        prev = reader.line_num
    return out


def _propose(cells: list[str], header: list[str]) -> tuple[list[str] | None, str]:
    width = len(header)
    extra = len(cells) - width
    if extra <= 0:
        return None, f"has {len(cells)} fields, fewer than the header's {width}: no safe fix; repair by hand"
    splits = [j for j in range(1, len(cells)) if cells[j].startswith(" ")]
    if len(splits) != extra:
        return None, (f"has {extra} extra field(s) but {len(splits)} field(s) start with a space, so the split "
                      f"points are ambiguous: repair by hand")
    if any(_VERDICT_WORDS.match(cells[j]) for j in splits):
        return None, "a fragment to re-join is a verdict word: repair by hand"
    fixed = list(cells)
    targets: list[int] = []
    for j in reversed(splits):
        fixed[j - 1] = fixed[j - 1] + "," + fixed[j]
        del fixed[j]
    # Map each re-join to the column it lands in (positions shift left by the joins before it).
    for n, j in enumerate(splits):
        targets.append(j - 1 - n)
    landed = [header[t] for t in targets]
    if any(col not in FREE_TEXT_COLUMNS for col in landed):
        return None, f"a fragment would be re-joined into {sorted(set(landed) - FREE_TEXT_COLUMNS)}: repair by hand"
    first = min(targets)
    if fixed[:first] != cells[:first]:
        return None, "the cells before the first re-join would change: repair by hand"
    moved = [c for c in DECISION_COLUMNS & set(header) if header.index(c) >= first]
    # conditions_verified decides whether a CONDITIONAL PASS clears (the gate compares it to "verified"): a
    # re-join may land IN it (the added comma means it can never read "verified"), but it must not move.
    moved += [c for c in POSITIONED_COLUMNS & set(header) if header.index(c) > first]
    if moved:
        return None, f"column(s) {sorted(moved)} sit after a re-join and would move: repair by hand"
    return fixed, f"re-joined {extra} fragment(s) split by an unquoted comma into {sorted(set(landed))}"


def _plan_from_bytes(path: Path, raw: bytes) -> RepairPlan:
    text = raw.decode("utf-8")
    all_records = _records_with_spans(text)
    records = [r for r in all_records if r[0]]
    header = [c.strip() for c in records[0][0]] if records else []
    plan = RepairPlan(path=path, header=header)
    if not records:
        plan.refusal = "the log has no header row"
    signed = sorted(SIGNING_COLUMNS & set(header))
    width = len(header)
    # Numbered as check_log numbers them: every record after the header, blank ones included.
    header_index = all_records.index(records[0]) if records else 0
    for number, (cells, first, last) in enumerate(all_records[header_index + 1:], 1):
        if not cells or len(cells) == width:
            continue
        fixed, reason = _propose(cells, header)
        plan.items.append(RepairItem(number, first, last, cells, fixed, reason))
    if signed and plan.items:
        plan.refusal = (f"the log is signed or chained ({', '.join(signed)}); a repair would void a signature or "
                        "break the chain, and with it every gate decision. Repair it with a reviewed migration")
    width_msgs = re.compile(r"record \d+ has \d+ fields, header has \d+")
    plan.other_problems = [p for p in check_log(path) if not width_msgs.search(p)] if path.exists() else []
    proposal = [[i.record, i.original, i.fixed] for i in plan.items]
    plan.digest = hashlib.sha256(raw + json.dumps(proposal, ensure_ascii=False).encode("utf-8")).hexdigest()
    return plan


def plan_repair(path: Path) -> RepairPlan:
    """Preview the repair of one governance log. Writes nothing.

    Args:
        path: The log file.

    Returns:
        RepairPlan: The malformed records with a proposal where one is safe, other ``check_log`` problems, any
        whole-log refusal, and the digest :func:`apply_repair` requires.

    Raises:
        TypeError: ``path`` is not a ``Path``.
        OSError: The file cannot be read.
        UnicodeDecodeError: The file is not UTF-8.
    """
    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path")
    return _plan_from_bytes(path, path.read_bytes())


def render_plan(plan: RepairPlan) -> str:
    """Render a preview for the operator.

    Args:
        plan: From :func:`plan_repair`.

    Returns:
        str: A human-readable preview: each record before and after, cell by cell, ending with the ``--apply``
        command when the plan is fixable.

    Raises:
        None.
    """
    lines = [f"{plan.path}: {len(plan.items)} malformed record(s); header has {len(plan.header)} fields"]
    for item in plan.items:
        span = f"line {item.first_line}" + (f"-{item.last_line}" if item.last_line != item.first_line else "")
        lines.append(f"\nrecord {item.record} ({span}): {len(item.original)} fields; {item.reason}")
        lines.append("  as read (by position):")
        for j, cell in enumerate(item.original):
            name = plan.header[j] if j < len(plan.header) else "(extra)"
            lines.append(f"    {name:>20}: {cell!r}")
        if item.fixed is not None:
            lines.append("  proposed:")
            for name, cell in zip(plan.header, item.fixed):
                lines.append(f"    {name:>20}: {cell!r}")
    for problem in plan.other_problems:
        lines.append(f"\nnot repaired here (fix by hand): {problem}")
    if plan.refusal:
        lines.append(f"\nREFUSED: {plan.refusal}")
    elif plan.clean:
        lines.append("nothing to repair (check_log is clean)")
    elif plan.fixable:
        lines.append(f"\nNothing written. The operator applies exactly this, from an interactive terminal:\n"
                     f"    python -m agentteams.governance_logs repair {plan.path} --apply {plan.digest}")
    else:
        lines.append("\nNot everything here has a safe fix; repair the rest by hand, then preview again.")
    return "\n".join(lines)


def _full_fsync(fd: int) -> None:
    """fsync, and on macOS F_FULLFSYNC (plain fsync there does not reach the platter)."""
    if sys.platform == "darwin" and fcntl is not None and hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        except OSError:
            os.fsync(fd)          # the filesystem refused F_FULLFSYNC; a plain fsync is the best left
        return
    os.fsync(fd)


def _write_in_place(fd: int, data: bytes) -> None:
    """Overwrite from offset 0, THEN truncate to the new length, then flush.

    Writing before truncating means a crash part-way leaves the old tail or a partial row, which ``check_log``
    rejects, never a shorter well-formed file that silently drops later rows (a HALT among them).
    """
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]
    os.ftruncate(fd, len(data))
    _full_fsync(fd)


def _write_backup(path: Path, raw: bytes) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    bfd = os.open(backup, flags, 0o600)
    try:
        _write_in_place(bfd, raw)
    finally:
        os.close(bfd)
    dfd = os.open(backup.parent, os.O_RDONLY)
    try:
        _full_fsync(dfd)
    except OSError as exc:        # some filesystems refuse fsync on a directory: say so, don't fail the repair
        import warnings

        warnings.warn(f"could not fsync {backup.parent} after writing the backup: {exc}", RuntimeWarning,
                      stacklevel=2)
    finally:
        os.close(dfd)
    return backup


def _tty_confirm(plan: RepairPlan, digest: str) -> bool:
    """Show the plan and ask the operator to retype the digest prefix; requires an interactive terminal.

    A speed-bump against an unattended apply, not a security boundary: an agent can allocate a pseudo-terminal
    or call :func:`apply_repair` with its own ``confirm``. What makes an agent-run repair harmless is that no
    proposal can change a decision cell (the column rules in :func:`_propose`).
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ValueError("--apply needs an interactive terminal; the operator runs it. Nothing written")
    print(render_plan(plan))
    typed = input("\nType the first 8 characters of the digest to apply it: ").strip()
    return typed == digest[:8]


def _read_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks)


def apply_repair(path: Path, digest: str, *, confirm=None) -> Path:
    """Apply the previewed repair, bound to ``digest``, in place under the log's lock; return the backup path.

    Args:
        path: The log file; its name must be one of :data:`agentteams.governance_logs.LOG_NAMES`.
        digest: The digest the preview printed (64 lowercase hex characters).
        confirm: ``(plan, digest) -> bool`` called under the lock after the digest matches; defaults to an
            interactive-terminal prompt (:func:`_tty_confirm`). Nothing is written unless it returns True.

    Returns:
        Path: The backup written before the change.

    Raises:
        TypeError: ``path`` is not a ``Path`` or ``digest`` is not a ``str``.
        ValueError: The file is not a governance log, the digest is malformed or does not match the current
            preview, the plan is refused or not fully fixable, confirmation is refused, or the repaired log still
            fails ``check_log`` (the original bytes are restored first).
        OSError: The file or the backup cannot be written (the original bytes are restored first when the log
            itself was being written).
    """
    if not isinstance(path, Path) or not isinstance(digest, str):
        raise TypeError("path must be a pathlib.Path and digest a str")
    if path.name not in LOG_NAMES:
        raise ValueError(f"{path.name} is not a governance log ({', '.join(LOG_NAMES)}); nothing written")
    if not _DIGEST_RE.match(digest):
        raise ValueError("digest must be the 64-character hex value the preview printed")
    fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        raw = _read_fd(fd)
        plan = _plan_from_bytes(path, raw)
        if plan.refusal:
            raise ValueError(f"refused: {plan.refusal}")
        if not plan.fixable:
            raise ValueError("not everything in this log has a safe fix; nothing written")
        if plan.digest != digest:
            raise ValueError("the digest does not match the current preview (the file or the proposal changed); "
                             "preview again and approve the new digest. Nothing written")
        if not (confirm or _tty_confirm)(plan, digest):
            raise ValueError("confirmation did not match; nothing written")
        lines = _lines(raw.decode("utf-8"))
        for item in sorted(plan.items, key=lambda i: i.first_line, reverse=True):
            old_last = lines[item.last_line - 1]
            ending = "\r\n" if old_last.endswith("\r\n") else ("\n" if old_last.endswith("\n") else
                                                               ("\r" if old_last.endswith("\r") else ""))
            buf = io.StringIO()
            csv.writer(buf, lineterminator=ending or "\n").writerow(item.fixed)
            rendered = buf.getvalue() if ending else buf.getvalue().rstrip("\n")
            lines[item.first_line - 1:item.last_line] = [rendered]
        backup = _write_backup(path, raw)
        try:
            _write_in_place(fd, "".join(lines).encode("utf-8"))
        except OSError:
            _write_in_place(fd, raw)
            raise
        problems = check_log(path)
        if problems:
            _write_in_place(fd, raw)
            raise ValueError("the repaired log still fails check (original restored): " + "; ".join(problems[:3]))
        return backup
    finally:
        os.close(fd)


__all__ = ["DECISION_COLUMNS", "FREE_TEXT_COLUMNS", "POSITIONED_COLUMNS", "SIGNING_COLUMNS", "RepairItem", "RepairPlan",
           "apply_repair", "plan_repair", "render_plan"]
