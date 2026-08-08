"""feature_audit.py — verify that documented features still function.

The inventory (``docs_src/api-reference/feature-inventory.md``) enumerates what this
project claims to do. Until 2026-08-07 nothing checked whether any of it still worked,
and the inventory said so itself: deriving its counts "requires a machine-readable
feature set, which the module does not yet carry."

``references/feature-registry.csv`` is that set. This module reads it, resolves the
tests each row names, runs the requested tiers, and reports. It **measures and reports;
it never remediates** — an unattended job that writes fixes is a larger risk than the
one it closes.

Three design constraints, each of which exists because skipping it produces a check
that cannot fail:

**1. ``proven`` is self-enforcing.** A row counts as proven only when it names *both* a
``proof_test`` and a ``negative_control``, and they differ. A proof with no negative
control demonstrates that a test runs, not that it can fail — the tautology
``references/redteam-verifiers.csv`` already pairs every verifier against. Nothing can
be placeholdered into ``proven``: :func:`load_registry` downgrades it.

**2. A tier that executes zero proofs is a finding, not a pass.** An all-``UNPROVEN``
registry would otherwise satisfy every structural check and exit clean — success
declared, nothing proven. See :func:`classify`.

**3. Unreachable is a value, not an exception.** ``agentteams/research/`` is
degrade-don't-raise by policy (``references/retrieval-transport-policy.md``), so a live
probe that merely asserts "no error" *passes on an empty degraded response*. Live proofs
must assert positive content, and a genuinely unreachable dependency returns
:data:`UNREACHABLE` — reported separately, never silently folded into pass or fail, and
never routed to harness-broken (a network blip is not a broken harness).

This module deliberately does **not** copy ``agentteams/redteam/``'s stated "no except
clauses" contract: that package does not honour it either (``findings_ledger.py`` has
handlers while ``__init__.py`` claims none), and constraint 3 requires catching a
reachability failure to turn it into a value. Every handler here is narrow and named,
per CH-24.
"""

from __future__ import annotations

import csv
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REGISTRY_REL_PATH = "references/feature-registry.csv"
INVENTORY_REL_PATH = "docs_src/api-reference/feature-inventory.md"

#: Environment override so tests can point the audit at a fixture registry instead of
#: mutating the tracked one. Mirrors ``REDTEAM_PROBE_MODULE`` / ``REDTEAM_REPORT_DIR``.
REGISTRY_ENV_VAR = "FEATURE_REGISTRY"

REQUIRED_COLUMNS = (
    "feature_id", "category", "name", "surface", "kind", "tier",
    "external_dep", "proof_test", "negative_control", "status", "notes",
)

PROVEN = "proven"
UNPROVEN = "UNPROVEN"
WAIVED = "waived"
VALID_STATUS = frozenset({PROVEN, UNPROVEN, WAIVED})

KIND_FEATURE = "feature"
KIND_NOT_PROVABLE = "not-provable"
VALID_KIND = frozenset({KIND_FEATURE, KIND_NOT_PROVABLE, WAIVED})

TIER_UNIT = "unit"
TIER_E2E = "e2e"
TIER_LIVE = "live"
VALID_TIER = frozenset({TIER_UNIT, TIER_E2E, TIER_LIVE})

#: Outcome of a single proof.
PASS = "PASS"
FAIL = "FAIL"
UNREACHABLE = "UNREACHABLE"

# Exit classification (the shell driver turns these into process exit codes).
CLEAN = 0
FINDINGS = 1
HARNESS_BROKEN = 2


class RegistryError(RuntimeError):
    """The registry is malformed. Routes to HARNESS_BROKEN, never to FINDINGS.

    A registry we cannot read is an indeterminate result, and indeterminate is not a
    pass. Raised rather than returned so a caller cannot accidentally treat it as an
    empty finding list.
    """


@dataclass
class FeatureRow:
    """One registry row, after normalisation."""

    feature_id: str
    category: str
    name: str
    surface: str
    kind: str
    tier: str
    external_dep: str
    proof_test: str
    negative_control: str
    status: str
    notes: str

    @property
    def is_provable(self) -> bool:
        """Whether this row is expected to carry a proof at all."""
        return self.kind == KIND_FEATURE

    @property
    def claims_proof(self) -> bool:
        """Whether the row names both halves of a non-tautological proof."""
        return bool(
            self.proof_test
            and self.negative_control
            and self.proof_test != self.negative_control
        )


