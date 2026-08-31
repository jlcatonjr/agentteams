"""runner.py — phase 1 (attack) and the phase 2 (review) data assembly.

Phase 1 runs the registered probes. Phase 2 turns their outcomes into a reviewable structure.
The two are one module because the boundary between them is a rendering decision, not a
measurement one: :mod:`agentteams.redteam.report` renders what this produces.

**Every count carries its population.** :class:`Count` has no constructor path that omits a
denominator or a ``population_source``, and the source must come from
:data:`agentteams.redteam.registry.CANONICAL_POPULATION_SOURCES`. This is the mechanical form
of the rule the 2026-08-06 session broke: *"0 agents on the ignored key across the fleet"* was
arithmetically true over a denominator of 15 repositories out of 85 workspaces, and the 719
exposed agents it hid surfaced only because the operator asked a follow-up question.

**What this deliberately does not measure**, stated as a population rather than omitted:
the judgment layer. The corpus is loaded and verified daily; running it against live subagents
spawns agents and needs operator authorization, so the daily count is ``0 of N measured``.
Omitting the row would make the report's coverage look complete.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from agentteams import integrity
from agentteams.redteam import corpus as corpus_mod
from agentteams.redteam import registry
from agentteams.redteam.registry import (
    CANONICAL_POPULATION_SOURCES,
    DEFENDED,
    EXPLOITED,
    Probe,
)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Count:
    """One quantitative claim, with the population it was computed over.

    Attributes:
        claim: What is being counted, in words.
        numerator: The measured value.
        denominator: The population size.
        population_source: The canonical enumerator that produced the population. Must be a
            member of :data:`~agentteams.redteam.registry.CANONICAL_POPULATION_SOURCES` — an
            ad-hoc list is exactly how a denominator goes unexamined.
        note: Optional context, e.g. why a numerator is zero.

    Raises:
        ValueError: If ``population_source`` is not canonical, or ``numerator`` exceeds
            ``denominator``. Both are constructed defects rather than measurements.
    """

    claim: str
    numerator: int
    denominator: int
    population_source: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.population_source not in CANONICAL_POPULATION_SOURCES:
            raise ValueError(
                f"count {self.claim!r} names population source {self.population_source!r}, "
                f"which is not canonical; use one of {sorted(CANONICAL_POPULATION_SOURCES)}"
            )
        if self.numerator > self.denominator:
            raise ValueError(
                f"count {self.claim!r} has numerator {self.numerator} > denominator "
                f"{self.denominator}"
            )

    def render(self) -> str:
        """Return the count as a single self-describing line."""
        suffix = f" — {self.note}" if self.note else ""
        return (
            f"{self.numerator} of {self.denominator} {self.claim} "
            f"(population: {self.population_source}){suffix}"
        )


@dataclass
class Findings:
    """The phase 1 + 2 result: what was attacked, what held, and over what population."""

    schema_version: int
    generated_at: str
    probe_module: str | None
    probes: list[Probe] = field(default_factory=list)
    counts: list[Count] = field(default_factory=list)
    registration_problems: list[str] = field(default_factory=list)
    control_failures: list[str] = field(default_factory=list)
    corpus_mismatches: list[str] = field(default_factory=list)
    exploited: list[str] = field(default_factory=list)

    @property
    def harness_is_broken(self) -> bool:
        """True when the instrument, not the target, is the problem.

        A failed control, an ill-formed registration or a corpus whose claims no longer match
        the scanner all mean the same thing: the measurement cannot be trusted in *either*
        direction. Reporting that as "no exploits found" is the most dangerous outcome
        available here, so it gets its own class.
        """
        return bool(self.control_failures or self.registration_problems or self.corpus_mismatches)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable mapping conforming to ``schemas/redteam-findings.schema.json``."""
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "probe_module": self.probe_module,
            "probes": [
                {
                    **asdict(probe),
                    "is_control": probe.is_control,
                    "evidence_digest": registry.evidence_digest(probe),
                }
                for probe in self.probes
            ],
            "counts": [asdict(count) for count in self.counts],
            "registration_problems": self.registration_problems,
            "control_failures": self.control_failures,
            "corpus_mismatches": self.corpus_mismatches,
            "exploited": self.exploited,
        }


