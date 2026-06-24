"""parallel_plan.py — derive fail-safe parallel "waves" from a plan-steps CSV.

This module activates the long-dormant dependency concept in agentteams plans.
Given a *runtime-schema* plan-steps CSV (the 7-column
``step,agent,action,inputs,outputs,status,notes`` form emitted teams use, plus
an **optional** ``depends_on`` column), it computes which steps are mutually
independent and may be dispatched together — under a deliberately *conservative*
heuristic, so under-declaration fails **safe** (sequential), never open.

Design contract (see ``references/plans/parallel-independent-plan-execution.report.md``)
---------------------------------------------------------------------------------
- **Targets the runtime schema only.** It does NOT reuse the strict 11-column
  parser in :mod:`agentteams.plan_steps_todo` (which raises on a 7-column CSV).
  The reader here is tolerant and header-keyed, like :mod:`agentteams.plan_steps`.
- **Independence is a heuristic, not a proof.** Two steps may share a wave only
  when their read/write *footprints* (parsed from ``inputs``/``outputs``) are
  disjoint AND neither touches known shared mutable state (git, databases,
  locks, network, servers, migrations). Anything ambiguous → its own wave.
- **Fail-safe.** A step with an empty / unparseable footprint is treated as
  non-independent (singleton). An undeclared read-after-write is detected from
  footprints and an *implicit* ordering edge is added so the schedule stays
  correct even when the orchestrator forgot to populate ``depends_on``.
- **Cycles are blocking.** A dependency cycle (declared or footprint-implied)
  yields no schedule, only an error.
- **Concurrency is the host's job.** This module only *computes* the schedule.
  Whether waves are dispatched concurrently (Claude ``agent`` fan-out) or merely
  surfaced as an "any-order" recommendation is decided by the orchestrator per
  the host runtime's capabilities.

CLI
---
    python -m agentteams.parallel_plan PLAN.steps.csv [MORE.steps.csv ...]
    python -m agentteams.parallel_plan PLAN.steps.csv --json
"""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared mutable state denylist
# ---------------------------------------------------------------------------

#: Substrings/patterns that signal a step touches shared mutable state with no
#: clean file footprint (the git index, a database, a lock, the network, a
#: running server, an order-significant migration). Such steps are NEVER batched
#: into a multi-member wave — they get their own singleton wave. Matched
#: case-insensitively against a step's ``action``/``inputs``/``outputs`` text.
_SHARED_STATE_PATTERNS: tuple[str, ...] = (
    r"\bgit\b",
    r"\.git\b",
    r"\bdatabase\b",
    r"\bdb\b",
    r"\bsql\b",
    r"\block(?:s|file)?\b",
    r"package-lock",
    r"yarn\.lock",
    r"\.env\b",
    r"\benv\b",
    r"://",
    r"\bhttps?\b",
    r"\bnetwork\b",
    r"\bserver\b",
    r"\bdeploy",
    r"\bmigrat",
    r"\bport\b",
)

_SHARED_STATE_RE = re.compile("|".join(_SHARED_STATE_PATTERNS), re.IGNORECASE)

#: Token looks "path-like" if it contains a path separator or a dotted filename.
_PATHLIKE_RE = re.compile(r"[\w./-]*[/.][\w./-]+")

#: Split footprint / dependency cells on whitespace, commas, and semicolons.
_SPLIT_RE = re.compile(r"[\s,;]+")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PlanStep:
    """One row of a runtime-schema plan-steps CSV.

    Only ``step`` is required. Every other field defaults to empty so the
    reader tolerates a minimal 1-column CSV and any column ordering.
    """

    step: str
    agent: str = ""
    action: str = ""
    inputs: str = ""
    outputs: str = ""
    status: str = ""
    notes: str = ""
    depends_on: str = ""

    def dep_ids(self) -> list[str]:
        """Declared dependency step-ids, parsed from ``depends_on``."""
        return [t for t in _SPLIT_RE.split(self.depends_on.strip()) if t]

    def read_tokens(self) -> set[str]:
        """Path-like read footprint, parsed from ``inputs``."""
        return _footprint_tokens(self.inputs)

    def write_tokens(self) -> set[str]:
        """Path-like write footprint, parsed from ``outputs``."""
        return _footprint_tokens(self.outputs)

    def touches_shared_state(self) -> bool:
        """True if the step matches the shared-mutable-state denylist."""
        scope = f"{self.action} {self.inputs} {self.outputs}"
        return bool(_SHARED_STATE_RE.search(scope))

    def has_footprint(self) -> bool:
        """True if the step declares at least one path-like read or write."""
        return bool(self.read_tokens() or self.write_tokens())


