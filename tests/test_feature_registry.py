"""Binding tests for the feature registry and the feature audit.

These are what stop the registry rotting. Without them it becomes a second
hand-maintained list beside `docs_src/api-reference/feature-inventory.md`, whose summary
table drifted to 125/12 against a body of 146/14 without a single test noticing —
because the only check compared the total to *its own column*.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import feature_audit as fa

REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Coverage ratchet.
#
# Operator-maintained constants, deliberately NOT a file the audit writes: a job that
# re-baselines itself silently absorbs the drift it exists to detect (the reasoning
# `.github/workflows/redteam-audit.yml` records for its own probe baseline).
#
# MIN_PROVEN may only ever be raised. MAX_UNPROVEN may only ever be lowered.
# ---------------------------------------------------------------------------
MIN_PROVEN = 6
MAX_UNPROVEN = 145


@pytest.fixture(scope="module")
def rows() -> list[fa.FeatureRow]:
    return fa.load_registry(REPO / fa.REGISTRY_REL_PATH)


@pytest.fixture(scope="module")
def collected_ids() -> set[str]:
    """Every test id pytest can actually collect in this repo."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/"],
        cwd=REPO, capture_output=True, text=True,
    )
    return {line.strip() for line in proc.stdout.splitlines() if "::" in line}


def test_registry_parses_and_has_no_duplicate_ids(rows):
    assert rows, "registry is empty"
    ids = [r.feature_id for r in rows]
    assert len(ids) == len(set(ids))


def test_registry_binds_to_the_inventory_per_feature(rows):
    """Per-FEATURE parity, not per-category.

    A category-level check would pass with 14 rows while 132 features went unregistered.
    """
    inventory = fa.parse_inventory(REPO / fa.INVENTORY_REL_PATH)
    findings = fa.check_parity(rows, inventory)
    assert not findings, "registry/inventory drift:\n" + "\n".join(findings)


def test_every_named_test_id_actually_resolves(rows, collected_ids):
    """A registry pointing at a renamed test is worse than an empty one.

    It reads as coverage and provides none.
    """
    dangling = []
    for r in rows:
        for field_name in ("proof_test", "negative_control"):
            tid = getattr(r, field_name)
            if tid and tid not in collected_ids:
                dangling.append(f"{r.feature_id}.{field_name} -> {tid}")
    assert not dangling, "registry names uncollectible test id(s):\n" + "\n".join(dangling)


def test_proven_rows_carry_a_distinct_negative_control(rows):
    """`proven` is self-enforcing.

    A proof with no negative control shows that a test runs, not that it can fail.
    `load_registry` downgrades such rows, so reaching here with one means the enforcement
    itself broke.
    """
    bad = [r.feature_id for r in rows if r.status == fa.PROVEN and not r.claims_proof]
    assert not bad, f"proven rows without a distinct negative control: {bad}"


def test_coverage_ratchet_has_not_slipped(rows):
    provable = [r for r in rows if r.is_provable]
    proven = [r for r in provable if r.status == fa.PROVEN]
    unproven = [r for r in provable if r.status != fa.PROVEN]
    assert len(proven) >= MIN_PROVEN, (
        f"proven coverage fell to {len(proven)}, below the ratchet floor {MIN_PROVEN}. "
        "Raise coverage or lower the floor deliberately."
    )
    assert len(unproven) <= MAX_UNPROVEN, (
        f"UNPROVEN rose to {len(unproven)}, above the ratchet ceiling {MAX_UNPROVEN}. "
        "A new feature landed without a proof."
    )


def test_the_summary_table_is_generated_from_the_registry(rows):
    """The summary table must agree with the registry, per category and in total.

    It drifted to 125/12 against a body of 146/14 and nothing caught it, because the
    existing check compared the total only to its own column.
    """
    table = (REPO / "docs_src/assets/feature-summary-table.md").read_text(encoding="utf-8")
    from collections import Counter

    counts = Counter(r.category for r in rows)
    missing = [c for c in counts if f"**{c}**" not in table]
    assert not missing, f"summary table omits categor(ies) present in the registry: {missing}"
    assert f"**Total:** {len(rows)} documented features across {len(counts)}" in table, (
        f"summary table total must read {len(rows)} features across {len(counts)} areas"
    )


