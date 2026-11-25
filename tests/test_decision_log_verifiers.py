"""Sensitivity + negative-control pairs for the decision-log C-2 verifiers.

These three functions authenticate the security-decisions log — the gate's only authority for a
destructive action — and had no dedicated tests before issue #16 (Group A). Each is registered in
``references/redteam-verifiers.csv`` (F-1), and F-1 requires every ``kind=verifier`` row to name a
sensitivity test (output CHANGES for tampered/dangerous input → refusal) and a negative control
(output does NOT change for valid input → accepted). Written to the real contract, not to satisfy
the resolver:

* ``_assert_no_unretracted_halt`` — C-2 "HALT is final" made mechanical (audit A1/A3).
* ``_assert_decision_chain_intact`` — deletion/reorder detection on a chained log.
* ``_assert_authorizing_row_is_authentic`` — a requesting agent cannot clear its own action (A4).

The id-matcher (now ``action_ids.action_matches``, re-exported as ``_action_matches``) is
registered as a verifier with direct tests in ``tests/test_redteam_verifiers_cli.py``; its match
decision is also exercised here through its caller ``_assert_no_unretracted_halt`` with both a matching action (which
must refuse) and a non-matching action (which must accept), so a regression in it would break a
security assertion rather than pass silently.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl

_LEGACY_COLUMNS = [
    "timestamp",
    "requesting_agent",
    "action_reviewed",
    "verdict",
    "conditions",
    "conditions_verified",
]


def _write_decisions_log(root: Path, rows: list[dict[str, str]]) -> None:
    """Write a legacy-schema security-decisions log under ``root/references``."""
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    with (refs / "security-decisions.log.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_LEGACY_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in _LEGACY_COLUMNS})


def _row(action: str, verdict: str, **over: str) -> dict[str, str]:
    base = {
        "timestamp": "2026-01-01T00:00:00Z",
        "requesting_agent": "security",
        "action_reviewed": action,
        "verdict": verdict,
        "conditions": "",
        "conditions_verified": "",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# _assert_no_unretracted_halt — C-2 HALT teeth
# ---------------------------------------------------------------------------

def test_unretracted_halt_blocks_the_action(tmp_path):
    """SENSITIVITY: a HALT for the action that was never retracted → refusal.

    The dangerous input (a standing HALT) changes the verifier's output from silent-accept to
    raise. This is C-2's "HALT is final".
    """
    _write_decisions_log(tmp_path, [_row("prune", "HALT")])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action="prune")


def test_later_pass_does_not_supersede_a_halt(tmp_path):
    """SENSITIVITY (probe A1): a HALT followed by a later PASS for the same action still refuses.

    A PASS is not a retraction; only an authenticated HALT-RETRACTED row clears a HALT.
    """
    _write_decisions_log(
        tmp_path,
        [_row("prune", "HALT"), _row("prune", "PASS", timestamp="2026-02-01T00:00:00Z")],
    )
    with pytest.raises(RuntimeError, match="never been retracted"):
        dl._assert_no_unretracted_halt(tmp_path, action="prune")


def test_no_halt_for_the_action_is_accepted(tmp_path):
    """NEGATIVE CONTROL: a log with no HALT for the action does not raise.

    Valid input (only a PASS row) → accepted; the verifier does not fire.
    """
    _write_decisions_log(tmp_path, [_row("prune", "PASS")])
    dl._assert_no_unretracted_halt(tmp_path, action="prune")  # must not raise


def test_halt_for_a_different_action_does_not_block(tmp_path):
    """NEGATIVE CONTROL for the caller's action match (covers _action_matches, non-matching input).

    A HALT for 'delete' must not block a 'prune' request — proving the id-matcher distinguishes
    actions rather than matching everything.
    """
    _write_decisions_log(tmp_path, [_row("delete", "HALT")])
    dl._assert_no_unretracted_halt(tmp_path, action="prune")  # must not raise


def test_absent_log_is_accepted(tmp_path):
    """NEGATIVE CONTROL: no decisions log at all → nothing to block on."""
    dl._assert_no_unretracted_halt(tmp_path, action="prune")  # must not raise


# ---------------------------------------------------------------------------
# _assert_decision_chain_intact — deletion / reorder detection
# ---------------------------------------------------------------------------

_CHAIN_COLUMNS = [
    "date",
    "action_reviewed",
    "verdict",
    "conditions_verified",
    "author",
    "prev_digest",
]


def _chain_digest(row: dict[str, str]) -> str:
    return hashlib.sha256(dl._decision_signature_payload(row).encode("utf-8")).hexdigest()


def _chained_rows() -> list[dict[str, str]]:
    """Return two rows with a genuinely valid prev_digest chain."""
    row1 = {
        "date": "2026-01-01",
        "action_reviewed": "prune",
        "verdict": "HALT",
        "conditions_verified": "",
        "author": "security",
        "prev_digest": "",
    }
    row2 = {
        "date": "2026-01-02",
        "action_reviewed": "prune",
        "verdict": "HALT-RETRACTED",
        "conditions_verified": "yes",
        "author": "security",
        "prev_digest": _chain_digest(row1),
    }
    return [row1, row2]


def test_intact_chain_is_accepted():
    """NEGATIVE CONTROL: a correctly-chained log verifies without raising."""
    dl._assert_decision_chain_intact(_chained_rows(), _CHAIN_COLUMNS)  # must not raise


def test_broken_chain_is_rejected():
    """SENSITIVITY: tampering a row's prev_digest (a deleted/reordered/edited row) → refusal."""
    rows = _chained_rows()
    rows[1]["prev_digest"] = "0" * 64  # forged link
    with pytest.raises(RuntimeError, match="chain broken"):
        dl._assert_decision_chain_intact(rows, _CHAIN_COLUMNS)


