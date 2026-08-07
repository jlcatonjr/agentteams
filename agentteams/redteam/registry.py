"""registry.py — the probe data model, the probe loader, and the ledger readers.

**Why this lives in the package rather than in ``tests/``.** The red-team battery was written
as a test-support module (``tests/constitutional_redteam_battery.py``), and the standing audit
that runs it daily needs the same vocabulary. Importing ``tests`` from ``agentteams`` would
invert the dependency and make a shipped package depend on a test tree that consumers do not
install. So the *data model* moved here and the battery imports it: the direction is
``tests -> agentteams``, never the reverse.

**No probe module is defaulted.** :func:`load_probe_module` takes a dotted path from the
caller. A downstream team that installs this package has no
``tests.constitutional_redteam_battery``, and a red-team command whose default target does not
exist would hand every consumer a permanently red check. Absent probe module is a
stated-population zero (see :mod:`agentteams.redteam.report`); a *named* module that fails to
import is a harness failure and raises.

**No ``except`` clauses in this package.** A probe that raises is a probe defect. Recording it
as a ``PROBE-ERROR`` row would let a battery of broken probes finish and report all-clear —
the exact inversion this audit exists to prevent — and CH-24 ratchets against adding one. The
traceback propagates; the shell driver classifies a traceback death as *harness broken*, which
is a distinct outcome from *no findings*.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable

#: Outcome classes, ordered strongest-to-weakest defence.
DEFENDED = "DEFENDED"
PARTIAL = "PARTIAL"          # control fired, but with degraded / mis-framed signal
EXPLOITED = "EXPLOITED"
DOC_LIMIT = "DOCUMENTED-LIMIT"
OUT_OF_TIER = "OUT-OF-TIER"

OUTCOMES: frozenset[str] = frozenset({DEFENDED, PARTIAL, EXPLOITED, DOC_LIMIT, OUT_OF_TIER})

#: A probe whose ``name`` starts with this is a control, not an attack. Controls establish that
#: the mechanism under test is engaged at all; an attack battery whose controls fail is
#: measuring a broken harness and would report all-clear either way.
CONTROL_PREFIX = "CONTROL:"

#: Ledger locations, relative to the repository (or generated-team) root.
ACCEPTED_WEAKNESSES_REL = "references/redteam-accepted-weaknesses.csv"
UNCONTROLLED_PROBES_REL = "references/redteam-uncontrolled-probes.csv"
VERIFIERS_REL = "references/redteam-verifiers.csv"
CALLPATH_PARITY_REL = "references/redteam-callpath-parity.csv"
RESOLUTION_EXEMPTIONS_REL = "references/redteam-canonical-resolution-exemptions.csv"
PROBE_BASELINE_REL = "references/redteam-probe-baseline.json"

#: Minimum length of a stated reason in any ledger. A one-word excuse is how an exemption
#: ledger stops describing the system and becomes a place failures go to die.
MIN_REASON_CHARS = 40

#: The enumerators a count may be computed over. A red-team report said *"0 agents on the
#: ignored key across the fleet"*; the statement was arithmetically true and its denominator
#: was 15 repositories out of 85 workspaces, hiding 719 exposed agents. Every count this
#: package emits — and every count it checks — must name which of these produced its
#: population, so the denominator is never the reader's problem to reconstruct.
CANONICAL_POPULATION_SOURCES: frozenset[str] = frozenset({
    "fleet.discover_workspaces",     # every workspace under a parent, not `for d in */`
    "registry.run_probes",           # every registered probe, not the ones that scored
    "corpus.load_corpus",            # every judgment-layer payload
    "integrity.ENFORCEMENT_MODULES",  # every module a control depends on
    "registry.load_accepted_weaknesses",
})


@dataclass(frozen=True)
class SelfAuditFinding:
    """One phase-6 defect: something wrong with the *red team*, not with the target.

    Lives here rather than in :mod:`agentteams.redteam.selfaudit` so both check modules can
    construct one without importing the orchestrator that imports them.

    Attributes:
        check: The failure mode's id — ``F-1``..``F-6``.
        subject: What the finding is about (a symbol, a ledger row, a file).
        detail: What is wrong, in one sentence.
        remedy: The concrete next action, including the exact command where one exists. A
            finding whose remedy is "investigate" gets deferred; one that names a command
            gets done.
    """

    check: str
    subject: str
    detail: str
    remedy: str

    def render(self) -> str:
        """Return the finding as a single line."""
        return f"[{self.check}] {self.subject}: {self.detail} → {self.remedy}"


@dataclass
class Probe:
    """One measured attack (or control) and its outcome.

    Attributes:
        pid: Stable probe id, e.g. ``"A1"``. The key every ledger joins on.
        name: Human-readable description. Prefixed ``CONTROL:`` for control probes.
        article: The constitutional article under test (``C-1``..``C-5``).
        tier: ``T0`` (read content only), ``T1`` (in-repo agent), ``T2`` (operator shell).
        outcome: One of :data:`OUTCOMES`.
        expected_if_sound: What a sound control would have produced.
        evidence: The observed behaviour, verbatim where possible.
        control: The paired control probe's ``pid``, or ``None`` for a control probe.
        notes: Free-text context.
    """

    pid: str
    name: str
    article: str
    tier: str
    outcome: str
    expected_if_sound: str
    evidence: str
    control: str | None = None
    notes: str = ""

    @property
    def is_control(self) -> bool:
        """True when this probe establishes that the mechanism is engaged."""
        return self.name.startswith(CONTROL_PREFIX)


@dataclass
class ProbeCollector:
    """Accumulates :class:`Probe` results for one battery run."""

    results: list[Probe] = field(default_factory=list)

    def record(self, **kwargs: object) -> None:
        """Append one probe result.

        Args:
            **kwargs: Field values for :class:`Probe`.

        Raises:
            ValueError: If ``outcome`` is not a member of :data:`OUTCOMES`. A typo'd outcome
                would silently escape both the "no EXPLOITED" assertion and the accepted-
                weakness ledger, because it matches neither.
        """
        probe = Probe(**kwargs)  # type: ignore[arg-type]
        if probe.outcome not in OUTCOMES:
            raise ValueError(
                f"probe {probe.pid} recorded unknown outcome {probe.outcome!r}; "
                f"valid outcomes are {sorted(OUTCOMES)}"
            )
        self.results.append(probe)

    def clear(self) -> None:
        """Drop accumulated results so the battery can be re-run in one process."""
        self.results.clear()


#: The module-level collector the battery writes into. Kept as a module global rather than
#: threaded through every probe signature because the battery's 38 probes already close over
#: it, and rewriting all of them to take a collector would be churn with no safety gain.
DEFAULT_COLLECTOR = ProbeCollector()
RESULTS: list[Probe] = DEFAULT_COLLECTOR.results


def record(**kwargs: object) -> None:
    """Record a probe result into :data:`DEFAULT_COLLECTOR`."""
    DEFAULT_COLLECTOR.record(**kwargs)


# ---------------------------------------------------------------------------
# probe module loading
# ---------------------------------------------------------------------------

def load_probe_module(dotted: str) -> ModuleType:
    """Import a probe module by dotted path.

    Args:
        dotted: Import path of a module exposing a ``PROBES`` sequence of zero-argument
            callables and a ``RESULTS`` list.

    Returns:
        The imported module.

    Raises:
        ImportError: If the module cannot be imported. Deliberately not caught anywhere in
            this package: a named-but-missing probe module is a broken harness, and a broken
            harness must not be reportable as "no findings".
        AttributeError: If the module exposes no ``PROBES``.
    """
    module = importlib.import_module(dotted)
    if not hasattr(module, "PROBES"):
        raise AttributeError(
            f"probe module {dotted!r} exposes no PROBES sequence; it cannot be run"
        )
    return module


def run_probes(module: ModuleType) -> list[Probe]:
    """Run every probe in ``module`` and return the recorded results.

    Args:
        module: A module loaded by :func:`load_probe_module`.

    Returns:
        The probes recorded during this run, in registration order.

    Raises:
        RuntimeError: If a probe returned without recording a result. Anti-vacuity: every
            assertion downstream says nothing about a probe that never scored.
    """
    probes: list[Callable[[], None]] = list(module.PROBES)
    results: list[Probe] = module.RESULTS
    results.clear()
    for probe in probes:
        probe()
    if len(results) != len(probes):
        raise RuntimeError(
            f"{len(probes)} probes defined but {len(results)} results recorded — a probe "
            f"returned without recording, so its attack was never scored"
        )
    return list(results)


# ---------------------------------------------------------------------------
# registration validation
# ---------------------------------------------------------------------------

def validate_registration(
    results: list[Probe], *, uncontrolled_exemptions: dict[str, str]
) -> list[str]:
    """Check that every attack probe declares a control, or is exempt on the record.

    A lone "the attack worked" observation cannot distinguish a breached control from a
    control that was never engaged. The handoff states the rule as *a probe without a declared
    control is a registration error, not a warning*; the exemption ledger is what keeps it an
    error rather than a permanent red, while still costing a diff.

    Args:
        results: Probe results from :func:`run_probes`.
        uncontrolled_exemptions: ``pid -> reason`` from
            :func:`load_uncontrolled_exemptions`.

    Returns:
        One message per registration defect; empty when the battery is well-formed.
    """
    by_pid = {p.pid: p for p in results}
    problems: list[str] = []
    for probe in results:
        if probe.is_control:
            if probe.control:
                problems.append(
                    f"{probe.pid}: a control probe must not itself name a control "
                    f"(names {probe.control!r})"
                )
            continue
        if not probe.control:
            reason = uncontrolled_exemptions.get(probe.pid, "")
            if len(reason) < MIN_REASON_CHARS:
                problems.append(
                    f"{probe.pid}: attack probe declares no control and has no exemption of "
                    f"at least {MIN_REASON_CHARS} chars in {UNCONTROLLED_PROBES_REL}"
                )
            continue
        if probe.control not in by_pid:
            problems.append(
                f"{probe.pid}: names control {probe.control!r}, which is not a registered probe"
            )
        elif not by_pid[probe.control].is_control:
            problems.append(
                f"{probe.pid}: names {probe.control!r} as its control, but that probe is not a "
                f"control (its name does not start with {CONTROL_PREFIX!r})"
            )
    for pid in sorted(uncontrolled_exemptions):
        if pid not in by_pid:
            problems.append(
                f"{pid}: exempted from control pairing in {UNCONTROLLED_PROBES_REL}, but no "
                f"such probe is registered — stale exemption"
            )
        elif by_pid[pid].control:
            problems.append(
                f"{pid}: now declares control {by_pid[pid].control!r} and no longer needs its "
                f"exemption in {UNCONTROLLED_PROBES_REL}"
            )
    return problems


def failed_controls(results: list[Probe]) -> list[str]:
    """Return the pids of control probes that did not DEFEND.

    A battery whose controls fail is measuring a broken harness, not a secure system — and it
    would report all-clear either way. This is the condition that maps to the *harness broken*
    exit class rather than to *findings*.
    """
    return [p.pid for p in results if p.is_control and p.outcome != DEFENDED]


# ---------------------------------------------------------------------------
# evidence digesting (F-5 support)
# ---------------------------------------------------------------------------

#: Substitutions applied before digesting a probe's evidence. Every one of these varies run to
#: run without the *behaviour* changing. Digesting raw evidence would flag all 38 probes every
#: night, and a check that fires constantly gets muted — which is strictly worse than not
#: having it. Each pattern is here because the current battery emits it.
_EVIDENCE_NOISE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/(?:private/)?(?:tmp|var/folders)/[^\s'\")]+"), "<TMPPATH>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    (re.compile(r"\b[0-9a-f]{32,64}\b"), "<HEX>"),
    (re.compile(r"\bctre-[A-Za-z0-9_]+"), "<TMPNAME>"),
    (re.compile(r"\s+"), " "),
)


#: A ``set``/``frozenset`` repr. Their iteration order depends on per-process string hash
#: randomisation, so a probe recording ``frozenset({'edit', 'read'})`` produces a different
#: string on the next run with the same behaviour. Found by F-5 itself on probe C2 the first
#: time the check ran against a baseline seeded in a different process — which is a reasonable
#: advertisement for writing the check.
_SET_REPR_RE = re.compile(r"(frozenset\(\{|\{)([^{}]*?)(\}\)|\})")


def _sort_set_reprs(text: str) -> str:
    """Sort the elements inside any set or frozenset repr so ordering is stable."""
    def _sorted(match: re.Match[str]) -> str:
        body = match.group(2)
        if not body.strip() or ":" in body:  # empty, or a dict repr — leave alone
            return match.group(0)
        items = sorted(part.strip() for part in body.split(","))
        return f"{match.group(1)}{', '.join(items)}{match.group(3)}"

    return _SET_REPR_RE.sub(_sorted, text)


def normalise_evidence(evidence: str) -> str:
    """Strip run-to-run noise from a probe's evidence string.

    Temp paths, timestamps, dates, hex digests and set-iteration order change on every run
    while the behaviour they describe does not. Normalising before digesting is what makes the
    F-5 intent check usable instead of permanently noisy — and a permanently noisy check gets
    muted, which is strictly worse than not having one.

    Args:
        evidence: Raw evidence text as recorded by a probe.

    Returns:
        The normalised text, with volatile spans replaced by stable placeholders.
    """
    text = _sort_set_reprs(evidence)
    for pattern, replacement in _EVIDENCE_NOISE:
        text = pattern.sub(replacement, text)
    return text.strip()


def evidence_digest(probe: Probe) -> str:
    """Return a stable SHA-256 over a probe's normalised evidence.

    Args:
        probe: The probe result to digest.

    Returns:
        A 16-character hex prefix — long enough that a collision is not a practical concern
        for a few dozen probes, short enough to read in a diff.
    """
    normalised = normalise_evidence(probe.evidence)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ledger readers
# ---------------------------------------------------------------------------

def read_csv_ledger(path: Path, *, required_columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Read a CSV ledger and return its rows as dicts.

    Args:
        path: Absolute path to the ledger.
        required_columns: Columns that must be present in the header.

    Returns:
        One dict per data row, values stripped. An absent file returns ``[]`` — an
        unconfigured project is not a failing one, and the checks that *require* a ledger say
        so themselves rather than relying on this to raise.

    Raises:
        ValueError: If the file exists but its header is missing a required column. A ledger
            with the wrong shape reads as empty otherwise, which is a silent pass.
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        missing = [column for column in required_columns if column not in header]
        if missing:
            raise ValueError(
                f"{path}: ledger is missing required column(s) {missing}; header was {header}"
            )
        return [
            {key: (value or "").strip() for key, value in row.items() if key is not None}
            for row in reader
        ]


def load_accepted_weaknesses(root: Path) -> dict[str, tuple[str, str]]:
    """Load the F-6 ledger: probes allowed to return something other than DEFENDED.

    This is the single source of truth. ``tests/test_constitutional_redteam.py`` reads it too,
    so a weakness cannot be accepted in the suite and unaccepted in the daily audit.

    Args:
        root: Repository (or generated-team) root.

    Returns:
        ``pid -> (outcome, reason)``.
    """
    rows = read_csv_ledger(
        root / ACCEPTED_WEAKNESSES_REL, required_columns=("pid", "outcome", "reason")
    )
    return {row["pid"]: (row["outcome"], row["reason"]) for row in rows}


def load_uncontrolled_exemptions(root: Path) -> dict[str, str]:
    """Load the ledger of attack probes deliberately registered without a control.

    Args:
        root: Repository (or generated-team) root.

    Returns:
        ``pid -> reason``.
    """
    rows = read_csv_ledger(root / UNCONTROLLED_PROBES_REL, required_columns=("pid", "reason"))
    return {row["pid"]: row["reason"] for row in rows}


__all__ = [
    "ACCEPTED_WEAKNESSES_REL",
    "CALLPATH_PARITY_REL",
    "CONTROL_PREFIX",
    "DEFAULT_COLLECTOR",
    "DEFENDED",
    "DOC_LIMIT",
    "EXPLOITED",
    "MIN_REASON_CHARS",
    "OUTCOMES",
    "OUT_OF_TIER",
    "PARTIAL",
    "PROBE_BASELINE_REL",
    "Probe",
    "ProbeCollector",
    "RESOLUTION_EXEMPTIONS_REL",
    "RESULTS",
    "SelfAuditFinding",
    "UNCONTROLLED_PROBES_REL",
    "VERIFIERS_REL",
    "evidence_digest",
    "failed_controls",
    "load_accepted_weaknesses",
    "load_probe_module",
    "load_uncontrolled_exemptions",
    "normalise_evidence",
    "read_csv_ledger",
    "record",
    "run_probes",
    "validate_registration",
]
