"""test_redteam_engine.py — the registry, the runner, the cycle, and the exit-code policy.

The properties pinned here are the ones a daily unattended job stands on:

* **A dry run writes nothing**, proven by comparing a recursive snapshot of the tree before and
  after rather than by trusting a flag. The renderers have no filesystem access at all, which
  is what makes the property structural rather than remembered.
* **Three exit codes stay distinguishable.** Clean, finding, and *harness broken* mean
  different things, and collapsing the third into the first is the most dangerous outcome
  available here: a battery whose controls failed reports "no exploits" just as loudly as one
  that found none.
* **A count cannot be constructed without its denominator.**
  :class:`~agentteams.redteam.runner.Count` raises on a non-canonical population source, so
  "state what you divided by" is enforced at the type.
* **Accepting a changed probe outcome costs a diff**, and cannot happen as a side effect of a
  dry run.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from agentteams.redteam import cycle, realcopy, registry, report as report_mod
from agentteams.redteam.registry import (
    ACCEPTED_WEAKNESSES_REL,
    DEFENDED,
    EXPLOITED,
    PARTIAL,
    Probe,
)
from agentteams.redteam.runner import Count, run_attack_phase

STAMP = "2026-08-06T00:00:00Z"


def _install_probe_module(name: str, probes: list[Probe]) -> str:
    """Register a synthetic probe module in ``sys.modules`` and return its dotted name."""
    module = types.ModuleType(name)
    module.RESULTS = []  # type: ignore[attr-defined]

    def _make(probe: Probe):
        def _run() -> None:
            module.RESULTS.append(probe)  # type: ignore[attr-defined]
        return _run

    module.PROBES = [_make(p) for p in probes]  # type: ignore[attr-defined]
    sys.modules[name] = module
    return name


@pytest.fixture(autouse=True)
def pristine_live_tree(monkeypatch):
    """Make the live-tree check verifiable and quiet by default.

    ``tmp_path`` is not a git repository, so without this every cycle test would exit 2 on
    "live tree unverifiable" — the correct production behaviour (indeterminate is not a pass)
    and the wrong code path for a test about exploit counting. The two tests that are *about*
    the live-tree check re-patch these afterwards, and a later ``monkeypatch.setattr`` wins.
    """
    monkeypatch.setattr(realcopy, "live_tree_is_verifiable", lambda root: True)
    monkeypatch.setattr(realcopy, "live_tree_fingerprint", lambda root: {})


@pytest.fixture
def probe_module(request):
    """Install a probe module for the test and remove it afterwards."""
    installed: list[str] = []

    def _install(probes: list[Probe]) -> str:
        name = f"_redteam_fixture_{request.node.name}_{len(installed)}"
        installed.append(name)
        return _install_probe_module(name, probes)

    yield _install
    for name in installed:
        sys.modules.pop(name, None)


def _probe(pid: str, outcome: str = DEFENDED, *, control: str | None = "Z0",
           name: str = "attack") -> Probe:
    return Probe(
        pid=pid, name=name, article="C-2", tier="T1", outcome=outcome,
        expected_if_sound="blocked", evidence=f"{pid} evidence", control=control,
    )


def _control(pid: str, outcome: str = DEFENDED) -> Probe:
    return Probe(
        pid=pid, name="CONTROL: the mechanism is engaged", article="C-2", tier="T1",
        outcome=outcome, expected_if_sound="blocked", evidence=f"{pid} evidence",
    )


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    """Return a content snapshot of every file under ``root``."""
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.read_bytes())
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# ===========================================================================
# registry
# ===========================================================================

def test_an_unknown_outcome_is_refused() -> None:
    """A typo'd outcome matches neither the exploit assertion nor the acceptance ledger."""
    collector = registry.ProbeCollector()
    with pytest.raises(ValueError, match="unknown outcome"):
        collector.record(
            pid="A1", name="x", article="C-2", tier="T1", outcome="DEFENDEDD",
            expected_if_sound="blocked", evidence="",
        )