def test_unchained_log_passes():
    """NEGATIVE CONTROL: a log without a prev_digest column is unchained and passes (opt-in)."""
    rows = _chained_rows()
    columns = [c for c in _CHAIN_COLUMNS if c != "prev_digest"]
    dl._assert_decision_chain_intact(rows, columns)  # must not raise


# ---------------------------------------------------------------------------
# _assert_authorizing_row_is_authentic — a requesting agent cannot self-clear (A4)
# ---------------------------------------------------------------------------

def test_authorizing_row_from_approved_author_is_accepted(tmp_path):
    """NEGATIVE CONTROL: a PASS from an approved security author (no signing, no roster) → accepted.

    Valid input → the verifier does not raise.
    """
    row = {"owner": "security", "decision": "prune", "status": "PASS"}
    dl._assert_authorizing_row_is_authentic(
        row, output_dir=tmp_path, signing_active=False, action="prune"
    )  # must not raise


def test_requesting_agent_cannot_clear_its_own_action(tmp_path):
    """SENSITIVITY (probe A4): an authorizing row whose author is not an approved security author
    → refusal. The requesting agent writing its own PASS must be rejected."""
    row = {"owner": "@primary-producer", "decision": "prune", "status": "PASS"}
    with pytest.raises(RuntimeError, match="not an approved security author"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=False, action="prune"
        )


def test_authorizing_row_outside_its_scope_is_rejected(tmp_path):
    """SENSITIVITY: a scoped clearance that does not list the requested action → refusal."""
    row = {"owner": "security", "decision": "delete", "status": "PASS", "scope": "delete"}
    with pytest.raises(RuntimeError, match="does not include"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=False, action="prune"
        )


# ---------------------------------------------------------------------------
# A HALT blocks every action named in its id (1.0 must-fix, mathAgents M6)
# ---------------------------------------------------------------------------

def test_a_halt_named_for_an_instance_blocks_the_action(tmp_path):
    """SENSITIVITY: a HALT recorded as m6-overwrite-render-x used to miss `overwrite` (prefix match only)."""
    _write_decisions_log(tmp_path, [_row("m6-overwrite-render-1a2fad6", "HALT")])
    with pytest.raises(RuntimeError, match="never been retracted"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_a_prefix_halt_still_blocks(tmp_path):
    _write_decisions_log(tmp_path, [_row("overwrite-replay-record-1", "HALT")])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_a_token_is_matched_whole_not_as_a_substring(tmp_path):
    """NEGATIVE CONTROL: `overwrites` and `overwritefoo` do not name `overwrite`."""
    _write_decisions_log(tmp_path, [_row("m6-overwrites-x", "HALT"), _row("overwritefoo", "HALT")])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # must not raise


def test_a_multi_token_action_must_appear_as_a_run(tmp_path):
    _write_decisions_log(tmp_path, [_row("m6-fleet-update-r3", "HALT")])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action="fleet-update")
    dl._assert_no_unretracted_halt(tmp_path, action="update-fleet")  # wrong order: no match


def test_an_instance_named_pass_still_does_not_clear_the_action():
    """The clearing side stays strict: widening it would let a row named for one thing clear another."""
    assert not dl._action_matches("m6-overwrite-render-1a2fad6", "overwrite")


