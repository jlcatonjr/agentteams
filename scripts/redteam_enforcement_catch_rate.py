#!/usr/bin/env python3
"""redteam_enforcement_catch_rate.py — publish the enforcement meta-evaluation catch-rate.

**Why (architecture-quality feature: complete + surface the enforcement meta-evaluation).** The
red-team engine already runs a strong planted-defect meta-evaluation: `check_verifier_sensitivity`
(F-1) requires every *ledgered* verifier to carry a sensitivity test and a negative control, and
`selfaudit.py` runs the "every check must be able to fail" discipline. What was missing is a
**published catch-rate** — a single number that makes a green enforcement run distinguishable from
an *un-exercised* one. A guard nobody has proven can catch a plant is not a guard.

This reporter is READ-ONLY and lives outside the enforcement modules (so running it never perturbs
the integrity manifest). It answers: of the modules the tool treats as enforcement controls
(`integrity.ENFORCEMENT_MODULES`), how many have a ledgered verifier with BOTH a sensitivity test
and a negative control? It names the uncovered ones — the honest gap — rather than reporting only a
reassuring numerator.

Run: ``python scripts/redteam_enforcement_catch_rate.py`` (``--json`` for machine output). Exit 0
always (this reports; it does not gate — gating stays in the constitutional battery).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agentteams import integrity  # noqa: E402

LEDGER = REPO_ROOT / "references" / "redteam-verifiers.csv"


def analyze(ledger: Path = LEDGER) -> dict:
    """Return the enforcement-module catch-rate report."""
    enforcement = sorted(integrity.ENFORCEMENT_MODULES)
    covered_modules: set[str] = set()
    if ledger.exists():
        for row in csv.DictReader(ledger.open(encoding="utf-8")):
            if (
                row.get("kind") == "verifier"
                and row.get("sensitivity_test")
                and row.get("negative_control_test")
            ):
                covered_modules.add(row["module"])
    covered = [m for m in enforcement if m in covered_modules]
    uncovered = [m for m in enforcement if m not in covered_modules]
    n = len(enforcement)
    return {
        "enforcement_modules": n,
        "covered": covered,
        "uncovered": uncovered,
        "catch_rate": round(len(covered) / n, 3) if n else 0.0,
    }


def render(report: dict) -> str:
    n = report["enforcement_modules"]
    c = len(report["covered"])
    lines = [
        f"Enforcement meta-evaluation catch-rate: {c}/{n} "
        f"({report['catch_rate']:.0%}) of enforcement modules have a ledgered verifier with a "
        "sensitivity test AND a negative control.",
    ]
    if report["uncovered"]:
        lines.append("UNCOVERED (no planted-defect verifier ledgered — the honest gap):")
        lines.extend(f"  - {m}" for m in report["uncovered"])
    else:
        lines.append("All enforcement modules are covered.")
    lines.append(
        "A covered module has been proven able to catch a planted defect; an uncovered one has not. "
        "This is a report, not a gate — closing the gap means adding those modules to "
        "references/redteam-verifiers.csv with real sensitivity + negative-control tests."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish the enforcement meta-evaluation catch-rate.")
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = analyze(args.ledger)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
