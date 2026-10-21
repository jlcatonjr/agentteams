"""--verify-integrity names authorizing and retraction rows that fail authentication (VERIFY-INTEGRITY-NONAUTH-ROWS).

After the rc.12 HALT-scan fix, a forged or non-authenticating HALT-RETRACTED row is skipped silently
when a later valid retraction clears the HALT, so nothing ever surfaced it. ``--verify-integrity``
now lists every PASS / CONDITIONAL PASS / HALT-RETRACTED row that does not authenticate, as an
advisory that does not change the exit code.
"""

from __future__ import annotations

import argparse
import csv
import secrets
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl
from agentteams.cli.commands import _run_verify_integrity
from agentteams.cli.decision_chain import _describe_unauthenticated_rows, row_chain_digest

COLUMNS = ["timestamp", "requesting_agent", *dl.SIGNED_DECISION_COLUMNS]


@pytest.fixture
def key(monkeypatch) -> str:
    value = secrets.token_hex(32)  # built at run time, never a literal
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", value)
    return value


def _signed_log(root: Path, specs: list[tuple[str, str, bool]], key: str) -> None:
    """Write a chained, signed log; each spec is ``(action, verdict, forge)``."""
    rows: list[dict[str, str]] = []
    for n, (action, verdict, forge) in enumerate(specs, start=1):
        row = {c: "" for c in COLUMNS}
        row.update(date=f"2026-10-{n:02d}", action_reviewed=action, verdict=verdict, author="security",
                   evidence="the reason it no longer applies", conditions_verified="verified",
                   prev_digest=row_chain_digest(rows[-1]) if rows else "")
        row["signature"] = dl.sign_decision_row(row, key=key)
        if forge:  # a keyless writer's row: any signature that is not the HMAC of its payload
            row["signature"] = secrets.token_hex(32)
        rows.append(row)
    log = root / "references" / "security-decisions.log.csv"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _args(root: Path) -> argparse.Namespace:
    ns = argparse.Namespace(output=str(root), project=None, description=None)
    setattr(ns, "self", False)
    return ns


def test_a_forged_retraction_cleared_by_a_later_valid_one_is_named(tmp_path: Path, key: str, capsys) -> None:
    _signed_log(tmp_path, [("purge-x", "HALT", False), ("purge-x", "HALT-RETRACTED", True),
                           ("purge-x", "HALT-RETRACTED", False), ("purge-y", "PASS", True)], key)
    dl._assert_no_unretracted_halt(tmp_path, action="purge-x")  # the scan is satisfied: nothing raised
    assert _run_verify_integrity(_args(tmp_path)) == 0  # advisory only
    out = capsys.readouterr().out
    assert "2 authorizing or retraction row(s) do not authenticate" in out
    assert "row 2 (HALT-RETRACTED 'purge-x')" in out and "row 4 (PASS 'purge-y')" in out
    assert "row 3 " not in out


def test_an_all_authentic_log_reports_nothing(tmp_path: Path, key: str) -> None:
    _signed_log(tmp_path, [("purge-x", "HALT", False), ("purge-x", "HALT-RETRACTED", False)], key)
    assert _describe_unauthenticated_rows(tmp_path) == []


def test_without_the_key_the_rows_are_reported_unchecked_not_forged(tmp_path: Path, key: str,
                                                                     monkeypatch) -> None:
    _signed_log(tmp_path, [("purge-y", "PASS", False)], key)
    monkeypatch.delenv("AGENTTEAMS_DECISION_SIGNING_KEY")
    lines = _describe_unauthenticated_rows(tmp_path)
    assert len(lines) == 1 and "not checked for row(s) [1]" in lines[0]