# ---------------------------------------------------------------------------
# Harness-broken proofs.
#
# A driver whose error path has never fired is not known to work. Each case uses a
# fixture registry via the FEATURE_REGISTRY override rather than mutating the tracked
# file — a mutate-then-revert has no failure path if the run dies mid-test, and opens a
# window where a concurrent run reads a corrupt registry.
# ---------------------------------------------------------------------------

def _write(path: Path, header: str, body: str = "") -> Path:
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path


GOOD_HEADER = ",".join(fa.REQUIRED_COLUMNS)


@pytest.mark.parametrize(
    "name,header,body",
    [
        ("wrong_columns", "a,b,c", "1,2,3\n"),
        ("empty_registry", GOOD_HEADER, ""),
        ("duplicate_id", GOOD_HEADER,
         "F-1,C,N,,feature,unit,none,,,UNPROVEN,\nF-1,C,M,,feature,unit,none,,,UNPROVEN,\n"),
        ("unknown_tier", GOOD_HEADER, "F-1,C,N,,feature,weekly,none,,,UNPROVEN,\n"),
        ("unknown_status", GOOD_HEADER, "F-1,C,N,,feature,unit,none,,,maybe,\n"),
        ("empty_feature_id", GOOD_HEADER, ",C,N,,feature,unit,none,,,UNPROVEN,\n"),
    ],
)
def test_a_malformed_registry_is_harness_broken_not_a_pass(tmp_path, name, header, body):
    """Every malformed shape must reach exit 2, never 0 and never 1.

    Indeterminate is not a pass, and a registry we cannot read is indeterminate.
    """
    reg = _write(tmp_path / f"{name}.csv", header, body)
    proc = subprocess.run(
        [sys.executable, "-m", "agentteams.feature_audit", "--tiers", "unit"],
        cwd=REPO, capture_output=True, text=True,
        env={**dict(__import__("os").environ), fa.REGISTRY_ENV_VAR: str(reg)},
    )
    assert proc.returncode == fa.HARNESS_BROKEN, (
        f"{name}: expected exit {fa.HARNESS_BROKEN}, got {proc.returncode}\n{proc.stdout}"
    )


def test_a_missing_registry_is_harness_broken(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "agentteams.feature_audit", "--tiers", "unit"],
        cwd=REPO, capture_output=True, text=True,
        env={**dict(__import__("os").environ),
             fa.REGISTRY_ENV_VAR: str(tmp_path / "does-not-exist.csv")},
    )
    assert proc.returncode == fa.HARNESS_BROKEN


def test_a_tier_that_executes_zero_proofs_is_a_finding(tmp_path):
    """An all-UNPROVEN registry must not exit clean.

    Otherwise every structural check passes, no proof runs, and the job reports success
    over nothing proven — the exact defect this audit exists to detect elsewhere.
    """
    reg = _write(tmp_path / "all-unproven.csv", GOOD_HEADER,
                 "F-1,C,N,,feature,unit,none,,,UNPROVEN,\n")
    report = fa.AuditReport(rows=fa.load_registry(reg), tiers_run=("unit",))
    assert fa.classify(report) == fa.FINDINGS
    assert any("proves nothing is a finding" in f for f in report.findings)


def test_status_proven_without_a_negative_control_is_downgraded(tmp_path):
    """The word `proven` alone must not confer proven status."""
    reg = _write(tmp_path / "tautology.csv", GOOD_HEADER,
                 "F-1,C,N,,feature,unit,none,tests/x.py::a,,proven,\n"
                 "F-2,C,M,,feature,unit,none,tests/x.py::a,tests/x.py::a,proven,\n")
    rows = fa.load_registry(reg)
    assert all(r.status == fa.UNPROVEN for r in rows), (
        "a row with a missing or identical negative_control must be downgraded"
    )
    assert all("downgraded" in r.notes for r in rows)


def test_unreachable_is_not_a_failure():
    """A third-party outage must never gate.

    Conversely a live proof asserting only "no exception" would pass on an empty
    degraded response, because agentteams/research is degrade-don't-raise.
    """
    report = fa.AuditReport(
        rows=[], tiers_run=("live",),
        # The tier must be stamped: an UNREACHABLE result still counts as the live tier
        # having EXECUTED. Leaving it blank would make the tier look empty and trip the
        # per-tier zero-proof finding instead — a different failure wearing this one's name.
        results=[fa.ProofResult("F-1", fa.UNREACHABLE, "ConnectionError", "live")],
    )
    assert fa.classify(report) == fa.CLEAN
    assert not report.failed