@dataclass
class WaveSchedule:
    """Result of :func:`compute_waves`.

    Attributes:
        waves:    Ordered list of waves; each wave is a sorted list of step-ids
                  that may be dispatched together. A singleton wave has one id.
        reasons:  Map of step-id → human-readable reason it was forced to a
                  singleton wave (shared state, or empty/unparseable footprint).
        errors:   Blocking problems (e.g. a dependency cycle). Non-empty ⇒
                  ``waves`` is empty and no schedule could be produced.
        warnings: Non-blocking advisories (unknown ``depends_on`` ids, inferred
                  footprint orderings, serialized write-write conflicts).
    """

    waves: list[list[str]] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def max_parallelism(self) -> int:
        """Largest wave size — the peak concurrency the schedule allows."""
        return max((len(w) for w in self.waves), default=0)


@dataclass
class PlanFootprint:
    """A whole-plan footprint, for cross-plan independence checks."""

    path: str
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    determinate: bool = True  # False if no parseable write footprint at all


# ---------------------------------------------------------------------------
# Footprint helpers
# ---------------------------------------------------------------------------

def _footprint_tokens(cell: str) -> set[str]:
    """Extract path-like tokens from a free-text footprint cell."""
    tokens: set[str] = set()
    for raw in _SPLIT_RE.split(cell.strip()):
        raw = raw.strip().strip(",;").rstrip("/")
        if raw and _PATHLIKE_RE.fullmatch(raw):
            tokens.add(raw)
    return tokens


def _paths_conflict(a: str, b: str) -> bool:
    """True if two path tokens refer to the same file or a dir/file containment.

    ``examples/x/expected`` conflicts with ``examples/x/expected/o.md`` (the
    directory contains the file), and identical paths conflict. Sibling paths
    do not.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return b.startswith(a + "/") or a.startswith(b + "/")


def _sets_overlap(s1: set[str], s2: set[str]) -> bool:
    """True if any token in ``s1`` conflicts (==/containment) with any in ``s2``."""
    return any(_paths_conflict(x, y) for x in s1 for y in s2)


def _steps_conflict(a: PlanStep, b: PlanStep) -> bool:
    """True if two steps cannot share a wave on footprint grounds.

    Conflict = write-write, read-after-write, or write-after-read overlap.
    Shared *reads* do NOT conflict.
    """
    wa, wb = a.write_tokens(), b.write_tokens()
    ra, rb = a.read_tokens(), b.read_tokens()
    return _sets_overlap(wa, wb) or _sets_overlap(wa, rb) or _sets_overlap(ra, wb)


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

_KNOWN_FIELDS = {"step", "agent", "action", "inputs", "outputs", "status", "notes", "depends_on"}


def read_steps(csv_path: Path | str) -> list[PlanStep]:
    """Read a runtime-schema plan-steps CSV into :class:`PlanStep` rows.

    Tolerant: unknown columns are ignored, missing columns default to empty,
    and rows with a blank ``step`` are skipped. Raises ``FileNotFoundError`` if
    the file is absent and ``ValueError`` if there is no ``step`` column.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"plan-steps CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = set(reader.fieldnames or [])
        if "step" not in fields:
            raise ValueError(
                f"plan-steps CSV {csv_path} has no 'step' column "
                f"(found: {sorted(fields)})"
            )
        steps: list[PlanStep] = []
        for row in reader:
            sid = (row.get("step") or "").strip()
            if not sid:
                continue
            steps.append(PlanStep(
                step=sid,
                agent=(row.get("agent") or "").strip(),
                action=(row.get("action") or "").strip(),
                inputs=(row.get("inputs") or "").strip(),
                outputs=(row.get("outputs") or "").strip(),
                status=(row.get("status") or "").strip(),
                notes=(row.get("notes") or "").strip(),
                depends_on=(row.get("depends_on") or "").strip(),
            ))
    return steps