def test_a_probe_that_does_not_record_is_an_error(probe_module) -> None:
    """Anti-vacuity: a probe that returns without scoring makes every later assertion empty."""
    name = probe_module([])
    module = sys.modules[name]
    module.PROBES = [lambda: None]

    with pytest.raises(RuntimeError, match="never scored"):
        registry.run_probes(module)


def test_an_attack_probe_without_a_control_is_a_registration_error() -> None:
    """A lone 'the attack worked' cannot distinguish a breached control from an idle one."""
    problems = registry.validate_registration(
        [_probe("A1", control=None)], uncontrolled_exemptions={}
    )
    assert len(problems) == 1 and "declares no control" in problems[0]


def test_an_exempted_probe_needs_a_substantive_reason() -> None:
    problems = registry.validate_registration(
        [_probe("A1", control=None)], uncontrolled_exemptions={"A1": "n/a"}
    )
    assert len(problems) == 1

    problems = registry.validate_registration(
        [_probe("A1", control=None)],
        uncontrolled_exemptions={"A1": "x" * registry.MIN_REASON_CHARS},
    )
    assert problems == []


def test_a_control_that_does_not_exist_is_a_registration_error() -> None:
    problems = registry.validate_registration(
        [_probe("A1", control="Z9")], uncontrolled_exemptions={}
    )
    assert len(problems) == 1 and "not a registered probe" in problems[0]


def test_a_stale_control_exemption_is_reported() -> None:
    """The F-6 property, applied to the control ledger."""
    problems = registry.validate_registration(
        [_probe("A1", control="Z0"), _control("Z0")],
        uncontrolled_exemptions={"A1": "x" * 60},
    )
    assert len(problems) == 1 and "no longer needs its exemption" in problems[0]


def test_failed_controls_are_reported_separately_from_findings() -> None:
    results = [_control("Z0", outcome=EXPLOITED), _probe("A1")]
    assert registry.failed_controls(results) == ["Z0"]


# ===========================================================================
# Count — the denominator is enforced at the type
# ===========================================================================

def test_a_count_refuses_an_ad_hoc_population_source() -> None:
    with pytest.raises(ValueError, match="not canonical"):
        Count(claim="agents", numerator=0, denominator=15,
              population_source="the repos I looked at")


def test_a_count_refuses_a_numerator_larger_than_its_population() -> None:
    with pytest.raises(ValueError, match="numerator"):
        Count(claim="probes", numerator=40, denominator=38,
              population_source="registry.run_probes")


def test_a_count_renders_with_its_population() -> None:
    rendered = Count(claim="probes held", numerator=33, denominator=38,
                     population_source="registry.run_probes").render()
    assert "33 of 38 probes held" in rendered
    assert "population: registry.run_probes" in rendered


# ===========================================================================
# the cycle
# ===========================================================================

def test_a_dry_run_writes_nothing(tmp_path: Path, probe_module) -> None:
    """Proven by snapshot, not by trusting the flag."""
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text("pid,outcome,reason\n", encoding="utf-8")
    name = probe_module([_control("Z0"), _probe("A1", control="Z0")])
    report_dir = tmp_path / "tmp" / "redteam" / "2026-08-06"

    before = _snapshot(tmp_path)
    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=report_dir,
        generated_at=STAMP, dry_run=True,
    )
    after = _snapshot(tmp_path)

    assert before == after, "a --dry-run red-team audit wrote to the tree"
    assert result.written == []
    assert not report_dir.exists()
    assert result.artifacts, "the artifacts were still rendered, just not written"


def test_a_real_run_writes_all_four_artifacts(tmp_path: Path, probe_module) -> None:
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text("pid,outcome,reason\n", encoding="utf-8")
    name = probe_module([_control("Z0"), _probe("A1", control="Z0")])
    report_dir = tmp_path / "out"

    cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=report_dir, generated_at=STAMP
    )

    written = {p.name for p in report_dir.iterdir()}
    assert written == {
        report_mod.FINDINGS_NAME, report_mod.DISCOVERIES_NAME,
        report_mod.REMEDIATION_NAME, report_mod.SELFAUDIT_NAME,
    }
    payload = json.loads((report_dir / report_mod.FINDINGS_NAME).read_text(encoding="utf-8"))
    assert payload["probe_module"] == name
    assert len(payload["probes"]) == 2
    assert all("evidence_digest" in p for p in payload["probes"])


