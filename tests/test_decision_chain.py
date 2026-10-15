"""test_decision_chain.py — the decision log's hash chain, including the pre-1.0 legacy prefix (row 338).

Before 1.0, ``--sign-decision`` created chained logs but never filled ``prev_digest``, so the second
signed row broke the chain and every gated action was refused. These tests pin the repair: the
signer links each row to the tip, and a leading run of empty links is accepted only when signing is
active, with the running digest kept through it so the first real link still binds its predecessor.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl
from agentteams.cli.decision_chain import chain_tip, row_chain_digest, verify_chain

SIGNED = ["timestamp", "requesting_agent", *dl.SIGNED_DECISION_COLUMNS]
UNSIGNED = ["date", "action_reviewed", "verdict", "conditions_verified", "author", "prev_digest"]


def _row(n: int, verdict: str = "PASS", prev: str = "") -> dict[str, str]:
    return {"date": f"2026-10-{n:02d}", "action_reviewed": f"act-{n}", "verdict": verdict,
            "conditions_verified": "", "author": "security", "prev_digest": prev}


def _legacy(count: int, halt_at: int | None = None) -> list[dict[str, str]]:
    """Rows as the pre-1.0 signer wrote them: every link empty."""
    return [_row(i + 1, "HALT" if i + 1 == halt_at else "PASS") for i in range(count)]


def _chained(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        r = dict(r, prev_digest=row_chain_digest(out[-1]) if out else "")
        out.append(r)
    return out


def test_pre_fix_log_with_two_signed_rows_verifies_as_a_legacy_prefix():
    report = verify_chain(_legacy(3), SIGNED, signing_active=True)
    assert report.chained and report.legacy_prefix == 2


def test_prefix_is_not_accepted_without_signing():
    with pytest.raises(RuntimeError, match="chain broken at row 2"):
        verify_chain(_legacy(2), UNSIGNED, signing_active=False)


def test_first_real_link_must_bind_the_row_before_it():
    rows = _legacy(3)
    rows.append(_row(4, prev=row_chain_digest(rows[-1])))
    assert verify_chain(rows, SIGNED, signing_active=True).legacy_prefix == 2
    rows[-1]["prev_digest"] = row_chain_digest(rows[0])  # binds the wrong row
    with pytest.raises(RuntimeError, match="row 4"):
        verify_chain(rows, SIGNED, signing_active=True)


def test_an_empty_link_after_the_chain_starts_is_a_break():
    rows = _legacy(2)
    rows.append(_row(3, prev=row_chain_digest(rows[-1])))
    rows.append(_row(4))  # an unchained row after a chained one
    with pytest.raises(RuntimeError, match="row 4"):
        verify_chain(rows, SIGNED, signing_active=True)


def test_deleting_the_last_prefix_row_is_caught():
    rows = _legacy(3)
    rows.append(_row(4, prev=row_chain_digest(rows[-1])))
    del rows[2]
    with pytest.raises(RuntimeError, match="chain broken"):
        verify_chain(rows, SIGNED, signing_active=True)


def test_a_link_on_row_one_is_a_break():
    with pytest.raises(RuntimeError, match="row 1"):
        verify_chain([_row(1, prev="ab" * 32)], SIGNED, signing_active=True)


def test_halts_inside_the_prefix_are_reported():
    report = verify_chain(_legacy(3, halt_at=2), SIGNED, signing_active=True)
    assert report.halts_in_prefix == [2]
    assert verify_chain(_chained(_legacy(3, halt_at=2)), SIGNED, signing_active=True).halts_in_prefix == []


def test_tip_is_never_empty_once_the_log_has_a_row_and_refuses_a_broken_chain():
    assert chain_tip([], SIGNED, signing_active=True) == ""
    rows = _legacy(2)
    assert chain_tip(rows, SIGNED, signing_active=True) == row_chain_digest(rows[-1])
    with pytest.raises(RuntimeError):
        chain_tip(_legacy(2), UNSIGNED, signing_active=False)


def test_a_fully_chained_log_has_no_prefix():
    assert verify_chain(_chained(_legacy(4)), SIGNED, signing_active=True).legacy_prefix == 0


def _write_log(root: Path, rows: list[dict[str, str]]) -> None:
    log = root / "references" / "security-decisions.log.csv"
    log.parent.mkdir(parents=True)
    with log.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SIGNED, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_signed_append_refuses_when_the_tip_moved(tmp_path):
    rows = _chained(_legacy(2))
    _write_log(tmp_path, rows)
    dl.append_signed_decision_row(tmp_path, _row(3, prev=row_chain_digest(rows[-1])))
    with pytest.raises(RuntimeError, match="changed while the decision was being confirmed"):
        dl.append_signed_decision_row(tmp_path, _row(4, prev=row_chain_digest(rows[-1])))  # stale tip
    read = dl._read_decision_rows(tmp_path)
    assert read is not None and len(read[0]) == 3
    dl._assert_decision_chain_intact(*read)


def test_a_log_created_by_the_first_signed_row_is_readable_and_chains(tmp_path):
    """The first signed append creates the log; it must be readable, and the second row must chain."""
    (tmp_path / "references").mkdir()
    dl.append_signed_decision_row(tmp_path, _row(1))
    read = dl._read_decision_rows(tmp_path)  # raised "malformed" before 1.0
    assert read is not None and read[0][0]["timestamp"] == "2026-10-01"
    dl.append_signed_decision_row(tmp_path, _row(2, prev=chain_tip(*read, signing_active=True)))
    read = dl._read_decision_rows(tmp_path)
    assert read is not None and len(read[0]) == 2
    assert verify_chain(*read, signing_active=True).legacy_prefix == 0


def test_appending_to_a_pre_fix_log_starts_the_chain(tmp_path):
    rows = _legacy(3)
    _write_log(tmp_path, rows)
    read = dl._read_decision_rows(tmp_path)
    assert read is not None
    dl.append_signed_decision_row(tmp_path, _row(4, prev=chain_tip(*read, signing_active=True)))
    read = dl._read_decision_rows(tmp_path)
    assert read is not None
    dl._assert_decision_chain_intact(*read)  # the legacy prefix plus a correctly bound row
