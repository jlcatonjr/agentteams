"""plan_priority.py — order plan items by expected net benefit.

Implements ``references/net-benefit-prioritization.reference.md``: every
development or remediation item in a plan or metaplan is rated for
**severity** (S, 1–5), **breadth of impact** (B, 1–5), **cost of remediation**
(C, 1–5) and an optional **confidence** (p, 0.25–1.0), and items are executed in
descending expected net benefit, subject to their dependencies::

    cost_points(C)   = 1, 2, 4, 8, 16 for C = 1..5
    expected_benefit = p × S × B
    net_benefit      = expected_benefit − cost_points(C)

Design contract
---------------
- **Compute-only.** It reads a plan-steps or metaplan CSV and reports; it never
  writes the CSV (the same contract as :mod:`agentteams.parallel_plan`).
- **Tolerant, header-keyed reader.** The key column is ``step``, or ``id`` when
  the CSV has no ``step`` column (backlog CSVs). Rows carrying the rating columns
  are items; ``kind=closing`` rows (integration, closeout) are never ranked and
  stay last, in CSV order unless an edge between two of them says otherwise.
- **Dependencies win.** Ranking never reorders across an edge of the combined
  graph: declared ``depends_on`` plus the implicit read-after-write edges
  :func:`agentteams.parallel_plan.footprint_edges` derives from ``inputs`` /
  ``outputs``. Write-write overlaps are ties, not edges; ``parallel_plan
  --priority`` breaks them by rank.
- **Priority inheritance.** An item's ``effective_rank`` is the best rank of the
  item itself and of every item that depends on it, directly or transitively, so a cheap
  prerequisite of a top item is not starved.
- **Never silently moved.** An unrated non-closing row is a warning (an error
  under ``--require``); it is never ranked, and it is ordered after every rated
  item unless a rated item depends on it.
- **Pins are the operator's.** A ``pin`` cell must read
  ``operator:<YYYY-MM-DD>:<reason>``. The module checks the form only; the plan
  audit checks that each pin traces to an operator instruction.

CLI
---
    python -m agentteams.plan_priority PLAN.steps.csv
    python -m agentteams.plan_priority PLAN.steps.csv --json
    python -m agentteams.plan_priority PLAN.steps.csv --require

Exit status: 0 on success, 1 when a file is missing or has no ``step``/``id``
column, 2 on any validation error or dependency cycle (every bad row is listed).
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.parallel_plan import PlanStep, footprint_edges

# ---------------------------------------------------------------------------
# Rubric constants
# ---------------------------------------------------------------------------

#: Cost tier → cost points. Points double per tier because effort and risk grow
#: roughly geometrically.
COST_POINTS: dict[int, int] = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}

#: Band thresholds on ``net_benefit``.
DO_NOW_MIN = 10.0
NEXT_MIN = 4.0

BAND_PINNED = "pinned"
BAND_DO_NOW = "do-now"
BAND_SAFETY_FLOOR = "next (safety floor)"
BAND_NEXT = "next"
BAND_LATER = "later"
BAND_RECONSIDER = "reconsider"

#: Sort position of each band.
BAND_ORDER: dict[str, int] = {
    BAND_PINNED: 0,
    BAND_DO_NOW: 1,
    BAND_SAFETY_FLOOR: 2,
    BAND_NEXT: 3,
    BAND_LATER: 4,
    BAND_RECONSIDER: 5,
}

RATING_COLUMNS: tuple[str, ...] = ("severity", "breadth", "cost")
RATIONALE_COLUMNS: dict[str, str] = {
    "severity": "rationale_s",
    "breadth": "rationale_b",
    "cost": "rationale_c",
    "confidence": "rationale_p",
}
#: Computed columns a CSV may already carry; they are checked, never written.
COMPUTED_COLUMNS: tuple[str, ...] = (
    "expected_benefit", "cost_points", "net_benefit", "band", "rank", "effective_rank",
)

_PIN_RE = re.compile(r"^operator:(\d{4}-\d{2}-\d{2}):\S.*$")
_INF = math.inf


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PriorityItem:
    """One row of the plan with its ratings and computed priority.

    Attributes:
        step: The row id (``step`` column, or ``id``).
        position: Zero-based CSV row position.
        kind: The ``kind`` cell (``closing`` rows are never ranked).
        title: ``title`` cell, falling back to ``action``.
        status: ``status`` cell.
        severity, breadth, cost: Integer ratings, or ``None`` when unrated.
        confidence: Confidence p (default 1.0).
        pin: The ``pin`` cell, empty when unpinned.
        depends_on: Declared dependency ids.
        expected_benefit, cost_points, net_benefit, band: Computed scores
            (``None`` / ``""`` for unrated and closing rows).
        rank: 1-based rank among rated items, or ``None``.
        effective_rank: Best rank of the item and its transitive dependents,
            or ``None`` when neither it nor any dependent is rated.
        order: 1-based execution position over every row.
    """

    step: str
    position: int
    kind: str = ""
    title: str = ""
    status: str = ""
    severity: int | None = None
    breadth: int | None = None
    cost: int | None = None
    confidence: float = 1.0
    pin: str = ""
    depends_on: list[str] = field(default_factory=list)
    expected_benefit: float | None = None
    cost_points: int | None = None
    net_benefit: float | None = None
    band: str = ""
    rank: int | None = None
    effective_rank: int | None = None
    order: int | None = None

    @property
    def is_closing(self) -> bool:
        """True for an integration/closeout row (``kind=closing``)."""
        return self.kind.strip().lower() == "closing"

    @property
    def is_rated(self) -> bool:
        """True when the row carries all three integer ratings."""
        return self.severity is not None and self.breadth is not None and self.cost is not None

    @property
    def pin_date(self) -> str:
        """The ISO date from a valid operator pin, or ``""``."""
        m = _PIN_RE.match(self.pin)
        return m.group(1) if m else ""

    def to_dict(self) -> dict:
        """JSON-ready view of the item.

        Returns:
            dict: The row id, ratings, computed scores, band, rank, effective
            rank and execution order, plus ``pin``, ``kind``, ``status`` and
            ``title``.
        """
        return {
            "step": self.step,
            "order": self.order,
            "rank": self.rank,
            "effective_rank": self.effective_rank,
            "net_benefit": self.net_benefit,
            "expected_benefit": self.expected_benefit,
            "cost_points": self.cost_points,
            "band": self.band,
            "severity": self.severity,
            "breadth": self.breadth,
            "cost": self.cost,
            "confidence": self.confidence if self.is_rated else None,
            "pin": self.pin,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
        }


@dataclass
class PriorityReport:
    """Result of :func:`rank_rows`.

    Attributes:
        items: Every row, in execution order (closing rows last).
        pins: ``(step, pin)`` for every pinned item, in pin order.
        warnings: Non-blocking advisories (unrated rows, inferred edges,
            computed-column mismatches).
        errors: Blocking problems. Non-empty ⇒ the order must not be used.
    """

    items: list[PriorityItem] = field(default_factory=list)
    pins: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def band_for(net: float, severity: int) -> str:
    """Return the reporting band for a net benefit, applying the safety floor.

    Args:
        net: The item's net benefit.
        severity: The item's severity rating (1–5).

    Returns:
        str: One of the ``BAND_*`` labels other than ``BAND_PINNED``. An S=5
        item whose net is below the ``next`` threshold gets
        ``BAND_SAFETY_FLOOR``, which sorts at the head of ``next``.

    Raises:
        ValueError: If ``severity`` is outside 1–5.
    """
    if not 1 <= severity <= 5:
        raise ValueError(f"severity={severity} outside 1..5")
    if net >= DO_NOW_MIN:
        return BAND_DO_NOW
    if net >= NEXT_MIN:
        return BAND_NEXT
    if severity == 5:
        return BAND_SAFETY_FLOOR
    return BAND_LATER if net >= 0 else BAND_RECONSIDER


def score(severity: int, breadth: int, cost: int, confidence: float = 1.0) -> tuple[float, int, float]:
    """Compute ``(expected_benefit, cost_points, net_benefit)`` for one item.

    Args:
        severity: S, 1–5.
        breadth: B, 1–5.
        cost: C, 1–5.
        confidence: p, 0.25–1.0.

    Returns:
        tuple[float, int, float]: Expected benefit and net benefit rounded to
        two places, and the integer cost points.

    Raises:
        ValueError: If a rating is outside its range.
    """
    for name, v in (("severity", severity), ("breadth", breadth), ("cost", cost)):
        if not 1 <= v <= 5:
            raise ValueError(f"{name}={v} outside 1..5")
    if not 0.25 <= confidence <= 1.0:
        raise ValueError(f"confidence={confidence} outside 0.25..1.0")
    eb = round(confidence * severity * breadth, 2)
    cp = COST_POINTS[cost]
    return eb, cp, round(eb - cp, 2)


def natural_key(item_id: str) -> tuple:
    """Sort key comparing the numeric runs of an id as numbers (``S2`` < ``S10``).

    Args:
        item_id: A step or item id.

    Returns:
        tuple: Alternating text and integer parts.

    Raises:
        None.
    """
    return tuple(int(x) if x.isdigit() else x for x in re.split(r"(\d+)", item_id))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_rows(csv_path: Path | str) -> tuple[list[dict[str, str]], list[str]]:
    """Read a plan/metaplan/backlog CSV into header-keyed rows.

    Args:
        csv_path: Path to the CSV.

    Returns:
        tuple[list[dict[str, str]], list[str]]: The rows (cells stripped) and
        the header. Each row also carries a ``"__id__"`` key holding the value
        of the key column (``step``, or ``id`` when there is no ``step``).
        Rows with an empty key are skipped.

    Raises:
        FileNotFoundError: If the file is absent.
        ValueError: If the CSV has neither a ``step`` nor an ``id`` column.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"plan CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        key = "step" if "step" in header else ("id" if "id" in header else "")
        if not key:
            raise ValueError(f"plan CSV {csv_path} has no 'step' or 'id' column (found: {header})")
        rows: list[dict[str, str]] = []
        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            rid = row.get(key, "")
            if not rid:
                continue
            row["__id__"] = rid
            rows.append(row)
    return rows, header


