"""coverage.py — diff the probe corpus's tagged techniques against an external taxonomy.

Improvement item I1 (references/plans/redteam-osint-ost-benchmark-improvements.csv):
the corpus is entirely hand-authored, reactive to internal incidents, with no mechanism
to notice a newly published AI-adversary technique the corpus doesn't yet probe. This
reads :data:`agentteams.redteam.registry.Probe.external_refs` — populated only where a
defensible mapping exists (see the battery's inline backfill) — against the static
taxonomy snapshot in ``references/redteam-external-taxonomy.json``, and reports which
taxonomy entries have no probe tagged against them yet.

Deliberately NOT wired into the 7-phase cycle (``cycle.py``) or the standing cron: this
is a corpus-maintenance task run manually on a quarterly cadence (per the taxonomy
file's own ``_meta.cadence``), not a gating check — a probe that has no obvious mapping
is not a defect, and an uncovered taxonomy entry is a candidate for future work, not a
finding. Run via ``python -m agentteams.redteam.coverage``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TAXONOMY_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "references"
    / "redteam-external-taxonomy.json"
)


@dataclass
class CoverageReport:
    """Result of diffing tagged probes against the taxonomy snapshot."""

    tagged_ids: frozenset[str]
    covered: list[tuple[str, str]]
    uncovered: list[tuple[str, str]]

    def render(self) -> str:
        """Return a human-readable summary."""
        lines = [
            f"Taxonomy coverage: {len(self.covered)}/{len(self.covered) + len(self.uncovered)} "
            "entries have at least one tagged probe.",
            "",
        ]
        if self.uncovered:
            lines.append("Uncovered (candidates for a new probe or a backfill tag):")
            lines.extend(f"  - {tid}: {name}" for tid, name in self.uncovered)
        else:
            lines.append("Every taxonomy entry has at least one tagged probe.")
        return "\n".join(lines)


@dataclass
class DensityReport:
    """Result of an attacks-per-taxonomy-leaf density check (F2, deferred-plan item 3).

    Coverage asks whether each taxonomy leaf is touched by *any* attack; density asks whether it is
    touched by *enough* — a leaf exercised by a single payload is a thin, brittle measurement of that
    surface. Like the coverage check this REPORTS, it does not gate: a thin leaf is a candidate for
    more authoring, not a defect (mirrors this module's stated stance).
    """

    min_per_leaf: int
    per_leaf: dict[str, int]
    thin: list[tuple[str, int]]  # (leaf, count) for touched leaves below the threshold

    def render(self) -> str:
        """Return a human-readable density summary."""
        touched = len(self.per_leaf)
        lines = [
            f"Attack density (min {self.min_per_leaf}/leaf): {touched} taxonomy leaf/leaves "
            f"exercised; {len(self.thin)} below threshold.",
            "",
        ]
        if self.thin:
            lines.append(f"Thin (under {self.min_per_leaf} attacks — candidates for more authoring):")
            lines.extend(f"  - {leaf}: {count}" for leaf, count in self.thin)
        else:
            lines.append(f"Every exercised leaf has at least {self.min_per_leaf} attacks.")
        return "\n".join(lines)


def compute_density(
    tags_per_attack: list[tuple[str, ...]], min_per_leaf: int = 2
) -> DensityReport:
    """Count attacks per taxonomy leaf and flag touched leaves below ``min_per_leaf``.

    Corpus-agnostic: ``tags_per_attack`` is each attack's taxonomy tags (e.g. a payload's
    ``owasp_llm_2026``/``mitre_atlas`` values). Only *touched* leaves are reported — an entirely
    untouched leaf is a *coverage* gap (see :func:`compute_coverage`), not a *density* one.

    Args:
        tags_per_attack: Each attack's taxonomy-leaf tags (may repeat leaves across attacks).
        min_per_leaf: The minimum attacks-per-leaf below which a touched leaf is "thin".

    Returns:
        A :class:`DensityReport` with per-leaf counts and the thin leaves.
    """
    per_leaf: dict[str, int] = {}
    for tags in tags_per_attack:
        for leaf in tags:
            per_leaf[leaf] = per_leaf.get(leaf, 0) + 1
    thin = sorted((leaf, count) for leaf, count in per_leaf.items() if count < min_per_leaf)
    return DensityReport(min_per_leaf=min_per_leaf, per_leaf=per_leaf, thin=thin)


def load_taxonomy(path: Path = TAXONOMY_PATH) -> list[tuple[str, str]]:
    """Return ``(id, name)`` pairs for every entry across both taxonomy lists.

    Args:
        path: Path to the taxonomy JSON snapshot.

    Returns:
        Combined MITRE ATLAS tactics and OWASP LLM Top 10 entries, in file order.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[tuple[str, str]] = []
    for key in ("mitre_atlas_tactics", "owasp_llm_top10_2025"):
        entries.extend((e["id"], e["name"]) for e in data.get(key, []))
    return entries


def compute_coverage(
    external_refs_per_probe: list[tuple[str, ...]], taxonomy_path: Path = TAXONOMY_PATH
) -> CoverageReport:
    """Diff the tagged probe references against the taxonomy snapshot.

    Args:
        external_refs_per_probe: Each probe's ``external_refs`` tuple (may be empty).
        taxonomy_path: Path to the taxonomy JSON snapshot.

    Returns:
        A :class:`CoverageReport` splitting taxonomy entries into covered/uncovered.
    """
    tagged: set[str] = set()
    for refs in external_refs_per_probe:
        tagged.update(refs)
    taxonomy = load_taxonomy(taxonomy_path)
    covered = [(tid, name) for tid, name in taxonomy if tid in tagged]
    uncovered = [(tid, name) for tid, name in taxonomy if tid not in tagged]
    return CoverageReport(tagged_ids=frozenset(tagged), covered=covered, uncovered=uncovered)


def _main() -> int:
    """Run every probe in ``tests.constitutional_redteam_battery`` and print the report.

    Mirrors the battery's own ``main()`` collection pattern (module-level ``PROBES`` list,
    each function appending to the shared ``registry.RESULTS`` list via ``record()``) rather
    than assuming an object-oriented collector API this module doesn't have.
    """
    import importlib

    from agentteams.redteam.registry import RESULTS

    try:
        battery = importlib.import_module("tests.constitutional_redteam_battery")
    except ImportError:
        print(
            "No tests.constitutional_redteam_battery module found — this command is "
            "meant to be run from the agentteams repo checkout, not a consumer project."
        )
        return 2
    for probe_fn in battery.PROBES:
        probe_fn()
    refs = [p.external_refs for p in RESULTS]
    report = compute_coverage(refs)
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