# ---------------------------------------------------------------------------
# Wave computation
# ---------------------------------------------------------------------------

def compute_waves(steps: list[PlanStep]) -> WaveSchedule:
    """Compute fail-safe parallel waves from a list of plan steps.

    Steps without a satisfied dependency wait; independent steps with disjoint
    footprints and no shared-state contact are grouped into the same wave.
    See the module docstring for the full contract.
    """
    result = WaveSchedule()
    if not steps:
        return result

    by_id: dict[str, PlanStep] = {}
    for s in steps:
        if s.step in by_id:
            result.warnings.append(f"duplicate step id {s.step!r}; keeping first")
            continue
        by_id[s.step] = s
    ids = list(by_id)

    # 1) Declared dependency edges (predecessor -> successor).
    succ: dict[str, set[str]] = {i: set() for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}

    def _add_edge(pred: str, node: str) -> None:
        if pred == node or node in succ[pred]:
            return
        succ[pred].add(node)
        indeg[node] += 1

    for node in ids:
        for dep in by_id[node].dep_ids():
            if dep not in by_id:
                result.warnings.append(
                    f"step {node!r} declares unknown dependency {dep!r}; ignored"
                )
                continue
            _add_edge(dep, node)

    # 2) Implicit ordering edges inferred from footprints (fail-safe). Iterate
    #    ordered pairs deterministically by id position.
    for ai in range(len(ids)):
        for bi in range(ai + 1, len(ids)):
            a, b = by_id[ids[ai]], by_id[ids[bi]]
            wa, wb = a.write_tokens(), b.write_tokens()
            ra, rb = a.read_tokens(), b.read_tokens()
            if _sets_overlap(wa, rb):  # b reads what a writes -> a before b
                if b.step not in succ[a.step]:
                    _add_edge(a.step, b.step)
                    result.warnings.append(
                        f"inferred order {a.step} -> {b.step} "
                        f"(read-after-write on shared path)"
                    )
            elif _sets_overlap(ra, wb):  # a reads what b writes -> b before a
                if a.step not in succ[b.step]:
                    _add_edge(b.step, a.step)
                    result.warnings.append(
                        f"inferred order {b.step} -> {a.step} "
                        f"(read-after-write on shared path)"
                    )
            elif _sets_overlap(wa, wb):  # both write -> serialize by id order
                if b.step not in succ[a.step] and a.step not in succ[b.step]:
                    _add_edge(a.step, b.step)
                    result.warnings.append(
                        f"serialized {a.step} before {b.step} "
                        f"(write-write conflict on shared path)"
                    )

    # 3) Kahn layering on the combined graph. A cycle leaves nodes unprocessed.
    remaining = dict(indeg)
    processed: set[str] = set()
    layers: list[list[str]] = []
    while True:
        ready = sorted(i for i in ids if i not in processed and remaining[i] == 0)
        if not ready:
            break
        layers.append(ready)
        for node in ready:
            processed.add(node)
            for nxt in succ[node]:
                remaining[nxt] -= 1

    if len(processed) != len(ids):
        cyc = sorted(set(ids) - processed)
        result.errors.append(
            f"dependency cycle detected among steps: {cyc}; no schedule emitted"
        )
        return result

    # 4) Split each layer into a grouped wave + forced singletons.
    for layer in layers:
        grouped: list[str] = []
        singletons: list[str] = []
        for sid in layer:
            step = by_id[sid]
            if step.touches_shared_state():
                result.reasons[sid] = "touches shared mutable state (denylist)"
                singletons.append(sid)
            elif not step.has_footprint():
                result.reasons[sid] = "no parseable inputs/outputs footprint"
                singletons.append(sid)
            else:
                grouped.append(sid)
        # Within the grouped set, footprint conflicts shouldn't exist (implicit
        # edges already ordered them across layers), but guard anyway.
        safe_group: list[str] = []
        for sid in grouped:
            if any(_steps_conflict(by_id[sid], by_id[other]) for other in safe_group):
                result.reasons[sid] = "footprint conflict with a wave-mate"
                singletons.append(sid)
            else:
                safe_group.append(sid)
        if safe_group:
            result.waves.append(sorted(safe_group))
        for sid in singletons:
            result.waves.append([sid])

    return result