def test_unreachable_classification_does_not_swallow_a_real_regression():
    """Only transport-level signatures count as unreachable.

    A plain assertion failure in a live proof is a defect, not an outage.
    """
    assert fa._looks_unreachable("requests.exceptions.ConnectionError: ...")
    assert not fa._looks_unreachable("AssertionError: expected 3 results, got 0")


def test_an_empty_tier_is_a_finding_even_when_another_tier_proved_something(tmp_path):
    """Per-tier, not global.

    The global form (`report.executed == 0`) let one unit proof mask two entirely empty
    tiers: `--tiers unit,e2e,live` exited CLEAN while nothing probed the external surface
    that justifies running daily — and that is precisely the combination the daily
    workflow runs.

    Uses a synthetic registry rather than the real one on purpose: pinning this to "the
    current registry" would make the test wrong the moment real e2e/live rows land.
    """
    reg = _write(
        tmp_path / "one-proven-unit-row.csv", GOOD_HEADER,
        "F-1,C,Proven unit,,feature,unit,none,tests/a.py::x,tests/a.py::y,proven,\n"
        "F-2,C,Unproven e2e,,feature,e2e,none,,,UNPROVEN,\n"
    )
    rows = fa.load_registry(reg)
    report = fa.AuditReport(rows=rows, tiers_run=("unit", "e2e", "live"))
    # The unit tier produced a result; e2e and live produced none.
    report.results.append(fa.ProofResult("F-1", fa.PASS, "", "unit"))

    assert fa.classify(report) == fa.FINDINGS, (
        "a tier that executed zero proofs must be a finding even when another tier passed"
    )
    empty_named = " ".join(report.findings)
    assert "'e2e'" in empty_named and "'live'" in empty_named, (
        f"both empty tiers must be named individually; got: {report.findings}"
    )
    assert "'unit'" not in empty_named, "the tier that DID prove something must not be flagged"


# ---------------------------------------------------------------------------
# run_proof tier stamping.
#
# The whole per-tier check rests on run_proof stamping row.tier onto every result it
# returns. That was asserted and never tested: a dropped stamp would make every tier read
# empty, turning every run into FINDINGS while the suite stayed green.
# ---------------------------------------------------------------------------

def _row(tier: str, proof: str) -> fa.FeatureRow:
    return fa.FeatureRow(
        feature_id="F-1", category="C", name="N", surface="", kind=fa.KIND_FEATURE,
        tier=tier, external_dep="none", proof_test=proof,
        negative_control="tests/x.py::neg", status=fa.PROVEN, notes="",
    )


def test_run_proof_stamps_the_tier_on_a_passing_result(tmp_path):
    row = _row("unit", "tests/test_feature_registry.py::test_registry_parses_and_has_no_duplicate_ids")
    result = fa.run_proof(row, REPO)
    assert result.outcome == fa.PASS
    assert result.tier == "unit", "a passing result must carry its row's tier"


def test_run_proof_stamps_the_tier_on_a_failing_result(tmp_path):
    row = _row("e2e", "tests/does_not_exist.py::nope")
    result = fa.run_proof(row, REPO)
    assert result.outcome == fa.FAIL
    assert result.tier == "e2e", "a failing result must carry its row's tier"


def test_proof_result_requires_a_tier():
    """A missing stamp must be a loud construction error, not a silent empty tier."""
    with pytest.raises(TypeError):
        fa.ProofResult("F-1", fa.PASS, "")  # type: ignore[call-arg]


def test_an_all_waived_tier_is_exempt_rather_than_a_finding(tmp_path):
    """A considered decision must not look like a defect.

    A tier whose every row is deliberately unprovable has nothing to prove. A tier with no
    rows at all is a different thing and stays a finding.
    """
    reg = _write(tmp_path / "waived.csv", GOOD_HEADER,
                 "F-1,C,N,,not-provable,live,network:nvd,,,UNPROVEN,inherently manual\n")
    report = fa.AuditReport(rows=fa.load_registry(reg), tiers_run=("live",))
    assert fa.classify(report) == fa.CLEAN, "an all-not-provable tier must be exempt"