@dataclass
class ProofResult:
    """The outcome of executing one row's proof."""

    feature_id: str
    outcome: str
    detail: str
    #: The tier this proof belongs to. Needed so :func:`classify` can detect an EMPTY
    #: tier rather than only an empty run — see the per-tier check there.
    #:
    #: REQUIRED, with no default, deliberately. A default of ``""`` would let a dropped
    #: stamp silently make every tier read empty — turning every run into FINDINGS while
    #: the suite stayed green. As a required field the same mistake is a TypeError at
    #: construction.
    tier: str


@dataclass
class AuditReport:
    """Everything one audit run learned."""

    rows: list[FeatureRow] = field(default_factory=list)
    results: list[ProofResult] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    tiers_run: tuple[str, ...] = ()

    @property
    def executed(self) -> int:
        return len(self.results)

    @property
    def failed(self) -> list[ProofResult]:
        return [r for r in self.results if r.outcome == FAIL]

    @property
    def unreachable(self) -> list[ProofResult]:
        return [r for r in self.results if r.outcome == UNREACHABLE]

    @property
    def unproven(self) -> list[FeatureRow]:
        return [r for r in self.rows if r.is_provable and r.status != PROVEN]


def registry_path(repo_root: Path) -> Path:
    """Resolve the registry path, honouring :data:`REGISTRY_ENV_VAR`.

    Args:
        repo_root: Repository root.

    Returns:
        Path to the registry CSV.
    """
    override = os.environ.get(REGISTRY_ENV_VAR)
    return Path(override) if override else repo_root / REGISTRY_REL_PATH


def load_registry(path: Path) -> list[FeatureRow]:
    """Read and normalise the registry.

    Downgrades any row to ``UNPROVEN`` when it does not name both a ``proof_test`` and a
    distinct ``negative_control``. This is the enforcement point for design constraint 1:
    a row cannot claim ``proven`` merely by writing the word.

    Args:
        path: Registry CSV path.

    Returns:
        Normalised rows, in file order.

    Raises:
        RegistryError: The file is missing, has the wrong columns, contains a duplicate
            ``feature_id``, or carries an unknown enum value. All route to
            HARNESS_BROKEN.
    """
    if not path.is_file():
        raise RegistryError(f"registry not found: {path}")
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None or tuple(reader.fieldnames) != REQUIRED_COLUMNS:
        raise RegistryError(
            f"registry columns are {reader.fieldnames!r}, expected {list(REQUIRED_COLUMNS)!r}"
        )
    rows: list[FeatureRow] = []
    seen: set[str] = set()
    for raw in reader:
        fid = (raw.get("feature_id") or "").strip()
        if not fid:
            raise RegistryError("registry has a row with an empty feature_id")
        if fid in seen:
            raise RegistryError(f"duplicate feature_id: {fid}")
        seen.add(fid)
        row = FeatureRow(**{c: (raw.get(c) or "").strip() for c in REQUIRED_COLUMNS})
        if row.kind not in VALID_KIND:
            raise RegistryError(f"{fid}: unknown kind {row.kind!r}")
        if row.tier not in VALID_TIER:
            raise RegistryError(f"{fid}: unknown tier {row.tier!r}")
        if row.status not in VALID_STATUS:
            raise RegistryError(f"{fid}: unknown status {row.status!r}")
        # Constraint 1, enforced rather than trusted.
        if row.status == PROVEN and not row.claims_proof:
            row.status = UNPROVEN
            row.notes = (row.notes + " | downgraded: proven requires a proof_test and a "
                                     "DISTINCT negative_control").strip(" |")
        rows.append(row)
    if not rows:
        raise RegistryError("registry is empty")
    return rows


def parse_inventory(path: Path) -> list[tuple[str, str]]:
    """Extract ``(category, name)`` for every numbered feature in the prose inventory.

    The inventory body is already machine-parseable — each feature is ``<n>. **Name**``
    under a ``## Category`` — which is why the registry is *derived* from it rather than
    hand-written beside it. A second hand-maintained list would rot the same way the
    summary table did.

    Args:
        path: Inventory markdown path.

    Returns:
        ``(category, name)`` pairs in document order.

    Raises:
        RegistryError: The inventory is missing.
    """
    import re

    if not path.is_file():
        raise RegistryError(f"inventory not found: {path}")
    skip = {"Summary", "Artifact Selection Matrix", "Getting Started"}
    category: str | None = None
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        head = re.match(r"^## (.+)", line)
        if head:
            name = head.group(1).strip()
            category = None if name in skip else name
        item = re.match(r"^(\d+)\. \*\*(.+?)\*\*", line)
        if item and category:
            out.append((category, item.group(2).strip()))
    return out