def test_a_live_exploit_exits_one(tmp_path: Path, probe_module) -> None:
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text(
        'pid,outcome,reason\nA1,EXPLOITED,"' + "x" * 60 + '"\n', encoding="utf-8"
    )
    name = probe_module([_control("Z0"), _probe("A1", EXPLOITED, control="Z0")])

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )

    assert result.exit_code == cycle.EXIT_FINDINGS


def test_a_failed_control_exits_two_not_one(tmp_path: Path, probe_module) -> None:
    """The whole point of the third code: a broken instrument is not a clean result.

    Note that this run has **zero** exploits. A policy that only looked at exploit counts
    would report it as the cleanest possible outcome.
    """
    name = probe_module([_control("Z0", outcome=EXPLOITED), _probe("A1", control="Z0")])

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )

    assert result.findings.exploited == []
    assert result.exit_code == cycle.EXIT_HARNESS_BROKEN
    assert result.findings.harness_is_broken


def test_a_run_that_modified_the_live_agent_tree_exits_two(
    tmp_path: Path, probe_module, monkeypatch
) -> None:
    """Attack the copy, never the original — asserted rather than promised."""
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text("pid,outcome,reason\n", encoding="utf-8")
    name = probe_module([_control("Z0"), _probe("A1", control="Z0")])

    fingerprints = iter([{}, {".claude/agents/security.md": " M"}])
    monkeypatch.setattr(realcopy, "live_tree_is_verifiable", lambda root: True)
    monkeypatch.setattr(realcopy, "live_tree_fingerprint", lambda root: next(fingerprints))

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )

    assert result.live_tree_modifications == [".claude/agents/security.md"]
    assert result.exit_code == cycle.EXIT_HARNESS_BROKEN


def test_an_unverifiable_live_tree_is_not_a_pass(
    tmp_path: Path, probe_module, monkeypatch
) -> None:
    """Indeterminate is not a pass: no git means unknown, and unknown is not clean."""
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text("pid,outcome,reason\n", encoding="utf-8")
    name = probe_module([_control("Z0"), _probe("A1", control="Z0")])
    monkeypatch.setattr(realcopy, "live_tree_is_verifiable", lambda root: False)

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )

    assert result.live_tree_verifiable is False
    assert result.exit_code == cycle.EXIT_HARNESS_BROKEN


def test_no_probe_module_runs_phase_six_only(tmp_path: Path) -> None:
    """A consumer with no probe module gets a stated-population zero, not an error."""
    result = cycle.run_cycle(
        tmp_path, probe_module_path=None, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )

    assert result.findings.probes == []
    assert set(result.selfaudit.checks_skipped) == {"F-5", "F-6"}
    probe_counts = [c for c in result.findings.counts if c.population_source ==
                    "registry.run_probes"]
    assert probe_counts and all(c.denominator == 0 for c in probe_counts)


def test_a_named_probe_module_that_will_not_import_raises(tmp_path: Path) -> None:
    """A missing *named* target is a broken harness; it must not read as clean."""
    with pytest.raises(ModuleNotFoundError):
        run_attack_phase(
            tmp_path, probe_module_path="no.such.module", generated_at=STAMP
        )


def test_accept_probe_baseline_is_refused_under_dry_run(tmp_path: Path, probe_module) -> None:
    """The one side effect that would silence a check cannot ride a dry run."""
    name = probe_module([_control("Z0")])
    with pytest.raises(RuntimeError, match="refused under"):
        cycle.accept_probe_baseline(
            tmp_path, probe_module_path=name, generated_at=STAMP, dry_run=True
        )