def _integrity_coverage(root: Path) -> tuple[int, int, str]:
    """Return (covered, total, note) for the enforcement-integrity manifest."""
    total = len(integrity.ENFORCEMENT_MODULES)
    manifest = root / integrity.MANIFEST_REL_PATH
    if not manifest.exists():
        return 0, total, "no manifest on disk — enforcement modules are unverified"
    findings = integrity.verify(root)
    return total - len(findings), total, ""


def run_attack_phase(
    root: Path, *, probe_module_path: str | None, generated_at: str
) -> Findings:
    """Run phases 1 and 2 and return the reviewable findings.

    Args:
        root: Repository (or generated-team) root.
        probe_module_path: Dotted import path of the probe module, or ``None`` to skip the
            attack phase entirely. There is no default: a consumer of this package has no
            ``tests.constitutional_redteam_battery``, and a command whose default target does
            not exist hands every consumer a permanently red check. ``None`` yields a
            stated-population zero, not an error.
        generated_at: ISO-8601 timestamp supplied by the caller. Taken as a parameter rather
            than read from the clock so a run is reproducible and testable.

    Returns:
        The assembled :class:`Findings`.

    Raises:
        ImportError: If ``probe_module_path`` names a module that cannot be imported. A
            *named* target that is missing is a broken harness; it must not read as clean.
    """
    probes: list[Probe] = []
    registration_problems: list[str] = []
    control_failures: list[str] = []

    if probe_module_path:
        module = registry.load_probe_module(probe_module_path)
        probes = registry.run_probes(module)
        registration_problems = registry.validate_registration(
            probes, uncontrolled_exemptions=registry.load_uncontrolled_exemptions(root)
        )
        control_failures = registry.failed_controls(probes)

    payloads = corpus_mod.load_corpus(root)
    verification = corpus_mod.verify_corpus(payloads) if payloads else None

    attacks = [p for p in probes if not p.is_control]
    # Attack probes only. A *control* returning EXPLOITED is a broken instrument, reported
    # through `control_failures` and the harness-broken exit class; folding it in here would
    # describe a failed control as a live attack and put a numerator above its own population.
    exploited = [p.pid for p in attacks if p.outcome == EXPLOITED]
    accepted = registry.load_accepted_weaknesses(root)
    non_defended = [p for p in attacks if p.outcome != DEFENDED]

    counts: list[Count] = [
        Count(
            claim="attack probes held their control",
            numerator=len([p for p in attacks if p.outcome == DEFENDED]),
            denominator=len(attacks),
            population_source="registry.run_probes",
            note=(
                "no probe module registered" if not probe_module_path else ""
            ),
        ),
        Count(
            # Counted over ATTACK probes only, because that is the denominator on the line.
            # `exploited` below covers every probe including controls — a control returning
            # EXPLOITED is a broken harness, reported by `control_failures`, not a live
            # attack. Mixing the two produced a numerator larger than its own population the
            # first time a control was made to fail, which the Count type refused outright.
            claim="attack probes are live exploits",
            numerator=len([p for p in attacks if p.outcome == EXPLOITED]),
            denominator=len(attacks),
            population_source="registry.run_probes",
        ),
        Count(
            claim="non-DEFENDED probes accepted on the record",
            numerator=len([p for p in non_defended if p.pid in accepted]),
            denominator=len(non_defended),
            population_source="registry.load_accepted_weaknesses",
        ),
        Count(
            claim="judgment-layer payloads measured against live agents",
            numerator=0,
            denominator=verification.total if verification else 0,
            population_source="corpus.load_corpus",
            note=(
                "the live-subagent harness spawns agents and requires explicit operator "
                "authorization (W14); the daily audit measures the deterministic layer only, "
                "so this population is UNMEASURED rather than clean"
            ),
        ),
    ]
    covered, total, note = _integrity_coverage(root)
    counts.append(
        Count(
            claim="enforcement modules matching the integrity manifest",
            numerator=covered,
            denominator=total,
            population_source="integrity.ENFORCEMENT_MODULES",
            note=note,
        )
    )

    return Findings(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        probe_module=probe_module_path,
        probes=probes,
        counts=counts,
        registration_problems=registration_problems,
        control_failures=control_failures,
        corpus_mismatches=list(verification.mismatches) if verification else [],
        exploited=exploited,
    )


__all__ = ["SCHEMA_VERSION", "Count", "Findings", "run_attack_phase"]
