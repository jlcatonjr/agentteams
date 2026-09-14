"""Tests for Workstream A — lineage-aware HALT scan + relaxing-row traceability (decision_log.py).

Covers Gap A: a HALT propagates along the transitive ``derives_from`` lineage, so renaming a
HALTed action's scope-id no longer evades C-2. Traversal is fail-closed on cycles and on a
reference to an id no row defines. Also covers the governed-workspace refusal of a relaxing
authorizing row that declares no lineage.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl

_COLUMNS = [
    "timestamp", "requesting_agent", "action_reviewed", "verdict",
    "conditions", "conditions_verified", "evidence", "derives_from",
]


def _write_log(root: Path, rows: list[dict[str, str]], *, columns: list[str] = _COLUMNS) -> None:
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    with (refs / "security-decisions.log.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def _r(action: str, verdict: str, **over: str) -> dict[str, str]:
    base = {
        "timestamp": "2026-01-01T00:00:00Z",
        "requesting_agent": "security",
        "action_reviewed": action,
        "verdict": verdict,
        "conditions": "",
        "conditions_verified": "",
        "evidence": "",
        "derives_from": "",
    }
    base.update(over)
    return base


def _govern(root: Path) -> None:
    """Turn the strict agent-privilege switch ON (a governed workspace)."""
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "references" / "agent-privilege.json").write_text(
        json.dumps({"enforce_decision_signing": True}), encoding="utf-8"
    )


# --- lineage HALT propagation (Gap A) ---------------------------------------------------------


def test_halt_propagates_along_lineage(tmp_path):
    # cleanup-collectors derives from prune-collectors, which is under an unretracted HALT.
    _write_log(tmp_path, [
        _r("prune-collectors", "HALT"),
        _r("cleanup-collectors", "PASS", derives_from="prune-collectors"),
    ])
    with pytest.raises(RuntimeError, match="derives from 'prune-collectors'"):
        dl._assert_no_unretracted_halt(tmp_path, action="cleanup-collectors")


def test_lineage_halt_cleared_by_ancestor_retraction(tmp_path):
    _write_log(tmp_path, [
        _r("prune-collectors", "HALT"),
        _r("prune-collectors", "HALT-RETRACTED", evidence="root cause fixed"),
        _r("cleanup-collectors", "PASS", derives_from="prune-collectors"),
    ])
    # No open HALT on the ancestor → the derived action is not blocked.
    dl._assert_no_unretracted_halt(tmp_path, action="cleanup-collectors")


def test_transitive_lineage_two_hops(tmp_path):
    _write_log(tmp_path, [
        _r("a", "HALT"),
        _r("b", "PASS", derives_from="a"),
        _r("c", "PASS", derives_from="b"),
    ])
    with pytest.raises(RuntimeError, match="unretracted HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action="c")


def test_unrelated_action_not_blocked_by_lineage(tmp_path):
    _write_log(tmp_path, [
        _r("prune-collectors", "HALT"),
        _r("draft-report", "PASS", derives_from=""),
    ])
    dl._assert_no_unretracted_halt(tmp_path, action="draft-report")


# --- fail-closed traversal --------------------------------------------------------------------


def test_lineage_cycle_fails_closed(tmp_path):
    _write_log(tmp_path, [
        _r("a", "PASS", derives_from="b"),
        _r("b", "PASS", derives_from="a"),
    ])
    with pytest.raises(RuntimeError, match="failed closed|cycle"):
        dl._assert_no_unretracted_halt(tmp_path, action="a")


def test_lineage_nonexistent_id_fails_closed(tmp_path):
    _write_log(tmp_path, [
        _r("b", "PASS", derives_from="ghost-action"),
    ])
    with pytest.raises(RuntimeError, match="failed closed|nonexistent|no recorded"):
        dl._assert_no_unretracted_halt(tmp_path, action="b")


def test_off_first_path_cycle_detected(tmp_path):
    # F-4: a cycle whose back-edge is not on the first-discovery path must still raise.
    # a -> {b, c}; b -> d; c -> d; d -> b  (cycle b <-> d).
    lineage_map = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": {"b"}}
    with pytest.raises(RuntimeError, match="cycle"):
        dl._transitive_ancestors("a", lineage_map)


def test_diamond_without_cycle_is_not_a_false_cycle(tmp_path):
    # a -> {b, c}; b -> d; c -> d; d -> (root).  Shared ancestor d, but NO cycle → must resolve.
    lineage_map = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}
    assert dl._transitive_ancestors("a", lineage_map) == {"b", "c", "d"}


# --- F-1: derives_from is inside the signature envelope ----------------------------------------


def test_lineage_is_a_signed_axis(tmp_path):
    # A row with no lineage signs the pre-WS-A payload (legacy signatures unaffected)...
    base = {"date": "2026-01-01", "action_reviewed": "prune", "verdict": "PASS",
            "conditions_verified": "verified", "author": "security", "prev_digest": ""}
    payload_no_lineage = dl._decision_signature_payload(base)
    # ...and adding/changing derives_from changes the signed payload (so tamper breaks the HMAC).
    with_lineage = {**base, "derives_from": "approved-cleanup"}
    payload_with = dl._decision_signature_payload(with_lineage)
    assert payload_with != payload_no_lineage
    tampered = {**base, "derives_from": "something-else"}
    assert dl._decision_signature_payload(tampered) != payload_with

    key = "k"
    sig = dl.sign_decision_row(with_lineage, key=key)
    assert dl.sign_decision_row({**base, "derives_from": "attacker-cleared"}, key=key) != sig


# --- backward compatibility (no derives_from column) ------------------------------------------


def test_no_lineage_column_keeps_prelineage_behaviour(tmp_path):
    legacy = ["timestamp", "requesting_agent", "action_reviewed", "verdict",
              "conditions", "conditions_verified"]
    _write_log(tmp_path, [_r("prune-collectors", "HALT")], columns=legacy)
    # A direct HALT still refuses...
    with pytest.raises(RuntimeError, match="HALT"):
        dl._assert_no_unretracted_halt(tmp_path, action="prune-collectors")
    # ...and an unrelated action is unaffected (no lineage traversal without the column).
    dl._assert_no_unretracted_halt(tmp_path, action="draft-report")


# --- relaxing-row traceability (governed workspace) -------------------------------------------


def _exception_row(**over) -> dict[str, str]:
    # A constraint-relaxing EXCEPTION (grants a capability) — the class that must be traceable.
    base = {"action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
            "effect_grants": "write:cross-repo", "derives_from": ""}
    base.update(over)
    return base


def test_exception_row_missing_lineage_refused_when_governed(tmp_path):
    _govern(tmp_path)
    row = _exception_row()
    with pytest.raises(RuntimeError, match="declares no"):
        dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="grant-cross-repo-write")


def test_routine_destructive_row_needs_no_lineage_when_governed(tmp_path):
    # A routine destructive action is NOT an exception — it keeps the ordinary clearance and is not
    # forced to declare lineage (fix for the --overwrite regression).
    _govern(tmp_path)
    row = {"action_reviewed": "prune-collectors", "verdict": "PASS", "derives_from": ""}
    dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="prune-collectors")


def test_exception_row_with_resolvable_lineage_accepted_when_governed(tmp_path):
    _govern(tmp_path)
    # The ancestor must be a RECORDED decision action-id (F-3 reconciliation), not a plan slug.
    _write_log(tmp_path, [_r("approved-cleanup", "PASS")])
    row = _exception_row(derives_from="approved-cleanup")
    dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="grant-cross-repo-write")


def test_exception_row_with_dangling_lineage_refused_when_governed(tmp_path):
    # F-3: a non-empty but UNRESOLVABLE lineage (a plan slug / fabricated id) must refuse here,
    # matching the end-to-end HALT scan rather than accepting then bricking downstream.
    _govern(tmp_path)
    _write_log(tmp_path, [_r("approved-cleanup", "PASS")])
    row = _exception_row(derives_from="approved-cleanup-plan")  # a slug, not a recorded action-id
    with pytest.raises(RuntimeError, match="untraceable relaxing lineage|no decision row records"):
        dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="grant-cross-repo-write")


def test_traceability_rejects_deep_dangling_lineage(tmp_path):
    # F-3 v2: traceability runs the whole-chain traversal, so a recorded direct parent whose OWN
    # lineage dangles is refused here (not just at the downstream HALT scan).
    _govern(tmp_path)
    _write_log(tmp_path, [_r("mid", "PASS", derives_from="ghost")])
    row = _exception_row(action_reviewed="grant-cross-repo-write", derives_from="mid")
    with pytest.raises(RuntimeError, match="untraceable relaxing lineage"):
        dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="grant-cross-repo-write")


def test_traceability_rejects_cycle_through_recorded_ids(tmp_path):
    # F-3 v2: a cycle among recorded ids (here the row deriving from an ancestor that derives back)
    # is refused at traceability, matching the transitive scan.
    _govern(tmp_path)
    _write_log(tmp_path, [_r("grant-cross-repo-write", "PASS", derives_from="mid"),
                          _r("mid", "PASS", derives_from="grant-cross-repo-write")])
    row = _exception_row(action_reviewed="grant-cross-repo-write", derives_from="mid")
    with pytest.raises(RuntimeError, match="untraceable relaxing lineage|cycle"):
        dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="grant-cross-repo-write")


def test_halt_retracted_row_exempt_from_traceability(tmp_path):
    # F-5: a retraction is relaxing-by-definition but needs no derives_from (its target is the
    # action-id it matches); it must not be refused for lack of lineage in a governed workspace.
    _govern(tmp_path)
    row = {"action_reviewed": "prune-collectors", "verdict": "HALT-RETRACTED",
           "evidence": "fixed", "derives_from": ""}
    dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="prune-collectors")


def test_benign_row_without_lineage_accepted_when_governed(tmp_path):
    _govern(tmp_path)
    row = {"action_reviewed": "draft-weekly-report", "verdict": "PASS", "derives_from": ""}
    dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="draft-weekly-report")


def test_traceability_noop_when_ungoverned(tmp_path):
    # No agent-privilege switch → ungoverned → a relaxing row without lineage is NOT refused here
    # (legacy behaviour preserved; the elevated path is what governs such rows in WS-B).
    row = {"action_reviewed": "prune-collectors", "verdict": "PASS", "derives_from": ""}
    dl._assert_relaxing_row_is_traceable(row, output_dir=tmp_path, action="prune-collectors")