def test_accept_probe_baseline_records_every_probe(tmp_path: Path, probe_module) -> None:
    name = probe_module([_control("Z0"), _probe("A1", control="Z0")])

    count, path = cycle.accept_probe_baseline(
        tmp_path, probe_module_path=name, generated_at=STAMP
    )

    assert count == 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload["probes"]) == {"Z0", "A1"}
    assert all(entry["note"] == "" for entry in payload["probes"].values())


# ===========================================================================
# the report
# ===========================================================================

def test_discoveries_states_the_unmeasured_judgment_layer(tmp_path: Path, probe_module) -> None:
    """The population nobody measured has to appear, or the coverage reads as complete."""
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text("pid,outcome,reason\n", encoding="utf-8")
    name = probe_module([_control("Z0"), _probe("A1", control="Z0")])

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )
    text = result.artifacts[report_mod.DISCOVERIES_NAME]

    assert "judgment-layer payloads measured against live agents" in text
    assert "population_source" in text


def test_a_broken_harness_says_so_at_the_top_of_the_report(
    tmp_path: Path, probe_module
) -> None:
    name = probe_module([_control("Z0", outcome=EXPLOITED)])

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )

    assert "The harness is broken" in result.artifacts[report_mod.DISCOVERIES_NAME]


def test_the_remediation_skeleton_leaves_the_decisions_blank(
    tmp_path: Path, probe_module
) -> None:
    """A plan that arrives pre-decided invites approval rather than review."""
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text(
        'pid,outcome,reason\nA1,PARTIAL,"' + "x" * 60 + '"\n', encoding="utf-8"
    )
    name = probe_module([_control("Z0"), _probe("A1", PARTIAL, control="Z0")])

    result = cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=tmp_path / "o",
        generated_at=STAMP, dry_run=True,
    )
    text = result.artifacts[report_mod.REMEDIATION_NAME]

    assert "verifier (fill in)" in text and "rehearsal target (fill in)" in text


# ===========================================================================
# realcopy — attack the copy, never the original
# ===========================================================================

def test_a_snapshot_into_the_source_tree_is_refused(tmp_path: Path) -> None:
    """An attacked copy living inside the source tree is not an isolated copy."""
    with pytest.raises(ValueError, match="not an isolated copy"):
        realcopy.snapshot_agent_infrastructure(tmp_path, tmp_path / "inside")


def test_a_snapshot_copies_real_content_and_reports_what_was_absent(tmp_path: Path) -> None:
    source = tmp_path / "src"
    (source / ".github" / "agents").mkdir(parents=True)
    (source / ".github" / "agents" / "security.agent.md").write_text("real", encoding="utf-8")

    copy = realcopy.snapshot_agent_infrastructure(source, tmp_path / "copy")

    assert ".github/agents" in copy.copied
    assert ".claude/agents" in copy.absent
    assert (copy.root / ".github" / "agents" / "security.agent.md").read_text() == "real"


def test_live_tree_modifications_is_a_delta_not_an_absolute_check() -> None:
    """Pre-existing working-tree state must not read as "the red team touched it".

    An absolute cleanliness check fires on every uncommitted edit an operator already had in
    flight, which on a working branch is every run — and a rule that fires on ordinary work
    gets muted.
    """
    before = {".claude/agents/security.md": "??"}
    after = {".claude/agents/security.md": "??"}
    assert realcopy.live_tree_modifications(before, after) == []

    after_changed = {".claude/agents/security.md": " M"}
    assert realcopy.live_tree_modifications(before, after_changed) == [
        ".claude/agents/security.md"
    ]


def test_mutating_a_file_absent_from_the_copy_is_an_error(tmp_path: Path) -> None:
    """An attack that silently targets nothing always 'succeeds' — the vacuity failure."""
    source = tmp_path / "src"
    (source / ".github" / "agents").mkdir(parents=True)
    copy = realcopy.snapshot_agent_infrastructure(source, tmp_path / "copy")

    with pytest.raises(FileNotFoundError):
        realcopy.apply_mutation(copy, "widen a grant", "nope.md", lambda text: text)