def analyze_plan(csv_path: Path | str) -> WaveSchedule:
    """Read a plan-steps CSV and compute its wave schedule."""
    return compute_waves(read_steps(csv_path))


# ---------------------------------------------------------------------------
# Cross-plan independence ("any-order", NOT concurrency)
# ---------------------------------------------------------------------------

def plan_footprint(csv_path: Path | str) -> PlanFootprint:
    """Union read/write footprint for a whole plan."""
    steps = read_steps(csv_path)
    reads: set[str] = set()
    writes: set[str] = set()
    for s in steps:
        reads |= s.read_tokens()
        writes |= s.write_tokens()
    return PlanFootprint(
        path=str(csv_path),
        reads=reads,
        writes=writes,
        determinate=bool(writes),
    )


def independent_plans(csv_paths: list[Path | str]) -> dict[str, list[list[str]]]:
    """Group plans that do not block each other (write-disjoint = any-order).

    NOTE: this reports plans that are safe to advance in *any order* — it is NOT
    a claim of concurrent execution (a single orchestrator session interleaves;
    true cross-plan concurrency needs a separate substrate and is out of scope).

    Returns a dict with:
        ``any_order_groups``: groups of plan paths with pairwise-disjoint write
            footprints (and no read-after-write across them).
        ``undetermined``: plans with no parseable write footprint (treated as
            ordered / not analyzable).
    """
    fps = [plan_footprint(p) for p in csv_paths]
    determinate = [f for f in fps if f.determinate]
    undetermined = [f.path for f in fps if not f.determinate]

    # Union-find over "blocks" relation: two plans block each other if their
    # footprints overlap on a write (write-write or read-after-write).
    parent: dict[str, str] = {f.path: f.path for f in determinate}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(x: str, y: str) -> None:
        parent[_find(x)] = _find(y)

    for i in range(len(determinate)):
        for j in range(i + 1, len(determinate)):
            a, b = determinate[i], determinate[j]
            blocks = (
                _sets_overlap(a.writes, b.writes)
                or _sets_overlap(a.writes, b.reads)
                or _sets_overlap(a.reads, b.writes)
            )
            if blocks:
                _union(a.path, b.path)

    clusters: dict[str, list[str]] = {}
    for f in determinate:
        clusters.setdefault(_find(f.path), []).append(f.path)
    # Each cluster is internally entangled; distinct clusters are mutually
    # any-order. Report each cluster as a group (singletons = fully free).
    any_order_groups = [sorted(v) for v in clusters.values()]
    any_order_groups.sort()
    return {"any_order_groups": any_order_groups, "undetermined": sorted(undetermined)}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def to_json(schedule: WaveSchedule) -> str:
    """Serialise a :class:`WaveSchedule` to JSON."""
    return json.dumps(
        {
            "waves": schedule.waves,
            "max_parallelism": schedule.max_parallelism,
            "reasons": schedule.reasons,
            "errors": schedule.errors,
            "warnings": schedule.warnings,
        },
        indent=2,
    )


def render_markdown(schedule: WaveSchedule, title: str = "Parallelization plan") -> str:
    """Render a human-readable wave schedule as Markdown."""
    lines = [f"# {title}", ""]
    if schedule.errors:
        lines.append("> ⛔ **Blocking errors — no schedule emitted:**")
        lines += [f"> - {e}" for e in schedule.errors]
        return "\n".join(lines)
    lines.append(
        f"**{len(schedule.waves)} wave(s); peak parallelism = "
        f"{schedule.max_parallelism}.** "
        "Dispatch a wave's members concurrently only where the host supports "
        "concurrent subagents (e.g. Claude `agent`); otherwise run them in any "
        "order. Run `@conflict-auditor` per member at wave join, and "
        "`@adversarial` once per wave on the remaining plan."
    )
    lines.append("")
    for i, wave in enumerate(schedule.waves):
        if len(wave) == 1:
            sid = wave[0]
            why = schedule.reasons.get(sid)
            suffix = f" — singleton ({why})" if why else ""
            lines.append(f"- **Wave {i + 1}:** `{sid}`{suffix}")
        else:
            lines.append(f"- **Wave {i + 1} (parallel):** " + ", ".join(f"`{s}`" for s in wave))
    if schedule.warnings:
        lines += ["", "## Advisories", ""]
        lines += [f"- {w}" for w in schedule.warnings]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill (Claude, bridge-emitted)
