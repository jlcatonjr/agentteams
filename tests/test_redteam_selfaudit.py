"""test_redteam_selfaudit.py — every phase-6 check must be able to fail.

Twelve tests, two per check. One feeds the check a defective fixture and asserts it **fires**;
one feeds it a clean fixture and asserts it **stays silent**. Both directions are required, and
the reason is the finding that started all of this: a payload-digest verifier shipped on
2026-08-06 hashing five keys the producer never emitted, so ``digest(payload) == digest({})``
for every input. It passed every test it had. It had only the silent direction.

A self-audit that cannot fail is that defect wearing a different hat, and it is the most
expensive thing this work could ship — a green light indistinguishable from a working one. So
the checks are audited the way they audit everything else: prove the alarm rings before
trusting the silence.

The fixtures build miniature repositories under ``tmp_path`` rather than pointing at the real
one. That is deliberate: a check tested only against the live repository passes today and says
nothing about what it does when something breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams.redteam import checks_report, checks_static, selfaudit
from agentteams.redteam.registry import (
    ACCEPTED_WEAKNESSES_REL,
    CALLPATH_PARITY_REL,
    DEFENDED,
    PARTIAL,
    PROBE_BASELINE_REL,
    VERIFIERS_REL,
    Probe,
    evidence_digest,
)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _probe(pid: str, outcome: str, evidence: str = "gate raised", control: str | None = "Z0",
           name: str = "attack probe") -> Probe:
    return Probe(
        pid=pid, name=name, article="C-2", tier="T1", outcome=outcome,
        expected_if_sound="blocked", evidence=evidence, control=control,
    )


# ===========================================================================
# F-1 — verifier sensitivity
# ===========================================================================

_VERIFIER_SOURCE = '''
def check_the_thing(value):
    """A verifier-shaped function."""
    return value == 1
'''

_VERIFIER_LEDGER_HEADER = (
    "module,symbol,kind,sensitivity_test,negative_control_test,reason\n"
)


def test_f1_fires_on_an_unregistered_verifier(tmp_path: Path) -> None:
    """A new verifier with no ledger row is a verifier nobody proved can fail."""
    _write(tmp_path, "agentteams/redteam/thing.py", _VERIFIER_SOURCE)

    findings = checks_static.check_verifier_sensitivity(tmp_path)

    assert [f.subject for f in findings] == ["agentteams/redteam/thing.py::check_the_thing"]
    assert findings[0].check == "F-1"


def test_f1_fires_when_a_declared_test_does_not_exist(tmp_path: Path) -> None:
    """Naming a test is not the same as having one — the reference is resolved, not trusted."""
    _write(tmp_path, "agentteams/redteam/thing.py", _VERIFIER_SOURCE)
    _write(tmp_path, VERIFIERS_REL, _VERIFIER_LEDGER_HEADER + (
        "agentteams/redteam/thing.py,check_the_thing,verifier,"
        "tests/test_imaginary.py::test_nope,tests/test_imaginary.py::test_also_nope,x\n"
    ))

    findings = checks_static.check_verifier_sensitivity(tmp_path)

    assert len(findings) == 2
    assert all("does not resolve" in f.detail for f in findings)


def test_f1_fires_on_a_thin_not_a_verifier_excuse(tmp_path: Path) -> None:
    """`not-a-verifier` must be argued, not asserted."""
    _write(tmp_path, "agentteams/redteam/thing.py", _VERIFIER_SOURCE)
    _write(tmp_path, VERIFIERS_REL, _VERIFIER_LEDGER_HEADER + (
        "agentteams/redteam/thing.py,check_the_thing,not-a-verifier,,,helper\n"
    ))

    findings = checks_static.check_verifier_sensitivity(tmp_path)

    assert len(findings) == 1 and "the bar is" in findings[0].detail


def test_f1_fires_on_a_stale_ledger_row(tmp_path: Path) -> None:
    """The other direction: a row for a symbol that is gone stops describing the system."""
    _write(tmp_path, "agentteams/redteam/thing.py", "x = 1\n")
    _write(tmp_path, VERIFIERS_REL, _VERIFIER_LEDGER_HEADER + (
        "agentteams/redteam/thing.py,check_the_thing,verifier,a::b,c::d,e\n"
    ))

    findings = checks_static.check_verifier_sensitivity(tmp_path)

    assert len(findings) == 1 and "no longer exists" in findings[0].detail


def test_f1_is_silent_on_a_complete_ledger(tmp_path: Path) -> None:
    """Negative control: a registered verifier whose two tests resolve produces nothing."""
    _write(tmp_path, "agentteams/redteam/thing.py", _VERIFIER_SOURCE)
    _write(tmp_path, "tests/test_thing.py",
           "def test_changes_with_input():\n    pass\n\n"
           "def test_ignores_irrelevant_input():\n    pass\n")
    _write(tmp_path, VERIFIERS_REL, _VERIFIER_LEDGER_HEADER + (
        "agentteams/redteam/thing.py,check_the_thing,verifier,"
        "tests/test_thing.py::test_changes_with_input,"
        "tests/test_thing.py::test_ignores_irrelevant_input,ok\n"
    ))

    assert checks_static.check_verifier_sensitivity(tmp_path) == []


# ===========================================================================
# F-2 — call-path parity
# ===========================================================================

_PARITY_LEDGER_HEADER = "callee,guard,scope_module,position,note\n"

_TWO_PATHS_ONE_GUARDED = '''
def guard(x):
    return x


def emit_all(x):
    return x


def run(update):
    if update:
        result = emit_all(1)
        guard(result)
        return result
    result = emit_all(2)
    return result
'''

_TWO_PATHS_BOTH_GUARDED = _TWO_PATHS_ONE_GUARDED.replace(
    "    result = emit_all(2)\n    return result\n",
    "    result = emit_all(2)\n    guard(result)\n    return result\n",
)

_ONE_PATH = '''
def guard(x):
    return x


def emit_all(x):
    return x


def run():
    result = emit_all(1)
    guard(result)
    return result
'''


def test_f2_fires_on_an_unguarded_call_site(tmp_path: Path) -> None:
    """The W20 shape exactly: two call sites in one function, a guard on only one branch."""
    _write(tmp_path, "agentteams/cli/generate.py", _TWO_PATHS_ONE_GUARDED)
    _write(tmp_path, CALLPATH_PARITY_REL,
           _PARITY_LEDGER_HEADER + "emit_all,guard,agentteams/cli/generate.py,after,W20\n")

    findings = checks_static.check_callpath_parity(tmp_path)

    assert len(findings) == 1
    assert findings[0].check == "F-2"
    assert "no call to the guard" in findings[0].detail


def test_f2_fires_on_a_vacuous_rule(tmp_path: Path) -> None:
    """A parity rule over one call site proves nothing and must not read as clean.

    This is the anti-vacuity branch. Without it, misspelling the callee in the ledger yields a
    rule that matches nothing and passes for every input — F-1 reproduced inside F-2, which is
    what the plan audit caught in this very design.
    """
    _write(tmp_path, "agentteams/cli/generate.py", _ONE_PATH)
    _write(tmp_path, CALLPATH_PARITY_REL,
           _PARITY_LEDGER_HEADER + "emit_all,guard,agentteams/cli/generate.py,after,n\n")

    findings = checks_static.check_callpath_parity(tmp_path)

    assert len(findings) == 1 and "passes vacuously" in findings[0].detail


def test_f2_fires_when_the_guard_does_not_resolve(tmp_path: Path) -> None:
    """A ledger naming a nonexistent guard is a rule that can never fire."""
    _write(tmp_path, "agentteams/cli/generate.py", _TWO_PATHS_BOTH_GUARDED)
    _write(tmp_path, CALLPATH_PARITY_REL,
           _PARITY_LEDGER_HEADER + "emit_all,_sweep_capability_keys,agentteams/cli/generate.py,after,typo\n")

    findings = checks_static.check_callpath_parity(tmp_path)

    assert len(findings) == 1 and "neither defined nor called" in findings[0].detail


def test_f2_is_silent_when_every_path_is_guarded(tmp_path: Path) -> None:
    """Negative control: both branches guarded produces nothing."""
    _write(tmp_path, "agentteams/cli/generate.py", _TWO_PATHS_BOTH_GUARDED)
    _write(tmp_path, CALLPATH_PARITY_REL,
           _PARITY_LEDGER_HEADER + "emit_all,guard,agentteams/cli/generate.py,after,W20\n")

    assert checks_static.check_callpath_parity(tmp_path) == []


# ===========================================================================
# F-3 — canonical resolution
# ===========================================================================

_HAND_ROLLED_SWEEP = '''
from pathlib import Path


def sweep(parent: Path):
    return [d for d in parent.glob("*") if d.is_dir()]
'''

_HAND_ROLLED_GIT_CHECK = '''
from pathlib import Path


def is_repo(path: Path) -> bool:
    return (path / ".git").is_dir()
'''

_CANONICAL_SWEEP = '''
from pathlib import Path

from agentteams import fleet


def sweep(parent: Path):
    return fleet.discover_workspaces(parent)
'''


def test_f3_fires_on_a_hand_rolled_workspace_sweep(tmp_path: Path) -> None:
    """`for d in parent.glob("*")` is the construct that missed 34 workspaces."""
    _write(tmp_path, "agentteams/redteam/sweep.py", _HAND_ROLLED_SWEEP)

    findings = checks_static.check_canonical_resolution(tmp_path)

    assert len(findings) == 1
    assert findings[0].check == "F-3"
    assert "workspace-enumeration" in findings[0].subject


def test_f3_fires_on_a_hand_rolled_git_check(tmp_path: Path) -> None:
    """`.git`.is_dir() would have excluded 14 recoverable git worktrees as unrecoverable."""
    _write(tmp_path, "agentteams/redteam/vcs.py", _HAND_ROLLED_GIT_CHECK)

    findings = checks_static.check_canonical_resolution(tmp_path)

    assert len(findings) == 1 and "vcs-status" in findings[0].subject


def test_f3_fires_on_a_stale_exemption(tmp_path: Path) -> None:
    """An exemption matching nothing is a permission for a defect that no longer exists."""
    _write(tmp_path, "agentteams/redteam/sweep.py", _CANONICAL_SWEEP)
    _write(tmp_path, "references/redteam-canonical-resolution-exemptions.csv",
           "file,function,construct,reason\n"
           "agentteams/redteam/sweep.py,sweep,workspace-enumeration,"
           "a reason long enough to clear the forty character bar comfortably\n")

    findings = checks_static.check_canonical_resolution(tmp_path)

    assert len(findings) == 1 and "stale row" in findings[0].detail


def test_f3_is_silent_on_canonical_calls(tmp_path: Path) -> None:
    """Negative control: code that calls the canonical enumerator produces nothing."""
    _write(tmp_path, "agentteams/redteam/sweep.py", _CANONICAL_SWEEP)

    assert checks_static.check_canonical_resolution(tmp_path) == []


def test_f3_does_not_flag_a_typed_rglob(tmp_path: Path) -> None:
    """Precision matters more than reach: `rglob("*.py")` is ordinary file work.

    A rule that flagged every glob would be muted within a week, and a muted rule is worse
    than no rule because it looks like coverage.
    """
    _write(tmp_path, "agentteams/redteam/walk.py",
           "from pathlib import Path\n\n\n"
           "def walk(root: Path):\n    return sorted(root.rglob('*.py'))\n")

    assert checks_static.check_canonical_resolution(tmp_path) == []


# ===========================================================================
# F-4 — counts carry their population
# ===========================================================================

_THE_2026_08_06_SENTENCE = (
    "# Report\n\nThe sweep found 0 agents on the ignored key across the fleet.\n"
)

_HONEST_COUNTS = (
    "# Report\n\n"
    "| claim | numerator | denominator | population_source |\n"
    "|---|---|---|---|\n"
    "| agents on the ignored key | 0 | 719 | `fleet.discover_workspaces` |\n"
)

_AD_HOC_SOURCE = (
    "# Report\n\n"
    "| claim | numerator | denominator | population_source |\n"
    "|---|---|---|---|\n"
    "| agents on the ignored key | 0 | 15 | `the repos I looked at` |\n"
)


def test_f4_fires_on_a_count_without_a_denominator(tmp_path: Path) -> None:
    """The literal 2026-08-06 sentence. Arithmetically true, and it hid 719 exposed agents."""
    findings = checks_report.check_counts_have_population(
        tmp_path, tmp_path / "nowhere", rendered={"report.md": _THE_2026_08_06_SENTENCE}
    )

    assert findings
    assert any("0 agents" in f.detail for f in findings)


def test_f4_fires_on_an_ad_hoc_population_source(tmp_path: Path) -> None:
    """A denominator is only as good as the enumerator that produced it."""
    findings = checks_report.check_counts_have_population(
        tmp_path, tmp_path / "nowhere", rendered={"report.md": _AD_HOC_SOURCE}
    )

    assert len(findings) == 1 and "not a canonical enumerator" in findings[0].detail


def test_f4_fires_when_the_scope_is_empty(tmp_path: Path) -> None:
    """A check with nothing to read reports clean, which is indistinguishable from working."""
    findings = checks_report.check_counts_have_population(tmp_path, tmp_path / "nowhere")

    assert len(findings) == 1 and "read nothing" in findings[0].detail


def test_f4_is_silent_on_a_counts_table(tmp_path: Path) -> None:
    """Negative control: numerator, denominator and a canonical source produces nothing."""
    findings = checks_report.check_counts_have_population(
        tmp_path, tmp_path / "nowhere", rendered={"report.md": _HONEST_COUNTS}
    )

    assert findings == []


# ===========================================================================
# F-5 — probe intent re-validation
# ===========================================================================

def _seed_baseline(root: Path, probes: list[Probe]) -> None:
    _write(root, PROBE_BASELINE_REL,
           json.dumps(checks_report.build_probe_baseline(probes), indent=2))


def test_f5_fires_when_a_probe_outcome_flips(tmp_path: Path) -> None:
    """A9 and B10 both flipped to a false DEFENDED because they got blinder, not better."""
    _seed_baseline(tmp_path, [_probe("A9", PARTIAL)])

    findings = checks_report.check_probe_intent(tmp_path, [_probe("A9", DEFENDED)])

    assert len(findings) == 1
    assert findings[0].check == "F-5"
    assert "PARTIAL → DEFENDED" in findings[0].detail
    assert "--accept-probe-baseline" in findings[0].remedy


def test_f5_fires_when_the_evidence_changed_under_a_stable_outcome(tmp_path: Path) -> None:
    """The subtler case: still DEFENDED, but now measuring something else."""
    _seed_baseline(tmp_path, [_probe("B10", DEFENDED, evidence="skipped the backup root")])

    findings = checks_report.check_probe_intent(
        tmp_path, [_probe("B10", DEFENDED, evidence="skipped the genuine project root")]
    )

    assert len(findings) == 1 and "may now be measuring something else" in findings[0].detail


def test_f5_fires_when_there_is_no_baseline(tmp_path: Path) -> None:
    """No baseline means no change is detectable — that is a gap, not a clean run."""
    findings = checks_report.check_probe_intent(tmp_path, [_probe("A1", DEFENDED)])

    assert len(findings) == 1 and "no committed baseline" in findings[0].detail


def test_f5_is_silent_when_nothing_changed(tmp_path: Path) -> None:
    """Negative control: identical outcome and evidence produces nothing."""
    probes = [_probe("A1", DEFENDED), _probe("A2", DEFENDED)]
    _seed_baseline(tmp_path, probes)

    assert checks_report.check_probe_intent(tmp_path, probes) == []


def test_f5_is_silent_when_a_change_carries_a_note(tmp_path: Path) -> None:
    """Accepting a changed outcome costs a diff — and a diff with a note clears the flag."""
    _write(tmp_path, PROBE_BASELINE_REL, json.dumps({
        "schema_version": 1,
        "probes": {"A9": {
            "outcome": PARTIAL,
            "evidence_digest": evidence_digest(_probe("A9", PARTIAL)),
            "note": "re-read the probe: it still names an off-roster approver, and the roster "
                    "check is what changed. Intent confirmed 2026-08-06.",
        }},
    }))

    assert checks_report.check_probe_intent(tmp_path, [_probe("A9", DEFENDED)]) == []


# ===========================================================================
# F-6 — accepted weaknesses are named
# ===========================================================================

_ACCEPTED_HEADER = "pid,outcome,reason\n"
_GOOD_REASON = (
    "the scope column is opt-in, and making it mandatory would invalidate every existing "
    "clearance row in every deployed team"
)


def test_f6_fires_on_an_unaccepted_weakness(tmp_path: Path) -> None:
    """A PARTIAL with no ledger row is a weakness nobody chose."""
    findings = checks_report.check_accepted_weaknesses(tmp_path, [_probe("A5", PARTIAL)])

    assert len(findings) == 1
    assert findings[0].check == "F-6" and "no entry" in findings[0].detail


def test_f6_fires_on_a_thin_reason(tmp_path: Path) -> None:
    """"Known issue" is not a reason; it is a place a failure goes to die."""
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL, _ACCEPTED_HEADER + "A5,PARTIAL,known issue\n")

    findings = checks_report.check_accepted_weaknesses(tmp_path, [_probe("A5", PARTIAL)])

    assert len(findings) == 1 and "the bar is" in findings[0].detail


def test_f6_fires_on_a_stale_exemption(tmp_path: Path) -> None:
    """The other direction: a probe that now DEFENDs must lose its exemption."""
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL,
           _ACCEPTED_HEADER + f'A5,PARTIAL,"{_GOOD_REASON}"\n')

    findings = checks_report.check_accepted_weaknesses(tmp_path, [_probe("A5", DEFENDED)])

    assert len(findings) == 1 and "keeps its exemption" in findings[0].detail


def test_f6_fires_when_the_accepted_outcome_no_longer_matches(tmp_path: Path) -> None:
    """A regression from PARTIAL to something worse must not ride an old acceptance."""
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL,
           _ACCEPTED_HEADER + f'A5,PARTIAL,"{_GOOD_REASON}"\n')

    findings = checks_report.check_accepted_weaknesses(
        tmp_path, [_probe("A5", "DOCUMENTED-LIMIT")]
    )

    assert any("ledger accepts PARTIAL" in f.detail for f in findings)


def test_f6_is_silent_on_a_matching_ledger(tmp_path: Path) -> None:
    """Negative control: outcome matches and the reason is substantive."""
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL,
           _ACCEPTED_HEADER + f'A5,PARTIAL,"{_GOOD_REASON}"\n')

    assert checks_report.check_accepted_weaknesses(tmp_path, [_probe("A5", PARTIAL)]) == []


# ===========================================================================
# the orchestrator
# ===========================================================================

def test_run_selfaudit_runs_all_six_checks_when_probes_are_present(tmp_path: Path) -> None:
    _write(tmp_path, ACCEPTED_WEAKNESSES_REL, _ACCEPTED_HEADER)
    _seed_baseline(tmp_path, [_probe("A1", DEFENDED)])

    result = selfaudit.run_selfaudit(
        tmp_path, probes=[_probe("A1", DEFENDED)], report_dir=tmp_path / "r",
        rendered={"discoveries.md": _HONEST_COUNTS},
    )

    assert result.checks_run == list(selfaudit.CHECK_IDS)
    assert result.checks_skipped == {}


def test_run_selfaudit_records_skips_rather_than_omitting_them(tmp_path: Path) -> None:
    """Five clean checks and a silent sixth reads as six clean checks. It must not."""
    result = selfaudit.run_selfaudit(
        tmp_path, probes=[], report_dir=tmp_path / "r",
        rendered={"discoveries.md": _HONEST_COUNTS},
    )

    assert set(result.checks_skipped) == {"F-5", "F-6"}
    assert result.is_clean is False, "a run with skipped checks must not report clean"


def test_every_check_id_has_a_title() -> None:
    """A check added to the tuple without a title renders as a blank row in the report."""
    assert set(selfaudit.CHECK_IDS) == set(selfaudit.CHECK_TITLES)


def test_f2_position_function_accepts_a_guard_that_precedes_the_call(tmp_path: Path) -> None:
    """`position=function` is an input PRECONDITION, not a post-condition.

    Added when the real defect it guards was found: `agent_system_prompt` must run before
    `run_payload` uses its result, and it sits in an outer suite while the call sits inside a
    loop. The default `after` semantics could not express that, and a check that cannot express
    the control it is asked to enforce gets an exemption instead of a rule.
    """
    _write(tmp_path, "scripts/driver.py", '''
def prep(p):
    return p


def call(x):
    return x


def measure(paths):
    text = prep(paths)
    for p in paths:
        call(text)
''')
    _write(tmp_path, CALLPATH_PARITY_REL,
           "callee,guard,scope_module,position,note\n"
           "call,prep,scripts/driver.py,function,precondition\n")

    assert checks_static.check_callpath_parity(tmp_path) == []


def test_f2_position_function_fires_when_the_guard_is_absent(tmp_path: Path) -> None:
    """The same rule must still catch a call path that never prepares its input."""
    _write(tmp_path, "scripts/driver.py", '''
def prep(p):
    return p


def call(x):
    return x


def measure(paths):
    for p in paths:
        call(p)


def measure_again(paths):
    text = prep(paths)
    call(text)
''')
    _write(tmp_path, CALLPATH_PARITY_REL,
           "callee,guard,scope_module,position,note\n"
           "call,prep,scripts/driver.py,function,precondition\n")

    findings = checks_static.check_callpath_parity(tmp_path)

    assert len(findings) == 1
    assert "enclosing function" in findings[0].detail