def test_restorability_classifies_a_surviving_mutation_as_preserved(tmp_path: Path) -> None:
    """C3's measured behaviour: an escalated grant survives `--update --merge`.

    This is why the merge is a measurement here and not a safety net.
    """
    source = tmp_path / "src"
    (source / ".github" / "agents").mkdir(parents=True)
    target = source / ".github" / "agents" / "a.agent.md"
    target.write_text("tools: Read\n", encoding="utf-8")
    copy = realcopy.snapshot_agent_infrastructure(source, tmp_path / "copy")

    mutation = realcopy.apply_mutation(
        copy, "front-matter capability widening", ".github/agents/a.agent.md",
        lambda text: text.replace("tools: Read", "tools: Read, Write, Bash"),
    )
    # No merge run: the mutation is simply still there, which is what PRESERVED means.
    result = realcopy.classify_restorability(copy, [mutation], 0, "")

    assert result.verdicts["front-matter capability widening"] == realcopy.PRESERVED
    assert result.restored == 0


# ===========================================================================
# schemas/redteam-findings.schema.json — the contract, checked against a real artifact
# ===========================================================================

def test_findings_json_conforms_to_its_published_schema(tmp_path: Path, probe_module) -> None:
    """A published contract nothing validates a real artifact against cannot catch its own drift.

    Structural conformance without a JSON Schema library (stdlib-only in `agentteams/`, and
    this keeps the check runnable anywhere): every required key present, no key the schema
    forbids, and every enumerated value inside its enum. That is the part of the contract that
    actually breaks — a renamed field or a new outcome class — rather than the part a validator
    would add on top.
    """
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "redteam-findings.schema.json")
        .read_text(encoding="utf-8")
    )
    (tmp_path / "references").mkdir()
    (tmp_path / ACCEPTED_WEAKNESSES_REL).write_text(
        'pid,outcome,reason\nA1,PARTIAL,"' + "x" * 60 + '"\n', encoding="utf-8"
    )
    name = probe_module([_control("Z0"), _probe("A1", PARTIAL, control="Z0")])
    report_dir = tmp_path / "out"
    cycle.run_cycle(
        tmp_path, probe_module_path=name, report_dir=report_dir, generated_at=STAMP
    )
    payload = json.loads((report_dir / report_mod.FINDINGS_NAME).read_text(encoding="utf-8"))

    assert set(payload) == set(schema["required"]), (
        "findings.json keys drifted from schemas/redteam-findings.schema.json"
    )
    assert payload["schema_version"] == schema["properties"]["schema_version"]["const"]

    probe_schema = schema["properties"]["probes"]["items"]
    for entry in payload["probes"]:
        assert set(entry) == set(probe_schema["required"]), f"probe keys drifted: {entry}"
        assert entry["outcome"] in probe_schema["properties"]["outcome"]["enum"]
        assert entry["tier"] in probe_schema["properties"]["tier"]["enum"]

    count_schema = schema["properties"]["counts"]["items"]
    allowed_sources = set(count_schema["properties"]["population_source"]["enum"])
    for entry in payload["counts"]:
        assert set(entry) == set(count_schema["required"]), f"count keys drifted: {entry}"
        assert entry["population_source"] in allowed_sources
        assert entry["numerator"] <= entry["denominator"]


def test_the_schema_enumerates_exactly_the_engines_outcome_classes() -> None:
    """Two enums that must not drift apart: one would silently accept an outcome the other rejects."""
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "schemas" / "redteam-findings.schema.json")
        .read_text(encoding="utf-8")
    )
    published = set(schema["properties"]["probes"]["items"]["properties"]["outcome"]["enum"])
    assert published == set(registry.OUTCOMES)

    sources = set(
        schema["properties"]["counts"]["items"]["properties"]["population_source"]["enum"]
    )
    assert sources == set(registry.CANONICAL_POPULATION_SOURCES)