def test_a_family_retraction_does_not_clear_a_token_only_halt(tmp_path):
    """Retracting plain `overwrite` must not silently retract an instance-specific HALT."""
    _write_decisions_log(tmp_path, [
        _row("m6-overwrite-render-1a2fad6", "HALT"),
        _row("overwrite", "HALT-RETRACTED", conditions="unrelated retraction", timestamp="2026-02-01T00:00:00Z"),
    ])
    with pytest.raises(RuntimeError, match="never been retracted"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_the_halts_own_id_retracts_it(tmp_path):
    _write_decisions_log(tmp_path, [
        _row("m6-overwrite-render-1a2fad6", "HALT"),
        _row("m6-overwrite-render-1a2fad6", "HALT-RETRACTED", conditions="operator cleared in session",
             timestamp="2026-02-01T00:00:00Z"),
    ])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # must not raise


def test_a_family_retraction_still_clears_prefix_halts(tmp_path):
    """Unchanged behaviour: a retraction for the action clears HALTs that prefix-match it."""
    _write_decisions_log(tmp_path, [
        _row("overwrite-001", "HALT"),
        _row("overwrite", "HALT-RETRACTED", conditions="no longer applies", timestamp="2026-02-01T00:00:00Z"),
    ])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # must not raise


@pytest.mark.parametrize("halt_id", ["m6OverwriteRender", "XMLOverwrite", "overwrite2", "(overwrite)", "overwrite, then render",
                                     "overwrite post-render closing check (P5)"])
def test_the_token_boundary_is_wide_on_the_halt_side(tmp_path, halt_id):
    """@adversarial: camelCase, digits and punctuation must not let a HALT id dodge the match."""
    _write_decisions_log(tmp_path, [_row(halt_id, "HALT")])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_a_scoped_exact_id_retraction_clears_its_halt(tmp_path):
    """@adversarial 3: an exact-id retraction scoped to the HALT's own id must clear it, not raise."""
    cols = _LEGACY_COLUMNS + ["scope"]
    refs = tmp_path / "references"
    refs.mkdir(parents=True)
    with (refs / "security-decisions.log.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({**_row("m6-overwrite-render-x", "HALT"), "scope": ""})
        w.writerow({**_row("m6-overwrite-render-x", "HALT-RETRACTED", conditions="settled",
                           timestamp="2026-02-01T00:00:00Z"), "scope": "m6-overwrite-render-x"})
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # must not raise



# --- #596 (OD7, 1.3.0): only a retraction of exactly the gate action clears its family; any other, its own id ----

_FAM = "s10-cooldown-exempt"


def _write_scoped(root: Path, rows: list[dict[str, str]]) -> None:
    cols = _LEGACY_COLUMNS + ["scope"]
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    with (refs / "security-decisions.log.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({col: row.get(col, "") for col in cols})


def _retract(action: str, **over: str) -> dict[str, str]:
    return _row(action, "HALT-RETRACTED", conditions="no longer applies", timestamp="2026-02-01T00:00:00Z", **over)


def test_a_member_retraction_leaves_the_family_halt_open(tmp_path):
    _write_decisions_log(tmp_path, [_row(_FAM, "HALT"), _retract(f"{_FAM}:o/b")])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action=_FAM)


def test_a_member_retraction_leaves_a_sibling_halt_open(tmp_path):
    _write_decisions_log(tmp_path, [_row(f"{_FAM}:o/a", "HALT"), _row(f"{_FAM}:o/b", "HALT"),
                                    _retract(f"{_FAM}:o/b")])
    for gate in (_FAM, f"{_FAM}:o/a"):
        with pytest.raises(RuntimeError, match="HALT"):
            dl._assert_no_unretracted_halt(tmp_path, action=gate)
    dl._assert_no_unretracted_halt(tmp_path, action=f"{_FAM}:o/b")  # its own HALT is retracted


def test_a_member_retraction_clears_its_own_halt_at_the_family_gate(tmp_path):
    _write_decisions_log(tmp_path, [_row(f"{_FAM}:o/b", "HALT"), _retract(f"{_FAM}:o/b")])
    dl._assert_no_unretracted_halt(tmp_path, action=_FAM)  # must not raise


def test_a_member_retraction_never_clears_a_token_only_family_halt(tmp_path):
    _write_decisions_log(tmp_path, [_row("m6-overwrite-render-x", "HALT"), _retract("overwrite-002")])
    with pytest.raises(RuntimeError, match="never been retracted"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


@pytest.mark.parametrize("scope", ["", f"{_FAM}:o/b"])
def test_a_scoped_member_retraction_behaves_like_an_unscoped_one(tmp_path, scope):
    _write_scoped(tmp_path, [{**_row(_FAM, "HALT"), "scope": ""}, {**_row(f"{_FAM}:o/b", "HALT"), "scope": ""},
                             {**_retract(f"{_FAM}:o/b"), "scope": scope}])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action=_FAM)       # the family HALT stays open
    _write_scoped(tmp_path, [{**_row(f"{_FAM}:o/b", "HALT"), "scope": ""}, {**_retract(f"{_FAM}:o/b"), "scope": scope}])
    dl._assert_no_unretracted_halt(tmp_path, action=f"{_FAM}:o/b")  # its own HALT is retracted either way


def test_a_member_retraction_scoped_to_its_own_id_clears_its_halt_at_the_family_gate(tmp_path):
    """@security review of 1cd73b9b: before #596 the family-gate scope check refused this named retraction."""
    _write_scoped(tmp_path, [{**_row(f"{_FAM}:o/b", "HALT"), "scope": ""},
                             {**_retract(f"{_FAM}:o/b"), "scope": f"{_FAM}:o/b"}])
    dl._assert_no_unretracted_halt(tmp_path, action=_FAM)  # must not raise


@pytest.mark.parametrize(("halt", "retraction", "scope"), [
    ("overwrite", "overwrite-retract", ""),           # a descriptive suffix is a member id, not the family
    (f"{_FAM}:o/b", f"{_FAM}:o/b", _FAM),             # a member id scoped to the family: its scope does not permit it
    (f"{_FAM}:o/a", f"{_FAM.upper()}:O/B", ""),       # case never turns a member into the family
])
def test_a_retraction_that_does_not_name_the_halt_leaves_it_open(tmp_path, halt, retraction, scope):
    gate = halt.split(":")[0]
    _write_scoped(tmp_path, [{**_row(halt, "HALT"), "scope": ""}, {**_retract(retraction), "scope": scope}])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action=gate)


def test_an_exact_family_retraction_ignores_case_and_whitespace(tmp_path):
    _write_decisions_log(tmp_path, [_row(f"{_FAM}:o/a", "HALT"), _retract(f" {_FAM.upper()} ")])
    dl._assert_no_unretracted_halt(tmp_path, action=_FAM)  # must not raise

# --- a HALT with no usable id blocks every action (it used to block none) -----------------------

_NO_ID = ["", "   ", "--", "(…)"]


def _handle(rows: list[dict[str, str]], number: int) -> str:
    """The row handle of the HALT on 1-based row *number*, computed as the gate does."""
    from agentteams.cli.action_ids import halt_row_handle

    return halt_row_handle(number, rows[number - 1])


@pytest.mark.parametrize("halt_id", _NO_ID)
def test_a_halt_with_no_usable_id_blocks_every_action(tmp_path, halt_id):
    rows = [_row(halt_id, "HALT"), _row("overwrite", "PASS", timestamp="2026-02-01T00:00:00Z")]
    _write_decisions_log(tmp_path, rows)
    for action in ("overwrite", "prune", "some-custom-action"):
        with pytest.raises(RuntimeError, match=r"row 1, .*no usable action id.*" + _handle(rows, 1)):
            dl._assert_no_unretracted_halt(tmp_path, action=action)


def test_a_halt_with_an_id_still_blocks_only_what_it_names(tmp_path):
    """Negative control: the id-less rule does not widen a HALT that has an id."""
    _write_decisions_log(tmp_path, [_row("prune", "HALT")])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # must not raise


@pytest.mark.parametrize("halt_id", _NO_ID)
def test_the_row_handle_retracts_a_halt_with_no_usable_id(tmp_path, halt_id):
    rows = [_row("prune", "PASS"), _row(halt_id, "HALT")]
    assert _handle(rows, 2).startswith("halt-row:2:") and len(_handle(rows, 2)) == len("halt-row:2:") + 16
    _write_decisions_log(tmp_path, rows + [_retract(f" {_handle(rows, 2).upper()} ")])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # must not raise


@pytest.mark.parametrize("retraction", ["overwrite", "", "--", "halt-row:2", "halt-row", "halt-row:2:0000000000000000",
                                        "ROW1", "ROW3"])
def test_nothing_but_its_own_row_handle_retracts_a_halt_with_no_usable_id(tmp_path, retraction):
    rows = [_row("", "HALT", timestamp="2025-12-01T00:00:00Z"), _row("", "HALT"), _row("", "HALT", conditions="x",
                                                                                    timestamp="2026-01-02T00:00:00Z")]
    retraction = {"ROW1": _handle(rows, 1), "ROW3": _handle(rows, 3)}.get(retraction, retraction)
    _write_decisions_log(tmp_path, rows + [_retract(_handle(rows, 1)), _retract(_handle(rows, 3)), _retract(retraction)])
    with pytest.raises(RuntimeError, match=_handle(rows, 2)):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_a_row_handle_retraction_needs_evidence(tmp_path):
    rows = [_row("", "HALT")]
    _write_decisions_log(tmp_path, rows + [{**_retract(_handle(rows, 1)), "conditions": ""}])
    with pytest.raises(RuntimeError, match="carries no evidence"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_a_row_handle_stops_matching_when_rows_before_the_halt_change(tmp_path):
    """@security: rows in an unchained prefix can be added or removed. A shifted HALT must block again."""
    halt, other = _row("", "HALT"), _row("", "HALT", timestamp="2025-12-01T00:00:00Z")
    retraction = _retract(_handle([halt], 1))
    _write_decisions_log(tmp_path, [halt, retraction])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")  # as written: retracted
    _write_decisions_log(tmp_path, [_row("prune", "PASS"), halt, retraction])  # a row inserted before it
    with pytest.raises(RuntimeError, match="no usable action id"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")
    _write_decisions_log(tmp_path, [other, retraction])  # a different HALT moved onto row 1
    with pytest.raises(RuntimeError, match="no usable action id"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


@pytest.mark.parametrize(("scope", "retracted"), [("", True), ("HANDLE", True), ("overwrite", False)])
def test_a_scoped_row_handle_retraction_must_name_the_handle_in_its_scope(tmp_path, scope, retracted):
    rows = [{**_row("", "HALT"), "scope": ""}]
    handle = _handle(rows, 1)
    _write_scoped(tmp_path, rows + [{**_retract(handle), "scope": handle if scope == "HANDLE" else scope}])
    if retracted:
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")
    else:
        with pytest.raises(RuntimeError, match="no usable action id"):
            dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_a_halt_with_no_usable_id_blocks_through_check_clearance(tmp_path):
    from agentteams.cli.security_gate import check_clearance

    rows = [_row("", "HALT"), _row("overwrite", "PASS", timestamp="2026-02-01T00:00:00Z")]
    _write_decisions_log(tmp_path, rows)
    cleared, why = check_clearance(tmp_path, action="overwrite")
    assert not cleared and _handle(rows, 1) in why
    # Tools build the retraction from the id the refusal names (`vulnscan halts`): it is the handle, not the action.
    assert f"latest decision for action '{_handle(rows, 1)}' is HALT" in why


def test_the_pre_upgrade_report_lists_halts_with_no_usable_id(tmp_path):
    from agentteams.cli.action_ids import describe_newly_blocking_halts, idless_open_halts

    rows = [_row("", "HALT"), _row("--", "HALT"), _row("overwrite", "PASS")]
    rows.append(_retract(_handle(rows, 1)))
    assert idless_open_halts(rows) == [(2, _handle(rows, 2))]
    _write_decisions_log(tmp_path, rows)
    text = "\n".join(describe_newly_blocking_halts(tmp_path))
    assert "no usable id (1)" in text and "EVERY gated action" in text
    assert "row 2: " in text and f"'{_handle(rows, 2)}'" in text and _handle(rows, 1) not in text
    assert "row 2: '--'" not in text  # not also listed among the ordinary HALTs


def test_the_pre_upgrade_report_names_newly_blocking_halts(tmp_path):
    from agentteams.cli.action_ids import describe_newly_blocking_halts, newly_blocking_halts

    rows = [_row("m6-overwrite-render-x", "HALT"), _row("overwrite", "HALT"),
            _row("cross-repo-merge PR 105", "HALT"), _row("old-prune-x", "HALT"),
            _row("old-prune-x", "HALT-RETRACTED", conditions="settled")]
    assert newly_blocking_halts(rows) == [(1, "m6-overwrite-render-x", "overwrite")]
    _write_decisions_log(tmp_path, rows)
    text = "\n".join(describe_newly_blocking_halts(tmp_path))
    assert "row 1: 'm6-overwrite-render-x' blocks 'overwrite'" in text
    assert "row 3: 'cross-repo-merge PR 105'" in text  # custom-action blockers are listed too
    assert "old-prune-x" not in text


def test_every_gate_call_site_is_listed():
    """@adversarial: the reverse direction. Every literal or constant action passed to a gate is in
    GATE_ACTIONS, and a variable action appears only at the one dynamic site (branch-cleanup:<sha>)."""
    import re as _re
    from pathlib import Path as _P

    from agentteams.cli.action_ids import GATE_ACTIONS

    repo = _P(__file__).resolve().parents[1]
    files = [*(repo / "agentteams").rglob("*.py"), repo / "build_team.py"]
    sources = {f: f.read_text(encoding="utf-8") for f in files}
    constants = dict(_re.findall(r'^([A-Z_]+)\s*=\s*"([a-z][a-z0-9-]*)"', "\n".join(sources.values()), _re.M))
    call = _re.compile(r"(?:_assert_destructive_action_allowed|check_clearance|_assert_no_unretracted_halt)"
                       r"\([^)]*?action=(\"[^\"]+\"|[A-Za-z_]+)")
    dynamic = []
    sandbox_sites: list[str] = []
    s10_sites: list[str] = []
    lineage_sites: list[str] = []
    for path, text in sources.items():
        for arg in call.findall(text):
            if arg.startswith('"'):
                assert arg.strip('"') in GATE_ACTIONS, (path.name, arg)
            elif arg.isupper() or arg in constants:
                assert constants[arg] in GATE_ACTIONS, (path.name, arg)
            elif arg == "action" and path.name in {"branch_cleanup.py", "security_gate.py"}:
                dynamic.append(path.name)
            elif arg in {"relaxation_action", "relaxation_family"} and path.name == "sandbox_relaxation_gate.py":
                sandbox_sites.append(arg)  # <kind>:<qualifier> and the bare <kind>, both checked below
            elif arg == "action" and path.name == "known_report.py":
                # Known-entry ids only: the site must refuse anything but ^vk-[0-9a-f]{16}$ before the gate call.
                from agentteams.vulnscan.known import ENTRY_ID_RE

                assert ENTRY_ID_RE.pattern == r"^vk-[0-9a-f]{16}$"
                call_text = "_assert_no_unretracted_halt(gov, action=action)"
                assert text.count(call_text) == 1 and text.count("if not ENTRY_ID_RE.fullmatch(action):") == 1
                guard = text.index("if not ENTRY_ID_RE.fullmatch(action):")
                assert guard < text.index(call_text) < guard + 400
            elif arg == "action" and path.name == "server_apply.py":
                # Per-path ids only: both calls use the one action built from removal_action, in the server-apply family.
                from agentteams.vulnscan.server_apply import removal_action

                assert "server-apply" in GATE_ACTIONS and removal_action("h", "/p") == "server-apply:remove-path:h:/p"
                assert text.count("action = removal_action(host, path)") == 1 and text.count("action=action") == 2
            elif arg in {"lid", "own_action"} and path.name == "carveout_checks.py":
                # A carve-out's lineage ids (logged decisions) and its own carveout:<id>: HALT scans only.
                assert "carveout" in GATE_ACTIONS and 'own_action = f"{GATE_FAMILY}:{entry[\'id\']}"' in text
            elif arg in {"scope", "action"} and path.name == "clearance_binding.py":
                # <scope>:sha256-<hex>: the scope is refused unless it is a plain action id, before both calls.
                assert text.count("action = clearance_action(scope, path)") == 1
                assert text.index("action = clearance_action(scope, path)") < text.index("action=scope)")
            elif arg in {"exempt_action", "exempt_family"} and path.name == "s10_exempt_gate.py":
                s10_sites.append(arg)  # s10-cooldown-exempt:<owner/name> and the bare family, checked below
            elif arg == "halt_action" and path.name == "s10_deprecation_gate.py":
                # agentteams#592: HALT scans only, over HALT_ACTIONS. Bare s10-cooldown is a HALT family and never a
                # clearable action (every s10-cooldown-* row would match it as a prefix), so it is not in GATE_ACTIONS.
                from agentteams.cli.s10_deprecation_gate import HALT_ACTIONS

                assert "for halt_action in HALT_ACTIONS:" in text
                assert set(HALT_ACTIONS) - {"s10-cooldown"} <= set(GATE_ACTIONS)
                assert "s10-cooldown" not in GATE_ACTIONS
            elif arg == "halt_family" and path.name == "dev_merge.py":
                # The integration-branch merge authorization's two HALT families, both listed.
                assert "for halt_family in (FAMILY, ACTION_FAMILY):" in text
                assert {"integration-merge", "dev-merge"} <= set(GATE_ACTIONS)
            elif arg == "merge_action" and path.name == "dev_merge.py":
                # dev-merge:<owner/name>:pr-<N>-<sha>, built (and validated) by merge_action_id only.
                assert text.count("merge_action = merge_action_id(repo, number, head_sha)") == 1
                assert "dev-merge" in GATE_ACTIONS
            elif arg == "halt_scan_action" and path.name == "operator_signing.py":
                # bl48-01: the signer's refusal of a clearance under an open HALT. A HALT scan only, never a clearance.
                assert text.count("_assert_no_unretracted_halt(output_dir, action=halt_scan_action)") == 1
                assert "_assert_destructive_action_allowed" not in text and "check_clearance" not in text
            elif arg == "lineage_action" and path.name == "toolchain_group.py":
                lineage_sites.append(arg)  # the group file's derives_from chain (v1.1.3): exactly one site, below
            else:
                raise AssertionError(f"unlisted gate action {arg!r} in {path.name}")
    assert "branch-cleanup" in GATE_ACTIONS and dynamic
    # The sandbox gate's ids are built from SANDBOX_RELAXATION_KINDS, every family of which is listed.
    from agentteams.cli.sandbox_effects import SANDBOX_RELAXATION_KINDS

    assert set(sandbox_sites) == {"relaxation_action", "relaxation_family"}
    assert set(SANDBOX_RELAXATION_KINDS) <= set(GATE_ACTIONS)
    # The S-10 carve-out gate builds s10-cooldown-exempt:<owner/name>; its family is listed.
    from agentteams.cli.s10_exempt_gate import S10_EXEMPT_KIND

    assert set(s10_sites) == {"exempt_action", "exempt_family"} and S10_EXEMPT_KIND in GATE_ACTIONS
    assert lineage_sites == ["lineage_action"]   # one call site only; a second must be reviewed and listed
    # A call shape the regex cannot parse would be skipped silently; the count makes such a miss fail.
    assert sum(len(call.findall(t)) for t in sources.values()) >= 11


def test_every_listed_gate_action_is_a_real_gate_call():
    """GATE_ACTIONS must stay in step with the code that calls the gate."""
    from pathlib import Path as _P

    from agentteams.cli.action_ids import GATE_ACTIONS

    repo = _P(__file__).resolve().parents[1]
    source = "\n".join(p.read_text(encoding="utf-8") for p in [*(repo / "agentteams").rglob("*.py"), repo / "build_team.py"])
    for action in GATE_ACTIONS:
        assert f'"{action}"' in source, action


# ---------------------------------------------------------------------------
# bl48-11: the gate never reads its switch or its log through a symlinked directory
# ---------------------------------------------------------------------------

def _linked_team(tmp_path: Path) -> Path:
    """A team whose ``references/`` links to an outside dir holding a switch and a HALT-carrying log."""
    outside = tmp_path / "outside"
    _write_decisions_log(outside, [_row("prune", "HALT")])
    (outside / "references" / "agent-privilege.json").write_text('{"enforce_decision_signing": true}')
    team = tmp_path / "team"
    team.mkdir()
    (team / "references").symlink_to(outside / "references", target_is_directory=True)
    return team


def test_gate_refuses_a_symlinked_references_dir(tmp_path):
    team = _linked_team(tmp_path)
    with pytest.raises(RuntimeError, match="symlink"):
        dl._enforce_decision_signing(team)
    with pytest.raises(RuntimeError, match="symlink"):
        dl._read_decision_rows(team)
    with pytest.raises(RuntimeError, match="symlink"):
        dl._assert_no_unretracted_halt(team, action="other")   # the outside log is never read as the team's


def test_gate_refuses_a_symlinked_decisions_log(tmp_path):
    outside = tmp_path / "outside"
    _write_decisions_log(outside, [_row("prune", "PASS")])
    (tmp_path / "team" / "references").mkdir(parents=True)
    (tmp_path / "team" / "references" / "security-decisions.log.csv").symlink_to(
        outside / "references" / "security-decisions.log.csv")
    with pytest.raises(RuntimeError, match="symlink"):
        dl._read_decision_rows(tmp_path / "team")


def test_gate_reads_a_real_references_dir(tmp_path):
    # Negative control: the same files in a real references/ dir read normally.
    _write_decisions_log(tmp_path, [_row("prune", "HALT")])
    (tmp_path / "references" / "agent-privilege.json").write_text('{"enforce_decision_signing": true}')
    assert dl._enforce_decision_signing(tmp_path) is True
    rows, _ = dl._read_decision_rows(tmp_path)
    assert rows[0]["verdict"] == "HALT"


# ---------------------------------------------------------------------------
# bl48-02 (#636, ruling R-18): a HALT on a family id blocks every id under it after a colon
# ---------------------------------------------------------------------------

_SA = "server-apply:remove-path:web1:/srv/old"


def test_family_halt_blocks_instance_clearance(tmp_path):
    from agentteams.cli import security_gate

    _write_decisions_log(tmp_path, [_row("server-apply", "HALT"), _row(_SA, "PASS")])
    ok, why = security_gate.check_clearance(tmp_path, action=_SA)
    assert not ok and "HALT" in why


@pytest.mark.parametrize("halt_id,action", [
    ("server-apply-old", _SA),           # no colon boundary
    ("server", "serverless:x"),          # a substring, not a family
    ("branch", "branch-delete"),         # R-18: '-' is not a family delimiter
    ("vulnscan", "vulnscan_publish"),    # nor '_'
    ("carve", "carve.out:x"),            # nor '.'
])
def test_family_halt_needs_a_colon_boundary(tmp_path, halt_id, action):
    from agentteams.cli import security_gate

    _write_decisions_log(tmp_path, [_row(halt_id, "HALT"), _row(action, "PASS")])
    assert security_gate.check_clearance(tmp_path, action=action) == (True, "")   # negative control


def test_intermediate_scope_halt_blocks_path(tmp_path):
    _write_decisions_log(tmp_path, [_row("server-apply:remove-path:web1", "HALT")])
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action=_SA)
    dl._assert_no_unretracted_halt(tmp_path, action="server-apply:remove-path:web2:/srv/old")  # must not raise


def test_family_halt_retracted_only_by_its_own_id(tmp_path):
    rows = [_row("server-apply", "HALT"),
            _row(_SA, "HALT-RETRACTED", conditions="instance settled", timestamp="2026-02-01T00:00:00Z")]
    _write_decisions_log(tmp_path, rows)
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action=_SA)
    _write_decisions_log(tmp_path, [*rows, _row("server-apply", "HALT-RETRACTED", conditions="family settled",
                                                  timestamp="2026-02-02T00:00:00Z")])
    dl._assert_no_unretracted_halt(tmp_path, action=_SA)  # must not raise


def test_newly_blocking_halts_lists_family_halts(tmp_path):
    from agentteams.cli.action_ids import describe_newly_blocking_halts

    _write_decisions_log(tmp_path, [_row("server-apply", "HALT"), _row("deploy", "HALT"),
                                    _row("deploy", "HALT-RETRACTED", conditions="done",
                                         timestamp="2026-02-01T00:00:00Z")])
    text = "\n".join(describe_newly_blocking_halts(tmp_path))
    assert "'server-apply' now also blocks every id under it ('server-apply:...')" in text
    assert "agentteams gate family 'server-apply'" in text
    assert "'deploy' now also blocks" not in text   # retracted: not listed
    _write_decisions_log(tmp_path, [_row("deploy", "HALT")])   # a consumer-defined id is listed too
    assert "'deploy' now also blocks every id under it ('deploy:...')" in "\n".join(
        describe_newly_blocking_halts(tmp_path))


# ---------------------------------------------------------------------------
# bl48-01 (#616, ruling R-17): after a HALT is retracted, no clearance older than the retraction survives
# ---------------------------------------------------------------------------

def test_pass_recorded_under_halt_is_void_after_retraction(tmp_path):
    from agentteams.cli import security_gate

    _write_decisions_log(tmp_path, [_row("overwrite", "HALT"), _row("overwrite", "PASS"), _retract("overwrite")])
    ok, why = security_gate.check_clearance(tmp_path, action="overwrite")
    assert not ok and "row 2" in why and "row 3" in why and "#616" in why
    # Negative control: a clearance recorded after the retraction clears.
    _write_decisions_log(tmp_path, [_row("overwrite", "HALT"), _retract("overwrite"), _row("overwrite", "PASS")])
    assert security_gate.check_clearance(tmp_path, action="overwrite") == (True, "")


def test_pass_before_halt_is_void_after_retraction(tmp_path):
    # R-17 replaces the plan body: a PASS older than the retraction is void even when it predates the HALT.
    from agentteams.cli import security_gate

    # (Only a family HALT leaves the older PASS as the newest matching row; an exact-id HALT is itself newest.)
    _write_decisions_log(tmp_path, [_row(_SA, "PASS"), _row("server-apply", "HALT"), _retract("server-apply")])
    ok, why = security_gate.check_clearance(tmp_path, action=_SA)
    assert not ok and "row 1" in why and "#616" in why


def test_pass_under_family_halt_is_void(tmp_path):
    from agentteams.cli import security_gate

    _write_decisions_log(tmp_path, [_row("server-apply", "HALT"), _row(_SA, "PASS"), _retract("server-apply")])
    ok, why = security_gate.check_clearance(tmp_path, action=_SA)
    assert not ok and "#616" in why


def test_a_retraction_of_another_action_voids_nothing(tmp_path):
    from agentteams.cli import security_gate

    _write_decisions_log(tmp_path, [_row("overwrite", "PASS"), _row("prune", "HALT"), _retract("prune")])
    assert security_gate.check_clearance(tmp_path, action="overwrite") == (True, "")   # negative control