def test_an_empty_tier_finding_is_not_masked_by_a_parity_finding(tmp_path):
    """One finding must not hide another.

    classify() used to early-return on any pre-existing finding, so routine parity churn
    during coverage work would suppress 'the live tier proved nothing' entirely.
    """
    reg = _write(tmp_path / "m2.csv", GOOD_HEADER,
                 "F-1,C,N,,feature,unit,none,,,UNPROVEN,\n")
    report = fa.AuditReport(rows=fa.load_registry(reg), tiers_run=("live",))
    report.findings.append("pre-existing parity drift")
    fa.classify(report)
    assert any("tier 'live'" in f for f in report.findings), (
        f"the empty-tier finding was masked by the parity finding: {report.findings}"
    )


# ---------------------------------------------------------------------------
# Live-tier classification.
#
# Three outcomes must stay distinguishable, because conflating any two of them either
# hides a regression or makes the daily job permanently red for someone else's reason.
# ---------------------------------------------------------------------------

def test_anti_bot_challenges_are_unreachable_not_failures():
    """DuckDuckGo serves 403/202 to shared datacenter IPs — which a runner always is.

    `references/retrieval-transport-policy.md` records the search chain as two free
    endpoints with no managed rate limiting. Classifying a challenge as FAIL would make
    the daily job permanently red for a third party's bot policy, which is precisely how
    a red build stops meaning anything.
    """
    for signature in ("HTTP 403", "HTTP 202", "HTTP 429", "HTTP 503"):
        assert fa._looks_unreachable(f"backend returned {signature}"), signature


def test_a_real_assertion_failure_is_never_excused_as_unreachable():
    """The excuse must stay narrow, or the live tier is worthless."""
    assert not fa._looks_unreachable("AssertionError: expected 3 results, got 0")
    assert not fa._looks_unreachable("ValueError: malformed response payload")


def test_a_missing_dependency_is_harness_broken_not_a_failed_feature():
    """`httpx` lives in the research extra.

    Reporting an uninstalled dependency as a FAILED feature sends the reader hunting a
    regression that does not exist.
    """
    assert fa._looks_like_broken_harness("ModuleNotFoundError: No module named 'httpx'")
    assert fa._looks_like_broken_harness("ImportError: cannot import name 'search'")
    assert not fa._looks_like_broken_harness("AssertionError: expected 3 results, got 0")


def test_run_proof_raises_harness_broken_on_a_missing_dependency(tmp_path):
    """End-to-end: the harness-broken path must reach RegistryError, not a FAIL result."""
    probe = tmp_path / "test_missing_dep.py"
    probe.write_text("import definitely_not_a_real_module_xyz\n", encoding="utf-8")
    row = _row("live", f"{probe}::nothing")
    with pytest.raises(fa.RegistryError, match="missing dependency"):
        fa.run_proof(row, REPO)


def test_exhausted_search_backends_are_unreachable_not_a_regression():
    """`backend=none` is the research CLI's exhausted-backends signal.

    It exits 0 with an empty list — degrade-don't-raise working as designed. Every backend
    was tried and none answered: availability, not a defect in this repository. Observed
    2026-08-07 from a residential IP, so it is not only a datacenter-IP effect.
    """
    assert fa._looks_unreachable(
        "provenance: backend=none cached=false tried=duckduckgo,ddg_lite"
    )
    assert not fa._looks_unreachable("provenance: backend=duckduckgo cached=false")


def test_a_skipped_proof_is_not_a_pass(tmp_path):
    """pytest exits 0 when every selected test skipped.

    The live probes are env-gated, so without this the audit would report the entire live
    tier green while running nothing — a proof that cannot fail, which is the defect this
    whole workstream exists to find.
    """
    assert fa._ran_nothing("1 skipped in 0.01s")
    assert fa._ran_nothing("no tests ran in 0.01s")
    assert not fa._ran_nothing("1 passed in 0.01s")
    assert not fa._ran_nothing("1 passed, 2 skipped in 0.01s")

    probe = tmp_path / "test_skipped.py"
    probe.write_text(
        "import pytest\n@pytest.mark.skip(reason='gated')\ndef test_x():\n    assert True\n",
        encoding="utf-8",
    )
    row = _row("live", str(probe))
    result = fa.run_proof(row, REPO)
    assert result.outcome == fa.FAIL, "a skipped proof must not report PASS"
    assert "did not run" in result.detail