# ---------------------------------------------------------------------------

_SKILL_TEMPLATE = """---
name: parallelize-plan
description: Derive fail-safe parallel waves from a plan-steps CSV's optional depends_on column. Read-only; computes which independent steps may be dispatched together.
bridge: copilot-vscode-to-claude
---

# Parallelize plan — independence analysis

When the orchestrator activates on a plan that has a ``*.steps.csv``, compute a
**wave schedule** so independent steps can be dispatched together instead of
strictly one at a time.

**Compute the schedule (read-only):**

    from pathlib import Path
    from agentteams.parallel_plan import analyze_plan, render_markdown
    schedule = analyze_plan(Path("tmp/by-week/<week>/<plan>.steps.csv"))
    print(render_markdown(schedule))

Or from the shell:

    python -m agentteams.parallel_plan tmp/by-week/<week>/<plan>.steps.csv

**How to act on it:**

- Each *wave* is a set of step-ids whose read/write footprints are disjoint and
  which touch no shared mutable state. Dispatch a wave's members **concurrently**
  via the ``agent`` tool when the host supports concurrent subagents; otherwise
  run them in any order.
- After each member of a wave finishes, run ``@conflict-auditor`` on that
  member's deliverable (member audits commute because footprints are disjoint).
  Run ``@adversarial`` once per wave on the remaining plan before the next wave.
- A **singleton** wave runs alone. Steps that touch shared state (git, a
  database, a lock, the network, a migration) or that declare no footprint are
  forced to singletons — never parallelize them.
- If the schedule reports a **cycle error**, the plan's ``depends_on`` is
  inconsistent: fix it before executing.

**Cross-plan (any-order, not concurrency):**

    python -m agentteams.parallel_plan PLAN_A.steps.csv PLAN_B.steps.csv

reports which open plans do not block each other (safe to advance in any order).
This is a scheduling note, not a claim of simultaneous execution.
"""


def render_skill() -> str:
    """Return the ``parallelize-plan`` Claude skill document."""
    return _SKILL_TEMPLATE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. See module docstring for usage."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agentteams.parallel_plan",
        description="Derive fail-safe parallel waves from plan-steps CSV(s).",
    )
    parser.add_argument(
        "csv", nargs="+", metavar="STEPS_CSV",
        help="One or more runtime-schema plan-steps CSV files.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of Markdown.",
    )
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.csv]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"Error: file(s) not found: {missing}", file=sys.stderr)
        return 1

    exit_code = 0
    payloads: list[dict] = []
    for p in paths:
        schedule = analyze_plan(p)
        if schedule.errors:
            exit_code = 1
        if args.json:
            payloads.append({"plan": str(p), "schedule": json.loads(to_json(schedule))})
        else:
            print(render_markdown(schedule, title=f"Parallelization plan — {p.name}"))
            print()

    if len(paths) > 1:
        cross = independent_plans(list(paths))
        if args.json:
            payloads.append({"cross_plan": cross})
        else:
            print("# Cross-plan independence (any-order, not concurrency)")
            print()
            for group in cross["any_order_groups"]:
                print(f"- entangled group (run internally per their own schedules): {group}")
            if cross["undetermined"]:
                print(f"- undetermined footprint (treat as ordered): {cross['undetermined']}")
            print()

    if args.json:
        print(json.dumps(payloads, indent=2))

    return exit_code


__all__ = [
    "PlanStep",
    "WaveSchedule",
    "PlanFootprint",
    "read_steps",
    "compute_waves",
    "analyze_plan",
    "plan_footprint",
    "independent_plans",
    "to_json",
    "render_markdown",
    "render_skill",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