def _parse_int(value: str, name: str, rid: str, errors: list[str]) -> int | None:
    try:
        n = int(value)
    except ValueError:
        errors.append(f"{rid}: {name}={value!r} is not an integer 1..5")
        return None
    if not 1 <= n <= 5:
        errors.append(f"{rid}: {name}={n} outside 1..5")
        return None
    return n


def _to_item(row: dict[str, str], position: int, header: list[str], require: bool,
             errors: list[str], warnings: list[str]) -> PriorityItem:
    rid = row["__id__"]
    item = PriorityItem(
        step=rid,
        position=position,
        kind=row.get("kind", ""),
        title=row.get("title") or row.get("action", ""),
        status=row.get("status", ""),
        pin=row.get("pin", ""),
        depends_on=PlanStep(step=rid, depends_on=row.get("depends_on", "")).dep_ids(),
    )
    present = [c for c in RATING_COLUMNS if row.get(c, "")]
    if item.is_closing:
        if present or row.get("confidence", ""):
            warnings.append(f"{rid}: kind=closing row carries ratings; they are ignored")
        if item.pin:
            errors.append(f"{rid}: a kind=closing row cannot be pinned")
        return item
    if not present:
        msg = f"{rid}: unrated (not ranked; ordered after every rated item unless one depends on it)"
        (errors if require else warnings).append(msg)
        if item.pin:
            errors.append(f"{rid}: pinned but unrated; a pin needs ratings")
        return item
    if len(present) < len(RATING_COLUMNS):
        missing = [c for c in RATING_COLUMNS if c not in present]
        errors.append(f"{rid}: partly rated; missing {', '.join(missing)}")
        return item
    s = _parse_int(row["severity"], "severity", rid, errors)
    b = _parse_int(row["breadth"], "breadth", rid, errors)
    c = _parse_int(row["cost"], "cost", rid, errors)
    p_raw = row.get("confidence", "")
    p = 1.0
    if p_raw:
        try:
            p = float(p_raw)
        except ValueError:
            errors.append(f"{rid}: confidence={p_raw!r} is not a number 0.25..1.0")
            return item
        if not 0.25 <= p <= 1.0:
            errors.append(f"{rid}: confidence={p} outside 0.25..1.0")
            return item
    if s is None or b is None or c is None:
        return item
    if item.pin and not _PIN_RE.match(item.pin):
        errors.append(
            f"{rid}: pin={item.pin!r} is not 'operator:<YYYY-MM-DD>:<reason>' (only the operator pins)"
        )
    if require:
        for col in (*RATING_COLUMNS, "confidence"):
            rcol = RATIONALE_COLUMNS[col]
            if rcol not in header:
                continue
            if col == "confidence" and p == 1.0 and not p_raw:
                continue
            if not row.get(rcol, ""):
                errors.append(f"{rid}: {col} has no rationale ({rcol} is empty)")
    item.severity, item.breadth, item.cost, item.confidence = s, b, c, p
    item.expected_benefit, item.cost_points, item.net_benefit = score(s, b, c, p)
    item.band = BAND_PINNED if item.pin_date else band_for(item.net_benefit, s)
    return item


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _edges(rows: list[dict[str, str]], ids: list[str], warnings: list[str]) -> dict[str, set[str]]:
    """Combined predecessor map: declared ``depends_on`` + implicit read-after-write."""
    known = set(ids)
    preds: dict[str, set[str]] = {i: set() for i in ids}
    first_rows: dict[str, dict[str, str]] = {}
    for r in rows:
        first_rows.setdefault(r["__id__"], r)
    steps = [
        PlanStep(step=rid, agent=r.get("agent", ""), action=r.get("action", ""),
                 inputs=r.get("inputs", ""), outputs=r.get("outputs", ""),
                 depends_on=r.get("depends_on", ""))
        for rid, r in first_rows.items()
    ]
    for st in steps:
        for dep in st.dep_ids():
            if dep not in known:
                warnings.append(f"{st.step}: depends_on names unknown row {dep!r}; ignored")
            elif dep != st.step:
                preds[st.step].add(dep)
    for first, second, kind in footprint_edges(steps):
        if kind == "raw" and first not in preds[second]:
            preds[second].add(first)
            warnings.append(f"inferred order {first} -> {second} (read-after-write on shared path)")
    return preds


