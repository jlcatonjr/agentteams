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

The pure id-matcher ``_action_matches`` is registered ``not-a-verifier``; its match decision is
exercised here through its caller ``_assert_no_unretracted_halt`` with both a matching action (which
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


@pytest.mark.parametrize("halt_id", ["m6OverwriteRender", "overwrite2", "(overwrite)", "overwrite, then render",
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


def test_every_listed_gate_action_is_a_real_gate_call():
    """GATE_ACTIONS must stay in step with the code that calls the gate."""
    from pathlib import Path as _P

    from agentteams.cli.action_ids import GATE_ACTIONS

    repo = _P(__file__).resolve().parents[1]
    source = "\n".join(p.read_text(encoding="utf-8") for p in [*(repo / "agentteams").rglob("*.py"), repo / "build_team.py"])
    for action in GATE_ACTIONS:
        assert f'"{action}"' in source, action