def check_parity(rows: list[FeatureRow], inventory: list[tuple[str, str]]) -> list[str]:
    """Bind the registry to the inventory **per feature**, not per category.

    Revision 1 of the plan checked category-level parity; a 14-row registry would have
    passed it while 132 features went unregistered. The rot this is credited with
    preventing requires per-feature binding.

    Args:
        rows: Registry rows.
        inventory: Parsed inventory pairs.

    Returns:
        Human-readable drift findings; empty when the two agree.
    """
    findings: list[str] = []
    reg = {(r.category, r.name) for r in rows}
    inv = set(inventory)
    for missing in sorted(inv - reg):
        findings.append(f"inventory feature absent from registry: {missing[0]} / {missing[1]}")
    for orphan in sorted(reg - inv):
        findings.append(f"registry row absent from inventory: {orphan[0]} / {orphan[1]}")
    if len(inventory) != len(inv):
        findings.append(
            f"inventory contains duplicate (category, name) pairs: "
            f"{len(inventory)} items, {len(inv)} distinct"
        )
    return findings


def run_proof(row: FeatureRow, repo_root: Path, timeout: int = 300) -> ProofResult:
    """Execute one row's ``proof_test`` and classify the outcome.

    Args:
        row: The registry row.
        repo_root: Repository root (working directory for the run).
        timeout: Seconds before the proof is treated as unreachable rather than failed.

    Returns:
        A :class:`ProofResult`. A ``live``-tier row whose dependency is unreachable
        returns :data:`UNREACHABLE`, never :data:`FAIL` — an outage in someone else's
        service is not a regression in this one, and must not gate.
    """
    cmd = ["python", "-m", "pytest", row.proof_test, "-q", "--no-header", "-p", "no:randomly"]
    try:
        proc = subprocess.run(
            cmd, cwd=repo_root, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # Constraint 3: a hang on a live dependency is unreachable, not failed.
        if row.tier == TIER_LIVE:
            return ProofResult(row.feature_id, UNREACHABLE, f"timed out after {timeout}s", row.tier)
        return ProofResult(row.feature_id, FAIL, f"timed out after {timeout}s", row.tier)
    if proc.returncode == 0:
        return ProofResult(row.feature_id, PASS, "", row.tier)
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    detail = tail[-1] if tail else f"exit {proc.returncode}"
    if row.tier == TIER_LIVE and _looks_unreachable(proc.stdout + proc.stderr):
        return ProofResult(row.feature_id, UNREACHABLE, detail, row.tier)
    return ProofResult(row.feature_id, FAIL, detail, row.tier)


def _looks_unreachable(output: str) -> str | bool:
    """Whether a live proof's failure is a reachability problem rather than a defect.

    Deliberately narrow. Misclassifying a real regression as "the network was down" is
    the failure mode that would make the live tier worthless, so this matches only
    transport-level signatures and never a plain assertion failure.
    """
    needles = (
        "ConnectionError", "ConnectTimeout", "ReadTimeout", "TimeoutError",
        "Temporary failure in name resolution", "Name or service not known",
        "SSLError", "Max retries exceeded", "HTTP 429", "HTTP 503",
    )
    return any(n in output for n in needles)


def audit(
    repo_root: Path,
    *,
    tiers: tuple[str, ...] = (TIER_UNIT,),
    execute: bool = True,
) -> AuditReport:
    """Run the audit.

    Args:
        repo_root: Repository root.
        tiers: Which tiers to execute proofs for.
        execute: When False, perform structural checks only (parity, resolution) and
            run nothing. Used by CI, where executing the unit tier would duplicate the
            suite that is already running.

    Returns:
        The report.

    Raises:
        RegistryError: The registry or inventory could not be read (HARNESS_BROKEN).
    """
    rows = load_registry(registry_path(repo_root))
    inventory = parse_inventory(repo_root / INVENTORY_REL_PATH)
    report = AuditReport(rows=rows, tiers_run=tiers)
    report.findings.extend(check_parity(rows, inventory))

    if not execute:
        return report

    for row in rows:
        if row.tier not in tiers or not row.claims_proof or row.status != PROVEN:
            continue
        report.results.append(run_proof(row, repo_root))
    return report


def classify(report: AuditReport) -> int:
    """Turn a report into an exit classification.

    Returns:
        :data:`CLEAN`, :data:`FINDINGS`, or :data:`HARNESS_BROKEN`.

    A tier that executed **zero** proofs is :data:`FINDINGS`, not :data:`CLEAN` — design
    constraint 2. Without this an all-``UNPROVEN`` registry passes every structural check
    and exits clean, which is success declared over nothing proven.

    ``UNREACHABLE`` results alone never fail the run; they are reported so an operator
    can see that a live dependency went dark, without a third-party outage turning into
    a red build.
    """
    # Computed FIRST, unconditionally. Early-returning on `report.findings` would let a
    # parity drift mask "the live tier proved nothing" — one finding hiding another, the
    # same shape as the global-vs-per-tier bug this check exists to fix. Parity churn is
    # routine during coverage work, so that masking would fire in practice.
    report.findings.extend(_empty_tier_findings(report))
    if report.findings:
        return FINDINGS
    if report.failed:
        return FINDINGS
    # PER-TIER, not global. The global form (`report.executed == 0`) let a single unit
    # proof mask two entirely empty tiers: `--tiers unit,e2e,live` exited CLEAN while
    # nothing at all probed the external surface that justifies running daily — which is
    # exactly the combination the daily workflow runs. That is "success declared over
    # nothing proven" reproduced inside the check written to prevent it.
    return CLEAN


def _empty_tier_findings(report: AuditReport) -> list[str]:
    """One finding per requested tier that executed no proof.

    A tier is **exempt** only when it has at least one row and every one of them is
    deliberately unprovable (``waived`` or ``kind=not-provable``) — there is genuinely
    nothing to prove, and saying otherwise would make a considered decision look like a
    defect. A tier with **no rows at all** is NOT exempt: being asked to run a tier that
    does not exist is precisely the defect this check was written for.
    """
    out: list[str] = []
    for tier in report.tiers_run:
        if any(r.tier == tier for r in report.results):
            continue
        rows = [r for r in report.rows if r.tier == tier]
        if rows and all(r.status == WAIVED or r.kind == KIND_NOT_PROVABLE for r in rows):
            continue
        out.append(
            f"no proof executed for tier '{tier}' — a tier that proves nothing is a "
            "finding, not a pass"
        )
    return out


def format_report(report: AuditReport) -> str:
    """Render a human-readable summary."""
    provable = [r for r in report.rows if r.is_provable]
    proven = [r for r in provable if r.status == PROVEN]
    lines = [
        "FEATURE AUDIT",
        f"  registry rows      : {len(report.rows)}",
        f"  provable features  : {len(provable)}",
        f"  proven             : {len(proven)}",
        f"  UNPROVEN           : {len(report.unproven)}",
        f"  tiers run          : {', '.join(report.tiers_run) or '(none)'}",
        f"  proofs executed    : {report.executed}",
        f"  failed             : {len(report.failed)}",
        f"  unreachable        : {len(report.unreachable)}",
    ]
    # Coverage is reported unmissably and separately from the verdict. CLEAN answers
    # "did anything we claim to prove stop working?" — NOT "is everything proven?".
    # Conflating them would either make the job permanently red (training operators to
    # ignore it) or let 145 unproven features hide behind a green tick. The ratchet that
    # stops coverage sliding lives in the test suite, where a regression should fail a
    # build; see tests/test_feature_registry.py.
    if provable:
        pct = 100.0 * len(proven) / len(provable)
        lines.append(f"  COVERAGE           : {len(proven)}/{len(provable)} proven ({pct:.1f}%)")
        if pct < 100.0:
            lines.append(
                "  NOTE: a CLEAN verdict means no PROVEN feature regressed. It does not "
                f"mean the other {len(report.unproven)} work — nothing checks them yet."
            )
    for f in report.findings:
        lines.append(f"  FINDING: {f}")
    for r in report.failed:
        lines.append(f"  FAIL   : {r.feature_id} — {r.detail}")
    for r in report.unreachable:
        lines.append(f"  UNREACH: {r.feature_id} — {r.detail}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    """``python -m agentteams.feature_audit`` entrypoint.

    Returns the classification directly as the process exit code so the shell driver can
    distinguish clean / findings / harness-broken without parsing text. A
    :class:`RegistryError` is caught here — and only here — to turn it into exit 2 rather
    than a traceback the driver would have to classify as "unclassified death".
    """
    import argparse

    parser = argparse.ArgumentParser(prog="agentteams.feature_audit")
    parser.add_argument("--tiers", default=TIER_UNIT,
                        help="comma-separated tiers to execute (unit,e2e,live)")
    parser.add_argument("--structural-only", action="store_true",
                        help="registry/inventory checks only; execute no proofs")
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)

    root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parents[1]
    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    unknown = [t for t in tiers if t not in VALID_TIER]
    if unknown:
        print(f"unknown tier(s): {unknown}", flush=True)
        return HARNESS_BROKEN
    try:
        report = audit(root, tiers=() if args.structural_only else tiers,
                       execute=not args.structural_only)
    except RegistryError as exc:
        print(f"HARNESS BROKEN: {exc}", flush=True)
        return HARNESS_BROKEN
    code = classify(report)
    print(format_report(report), flush=True)
    return code


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(_main())