def _successors(ids: list[str], preds: dict[str, set[str]]) -> dict[str, set[str]]:
    """Invert a predecessor map."""
    succ: dict[str, set[str]] = {i: set() for i in ids}
    for node, ps in preds.items():
        for p in ps:
            succ[p].add(node)
    return succ


def _find_cycle(ids: list[str], preds: dict[str, set[str]]) -> list[str]:
    """Return the ids left over by Kahn's algorithm (non-empty ⇒ a cycle)."""
    indeg = {i: len(preds[i]) for i in ids}
    succ = _successors(ids, preds)
    ready = [i for i in ids if indeg[i] == 0]
    seen: set[str] = set()
    while ready:
        n = ready.pop()
        seen.add(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return sorted(set(ids) - seen, key=natural_key)


def _check_computed(item: PriorityItem, row: dict[str, str], require: bool,
                    errors: list[str], warnings: list[str]) -> None:
    """Compare computed columns already in the CSV against the recomputation."""
    computed = {
        "expected_benefit": item.expected_benefit,
        "cost_points": item.cost_points,
        "net_benefit": item.net_benefit,
        "band": item.band or None,
        "rank": item.rank,
        "effective_rank": item.effective_rank,
    }
    for col in COMPUTED_COLUMNS:
        given = row.get(col, "")
        if not given:
            continue
        want = computed[col]
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            try:
                ok = abs(float(given) - float(want)) < 0.005
            except ValueError:
                ok = False
        else:
            ok = want is not None and given == str(want)
        if not ok:
            msg = (f"{item.step}: CSV {col}={given!r} does not match the recomputed value "
                   f"{want!r} (the CSV is never rewritten; update or drop the column)")
            (errors if require else warnings).append(msg)


def rank_rows(rows: list[dict[str, str]], header: list[str] | None = None,
              require: bool = False) -> PriorityReport:
    """Rate, rank and order plan rows by expected net benefit.

    Args:
        rows: Rows from :func:`read_rows` (each with ``"__id__"``).
        header: The CSV header, used to decide whether rationale columns exist.
            Defaults to the union of the rows' keys.
        require: Make an unrated non-closing row, a missing rationale column or
            empty rationale cell, and a computed-column mismatch errors instead
            of warnings.

    Returns:
        PriorityReport: Items in execution order. When ``errors`` is non-empty
        the order is still filled in where it could be computed, but must not
        be used for dispatch.

    Raises:
        None.
    """
    report = PriorityReport()
    if header is None:
        header = sorted({k for r in rows for k in r})
    items: list[PriorityItem] = []
    by_id: dict[str, PriorityItem] = {}
    row_of: dict[str, dict[str, str]] = {}
    for pos, row in enumerate(rows):
        rid = row["__id__"]
        if rid in by_id:
            report.errors.append(f"{rid}: duplicate row id")
            continue
        it = _to_item(row, pos, header, require, report.errors, report.warnings)
        items.append(it)
        by_id[rid] = it
        row_of[rid] = row

    if require and any(it.is_rated for it in items):
        absent = [RATIONALE_COLUMNS[c] for c in RATING_COLUMNS if RATIONALE_COLUMNS[c] not in header]
        if absent:
            report.errors.append(
                f"ratings need a one-line rationale each: add column(s) {', '.join(absent)}"
            )

    ids = [it.step for it in items]
    preds = _edges(rows, ids, report.warnings)
    cycle = _find_cycle(ids, preds)
    if cycle:
        report.errors.append(f"dependency cycle among rows: {cycle}; no order computed")
        report.items = items
        return report

    # Rank the rated, non-closing items.
    rated = [it for it in items if it.is_rated and not it.is_closing]
    rated.sort(key=lambda it: (
        BAND_ORDER[it.band],
        it.pin_date if it.band == BAND_PINNED else "",
        it.position if it.band == BAND_PINNED else 0,
        -float(it.net_benefit or 0),
        -(it.severity or 0),
        it.cost or 0,
        natural_key(it.step),
    ))
    for i, it in enumerate(rated, 1):
        it.rank = i
    report.pins = [(it.step, it.pin) for it in rated if it.band == BAND_PINNED]

    # A ranked or unrated work row may not wait on a closing row.
    for it in items:
        if it.is_closing:
            continue
        for p in sorted(preds[it.step], key=natural_key):
            if by_id[p].is_closing:
                report.errors.append(
                    f"{it.step}: depends on closing row {p}; closing rows run last, so the "
                    f"order cannot honour this edge (drop kind=closing from {p}, or the edge)"
                )

    # Priority inheritance over the combined graph.
    succ = _successors(ids, preds)
    memo: dict[str, float] = {}

    def _best(node: str) -> float:
        if node not in memo:
            own = by_id[node].rank if by_id[node].rank is not None else _INF
            memo[node] = own  # acyclic: no re-entry
            memo[node] = min([own] + [_best(m) for m in succ[node] if not by_id[m].is_closing])
        return memo[node]

    work = [it for it in items if not it.is_closing]
    for it in work:
        best = _best(it.step)
        it.effective_rank = None if best == _INF else int(best)

    # Execution order: always the ready row with the smallest key.
    def _key(it: PriorityItem) -> tuple:
        eff = it.effective_rank if it.effective_rank is not None else _INF
        own = it.rank if it.rank is not None else _INF
        return (eff, own, it.position)

    done: set[str] = set()

    def _topo(group: list[PriorityItem], key) -> list[PriorityItem]:
        out: list[PriorityItem] = []
        pending = sorted(group, key=key)
        members = {it.step for it in group}
        while pending:
            ready = next(
                (it for it in pending if all(p in done or p not in members for p in preds[it.step])),
                None,
            )
            if ready is None:  # excluded by the cycle check above
                raise AssertionError("dependency cycle survived the cycle check")
            out.append(ready)
            done.add(ready.step)
            pending.remove(ready)
        return out

    ordered = _topo(work, _key)
    # Closing rows: last, in CSV order, except that an edge between two of them wins.
    closing = _topo([it for it in items if it.is_closing], lambda it: it.position)
    for i, it in enumerate(ordered + closing, 1):
        it.order = i
    report.items = ordered + closing

    for it in items:
        if not it.is_closing:
            _check_computed(it, row_of[it.step], require, report.errors, report.warnings)
        if it.band == BAND_SAFETY_FLOOR:
            report.warnings.append(
                f"{it.step}: safety floor (S=5, net {it.net_benefit}); look for a cheaper partial fix"
            )
    return report


def analyze_priority(csv_path: Path | str, require: bool = False) -> PriorityReport:
    """Read a plan CSV and rank it.

    Args:
        csv_path: Path to a plan-steps, metaplan or backlog CSV.
        require: See :func:`rank_rows`.

    Returns:
        PriorityReport: The ranked report.

    Raises:
        FileNotFoundError: If the file is absent.
        ValueError: If the CSV has no ``step`` or ``id`` column.
    """
    rows, header = read_rows(csv_path)
    return rank_rows(rows, header, require=require)


def priority_map(report: PriorityReport) -> dict[str, int]:
    """Map each row id to its execution position, for ``compute_waves(priority=...)``.

    Args:
        report: A :class:`PriorityReport` without errors.

    Returns:
        dict[str, int]: Row id → 1-based ``order`` (rows with no order omitted).

    Raises:
        None.
    """
    return {it.step: it.order for it in report.items if it.order is not None}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def to_json(report: PriorityReport, plan: str = "") -> dict:
    """Return the JSON-ready form of a report.

    Args:
        report: The report.
        plan: The CSV path, echoed in the ``plan`` key.

    Returns:
        dict: ``{plan, items, pins, warnings, errors}``.

    Raises:
        None.
    """
    return {
        "plan": plan,
        "items": [it.to_dict() for it in report.items],
        "pins": [{"step": s, "pin": p} for s, p in report.pins],
        "warnings": report.warnings,
        "errors": report.errors,
    }


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def render_markdown(report: PriorityReport, title: str = "Net-benefit order") -> str:
    """Render a report as a Markdown table.

    Args:
        report: The report.
        title: Heading text.

    Returns:
        str: The Markdown document.

    Raises:
        None.
    """
    lines = [f"# {title}", ""]
    if report.errors:
        lines.append("> ⛔ **Errors — do not dispatch from this order:**")
        lines += [f"> - {e}" for e in report.errors]
        lines.append("")
    lines += [
        "| order | rank | eff. | step | net | S | B | C | p | band | status | title |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for it in report.items:
        band = it.band or ("closing" if it.is_closing else "unrated")
        p = it.confidence if it.is_rated else None
        t = it.title if len(it.title) <= 70 else it.title[:67] + "..."
        lines.append(
            f"| {_fmt(it.order)} | {_fmt(it.rank)} | {_fmt(it.effective_rank)} | {it.step} | "
            f"{_fmt(it.net_benefit)} | {_fmt(it.severity)} | {_fmt(it.breadth)} | {_fmt(it.cost)} | "
            f"{_fmt(p)} | {band} | {it.status or '—'} | {t.replace('|', '/')} |"
        )
    if report.pins:
        lines += ["", "**Operator pins:**"]
        lines += [f"- `{s}` — {p}" for s, p in report.pins]
    if report.warnings:
        lines += ["", "## Advisories", ""]
        lines += [f"- {w}" for w in report.warnings]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: 0 on success, 1 when a file is missing or unreadable, 2 on a
        validation error or dependency cycle.

    Raises:
        None.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agentteams.plan_priority",
        description="Order plan items by expected net benefit (compute-only; never writes the CSV).",
    )
    parser.add_argument("csv", nargs="+", metavar="PLAN_CSV",
                        help="Plan-steps, metaplan or backlog CSV(s) with severity/breadth/cost columns.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON: one object for one CSV, a list for several.")
    parser.add_argument("--require", action="store_true",
                        help="Treat unrated rows, empty rationales and stale computed columns as errors.")
    args = parser.parse_args(argv)

    exit_code = 0
    payloads: list[dict] = []
    for raw in args.csv:
        path = Path(raw)
        try:
            report = analyze_priority(path, require=args.require)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            exit_code = max(exit_code, 1)
            continue
        if report.errors:
            exit_code = 2
        if args.json:
            payloads.append(to_json(report, str(path)))
        else:
            print(render_markdown(report, title=f"Net-benefit order — {path.name}"))
            print()
    if args.json:
        print(json.dumps(payloads[0] if len(payloads) == 1 else payloads, indent=2))
    return exit_code


__all__ = [
    "COST_POINTS",
    "BAND_ORDER",
    "PriorityItem",
    "PriorityReport",
    "band_for",
    "score",
    "natural_key",
    "read_rows",
    "rank_rows",
    "analyze_priority",
    "priority_map",
    "to_json",
    "render_markdown",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
